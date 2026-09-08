# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ext_api import gitdeploy


class GitDeployReadinessTests(unittest.TestCase):
    def _discovery(self):
        return {
            "is_git_repo": True,
            "remotes": [],
            "git_config_refs": [],
            "env_refs": [],
            "systemd_refs": [],
            "script_refs": [],
            "other_repos": [],
            "ssh_keys_present": [],
        }

    def test_existing_remote_branch_write_probe_is_noop_not_local_head(self):
        sha = "a" * 40
        calls = []

        def fake_git(args, cwd=None, timeout=300):
            calls.append(list(args))
            if args[:2] == ["ls-remote", "--heads"]:
                return 0, f"{sha}\trefs/heads/{gitdeploy.BRANCH}"
            if args[:2] == ["fetch", "--no-tags"]:
                self.assertEqual(args[-1], sha)
                return 0, "fetched"
            if args[:2] == ["push", "--dry-run"]:
                self.assertEqual(args[-1], f"{sha}:refs/heads/{gitdeploy.BRANCH}")
                self.assertFalse(args[-1].startswith("HEAD:"))
                return 0, "Everything up-to-date"
            self.fail(f"unexpected git call: {args}")

        with patch.object(gitdeploy, "discover", return_value=self._discovery()), \
             patch.object(gitdeploy, "repo_url", return_value=("https://example.invalid/repo.git", "test")), \
             patch.object(gitdeploy, "_git", side_effect=fake_git), \
             patch.object(gitdeploy, "ensure_staging", return_value={"cloned": False}):
            out = gitdeploy.readiness(deep=True)

        self.assertEqual(out["status"], gitdeploy.READY)
        self.assertTrue(any(c[:2] == ["fetch", "--no-tags"] for c in calls))
        self.assertTrue(any(c[:2] == ["push", "--dry-run"] for c in calls))

    def test_missing_remote_branch_still_probes_creation_from_head(self):
        calls = []

        def fake_git(args, cwd=None, timeout=300):
            calls.append(list(args))
            if args[:2] == ["ls-remote", "--heads"]:
                return 0, ""
            if args[:2] == ["push", "--dry-run"]:
                self.assertEqual(args[-1], f"HEAD:refs/heads/{gitdeploy.BRANCH}")
                return 0, "dry-run ok"
            self.fail(f"unexpected git call: {args}")

        with patch.object(gitdeploy, "discover", return_value=self._discovery()), \
             patch.object(gitdeploy, "repo_url", return_value=("https://example.invalid/repo.git", "test")), \
             patch.object(gitdeploy, "_git", side_effect=fake_git), \
             patch.object(gitdeploy, "ensure_staging", return_value={"cloned": True}):
            out = gitdeploy.readiness(deep=True)

        self.assertEqual(out["status"], gitdeploy.READY)
        self.assertFalse(any(c[:2] == ["fetch", "--no-tags"] for c in calls))

    def test_remote_branch_fetch_failure_blocks_write_readiness(self):
        sha = "b" * 40

        def fake_git(args, cwd=None, timeout=300):
            if args[:2] == ["ls-remote", "--heads"]:
                return 0, f"{sha}\trefs/heads/{gitdeploy.BRANCH}"
            if args[:2] == ["fetch", "--no-tags"]:
                return 1, "fetch denied"
            self.fail(f"unexpected git call: {args}")

        with patch.object(gitdeploy, "discover", return_value=self._discovery()), \
             patch.object(gitdeploy, "repo_url", return_value=("https://example.invalid/repo.git", "test")), \
             patch.object(gitdeploy, "_git", side_effect=fake_git), \
             patch.object(gitdeploy, "ensure_staging", return_value={"cloned": False}):
            out = gitdeploy.readiness(deep=True)

        self.assertEqual(out["status"], gitdeploy.BLOCKED)
        self.assertTrue(any("доступ на запись" in reason for reason in out["blocked_by"]))


    def test_git_trusts_only_controlled_publish_worktree_per_invocation(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            seen["cwd"] = kwargs.get("cwd")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with patch.object(gitdeploy, "_git_env", return_value=([], {}, False)), \
             patch.object(gitdeploy.subprocess, "run", side_effect=fake_run):
            code, _ = gitdeploy._git(["status"], cwd=gitdeploy.PUBLISH_TREE)

        self.assertEqual(code, 0)
        self.assertEqual(seen["cwd"], gitdeploy.PUBLISH_TREE)
        self.assertIn("-c", seen["argv"])
        self.assertIn(
            "safe.directory=" + gitdeploy.os.path.realpath(gitdeploy.PUBLISH_TREE),
            seen["argv"],
        )

    def test_git_does_not_trust_arbitrary_directory(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with patch.object(gitdeploy, "_git_env", return_value=([], {}, False)), \
             patch.object(gitdeploy.subprocess, "run", side_effect=fake_run):
            code, _ = gitdeploy._git(["status"], cwd="/tmp/not-boris-controlled")

        self.assertEqual(code, 0)
        self.assertFalse(any(str(x).startswith("safe.directory=") for x in seen["argv"]))


    def test_stale_unowned_worktree_index_lock_self_heals(self):
        with tempfile.TemporaryDirectory() as td:
            tree = os.path.join(td, "publish")
            gitdir = os.path.join(td, "gitdir")
            os.makedirs(tree)
            os.makedirs(gitdir)
            with open(os.path.join(tree, ".git"), "w", encoding="utf-8") as fh:
                fh.write("gitdir: " + gitdir + "\n")
            lock = os.path.join(gitdir, "index.lock")
            open(lock, "w", encoding="utf-8").close()
            old = time.time() - 3600
            os.utime(lock, (old, old))

            with patch.object(gitdeploy, "_path_open_by_process", return_value=False):
                out = gitdeploy._repair_stale_worktree_index_lock(
                    tree, min_age_sec=600
                )

            self.assertTrue(out["removed"])
            self.assertEqual(out["reason"], "stale_lock_removed")
            self.assertFalse(os.path.exists(lock))

    def test_fresh_worktree_index_lock_is_never_removed(self):
        with tempfile.TemporaryDirectory() as td:
            tree = os.path.join(td, "publish")
            gitdir = os.path.join(td, "gitdir")
            os.makedirs(tree)
            os.makedirs(gitdir)
            with open(os.path.join(tree, ".git"), "w", encoding="utf-8") as fh:
                fh.write("gitdir: " + gitdir + "\n")
            lock = os.path.join(gitdir, "index.lock")
            open(lock, "w", encoding="utf-8").close()

            with patch.object(gitdeploy, "_path_open_by_process", return_value=False):
                out = gitdeploy._repair_stale_worktree_index_lock(
                    tree, min_age_sec=600
                )

            self.assertFalse(out["removed"])
            self.assertEqual(out["reason"], "lock_fresh")
            self.assertTrue(os.path.exists(lock))


    def test_syntax_gate_does_not_write_pyc_into_read_only_cache(self):
        with tempfile.TemporaryDirectory() as td:
            app = os.path.join(td, "app")
            pkg = os.path.join(app, "pkg")
            cache = os.path.join(pkg, "__pycache__")
            os.makedirs(cache)
            with open(os.path.join(app, "__init__.py"), "w", encoding="utf-8") as fh:
                fh.write("")
            with open(os.path.join(pkg, "ok.py"), "w", encoding="utf-8") as fh:
                fh.write("VALUE = 1\n")
            os.chmod(cache, 0o500)
            try:
                code, out = gitdeploy._syntax_check_backend_sources(td, sys.executable)
            finally:
                os.chmod(cache, 0o700)

            self.assertEqual(code, 0, out)
            self.assertEqual(os.listdir(cache), [])

    def test_syntax_gate_still_rejects_real_syntax_error(self):
        with tempfile.TemporaryDirectory() as td:
            app = os.path.join(td, "app")
            os.makedirs(app)
            with open(os.path.join(app, "bad.py"), "w", encoding="utf-8") as fh:
                fh.write("def broken(:\n")

            code, _ = gitdeploy._syntax_check_backend_sources(td, sys.executable)

            self.assertNotEqual(code, 0)


    def test_rollback_restores_existing_and_removes_new_files(self):
        with tempfile.TemporaryDirectory() as td:
            backup = os.path.join(td, "backup")
            dst = os.path.join(td, "dst")
            old_rel = "backend/app/old.py"
            new_rel = "backend/app/new.py"
            os.makedirs(os.path.dirname(os.path.join(backup, old_rel)), exist_ok=True)
            os.makedirs(os.path.dirname(os.path.join(dst, old_rel)), exist_ok=True)

            with open(os.path.join(backup, old_rel), "w", encoding="utf-8") as fh:
                fh.write("OLD\n")
            with open(os.path.join(dst, old_rel), "w", encoding="utf-8") as fh:
                fh.write("BROKEN\n")
            with open(os.path.join(dst, new_rel), "w", encoding="utf-8") as fh:
                fh.write("NEW\n")

            out = gitdeploy._rollback_files(
                [old_rel, new_rel],
                backup,
                dst,
                {old_rel: True, new_rel: False},
            )

            self.assertTrue(out["ok"])
            self.assertEqual(out["restored"], [old_rel])
            self.assertEqual(out["removed_new"], [new_rel])
            with open(os.path.join(dst, old_rel), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "OLD\n")
            self.assertFalse(os.path.exists(os.path.join(dst, new_rel)))


    def test_no_runtime_units_is_successful_noop_restart(self):
        self.assertEqual(
            gitdeploy._units_for(["backend/app/ext_api/.transport_ok"]),
            [],
        )
        self.assertTrue(gitdeploy._restart_detail_ok([], []))
        self.assertTrue(
            gitdeploy._restart_detail_ok(
                ["boris-backend"],
                [{"restarted": True, "active": True}],
            )
        )
        self.assertFalse(
            gitdeploy._restart_detail_ok(
                ["boris-backend"],
                [{"restarted": False, "active": True}],
            )
        )


if __name__ == "__main__":
    unittest.main()
