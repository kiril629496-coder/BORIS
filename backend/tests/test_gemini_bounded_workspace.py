# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from unittest.mock import patch

from app.ext_api import executor, lead, repo
from app.ext_api.errors import ApiError


class GeminiBoundedWorkspaceTest(unittest.TestCase):
    def _order(self, scope):
        return {
            "order_id": 990001,
            "dev_job_id": "dev_990001",
            "phase": "PLANNING",
            "goal": "inspect exact bounded files",
            "scope": scope,
            "acceptance_criteria": ["files are visible"],
            "forbidden_changes": [],
            "required_qa": [],
            "required_evidence": [],
            "iteration": 1,
            "max_iterations": 3,
            "last_evidence": {},
        }

    def test_read_only_scope_paths_are_materialized_with_mixed_path_roots(self):
        with tempfile.TemporaryDirectory() as root:
            backend_file = os.path.join(root, "backend", "app", "api", "sample.py")
            frontend_file = os.path.join(root, "frontend", "app", "page.tsx")
            os.makedirs(os.path.dirname(backend_file), exist_ok=True)
            os.makedirs(os.path.dirname(frontend_file), exist_ok=True)
            with open(backend_file, "w", encoding="utf-8") as fh:
                fh.write("VALUE = 1\n")
            with open(frontend_file, "w", encoding="utf-8") as fh:
                fh.write("export default function Page(){return null}\n")

            order = self._order({
                "inspection_paths": ["app/api/sample.py", "../frontend/app/page.tsx"],
                "read_only_paths": ["tests/missing_read_only_evidence.py"],
                "read_only": True,
                "scope_mode": "planning_read_only",
            })
            with patch.object(repo, "ROOT", root), \
                 patch.object(executor.dev, "get", return_value={"title": "bounded test", "scope": {}}):
                files = executor._gemini_context_candidates(order)

            self.assertIn("backend/app/api/sample.py", files)
            self.assertIn("frontend/app/page.tsx", files)
            self.assertNotIn("app/api/sample.py", files)

    def test_prepare_workspace_copies_allowed_inspection_files(self):
        with tempfile.TemporaryDirectory() as root:
            src = os.path.join(root, "backend", "app", "api", "sample.py")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("VALUE = 2\n")
            worker = os.path.join(root, "updates", "workers", "test")
            order = self._order({
                "inspection_paths": ["app/api/sample.py"],
                "read_only": True,
                "scope_mode": "planning_read_only",
            })
            with patch.object(repo, "ROOT", root), \
                 patch.object(executor, "WORKSPACE", worker), \
                 patch.object(executor.dev, "get", return_value={"title": "bounded test", "scope": {}}):
                work, ctx = executor._prepare_gemini_workspace(order)

            copied = os.path.join(work, "source", "backend", "app", "api", "sample.py")
            self.assertTrue(os.path.isfile(copied), copied)
            self.assertIn("backend/app/api/sample.py", ctx["source_snapshot_files"])
            self.assertGreater(ctx["source_snapshot_bytes"], 0)

    def test_explicit_240kb_file_is_included_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as root:
            src = os.path.join(root, "backend", "posting_runner.py")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, "wb") as fh:
                fh.write(b"x" * 240517)
            order = self._order({"paths": ["backend/posting_runner.py"]})
            with patch.object(repo, "ROOT", root), \
                 patch.object(executor.dev, "get",
                              return_value={"title": "dev977 shape", "scope": {}}):
                files = executor._gemini_context_candidates(order)
            self.assertIn("backend/posting_runner.py", files)

    def test_explicit_file_over_300kb_fails_closed_with_name(self):
        with tempfile.TemporaryDirectory() as root:
            src = os.path.join(root, "backend", "too_large.py")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, "wb") as fh:
                fh.write(b"x" * 300001)
            order = self._order({"paths": ["backend/too_large.py"]})
            with patch.object(repo, "ROOT", root), \
                 patch.object(executor.dev, "get",
                              return_value={"title": "large explicit", "scope": {}}):
                with self.assertRaises(ApiError) as cm:
                    executor._gemini_context_candidates(order)
            self.assertEqual(cm.exception.code, "CONTEXT_TRUNCATED")
            self.assertIn("backend/too_large.py", cm.exception.message)

    def test_explicit_files_over_total_workspace_limit_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for idx in range(3):
                rel = f"backend/explicit_{idx}.py"
                src = os.path.join(root, rel)
                os.makedirs(os.path.dirname(src), exist_ok=True)
                with open(src, "wb") as fh:
                    fh.write(b"x" * 230000)
                paths.append(rel)
            worker = os.path.join(root, "updates", "workers", "test")
            order = self._order({"paths": paths})
            with patch.object(repo, "ROOT", root), \
                 patch.object(executor, "WORKSPACE", worker), \
                 patch.object(executor.dev, "get",
                              return_value={"title": "total limit", "scope": {}}):
                with self.assertRaises(ApiError) as cm:
                    executor._prepare_gemini_workspace(order)
            self.assertEqual(cm.exception.code, "CONTEXT_TRUNCATED")
            self.assertIn("backend/explicit_2.py", cm.exception.message)

    def test_gemini_reviewer_uses_order_bounded_workspace(self):
        order = self._order({
            "inspection_paths": ["app/ext_api/executor.py"],
            "read_only": True,
            "scope_mode": "planning_read_only",
        })
        with patch.object(lead, "provider", return_value="gemini_cli"), \
             patch.object(executor, "_prepare_gemini_workspace",
                          return_value=("/tmp/boris-review-bounded", {"source_snapshot_files": ["x"]})) as prep, \
             patch.object(lead, "_ask_gemini_cli",
                          return_value={"verdict": "ACCEPT", "note": "ok"}) as ask:
            out = lead.decide(order)

        self.assertEqual(out["verdict"], "ACCEPT")
        prep.assert_called_once_with(order)
        self.assertEqual(ask.call_args.kwargs["cwd"], "/tmp/boris-review-bounded")

    def test_generic_old_workspace_access_block_is_auto_recoverable(self):
        from app.ext_api import a2a
        order = self._order({
            "inspection_paths": ["app/ext_api/executor.py"],
            "read_only": True,
            "scope_mode": "planning_read_only",
        })
        with patch.object(a2a, "get", return_value=order), \
             patch.object(executor, "_gemini_context_candidates",
                          return_value=["backend/app/ext_api/executor.py"]):
            healed = a2a._workspace_access_blocker_healed(
                990001,
                "Рабочая директория /root/BORIS/updates/workers/claude-lead пуста",
            )
        self.assertTrue(healed)

    def test_specific_missing_file_is_not_hidden_by_workspace_recovery(self):
        from app.ext_api import a2a
        with patch.object(a2a, "get") as get_order:
            healed = a2a._workspace_access_blocker_healed(
                990002,
                "The repository source code for the requested investigation path "
                "(app/services/missing.py) is missing from the environment.",
            )
        self.assertFalse(healed)
        get_order.assert_not_called()

    def test_filesystem_scope_recovery_heals_only_when_declared_source_exists(self):
        from app.ext_api import a2a
        existing = self._order({
            "paths": ["backend/app/ext_api/a2a.py"],
            "server_owned": True,
        })
        with patch.object(a2a, "get", return_value=existing), \
             patch.object(
                 executor,
                 "_prepare_gemini_workspace",
                 return_value=("/tmp/boris-proven", {
                     "source_snapshot_files": ["backend/app/ext_api/a2a.py"]
                 }),
             ) as prep:
            healed = a2a._filesystem_scope_blocker_healed(
                990010,
                "Отсутствует доступ к репозиторию проекта в рабочей области.",
            )
        self.assertTrue(healed)
        prep.assert_called_once()

        missing = self._order({
            "paths": ["backend/app/services/definitely_missing_boris_file_xyz.py"],
            "server_owned": True,
        })
        with patch.object(a2a, "get", return_value=missing), \
             patch.object(executor, "_prepare_gemini_workspace") as prep:
            healed = a2a._filesystem_scope_blocker_healed(
                990011,
                "The repository source code for the requested investigation path "
                "is missing from the environment.",
            )
        self.assertFalse(healed)
        prep.assert_not_called()

    def test_workspace_recovery_requeues_only_proven_healed_order(self):
        from app.ext_api import a2a
        rows = [{
            "id": 990003,
            "status": a2a.BLOCKED_INFRA,
            "blocked_reason": "Отсутствует доступ к кодовой базе в рамках назначенной рабочей директории.",
        }]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "_workspace_access_blocker_healed", return_value=True), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            recovered = a2a.recover_internal_blockers(actor="test")
        self.assertEqual(recovered, [990003])
        set_state.assert_called_once_with(
            990003, status=a2a.QUEUED, claimed_by=None, claimed_at=None,
            blocked_reason=None,
        )
        message.assert_called_once()

    def test_context_truncation_recovery_requires_successful_materialization(self):
        from app.ext_api import a2a
        order = self._order({"paths": ["backend/posting_runner.py"]})
        with patch.object(a2a, "get", return_value=order), \
             patch.object(executor, "_prepare_gemini_workspace",
                          return_value=("/tmp/x", {"source_snapshot_files": ["backend/posting_runner.py"]})):
            self.assertTrue(a2a._context_truncation_blocker_healed(
                990004,
                "AUTONOMOUS_CONTEXT_TRUNCATED: old 180000 byte limit",
            ))

        with patch.object(a2a, "get", return_value=order), \
             patch.object(executor, "_prepare_gemini_workspace",
                          side_effect=ApiError("CONTEXT_TRUNCATED", "still too large")):
            self.assertFalse(a2a._context_truncation_blocker_healed(
                990004,
                "AUTONOMOUS_CONTEXT_TRUNCATED: old 180000 byte limit",
            ))


if __name__ == "__main__":
    unittest.main()
