import unittest
from app.ext_api import auth, repo
from app.ext_api.errors import ApiError

class TestAutonomousDev(unittest.TestCase):
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
            self.assertFalse(repo._autonomous_path_allowed(path))

    def test_db_deploy_external_paths_are_hard_denied(self):
        for path in (
            "backend/migrations/001.sql",
            "backend/app/systemd/boris.service",
            "backend/app/api/avito/write.py",
            "backend/app/api/billing/pay.py",
            "backend/app/secrets/token.py",
        ):
            self.assertFalse(repo._autonomous_path_allowed(path))

    def test_scope_and_lock_cover_exact_or_children(self):
        self.assertTrue(repo._covers("backend/app/foo", "backend/app/foo/bar.py"))
        self.assertTrue(repo._covers("backend/app/foo.py", "backend/app/foo.py"))
        self.assertFalse(repo._covers("backend/app/foo", "backend/app/foobar.py"))

    def test_skip_verify_not_part_of_autonomous_apply_signature(self):
        import inspect
        self.assertEqual(list(inspect.signature(repo.autonomous_apply).parameters), ["key", "patch_id"])

    def test_content_guard_denies_destructive_sql(self):
        with self.assertRaises(ApiError):
            repo._validate_autonomous_content("backend/app/safe_module.py", "DROP TABLE users")

if __name__ == '__main__':
    unittest.main()
