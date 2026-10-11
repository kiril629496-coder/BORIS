"""Offline behavior tests against functions extracted from the actual candidate.

The Campaign adapter, database and revision boundary are dependency mocks.
No application import opens a DB/network connection; no provider or Avito call
is available in this harness. Adapter receipt/hash integration is a separate gate.
"""
import ast
import copy
import importlib.util
import json
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import get_type_hints

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_JOBS = ROOT / "tests/banner_jobs_before_fixture.py"
for name in ("app", "app.services"):
    sys.modules.setdefault(name, types.ModuleType(name))
spec = importlib.util.spec_from_file_location(
    "app.services.campaign_banner_job_policy", ROOT / "app/services/campaign_banner_job_policy.py")
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class StaleDataError(Exception):
    pass


def extract(path, names, extra=None):
    tree = ast.parse(Path(path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))
             and (getattr(n, "name", "") in names
                  or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)))]
    env = {"datetime": datetime, "timezone": timezone, "json": json,
           "get_type_hints": get_type_hints, "HTTPException": HTTPException,
           "StaleDataError": StaleDataError, **(extra or {})}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


class DB:
    def __init__(self, job):
        self.job, self.info, self.commits, self.scopes = job, {}, [], []
        self.own_revisions, self.conflict = set(), False
    def commit(self):
        self.commits.append(copy.deepcopy(self.job.payload_json))
    def rollback(self):
        self.info["rollback_called"] = True


@contextmanager
def revision_scope(db, campaign_id, ids, expected, allowed, job_id, reason):
    db.scopes.append((list(ids), copy.deepcopy(expected), job_id))
    if db.conflict:
        raise HTTPException(409, {"code": "CARD_VERSION_CONFLICT"})
    db.info.pop("campaign_written_versions", None)
    yield


revisions = types.ModuleType("app.services.campaign_card_revisions")
revisions.job_revision_scope = revision_scope
sys.modules[revisions.__name__] = revisions
acceptance = types.ModuleType("app.services.campaign_editor_acceptance")
acceptance.operation_for = lambda *a: None
sys.modules[acceptance.__name__] = acceptance
metadata_migration = types.ModuleType("app.services.banner_machine_metadata_migration")
metadata_migration.migrate_receipt = lambda *args, **kwargs: {"status": "test_stub", "changed": False}
sys.modules[metadata_migration.__name__] = metadata_migration


class Campaign:
    FFBannerBody = types.SimpleNamespace
    def __init__(self, case, callback=None):
        self.case, self.callback, self.calls = case, callback, []
        self.guardian, self.ready_reads = [], []
    def _ff_items(self, *args):
        return list(self.case.rows.values())
    def _ff_payload(self, row):
        return row.payload
    def _ff_premium_banner_ready(self, db, row, payload=None, expected_intent=None):
        self.ready_reads.append((row.id, expected_intent))
        p = row.payload if payload is None else payload
        return bool(p.get("receipt_and_hash_verified") is True and (
            expected_intent is None or p.get("banner_regeneration_key") == expected_intent))
    def _ff_account_campaign_ai_policy(self, *args):
        return {"banner_engine": self.case.engine}
    def _ff_paid_qa_forbidden(self, *args):
        return False
    def _campaign_guardian_enqueue(self, *args):
        self.guardian.append(args[-1])
    def ff_workflow_banners(self, campaign_id, body: dict, db, user):
        data = body if isinstance(body, dict) else vars(body)
        self.calls.append(copy.deepcopy(data))
        if self.case.require_maps:
            assert db.commits, "paid boundary entered before a durable checkpoint"
            persisted = db.commits[-1]
            for iid in data["item_ids"]:
                assert persisted["banner_item_intents"][str(iid)] == data["item_idempotency_keys"][str(iid)]
                assert persisted["banner_item_budget_intents"][str(iid)] == data["item_budget_intent_keys"][str(iid)]
        if self.callback:
            return self.callback(self.case, data)
        self.case.pass_items(data["item_ids"], data)
        return {"status": "ok", "changed": data["item_ids"], "errors": []}
    def ff_workflow_texts(self, campaign_id, body: dict, db, user):
        data = body if isinstance(body, dict) else vars(body)
        self.calls.append(copy.deepcopy(data))
        return {"status": "ok", "changed": data["item_ids"], "errors": []}


class Case:
    def __init__(self, count=4, callback=None, payload=None, attempts=1, force=False,
                 original=False, operation="banners"):
        p = {"campaign_id": 900, "account_id": "acct-a", "item_ids": list(range(1, count+1)),
             "remaining": list(range(1, count+1)), "_execution_count": 1,
             "operation": operation, "body": {"force": force},
             "expected_updated_at": {str(i): "v0" for i in range(1, count+1)}}
        p.update(payload or {})
        self.job = types.SimpleNamespace(id=77, account_id="acct-a", owner_user_id=8,
            payload_json=p, progress_done=0, progress_total=count, attempts=attempts, result_json=None)
        self.db = DB(self.job)
        self.rows = {i: types.SimpleNamespace(id=i, account_id="acct-a", payload={}) for i in range(1, count+1)}
        self.engine, self.require_maps = "full_ai_premium", not original
        self.C = Campaign(self, callback)
        self.campaign, self.user = types.SimpleNamespace(id=900), types.SimpleNamespace(id=8)
        context = lambda *a: (self.C, self.campaign, self.user, ["acct-a"], "acct-a")
        path = ORIGINAL_JOBS if original else ROOT / "app/services/jobs.py"
        self.jobs = extract(path, {"_run_campaign_banners", "_run_campaign_banners_premium", "_run_campaign_banners_standard", "_campaign_checkpoint", "_continuation_progress_basis", "_enqueue_scoped_prepare_after_banners"},
                            {"_campaign_job_context": context, "_campaign_mutation_locked": lambda *a: (False, {}, {}),
                             "enqueue": lambda *a, **k: (types.SimpleNamespace(id=999), True), "types": types})
        jobs_mod = types.ModuleType("app.services.jobs")
        jobs_mod._campaign_job_context = context
        sys.modules[jobs_mod.__name__] = jobs_mod
        self.editor = extract(ROOT / "app/services/campaign_editor_jobs.py",
            {"_run_premium_banner_slice", "run", "OPERATIONS", "_BANNER_QA_AUTO_RETRY_LIMIT",
             "_BANNER_QA_RETRY_MARKERS", "_retryable_banner_qa_failure"},
            {"_own_completed_revision": lambda db, job, iid: iid in db.own_revisions})
    def pass_items(self, ids, body=None):
        for iid in ids:
            item = self.rows[iid].payload
            item.update(receipt_and_hash_verified=True, banner_url=f"/image/{iid}.png",
                        banner_provider="openai_full_ai_premium", banner_qa={"passed": True})
            if body and body.get("item_idempotency_keys"):
                item["banner_regeneration_key"] = body["item_idempotency_keys"][str(iid)]
            self.db.own_revisions.add(iid)
        self.db.info["campaign_written_versions"] = {str(i): f"v1-{i}" for i in ids}
    def run(self, editor=False):
        fn = self.editor["run"] if editor else self.jobs["_run_campaign_banners"]
        out = fn(self.db, self.job, self.job.payload_json)
        self.job.result_json = out[0]
        return out


def qa_error(iid, action="retry_qa", **extra):
    return {"id": iid, "code": "premium_batch_qa_not_passed", "retry_action": action,
            "retry_allowed": True, "image_outcome": "succeeded", "banner_id": f"bn-{iid}",
            "version": 1, "artifact_sha256": ("a" if iid % 2 else "b")*64,
            "qa_terminal": action == "regenerate_quality", "qa_rejection_reason": "wrong visible text", **extra}


class WorkerTests(unittest.TestCase):
    def test_maps_commit_before_call_and_batch_membership_does_not_change_identity(self):
        def callback(c, data):
            if len(c.C.calls) == 1:
                c.pass_items([1, 2, 3], data)
                return {"status": "ok", "errors": [qa_error(4)]}
            c.pass_items(data["item_ids"], data)
            return {"status": "ok", "errors": []}
        c = Case(count=5, callback=callback)
        self.assertEqual(c.run()[1], "retry")
        self.assertEqual(c.run()[1], "completed")
        first, second = c.C.calls
        self.assertEqual(first["item_ids"], [1, 2, 3, 4])
        self.assertEqual(second["item_ids"], [4, 5])
        self.assertEqual(first["item_idempotency_keys"]["4"], second["item_idempotency_keys"]["4"])
        self.assertEqual(c.job.payload_json["quality_retry_counts"], {})
    def test_unknown_outcome_preserves_exact_image_and_budget_intent(self):
        err = qa_error(1, "reconcile_image", image_outcome="unknown", banner_id=None,
                       artifact_sha256=None, qa_terminal=False)
        c = Case(1, lambda c, data: {"status": "error", "errors": [err]})
        self.assertEqual(c.run()[1], "retry")
        self.assertEqual(c.run()[1], "retry")
        self.assertEqual(c.C.calls[0]["item_idempotency_keys"], c.C.calls[1]["item_idempotency_keys"])
        self.assertEqual(c.job.payload_json["quality_retry_counts"], {})
        self.assertNotIn("_banner_last_progress_at", c.job.payload_json)
    def test_unknown_reconciliation_not_authorized_stops_without_new_image_key(self):
        c = Case(1, lambda c, data: {"status": "error", "errors": [qa_error(1, "reconcile_image", retry_allowed=False)]})
        self.assertEqual(c.run()[1], "partial")
        self.assertTrue(c.job.payload_json["banner_item_intents"]["1"].endswith(":r0"))
    def test_crash_after_intent_checkpoint_resumes_with_same_keys(self):
        def crash(c, data):
            raise TimeoutError("provider outcome not yet known")
        c = Case(1, crash)
        with self.assertRaises(TimeoutError): c.run()
        first = copy.deepcopy(c.C.calls[0])
        c.job.attempts, c.job.payload_json["_execution_count"] = 2, 2
        c.C.callback = None
        self.assertEqual(c.run()[1], "completed")
        self.assertEqual(first["item_idempotency_keys"], c.C.calls[1]["item_idempotency_keys"])
        self.assertEqual(first["item_budget_intent_keys"], c.C.calls[1]["item_budget_intent_keys"])
    def test_unclassified_timeout_does_not_become_quality_regeneration(self):
        c = Case(1, lambda c, data: {"status": "error", "errors": [{"id": 1, "code": "timeout"}]})
        self.assertEqual(c.run()[1], "partial")
        self.assertEqual(c.job.payload_json["quality_retry_counts"], {})
    def test_only_rejected_item_gets_new_image_key_and_keeps_budget_root(self):
        c = Case(2, lambda c, data: {"status": "error", "errors": [qa_error(1, "regenerate_quality"), qa_error(2)]})
        self.assertEqual(c.run()[1], "retry")
        p = c.job.payload_json
        self.assertEqual(p["quality_retry_counts"], {"1": 1})
        self.assertEqual(p["banner_item_intents"]["1"], "campaign_banners:77:item:1:r1")
        self.assertEqual(p["banner_item_intents"]["2"], "campaign_banners:77:item:2:r0")
        self.assertEqual(p["banner_item_budget_intents"]["1"], "campaign_banners:77:item:1")
    def test_quality_rejection_requires_terminal_current_artifact_evidence(self):
        for patch in ({"qa_terminal": False}, {"artifact_sha256": None}, {"image_outcome": "unknown"}, {"qa_rejection_reason": ""}):
            with self.subTest(patch=patch):
                c = Case(1, lambda c, data: {"status": "error", "errors": [qa_error(1, "regenerate_quality", **patch)]})
                self.assertEqual(c.run()[1], "partial")
                self.assertEqual(c.job.payload_json["quality_retry_counts"], {})
    def test_same_terminal_reject_cannot_authorize_two_generations(self):
        c = Case(1, lambda c, data: {"status": "error", "errors": [qa_error(1, "regenerate_quality")]})
        self.assertEqual(c.run()[1], "retry")
        result, final = c.run()
        self.assertEqual(final, "partial")
        self.assertEqual(result["code"], "BANNER_REJECTION_ALREADY_CONSUMED")
        self.assertEqual(c.job.payload_json["quality_retry_counts"], {"1": 1})
    def test_existing_four_quality_retry_limit_is_enforced_per_item(self):
        def reject(c, data):
            return {"status": "error", "errors": [qa_error(1, "regenerate_quality", version=len(c.C.calls))]}
        c = Case(1, reject)
        for _ in range(4): self.assertEqual(c.run()[1], "retry")
        result, final = c.run()
        self.assertEqual((final, result["code"]), ("partial", "BANNER_QUALITY_RETRY_EXHAUSTED"))
        self.assertEqual(c.job.payload_json["quality_retry_counts"], {"1": 4})
    def test_empty_errors_preflight_block_is_not_completion(self):
        c = Case(1, lambda c, data: {"status": "blocked", "code": "FULL_AI_PREMIUM_NOT_READY", "errors": []})
        result, final = c.run()
        self.assertEqual(final, "partial")
        self.assertEqual(result["code"], "FULL_AI_PREMIUM_NOT_READY")
        self.assertEqual(result["done"], 0)
        self.assertEqual(c.C.guardian, [])
    def test_changed_array_and_old_positive_flag_are_not_current_receipt_proof(self):
        c = Case(1, lambda c, data: {"status": "ok", "changed": [1], "errors": []})
        c.rows[1].payload.update(banner_url="/old.png", banner_provider="openai_full_ai_premium", banner_qa={"passed": True})
        result, final = c.run()
        self.assertEqual(final, "partial")
        self.assertEqual(result["code"], "BANNER_RESULT_UNVERIFIED")
        self.assertGreaterEqual(len(c.C.ready_reads), 2)
    def test_existing_legacy_attempt_never_gets_fresh_identity(self):
        c = Case(1, attempts=2, payload={"_execution_count": 2})
        result, final = c.run()
        self.assertEqual(final, "partial")
        self.assertEqual(result["code"], "LEGACY_BANNER_INTENT_RECONCILIATION_REQUIRED")
        self.assertEqual(c.C.calls, [])
    def test_reconciled_legacy_map_preserves_exact_old_key(self):
        old = "campaign_banners:77:1-2-3-4:r2"
        c = Case(1, attempts=3, payload={"banner_item_intents": {"1": old},
            "banner_item_budget_intents": {"1": "reconciled-existing-order:1"}, "quality_retry_counts": {"1": 2}})
        self.assertEqual(c.run()[1], "completed")
        self.assertEqual(c.C.calls[0]["item_idempotency_keys"]["1"], old)
    def test_legacy_image_map_without_budget_mapping_is_fail_closed(self):
        c = Case(1, attempts=2, payload={"banner_item_intents": {"1": "old-key"}})
        result, final = c.run()
        self.assertEqual((final, result["code"]), ("partial", "BANNER_INTENT_MAPPING_INCOMPLETE"))
        self.assertEqual(c.C.calls, [])
    def test_completed_replay_and_wrong_empty_remaining_recheck_current_rows(self):
        c = Case(1)
        self.assertEqual(c.run()[1], "completed")
        self.assertEqual(c.run()[1], "completed")
        self.assertEqual(len(c.C.calls), 1)
        c.rows[1].payload["receipt_and_hash_verified"] = False
        c.C.callback = lambda c, data: {"status": "blocked", "code": "CURRENT_PROOF_MISSING", "errors": []}
        result, final = c.run()
        self.assertEqual(final, "partial")
        self.assertEqual(result["done"], 0)
    def test_foreign_item_and_foreign_intent_map_block_before_adapter(self):
        c = Case(1); c.rows[1].account_id = "acct-b"
        self.assertEqual(c.run()[1], "partial"); self.assertEqual(c.C.calls, [])
        c = Case(1, payload={"banner_item_intents": {"99": "foreign"}})
        self.assertEqual(c.run()[1], "partial"); self.assertEqual(c.C.calls, [])
    def test_retry_counters_do_not_change_progress_but_saved_artifact_does(self):
        c = Case(1)
        basis = c.jobs["_continuation_progress_basis"]
        a = {"unit": "banners", "stage": "banners", "done": 0, "total": 1, "pending_item_ids": [1], "retry_generation": 0}
        self.assertEqual(basis(a), basis({**a, "retry_generation": 4, "retry_number": 4}))
        self.assertNotEqual(basis(a), basis({**a, "progress_evidence": {"1": "saved-hash"}}))
    def test_editor_premium_uses_four_item_batch_and_preserves_revision_scope(self):
        c = Case(5)
        self.assertEqual(c.run(editor=True)[1], "retry")
        self.assertEqual(c.C.calls[0]["item_ids"], [1, 2, 3, 4])
        self.assertEqual(c.db.scopes[0][0], [1, 2, 3, 4])
        self.assertEqual(c.job.payload_json["expected_updated_at"]["1"], "v1-1")
        self.assertEqual(c.run(editor=True)[1], "completed")
        self.assertEqual(c.C.calls[1]["item_ids"], [5])
        self.assertTrue(c.C.calls[0]["item_idempotency_keys"]["1"].startswith("editor:77:banner:item:1:"))
        self.assertNotIn("campaign_editor_executing", c.db.info)
    def test_editor_force_does_not_accept_old_good_banner_as_new_result(self):
        c = Case(1, force=True)
        c.pass_items([1]); c.db.own_revisions.clear()
        self.assertEqual(c.run(editor=True)[1], "completed")
        self.assertEqual(len(c.C.calls), 1)
        self.assertTrue(c.C.calls[0]["force"])
    def test_editor_own_revision_does_not_certify_missing_current_proof(self):
        c = Case(1, attempts=2, force=True, payload={"_execution_count": 2})
        c.db.own_revisions.add(1)
        result, final = c.run(editor=True)
        self.assertEqual(final, "partial")
        self.assertEqual(c.C.calls, [])
    def test_editor_blocked_empty_errors_never_completes_and_conflict_restores_flag(self):
        c = Case(1, lambda c, data: {"status": "blocked", "code": "FULL_AI_PREMIUM_NOT_READY", "errors": []})
        self.assertEqual(c.run(editor=True)[1], "partial")
        c = Case(1); c.db.conflict = True
        result, final = c.run(editor=True)
        self.assertEqual((final, result["code"]), ("awaiting_confirmation", "CARD_VERSION_CONFLICT"))
        self.assertEqual(c.C.calls, [])
        self.assertNotIn("campaign_editor_executing", c.db.info)
    def test_unrelated_text_editor_still_checkpoints_one_item(self):
        c = Case(5, operation="texts")
        self.assertEqual(c.run(editor=True)[1], "retry")
        self.assertEqual(c.C.calls[0]["item_ids"], [1])
        self.assertNotIn("banner_item_intents", c.job.payload_json)
    def test_standard_campaign_worker_keeps_its_existing_dispatch_and_body(self):
        c = Case(4)
        c.engine, c.require_maps = "local_first", False
        self.assertEqual(c.run()[1], "completed")
        self.assertEqual(c.C.calls[0]["item_ids"], [1, 2, 3, 4])
        self.assertEqual(c.C.calls[0]["idempotency_key"], "campaign_banners:77:1-2-3-4:r0")
        self.assertEqual(c.C.ready_reads, [])
    def test_original_snapshot_reproduces_empty_error_false_completion(self):
        c = Case(1, lambda c, data: {"status": "blocked", "code": "FULL_AI_PREMIUM_NOT_READY", "errors": []}, original=True)
        result, final = c.run()
        self.assertEqual((final, result["done"]), ("completed", 1))
    def test_original_snapshot_reproduces_new_intent_after_unknown_timeout(self):
        c = Case(1, lambda c, data: {"status": "error", "errors": [{"id": 1, "code": "timeout", "image_outcome": "unknown"}]}, original=True)
        self.assertEqual(c.run()[1], "retry")
        self.assertEqual(c.run()[1], "retry")
        self.assertNotEqual(c.C.calls[0]["idempotency_key"], c.C.calls[1]["idempotency_key"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
