# -*- coding: utf-8 -*-
import inspect
import unittest
from unittest.mock import patch

from app.ext_api import auth, repo, devflow
from app.ext_api import dev as dev_module
from app.ext_api.errors import ApiError


class AutonomousDevPolicyTests(unittest.TestCase):
    def test_autonomous_role_never_implies_admin(self):
        key = {"actor_type": "autonomous-dev", "scope_set": {"read", "write", "tasks"}}
        self.assertTrue(auth.is_autonomous_dev(key))
        self.assertNotIn("admin", key["scope_set"])
        self.assertFalse(auth.is_autonomous_dev(dict(key, scope_set={"read", "write", "tasks", "admin"})))

    def test_policy_and_tool_binding_files_are_hard_denied(self):
        for path in (
            "backend/app/ext_api/auth.py",
            "backend/app/ext_api/repo.py",
            "backend/app/ext_api/router.py",
            "backend/app/ext_api/schema.py",
            "backend/app/ext_api/mcp_server.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(repo._autonomous_path_allowed(path))

    def test_db_deploy_external_paths_are_hard_denied(self):
        for path in (
            "backend/migrations/001.sql",
            "backend/app/systemd/boris.service",
            "backend/app/api/avito/write.py",
            "backend/app/api/billing/pay.py",
            "backend/app/secrets/token.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(repo._autonomous_path_allowed(path))

    def test_scope_and_lock_cover_exact_or_children(self):
        self.assertTrue(repo._covers("backend/app/foo", "backend/app/foo/bar.py"))
        self.assertTrue(repo._covers("backend/app/foo.py", "backend/app/foo.py"))
        self.assertFalse(repo._covers("backend/app/foo", "backend/app/foobar.py"))

    def test_skip_verify_not_part_of_autonomous_apply_signature(self):
        self.assertEqual(list(inspect.signature(repo.autonomous_apply).parameters), ["key", "patch_id"])

    def test_content_guard_denies_destructive_sql(self):
        with self.assertRaises(ApiError):
            repo._validate_autonomous_content("backend/app/safe_module.py", "DROP TABLE users")

    def test_coordination_only_job_cannot_apply_patch(self):
        key={"actor_type":"autonomous-dev","scope_set":{"read","write","tasks"}}
        patch_row={"status":"approved","author":"qa","dev_job_id":123}
        job={
            "id":123,
            "scope":{"coordination_only":True,"read_only":True},
            "locks":["coordination:test"],
            "owner_agent":"qa",
            "assigned_agent":"qa",
        }
        with patch.object(repo.auth,"need_scope"), \
             patch.object(repo.auth,"need_autonomous_dev"), \
             patch.object(repo.auth,"actor_of",return_value={"name":"qa"}), \
             patch.object(dev_module,"get",return_value=job), \
             patch.object(repo,"_actor_has_job",return_value=True):
            with self.assertRaises(ApiError) as cm:
                repo._validate_autonomous_patch(
                    key,patch_row,
                    [{"path":"backend/app/safe_module.py","mode":"replace","old":"x","new":"y"}],
                )
        self.assertIn("coordination-only",cm.exception.message)

    def test_read_only_job_cannot_apply_patch(self):
        key={"actor_type":"autonomous-dev","scope_set":{"read","write","tasks"}}
        patch_row={"status":"approved","author":"qa","dev_job_id":124}
        job={
            "id":124,
            "scope":{"read_only":True},
            "locks":[],
            "owner_agent":"qa",
            "assigned_agent":"qa",
        }
        with patch.object(repo.auth,"need_scope"), \
             patch.object(repo.auth,"need_autonomous_dev"), \
             patch.object(repo.auth,"actor_of",return_value={"name":"qa"}), \
             patch.object(dev_module,"get",return_value=job), \
             patch.object(repo,"_actor_has_job",return_value=True):
            with self.assertRaises(ApiError) as cm:
                repo._validate_autonomous_patch(
                    key,patch_row,
                    [{"path":"backend/app/safe_module.py","mode":"replace","old":"x","new":"y"}],
                )
        self.assertIn("read-only",cm.exception.message)

    def test_one_action_development_priority_does_not_starve_behind_priority_60_backlog(self):
        with patch.object(devflow.sched, "P0_PRIORITY", 90), patch.object(devflow.sched, "CLIENT_FLOOR", 95):
            priority = devflow._autonomous_priority(10)
            self.assertEqual(priority, 90)
            self.assertLess(priority, devflow.sched.CLIENT_FLOOR)
            backlog = [{"id": i + 1, "priority": 60} for i in range(50_000)]
            canary = {"id": 85_502, "priority": priority}
            selected = sorted(backlog + [canary], key=lambda r: (-r["priority"], r["id"]))[0]
            self.assertEqual(selected, canary)

    def test_autonomous_development_priority_never_enters_client_class(self):
        with patch.object(devflow.sched, "P0_PRIORITY", 90), patch.object(devflow.sched, "CLIENT_FLOOR", 95):
            self.assertEqual(devflow._autonomous_priority(1000), 94)


if __name__ == "__main__":
    unittest.main()
