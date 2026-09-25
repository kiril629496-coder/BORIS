# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.ext_api import deploy, repo
from app.ext_api.errors import ApiError


def _job(server_owned=True):
    return {"id":1056,"dev_job_id":"dev_1056","scope":{"paths":["backend/app","backend/tests"],"autonomous_dev":True,"server_owned":server_owned},"locks":["backend/app","backend/tests"],"owner_agent":"gpt-executor","reviewer_agent":"claude-lead"}


class ReviewedAutoDeployTests(unittest.TestCase):
    def setUp(self):
        # Exercise real flock contention on a private inode. The certifier owns
        # the production lock throughout pytest; tests must never touch it.
        self.lock_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.lock_tmp.cleanup)
        real_os = os
        private_lock = os.path.join(self.lock_tmp.name, "source-write.lock")
        production_lock = "/tmp/boris-production-source-write.lock"
        class TestOS:
            def __getattr__(self, name):
                return getattr(real_os, name)
            def open(self, path, *args, **kwargs):
                return real_os.open(private_lock if path == production_lock else path, *args, **kwargs)
            def chmod(self, path, *args, **kwargs):
                return real_os.chmod(private_lock if path == production_lock else path, *args, **kwargs)
        scoped_os = patch.object(repo, "os", TestOS())
        scoped_os.start()
        self.addCleanup(scoped_os.stop)

    def test_chatgpt_arbitrary_repo_apply_stays_denied(self):
        key={"scope_set":{"read","write","tasks"},"actor_type":"ai_agent","actor_name":"chatgpt","name":"chatgpt"}
        with self.assertRaises(ApiError) as exc:
            repo.apply(key,1,confirm=True)
        self.assertEqual(exc.exception.code,"FORBIDDEN_SCOPE")

    def test_reviewed_auto_denies_owner_only_bootstrap_core(self):
        for path in ("backend/app/ext_api/repo.py","backend/app/ext_api/deploy.py","backend/app/ext_api/auth.py","backend/app/ext_api/router.py"):
            with self.subTest(path=path): self.assertIn(path,repo.REVIEWED_AUTO_DENY_FILES)

    def test_server_owned_policy_denies_non_executor_author(self):
        with patch.object(repo,"_accepted_review_for_patch",return_value={"order_id":85563}):
            with self.assertRaises(ApiError): repo._reviewed_server_owned_context({"author":"chatgpt"},_job(),["backend/app/foo.py"])

    def test_server_owned_policy_denies_unreviewed_patch(self):
        with patch.object(repo,"_accepted_review_for_patch",return_value=None):
            with self.assertRaises(ApiError): repo._reviewed_server_owned_context({"author":"gpt-executor"},_job(),["backend/app/foo.py"])

    def test_server_owned_policy_denies_foreign_or_out_of_scope_patch(self):
        with patch.object(repo,"_accepted_review_for_patch",return_value={"order_id":85563}):
            with self.assertRaises(ApiError): repo._reviewed_server_owned_context({"author":"gpt-executor"},_job(),["frontend/src/App.tsx"])

    def test_server_owned_reviewed_executor_patch_is_allowed(self):
        with patch.object(repo,"_accepted_review_for_patch",return_value={"order_id":85563}):
            review=repo._reviewed_server_owned_context({"author":"gpt-executor"},_job(),["backend/app/foo.py"])
        self.assertEqual(review["order_id"],85563)

    def test_unreviewed_autonomous_apply_is_denied_before_write(self):
        row={"id":500,"dev_job_id":1056,"author":"gpt-executor","status":"proposed","edits_json":"[]"}
        key={"scope_set":{"read","write","tasks"},"actor_type":"autonomous-dev","actor_name":"gpt-executor","name":"gpt-executor"}
        with patch.object(repo.db,"one",return_value=row):
            with self.assertRaises(ApiError) as exc: repo.autonomous_apply(key,500)
        self.assertEqual(exc.exception.code,"FORBIDDEN_SCOPE")

    def test_source_write_lock_is_global_across_different_patch_ids(self):
        key={"scope_set":{"read","write","tasks"},"actor_type":"autonomous-dev",
             "actor_name":"gpt-executor","name":"gpt-executor"}
        with repo._patch_apply_lock(500001):
            with self.assertRaises(ApiError) as reviewed:
                repo.reviewed_auto_apply(500002)
            with self.assertRaises(ApiError) as autonomous:
                repo.autonomous_apply(key,500003)
        self.assertEqual(reviewed.exception.code,"JOB_NOT_RETRYABLE")
        self.assertEqual(autonomous.exception.code,"JOB_NOT_RETRYABLE")
        self.assertIn("active writer", str(reviewed.exception))
        self.assertIn("active writer", str(autonomous.exception))

    def test_autonomous_apply_allows_same_known_backend_failures(self):
        key={"scope_set":{"read","write","tasks"},"actor_type":"autonomous-dev",
             "actor_name":"gpt-executor","name":"gpt-executor"}
        with tempfile.TemporaryDirectory() as root:
            rel="backend/tests/sample.py"
            full=os.path.join(root,rel)
            os.makedirs(os.path.dirname(full),exist_ok=True)
            open(full,"w",encoding="utf-8").write("VALUE = 'OLD'\n")
            row={
                "id":500002,"dev_job_id":1056,"author":"gpt-executor",
                "status":"approved",
                "edits_json":json.dumps([{
                    "path":rel,"mode":"replace",
                    "old":"VALUE = 'OLD'","new":"VALUE = 'NEW'","content":None
                }]),
            }
            known="FAILED tests/test_known.py::test_known - AssertionError\n"
            runs=iter([(1,known),(1,known)])
            job=_job() | {"current_phase":"BACKEND"}
            with patch.object(repo,"ROOT",root), \
                 patch.object(repo.db,"one",return_value=row), \
                 patch.object(repo.db,"q"), \
                 patch.object(repo,"_validate_autonomous_patch",return_value=(job,[rel])), \
                 patch.object(repo,"_verify_for",return_value=[]), \
                 patch.object(repo,"_pytest_run",side_effect=lambda *a,**k: next(runs)), \
                 patch.object(repo,"get_patch",
                              return_value={"patch_id":500002,"status":"applied"}), \
                 patch("app.ext_api.dev.event",return_value=None):
                out=repo.autonomous_apply(key,500002)
            self.assertEqual(out["status"],"applied")
            self.assertIn("NEW",open(full,encoding="utf-8").read())

    def test_executor_patch_uses_executor_pool_but_not_direct_api_restart(self):
        units=deploy._reviewed_units(["backend/app/ext_api/executor.py"])
        self.assertNotIn("boris-backend",units)
        self.assertNotIn("boris-backend-replica",units)
        self.assertNotIn("boris-background-worker",units)
        self.assertTrue(set(deploy.EXECUTOR_UNITS).issubset(set(units)))

    def test_unrelated_backend_patch_never_directly_restarts_ha_api_pair(self):
        units=deploy._reviewed_units(["backend/app/api/campaigns.py"])
        self.assertNotIn("boris-backend",units)
        self.assertNotIn("boris-backend-replica",units)
        self.assertNotIn("boris-background-worker",units)

    def test_reviewed_apply_has_no_admin_key_parameter(self):
        self.assertEqual(repo.reviewed_auto_apply.__code__.co_argcount,1)
        self.assertEqual(repo.approve_reviewed_auto.__code__.co_argcount,1)

    def test_workspace_sync_patch_restarts_dispatcher(self):
        units=deploy._reviewed_units(["backend/app/ext_api/workspace_sync.py"])
        self.assertIn("boris-dispatcher",units)
        self.assertNotIn("boris-backend",units)
        self.assertNotIn("boris-backend-replica",units)

    def test_pytest_regression_baseline_allows_same_known_failures(self):
        baseline="FAILED tests/test_a.py::test_a - AssertionError\nFAILED tests/test_b.py::test_b - AssertionError\n"
        post="FAILED tests/test_a.py::test_a - AssertionError\nFAILED tests/test_b.py::test_b - AssertionError\n"
        out=repo._pytest_regression_compare(1,baseline,1,post)
        self.assertTrue(out["ok"])
        self.assertEqual(out["new_failures"],[])

    def test_pytest_regression_baseline_blocks_new_failure(self):
        baseline="FAILED tests/test_a.py::test_a - AssertionError\n"
        post="FAILED tests/test_a.py::test_a - AssertionError\nFAILED tests/test_new.py::test_new - AssertionError\n"
        out=repo._pytest_regression_compare(1,baseline,1,post)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"],"new_test_regressions")
        self.assertEqual(out["new_failures"],["FAILED:tests/test_new.py::test_new"])

    def test_pytest_regression_baseline_blocks_unclassified_runner_error(self):
        out=repo._pytest_regression_compare(2,"internal pytest crash",2,"another crash")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"],"baseline_pytest_failure_unclassified")


    def test_runtime_postflight_accepts_only_successful_idle_workers(self):
        def run_case(unit, active, show, expected):
            def fake_run(argv, cwd=None, timeout=900):
                if argv[:2] == ["systemctl", "is-active"]:
                    return (0, "active\n") if active else (3, "inactive\n")
                if argv[:2] == ["systemctl", "show"]:
                    return 0, show
                raise AssertionError(argv)
            with patch.object(deploy, "health", return_value={"ok": True}), \
                 patch.object(deploy, "_run", side_effect=fake_run):
                out = deploy._runtime_postflight([unit])
            self.assertEqual(out["ok"], expected)

        idle_ok = "Result=success\nActiveState=inactive\nSubState=dead\n"
        failed = "Result=exit-code\nActiveState=failed\nSubState=failed\n"
        run_case("boris-executor", False, idle_ok, True)
        run_case("boris-lead", False, idle_ok, True)
        run_case("boris-executor", False, failed, False)
        run_case("boris-dispatcher", False, idle_ok, False)


if __name__ == "__main__":
    unittest.main()
