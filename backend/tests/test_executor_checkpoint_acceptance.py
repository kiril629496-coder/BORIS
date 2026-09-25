# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.ext_api import a2a, executor, repo
from app.ext_api.errors import ApiError


class CheckpointAndAcceptGateTests(unittest.TestCase):
    def _order(self, **kw):
        base = {
            "order_id": 7001,
            "dev_job_id": "dev_968",
            "status": a2a.RUNNING,
            "iteration": 2,
            "required_qa": [],
            "required_evidence": [],
            "last_evidence": None,
        }
        base.update(kw)
        return base

    def test_checkpoint_persists_on_existing_order(self):
        order = self._order()
        with patch.object(a2a, "get", return_value=order), patch.object(a2a, "message") as msg:
            out = a2a.checkpoint(7001, "worker-a", {"step": "read"})
        self.assertEqual(out["step"], "read")
        self.assertEqual(out["iteration"], 2)
        self.assertEqual(msg.call_args.args[1], "CHECKPOINT")

    def test_checkpoint_can_persist_progress_while_blocked_infra(self):
        order = self._order(status=a2a.BLOCKED_INFRA)
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "message") as msg:
            out = a2a.checkpoint(7001, "worker-a", {"stage": "verified"})
        self.assertEqual(out["stage"], "verified")
        self.assertEqual(out["status"], a2a.BLOCKED_INFRA)
        self.assertEqual(msg.call_args.args[1], "CHECKPOINT")

    def test_checkpoint_stays_forbidden_after_accept(self):
        order = self._order(status=a2a.ACCEPTED)
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "message") as msg:
            with self.assertRaises(ApiError) as cm:
                a2a.checkpoint(7001, "worker-a", {"stage": "late"})
        self.assertEqual(cm.exception.code, "JOB_NOT_RETRYABLE")
        msg.assert_not_called()

    def test_last_checkpoint_reads_newest_message(self):
        row = {"body_json": json.dumps({"step": "tests"}),
               "author": "worker-b", "created_at": "now"}
        with patch.object(a2a.db, "one", return_value=row):
            out = a2a.last_checkpoint(7001)
        self.assertEqual(out["step"], "tests")
        self.assertEqual(out["_author"], "worker-b")

    def test_resume_checkpoint_skips_transient_provider_noise(self):
        rows = [
            {
                "body_json": json.dumps({
                    "reason": "all_free_providers_unavailable",
                    "provider_attempts": [{"provider": "gemini_cli", "kind": "RATE_LIMITED"}],
                }),
                "author": "worker-new",
                "created_at": "newest",
            },
            {
                "body_json": json.dumps({
                    "reason": "provider_failover",
                    "provider": "gemini_cli",
                    "kind": "RATE_LIMITED",
                }),
                "author": "worker-new",
                "created_at": "newer",
            },
            {
                "body_json": json.dumps({
                    "stage": "focused_p1_fix_verified",
                    "summary": "proof already complete",
                }),
                "author": "worker-old",
                "created_at": "progress",
            },
        ]
        with patch.object(a2a.db, "rows", return_value=rows):
            out = a2a.resume_checkpoint(7001)
        self.assertEqual(out["stage"], "focused_p1_fix_verified")
        self.assertEqual(out["summary"], "proof already complete")
        self.assertEqual(
            out["_latest_transient"]["reason"],
            "all_free_providers_unavailable",
        )

    def test_resume_checkpoint_returns_transient_when_no_progress_exists(self):
        rows = [{
            "body_json": json.dumps({"reason": "provider_failover", "provider": "gemini_cli"}),
            "author": "worker-a",
            "created_at": "now",
        }]
        with patch.object(a2a.db, "rows", return_value=rows):
            out = a2a.resume_checkpoint(7001)
        self.assertEqual(out["reason"], "provider_failover")

    def test_order_prompt_contains_checkpoint_for_resume(self):
        order = {
            "order_id": 7001, "dev_job_id": "dev_968", "phase": "BACKEND",
            "goal": "x", "scope": {}, "acceptance_criteria": [],
            "forbidden_changes": [], "required_qa": [], "required_evidence": [],
            "iteration": 2, "last_review": {},
        }
        with patch.object(a2a, "resume_checkpoint",
                          return_value={"step": "tests", "_author": "worker-a"}),              patch.object(executor, "_development_contract", return_value="CONTRACT"):
            text = executor.order_prompt(order)
        self.assertIn("RESUME_CHECKPOINT", text)
        self.assertIn('"step": "tests"', text)

    def test_accept_gate_rejects_missing_e2e(self):
        order = self._order(
            status=a2a.REVIEW,
            required_qa=["Compile/import", "Targeted tests", "Runtime QA", "A2A E2E"],
            required_evidence=["Compile output", "Tests output", "Runtime QA output", "E2E output"],
            last_evidence={"evidence": "proof", "build": "PASS", "tests": "PASS",
                           "runtime": "PASS", "e2e": None, "files_changed": []},
        )
        with patch.object(a2a, "get", return_value=order),              patch.object(a2a.auth, "actor_of", return_value={"name": "reviewer"}),              patch.object(a2a, "message") as msg:
            with self.assertRaises(ApiError) as cm:
                a2a.review({"actor_name": "reviewer"}, 7001, "ACCEPT")
        self.assertEqual(cm.exception.code, "ACCEPTANCE_NOT_PASSED")
        self.assertIn("E2E", cm.exception.message)
        self.assertEqual(msg.call_args.args[1], "ACCEPT_EVIDENCE_REJECTED")

    def test_accept_gate_requires_applied_patch_backup_verify(self):
        order = self._order(
            status=a2a.REVIEW,
            required_qa=["Compile", "Tests", "Runtime QA", "E2E"],
            required_evidence=["PATCH_ID and files changed", "Backup evidence",
                               "Verification", "Compile output", "Tests output",
                               "Runtime QA output", "E2E output"],
            last_evidence={"evidence": "proof", "build": "PASS", "tests": "PASS",
                           "runtime": "PASS", "e2e": "PASS",
                           "files_changed": ["backend/app/ext_api/a2a.py"]},
        )
        fake_patch = {"status": "applied",
                      "files": ["backend/app/ext_api/a2a.py"],
                      "backup_dir": "", "verify_output": ""}
        with patch("app.ext_api.repo.list_patches", return_value=[fake_patch]):
            gaps = a2a._accept_evidence_gaps(order)
        self.assertIn("BACKUP", gaps)
        self.assertIn("VERIFY", gaps)
        self.assertNotIn("PATCH_APPLIED", gaps)

    def test_accept_gate_passes_with_complete_factual_evidence(self):
        order = self._order(
            status=a2a.REVIEW,
            required_qa=["Compile", "Tests", "Runtime QA", "E2E"],
            required_evidence=["PATCH_ID and files changed", "Backup evidence",
                               "Verification", "Compile output", "Tests output",
                               "Runtime QA output", "E2E output"],
            last_evidence={"evidence": "proof", "build": "compile PASS",
                           "tests": "tests PASS", "runtime": "runtime PASS",
                           "e2e": "E2E PASS",
                           "files_changed": ["backend/app/ext_api/a2a.py"]},
        )
        fake_patch = {"status": "applied",
                      "files": ["backend/app/ext_api/a2a.py"],
                      "backup_dir": "/backup/1", "verify_output": "VERIFY PASS"}
        with patch("app.ext_api.repo.list_patches", return_value=[fake_patch]):
            self.assertEqual(a2a._accept_evidence_gaps(order), [])

    def test_server_owned_review_breaks_apply_accept_cycle_safely(self):
        order = self._order(
            status=a2a.REVIEW,
            scope={"server_owned": True, "autonomous_dev": True},
            required_qa=["Compile", "Tests", "Runtime QA", "E2E"],
            required_evidence=["PATCH_ID and files changed", "Backup evidence", "Verification"],
            last_evidence={"evidence": "reviewable proposed patch",
                           "files_changed": ["frontend/app/dashboard/development/page.tsx"]},
        )
        proposed = {"status": "proposed",
                    "files": ["frontend/app/dashboard/development/page.tsx"],
                    "backup_dir": "", "verify_output": ""}
        with patch("app.ext_api.repo.list_patches", return_value=[proposed]):
            self.assertEqual(a2a._accept_evidence_gaps(order), [])

    def test_server_owned_review_requires_real_proposed_patch(self):
        order = self._order(
            status=a2a.REVIEW,
            scope={"server_owned": True, "autonomous_dev": True},
            required_qa=["Runtime QA"],
            required_evidence=["PATCH_ID and files changed"],
            last_evidence={"evidence": "claim only",
                           "files_changed": ["frontend/app/dashboard/development/page.tsx"]},
        )
        with patch("app.ext_api.repo.list_patches", return_value=[]):
            self.assertIn("PATCH_PROPOSED", a2a._accept_evidence_gaps(order))

    def test_read_only_planning_ignores_stale_mutation_evidence_contract(self):
        order = self._order(
            status=a2a.REVIEW,
            phase="PLANNING",
            scope={
                "read_only": True,
                "scope_mode": "planning_read_only",
                "inspection_paths": ["app/ext_api"],
                "server_owned": True,
                "autonomous_dev": True,
            },
            required_qa=[],
            required_evidence=[
                "applied patch id and files",
                "backup/verify output",
            ],
            last_evidence={
                "evidence": "repo inspection PASS",
                "files_changed": [],
            },
        )
        with patch("app.ext_api.repo.list_patches") as list_patches:
            gaps = a2a._accept_evidence_gaps(order)
        self.assertEqual(gaps, [])
        list_patches.assert_not_called()

    def test_read_only_planning_rejects_claimed_file_changes(self):
        order = self._order(
            status=a2a.REVIEW,
            phase="PLANNING",
            scope={
                "read_only": True,
                "scope_mode": "planning_read_only",
                "inspection_paths": ["app/ext_api"],
            },
            last_evidence={
                "evidence": "inspection",
                "files_changed": ["backend/app/ext_api/a2a.py"],
            },
        )
        gaps = a2a._accept_evidence_gaps(order)
        self.assertIn("READ_ONLY_SCOPE_WRITE", gaps)
        self.assertNotIn("PATCH_APPLIED", gaps)
        self.assertNotIn("PATCH_PROPOSED", gaps)

    def test_full_mock_lifecycle_claim_checkpoint_review_accept_complete_same_order(self):
        from app.ext_api import lead

        state = {
            "order_id": 7010,
            "dev_job_id": "dev_1057",
            "phase": "TESTING",
            "goal": "mock lifecycle",
            "scope": {"server_owned": True, "autonomous_dev": True},
            "acceptance_criteria": [],
            "forbidden_changes": [],
            "required_qa": [],
            "required_evidence": [],
            "assigned_to": "gpt-executor",
            "status": a2a.QUEUED,
            "iteration": 0,
            "max_iterations": 6,
            "last_evidence": None,
            "last_review": None,
            "claimed_by": None,
            "claimed_at": None,
        }
        events = []

        def get_order(_oid, with_messages=True):
            return dict(state)

        def set_order(_oid, **fields):
            evidence = fields.pop("last_evidence_json", None)
            review = fields.pop("last_review_json", None)
            state.update(fields)
            if evidence is not None:
                state["last_evidence"] = json.loads(evidence)
            if review is not None:
                state["last_review"] = json.loads(review)

        def db_one(sql, **params):
            if "UPDATE ext_a2a_orders" in sql and "status=:running" in sql:
                self.assertEqual(params["oid"], 7010)
                self.assertEqual(state["status"], a2a.QUEUED)
                state["status"] = a2a.RUNNING
                state["claimed_by"] = params["worker"]
                state["claimed_at"] = "now"
                return {"id": 7010}
            raise AssertionError("unexpected SQL in mock lifecycle: " + sql[:120])

        def event(_oid, kind, author, body=None):
            events.append((kind, author, body or {}))

        provider_result = {
            "summary": "mock provider completed factual TESTING work",
            "files_changed": [],
            "build": "PASS",
            "tests": "PASS",
            "runtime": "PASS",
            "e2e": "PASS",
            "acceptance_criteria": [],
            "evidence": "MOCK_PROVIDER_EVIDENCE=PASS",
            "blockers": [],
            "safety": "no external calls",
        }
        provider_attempt = {
            "result": provider_result,
            "provider": "mock",
            "model": "mock",
            "steps": 1,
            "calls": 0,
            "ptok": 0,
            "ctok": 0,
        }

        with patch.object(a2a, "recover_internal_blockers"), \
             patch.object(a2a, "get", side_effect=get_order), \
             patch.object(a2a.db, "one", side_effect=db_one), \
             patch.object(a2a, "_set", side_effect=set_order), \
             patch.object(a2a, "message", side_effect=event), \
             patch.object(a2a, "reconcile_parent_acceptance", return_value={}), \
             patch.object(a2a, "heartbeat_claim", return_value=True), \
             patch.object(executor, "worker_name", return_value="mock-worker"), \
             patch.object(executor, "_run_with_provider_failover",
                          return_value=provider_attempt), \
             patch.object(executor, "_materialize_model_patch"), \
             patch.object(executor, "_validate_real_change_evidence"):
            claimed = a2a.claim_order(
                7010, "gpt-executor", worker="mock-worker"
            )
            self.assertEqual(claimed["status"], a2a.RUNNING)
            checkpoint = a2a.checkpoint(
                7010, "mock-worker", {"stage": "after_claim"}
            )
            self.assertEqual(checkpoint["stage"], "after_claim")
            reviewed = executor.execute(claimed)
            self.assertEqual(reviewed["status"], a2a.REVIEW)
            accepted = a2a.review(
                {"actor_name": "mock-reviewer", "actor_type": "ai_agent"},
                7010, "ACCEPT", note="mock deterministic review",
            )
            self.assertEqual(accepted["status"], a2a.ACCEPTED)

        job = {
            "id": 1057,
            "dev_job_id": "dev_1057",
            "status": "testing",
            "current_phase": "TESTING",
            "phases": {"TESTING": {"state": "pending"}},
            "required_phases": ["TESTING"],
            "acceptance_criteria": [],
            "goal": "mock lifecycle",
            "scope": {"server_owned": True, "autonomous_dev": True},
        }
        completed = dict(job, status="completed", current_phase="COMPLETED")
        completed["phases"] = {"TESTING": {"state": "pass"}}
        phase_calls = []
        complete_calls = []

        def update_phase(_key, _jid, phase, phase_state, **kwargs):
            phase_calls.append((phase, phase_state, kwargs.get("evidence")))
            job["phases"][phase] = {"state": phase_state}
            return dict(job)

        def complete_job(_key, _jid, summary=None):
            complete_calls.append(summary)
            return dict(completed)

        accepted_for_advance = dict(state)
        with patch.object(lead.dev, "get", return_value=dict(job)), \
             patch.object(lead.dev, "update_phase", side_effect=update_phase), \
             patch.object(lead, "_close_criteria", return_value=[]), \
             patch.object(lead, "plan", return_value={
                 "decision": "DONE",
                 "reason": "mock lifecycle complete",
                 "not_applicable_phases": [],
             }), \
             patch.object(lead.dev, "complete", side_effect=complete_job), \
             patch.object(lead.a2a, "message"), \
             patch.object(lead.notify, "done"):
            out = lead._advance(accepted_for_advance)

        self.assertEqual(out["status"], "completed")
        self.assertEqual(phase_calls[0][0:2], ("TESTING", "pass"))
        self.assertTrue(complete_calls)
        kinds = [x[0] for x in events]
        self.assertEqual(kinds.count("CLAIMED"), 1)
        self.assertEqual(kinds.count("CHECKPOINT"), 1)
        self.assertEqual(kinds.count("REVIEW_REQUEST"), 1)
        self.assertEqual(kinds.count("ACCEPT"), 1)
        self.assertEqual(state["order_id"], 7010)
        self.assertEqual(state["status"], a2a.ACCEPTED)

    def test_worker_crash_stale_lease_requeues_same_order_without_duplicate(self):
        rows = [{"id": 7011, "claimed_by": "dead-worker"}]
        changed = []
        messages = []
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "_set",
                          side_effect=lambda oid, **kw: changed.append((oid, kw))), \
             patch.object(a2a, "message",
                          side_effect=lambda *args, **kwargs: messages.append(args)):
            recovered = a2a.release_stale_claims(minutes=3)
        self.assertEqual(
            recovered,
            [{"order_id": 7011, "was": "dead-worker", "orphan": False}],
        )
        self.assertEqual(changed, [(
            7011,
            {"status": a2a.QUEUED, "claimed_by": None, "claimed_at": None},
        )])
        self.assertEqual(messages[0][0], 7011)
        self.assertEqual(messages[0][1], "RESET")

    def test_startup_recovery_after_executor_error_releases_same_order(self):
        rows = [{
            "id": 7012,
            "body_json": json.dumps({
                "code": "VALIDATION_ERROR",
                "message": "worker died after persisting executor error",
            }),
        }]
        releases = []
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(
                 a2a, "release_order",
                 side_effect=lambda oid, reason, actor=None:
                     releases.append((oid, reason, actor)) or {
                         "order_id": oid, "status": a2a.QUEUED,
                     },
             ):
            recovered = a2a.recover_stranded_executor_orders(
                "gpt-executor", actor="startup-recovery"
            )
        self.assertEqual(recovered, [7012])
        self.assertEqual(releases[0][0], 7012)
        self.assertIn("VALIDATION_ERROR", releases[0][1])

    def test_accept_advances_exactly_once_to_next_required_order(self):
        from app.ext_api import lead

        before = {
            "id": 1057,
            "dev_job_id": "dev_1057",
            "status": "testing",
            "current_phase": "TESTING",
            "phases": {
                "TESTING": {"state": "pending"},
                "INTEGRATION": {"state": "pending"},
            },
            "required_phases": ["TESTING", "INTEGRATION"],
            "acceptance_criteria": [
                {"text": "integration remains", "done": False, "evidence": None},
            ],
            "goal": "mock next-order lifecycle",
            "scope": {"server_owned": True, "autonomous_dev": True},
        }
        after = {
            **before,
            "status": "running",
            "current_phase": "INTEGRATION",
            "phases": {
                "TESTING": {"state": "pass"},
                "INTEGRATION": {"state": "pending"},
            },
        }
        order = {
            "order_id": 7020,
            "dev_job_id": "dev_1057",
            "phase": "TESTING",
            "iteration": 0,
            "status": a2a.ACCEPTED,
            "last_evidence": {
                "summary": "testing passed",
                "evidence": "PASS",
                "files_changed": [],
            },
        }
        phase_calls = []
        next_calls = []
        messages = []

        def update_phase(_key, _jid, phase, state, **kwargs):
            phase_calls.append((phase, state, kwargs.get("evidence")))
            return dict(after)

        def next_order(job, previous, phase=None, goal_override=None):
            next_calls.append((job["current_phase"], previous["order_id"], phase))
            return {
                "order_id": 7021,
                "dev_job_id": "dev_1057",
                "phase": "INTEGRATION",
                "status": a2a.QUEUED,
            }

        with patch.object(lead.dev, "get", side_effect=[
                 dict(before), dict(after), dict(after), dict(after)
             ]), \
             patch.object(lead.dev, "update_phase", side_effect=update_phase), \
             patch.object(lead, "_close_criteria", return_value=[]), \
             patch.object(lead, "_next_order", side_effect=next_order), \
             patch.object(lead.a2a, "message",
                          side_effect=lambda *args, **kwargs: messages.append(args)):
            out = lead._advance(order)

        self.assertEqual(out["current_phase"], "INTEGRATION")
        self.assertEqual(len(phase_calls), 1)
        self.assertEqual(phase_calls[0][0:2], ("TESTING", "pass"))
        self.assertEqual(len(next_calls), 1)
        self.assertEqual(next_calls[0][0:2], ("INTEGRATION", 7020))
        next_messages = [m for m in messages if len(m) > 1 and m[1] == "NEXT_ORDER"]
        self.assertEqual(len(next_messages), 1)

    def test_regression_lock_large_explicit_file_is_materialized(self):
        with tempfile.TemporaryDirectory() as root:
            src = os.path.join(root, "backend", "posting_runner.py")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, "wb") as fh:
                fh.write(b"x" * 240517)
            worker = os.path.join(root, "updates", "workers", "qa")
            order = self._order(
                status=a2a.RUNNING,
                dev_job_id="dev_1057",
                phase="BACKEND",
                scope={"paths": ["backend/posting_runner.py"]},
            )
            with patch.object(repo, "ROOT", root), \
                 patch.object(executor, "WORKSPACE", worker), \
                 patch.object(
                     executor.dev,
                     "get",
                     return_value={"title": "dev977 regression lock", "scope": {}},
                 ):
                work, ctx = executor._prepare_gemini_workspace(order)

            copied = os.path.join(
                work, "source", "backend", "posting_runner.py"
            )
            self.assertTrue(os.path.isfile(copied), copied)
            self.assertEqual(os.path.getsize(copied), 240517)
            self.assertIn(
                "backend/posting_runner.py",
                ctx["source_snapshot_files"],
            )
            self.assertEqual(ctx["source_snapshot_bytes"], 240517)

    def test_submit_review_tuple_blockers_are_rejected_without_state_write(self):
        order = self._order(status=a2a.RUNNING)
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message"):
            with self.assertRaises(ApiError) as cm:
                a2a.submit_review(
                    7001, "worker", "summary",
                    evidence="proof", blockers=("first", "second"),
                )
        self.assertEqual(cm.exception.code, "VALIDATION_ERROR")
        set_state.assert_not_called()

    def test_submit_review_string_blocker_is_one_list_element(self):
        order = self._order(status=a2a.RUNNING)
        captured = {}
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set",
                          side_effect=lambda _oid, **kw: captured.update(kw)), \
             patch.object(a2a, "message"):
            a2a.submit_review(
                7001, "worker", "summary",
                evidence="proof", blockers="one blocker",
            )
        payload = json.loads(captured["last_evidence_json"])
        self.assertEqual(payload["blockers"], ["one blocker"])

    def test_submit_review_blocker_list_is_preserved(self):
        order = self._order(status=a2a.RUNNING)
        captured = {}
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set",
                          side_effect=lambda _oid, **kw: captured.update(kw)), \
             patch.object(a2a, "message"):
            a2a.submit_review(
                7001, "worker", "summary",
                evidence="proof", blockers=["first", "second"],
            )
        payload = json.loads(captured["last_evidence_json"])
        self.assertEqual(payload["blockers"], ["first", "second"])

    def test_submit_review_malformed_blockers_are_rejected(self):
        order = self._order(status=a2a.RUNNING)
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message"):
            with self.assertRaises(ApiError) as cm:
                a2a.submit_review(
                    7001, "worker", "summary",
                    evidence="proof", blockers={"bad": "shape"},
                )
        self.assertEqual(cm.exception.code, "VALIDATION_ERROR")
        set_state.assert_not_called()

    def test_submit_review_releases_worker_claim(self):
        order = self._order(status=a2a.RUNNING)
        captured = {}
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a, "_set", side_effect=lambda _oid, **kw: captured.update(kw)), \
             patch.object(a2a, "message"):
            a2a.submit_review(7001, "worker", "summary", evidence="proof")
        self.assertEqual(captured["status"], a2a.REVIEW)
        self.assertIsNone(captured["claimed_by"])
        self.assertIsNone(captured["claimed_at"])

    def test_blocked_review_releases_worker_claim(self):
        order = self._order(status=a2a.REVIEW)
        captured = {}
        with patch.object(a2a, "get", return_value=order), \
             patch.object(a2a.auth, "actor_of", return_value={"name": "reviewer"}), \
             patch.object(a2a, "_set", side_effect=lambda _oid, **kw: captured.update(kw)), \
             patch.object(a2a, "message"):
            a2a.review({"actor_name": "reviewer"}, 7001, "BLOCKED_INFRA",
                       blocked_reason="provider_unavailable: cooling")
        self.assertEqual(captured["status"], a2a.BLOCKED_INFRA)
        self.assertIsNone(captured["claimed_by"])
        self.assertIsNone(captured["claimed_at"])

    def test_nonrunning_claim_cleanup_is_status_preserving(self):
        rows = [{"id": 7002, "status": a2a.REVIEW, "claimed_by": "worker-old"}]
        with patch.object(a2a.db, "rows", return_value=rows), \
             patch.object(a2a, "_set") as set_state, \
             patch.object(a2a, "message") as message:
            released = a2a.release_nonrunning_claims(actor="test")
        self.assertEqual(released, [7002])
        set_state.assert_called_once_with(7002, claimed_by=None, claimed_at=None)
        message.assert_called_once()

    def test_duplicate_targeted_claim_remains_forbidden(self):
        order = self._order(status=a2a.RUNNING)
        with patch.object(a2a, "recover_internal_blockers"), patch.object(a2a, "get", return_value=order):
            with self.assertRaises(ApiError) as cm:
                a2a.claim_order(7001, "gpt-executor", worker="worker-b")
        self.assertEqual(cm.exception.code, "JOB_NOT_RETRYABLE")


    def test_reviewer_none_becomes_blocked_infra_not_accept(self):
        from app.ext_api import lead

        order = {"order_id": 88002}
        captured = {}
        messages = []

        def fake_review(key, order_id, verdict, note=None, fixes=None, blocked_reason=None):
            captured.update({
                "order_id": order_id,
                "verdict": verdict,
                "blocked_reason": blocked_reason,
            })
            return {
                "order_id": order_id,
                "status": a2a.BLOCKED_INFRA,
                "blocked_reason": blocked_reason,
            }

        with patch.object(lead, "decide", return_value=None), \
             patch.object(lead.a2a, "review", side_effect=fake_review), \
             patch.object(
                 lead.a2a, "message",
                 side_effect=lambda *args, **kwargs: messages.append(args),
             ), \
             patch.object(lead.notify, "blocked_human") as notify_owner:
            out = lead.handle(order)

        self.assertEqual(out["status"], a2a.BLOCKED_INFRA)
        self.assertEqual(captured["verdict"], "BLOCKED_INFRA")
        self.assertIn("no structured verdict", captured["blocked_reason"])
        self.assertTrue(any(len(m) > 1 and m[1] == "REVIEWER_ERROR" for m in messages))
        notify_owner.assert_not_called()

    def test_completion_gate_cannot_be_bypassed_with_open_parent_criterion(self):
        from app.ext_api import dev

        missing = [{
            "gate": "acceptance[0]",
            "state": "not_done",
            "text": "still open",
        }]
        with patch.object(dev, "get", return_value={"id": 1057}), \
             patch.object(dev, "gate_report", return_value=missing), \
             patch.object(dev, "_set") as setter:
            with self.assertRaises(ApiError) as cm:
                dev.complete({"actor_name": "lead"}, "dev_1057", summary="should fail")

        self.assertEqual(cm.exception.code, "POSTFLIGHT_MISMATCH")
        self.assertEqual(cm.exception.details["missing"], missing)
        setter.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
