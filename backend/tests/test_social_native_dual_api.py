"""Actual native412 + actual Social generation/QA; only external IO is fake.

The approved native snapshots are byte-pinned fixtures, never production
imports. Image/Vision/provider, storage, locks and media registration are local
boundaries. Native create/normalize/receipts/read/approval and Social policies
execute their real code. No network, real provider or database is contacted.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
SERVICES = BACKEND / "app/services"
FIXTURES = BACKEND / "tests/fixtures"
NATIVE_PINS = {
    "native_banner_33c5.py": "33c5ce15194fedc92fbf777e477c2baf4fbe700fd1a5a7660c4da70f53a91460",
    "native_banner_412bb.py": "412bb055b7df06cebbaceb355bc314f64db593f172d757b470967db5d2fc4617",
}


def _flow_harness():
    source = BACKEND / "tests/test_social_final_payload_flow.py"
    spec = importlib.util.spec_from_file_location("dual_native_actual_flow_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeLedgerSession:
    """SQL boundary preserving real native query scopes and receipt checks."""
    def __init__(self, case): self.case = case
    def execute(self, sql, params):
        rows = [copy.deepcopy(x) for x in self.case.usage if x["account_id"] == params["a"]]
        if "operation IN" in sql:
            rows = [x for x in rows if x["operation"] in {params["im"], params["sq"], params["bq"]}
                    and x["provenance_json"].get("budget_intent_hash") == params["s"]]
        else:
            rows = [x for x in rows if x["operation"] == params["o"] and x["request_id"] == params["r"]]
        if self.case.hide_native_usage:
            rows = [x for x in rows if x["operation"] != "banner_premium_vision_qa"]
        mapped = SimpleNamespace(first=lambda: rows[0] if rows else None, all=lambda: rows)
        return SimpleNamespace(mappings=lambda: mapped)
    def close(self): pass


class SocialNativeDualApiTests(unittest.TestCase):
    def setUp(self):
        for name, digest in NATIVE_PINS.items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)
        self.assertIn(hashlib.sha256((SERVICES / "banner_full_ai_premium.py").read_bytes()).hexdigest(), NATIVE_PINS.values(),
                      "The active native dependency is not either reviewed version")
        harness = _flow_harness()
        self.f = harness.FinalPayloadFlowTests("test_initial_final_qa_is_single_paid_proof_and_delivery_reuses_it")
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        workspace = self.f.root
        self.f.root = workspace / "banners"
        self.f.root.mkdir()
        self.f.ns["BANNER_DIR"] = str(self.f.root)
        self.rows, self.usage = {}, []
        self.native_image_calls, self.native_qa_calls, self.approvals, self.reads, self.native_applies, self.creates = [], [], [], [], [], []
        # Root's read-only production observation: gpt-image-2/low is estimated
        # at 260 kopeks; both canonical QA reserves remain 100 kopeks. Actual
        # provider debit is independently configurable for boundary cases.
        self.native_qa_cost, self.image_actual, self.image_estimate = .4, 1.3, 2.6
        self.native_reject = False
        self.native_drop_key = None
        self.hide_native_usage = False
        self.native_create_exception = False
        self.native_process_loss = False
        self.native_approval_exception = False
        self.drift_on_approval = False
        self.fail_second_read = False
        self.f.project = copy.deepcopy(self.f.fixtures["projects"]["270dbf472f7f"])
        source = self.f.fixtures["posts"]["manicure"]
        self.f.theme, self.f.raw_text = source["theme"], source["text"]
        self.f.project.update(themes=[self.f.theme], allow_hashtags=True, image_source="ai", mode="auto",
                              platforms="vk", channel_tg="", paid_auto_generation=True, expected_auto_generation=True)
        self.f.store.write("posting_projects", [self.f.project])
        native = self.f.load_module("app.services.banner_full_ai_premium", FIXTURES / "native_banner_412bb.py")
        self.native = native
        native.IMAGES_ROOT = workspace / "images"
        native.LOCK_ROOT = workspace / "native_locks"
        native.SessionLocal = lambda: NativeLedgerSession(self)
        native._budget_scope_locks = lambda *a, **k: nullcontext()
        native._load_versions = lambda account, banner: copy.deepcopy(self.rows.get((str(account), str(banner)), []))
        native._save_versions = lambda account, banner, rows: self.rows.update({(str(account), str(banner)): copy.deepcopy(rows)})
        native.estimate_cost_rub = lambda: self.image_estimate
        native._load_cost_circuit = lambda: {}
        native._trip_cost_circuit = lambda **k: {"local_fixture_circuit": copy.deepcopy(k)}
        native._generate_image_core = self.image_provider
        native._technical_image_qa = lambda path, target, versions: {
            "passed": True, "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "issues": []}
        reference = self.f.root / "native_owner_reference.png"
        Image.new("RGB", (512, 512), (140, 40, 180)).save(reference, compress_level=0)
        native.OWNER_REFERENCE_IMAGES = {"manicure": str(reference), "pedicure": str(reference)}
        native.OWNER_REFERENCE_VERSION = "FIXTURE_JULY_OWNER_REFERENCE"
        sys.modules["app.services.media_semantic_validator"]._vision_chat_json = self.vision_provider
        media = ModuleType("app.services.banner_media")
        media.register_banner = lambda **kwargs: {"id": "local_media_fixture", **kwargs}
        sys.modules["app.services.banner_media"] = media
        original_apply, original_approve, original_read, original_create = native._apply_vision_verdict, native.approve_preview, native.read_current_preview, native.create_preview
        def create(**kwargs):
            self.creates.append(copy.deepcopy(kwargs)); return original_create(**kwargs)
        def apply(**kwargs):
            self.native_applies.append(copy.deepcopy(kwargs)); return original_apply(**kwargs)
        def approve(*args):
            self.approvals.append(args)
            if self.native_approval_exception: raise OSError("local approval store failure")
            if self.drift_on_approval:
                self.rows[(str(args[0]), str(args[1]))][0]["qa_contract"]["exact_title"] = "Changed after Social QA"
            return original_approve(*args)
        def read(*args):
            self.reads.append(args)
            if self.fail_second_read and len(self.reads) == 2: raise OSError("local readback unavailable")
            return original_read(*args)
        native._apply_vision_verdict, native.approve_preview, native.read_current_preview, native.create_preview = apply, approve, read, create
        self.premium = self.f.load_module("app.services.social_premium_banner", SERVICES / "social_premium_banner.py")

    def usage_row(self, operation, rid, amount, provenance):
        return {"account_id": "u2", "request_id": rid, "provider": "openai", "model": "fixture_model",
                "operation": operation, "cost_rub": amount, "rate_source": "explicit", "pricing_version": "fixture_price_v1",
                "provenance_json": copy.deepcopy(provenance), "prompt_tokens": 100, "completion_tokens": 100}

    def image_provider(self, **kwargs):
        self.native_image_calls.append(copy.deepcopy(kwargs))
        scope = kwargs["provenance"]["budget_intent_key"]
        self.assertEqual(scope, kwargs["provenance"]["generation_key"])
        plans = [json.loads(row.value) for (_, key), row in self.f.store.rows.items() if key.startswith("social_final_payload_generation:")]
        self.assertEqual(len(plans), 1)
        checkpoint = plans[0]["result"]
        self.assertEqual(checkpoint["final_payload_stage"], "image_request")
        self.assertEqual(checkpoint["july_premium"]["social_native_v2"]["generation_key"], scope)
        rid = "fixture_native_image_" + str(len(self.native_image_calls))
        self.usage.append(self.usage_row("banner_premium_ai", rid, self.image_actual, kwargs["provenance"]))
        if self.native_process_loss:
            raise SystemExit(17)
        if self.native_create_exception:
            raise OSError("local lost image response after ledger debit")
        Image.new("RGB", (1024, 1024), (45, 65, 85)).save(kwargs["save_path"], compress_level=0)
        return {"ok": True, "request_id": rid, "provider_request_id": rid, "request_id_exact": True}

    def vision_provider(self, account, operation, content, **kwargs):
        if operation == "social_banner_vision_qa_v1":
            return self.f.provider_qa(account, operation, content, **kwargs)
        self.assertEqual(operation, "banner_premium_vision_qa")
        self.native_qa_calls.append({"account": account, "operation": operation, "content": copy.deepcopy(content), **kwargs})
        prompt = content[0]["text"]
        identity = json.loads(prompt.split("IDENTITY=", 1)[1].split("; CONTRACT=", 1)[0])
        raw = {"pass": not self.native_reject, **{key: True for key in self.f.sq.NATIVE_PREMIUM_REQUIRED_FLAGS},
               "issues": ["fixture native quality reject"] if self.native_reject else [], **identity}
        if self.native_reject: raw["composition_ok"] = False
        if self.native_drop_key: raw.pop(self.native_drop_key, None)
        rid = "fixture_native_qa_" + str(len(self.native_qa_calls))
        self.usage.append(self.usage_row(operation, rid, self.native_qa_cost, kwargs["provenance"]))
        return raw, {"request_id": rid, "provider_request_id": rid, "cost_rub": self.native_qa_cost, "cache_hit": False}

    def run_slot(self):
        self.f.tick(); return self.f.post()

    def state(self, record): return record["july_premium"]["social_native_v2"]

    def test_native_and_social_actual_verdicts_both_pass_with_unique_cost(self):
        record = self.run_slot()
        self.assertEqual(record["status"], "draft", record)
        self.assertEqual((len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls)), (1, 1, 1))
        self.assertIs(self.creates[0]["defer_vision_qa"], False)
        self.assertAlmostEqual(record["banner_cost_rub"], 2.1)
        self.assertEqual(record["banner_attempt_costs"], [1.3])
        self.assertEqual(record["banner_vision_qa_costs"], [.4, .4])
        self.assertEqual(record["july_premium"]["preview_status"], "AVAILABLE_FOR_USE")
        state = self.state(record)
        self.assertEqual(state["native_verdict"]["qa_status"], "PASS")
        self.assertEqual(state["native_total_rub"], 1.7)
        self.assertEqual(state["social_total_rub"], .4)
        self.assertEqual(set(state["native_receipts"]), {"fixture_native_image_1", "fixture_native_qa_1"})
        self.assertEqual(len(self.approvals), 1); self.assertEqual(len(self.reads), 2)
        self.assertTrue(all(x["vision_qa"]["operation"] == "banner_premium_vision_qa" for x in self.native_applies))
        self.assertNotIn("social_editorial", state["native_verdict"])
        self.assertIn("шоколадный металлик", self.native_image_calls[0]["prompt"])
        self.assertIn("овальной формой", self.native_image_calls[0]["prompt"])
        self.assertEqual(record["publish_at"], "2026-10-09 09:00")

    def test_native_quality_reject_preserves_paid_cost_without_social_or_retry(self):
        self.native_reject = True
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(self.state(record)["native_verdict"]["qa_status"], "REJECT")
        self.assertEqual(record["banner_cost_rub"], 1.7); self.assertIs(record["banner_cost_known"], True)
        self.assertEqual((len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls)), (1, 1, 0))
        self.assertFalse(self.approvals)
        self.f.tick(); self.assertEqual(len(self.native_image_calls), 1)

    def test_native_invalid_boolean_blocks_before_social_payment(self):
        self.native_drop_key = "exact_text_ok"
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(self.state(record)["native_verdict"]["qa_status"], "INVALID_RESPONSE")
        self.assertEqual(record["banner_cost_rub"], 1.7); self.assertFalse(self.f.qa_calls)
        self.assertFalse(self.approvals)

    def test_native_missing_receipt_never_becomes_free_or_social_pass(self):
        self.hide_native_usage = True
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertIs(record["banner_cost_known"], False)
        self.assertIsNone(record.get("banner_cost_rub"))
        self.assertEqual(self.state(record)["native_create_result"]["actual_cost_rub"], 1.3)
        self.assertFalse(self.f.qa_calls); self.assertFalse(self.approvals)

    def test_native_stage_upfront_reserve_includes_full_native_qa(self):
        self.image_estimate = 3.1
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual((self.native_image_calls, self.native_qa_calls, self.f.qa_calls), ([], [], []))

    def test_native_cost_circuit_is_a_known_unpaid_block(self):
        self.native._load_cost_circuit = lambda: {"tripped": True}
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual((self.native_image_calls, self.native_qa_calls, self.f.qa_calls), ([], [], []))
        self.assertEqual(record["banner_cost_rub"], 0.0)
        self.assertIs(record["banner_cost_known"], True)
        self.assertFalse(record.get("final_payload_reconciliation_required"))
        self.assertEqual(self.state(record)["native_create_result"]["code"], "COST_CIRCUIT_OPEN")

    def test_actual_native_spend_can_close_remaining_social_budget(self):
        self.native_qa_cost = 2.0
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 3.3)
        self.assertFalse(self.f.qa_calls); self.assertFalse(self.approvals)
        self.assertEqual(self.state(record)["social_qa_status"], "not_performed_remaining_budget_blocked")
        self.assertEqual(self.state(record)["remaining_step"], "social_final_payload_qa")

    def test_production_estimate_260_admits_actual_native_18690_then_social(self):
        self.image_actual, self.native_qa_cost, self.f.qa_cost = 1.3134, .5556, .5556
        record = self.run_slot()
        self.assertEqual(record["status"], "draft", record.get("error"))
        state = self.state(record)
        self.assertEqual(state["paid_admission"]["image_estimate_rub"], 2.6)
        self.assertEqual(state["paid_admission"]["native_qa_reserve_rub"], 1.0)
        self.assertEqual(state["paid_admission"]["social_qa_reserve_rub"], 1.0)
        self.assertEqual(state["paid_admission"]["native_stage_reserve_rub"], 3.6)
        self.assertEqual(state["native_total_rub"], 1.869)
        self.assertEqual(record["banner_cost_rub"], 2.4246)
        self.assertEqual((len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls)), (1, 1, 1))
        self.assertNotIn("remaining_step", state)

    def test_native_30001_blocks_social_before_payment_and_preserves_cost(self):
        self.native_qa_cost = 1.7001
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 3.0001)
        self.assertIs(record["banner_cost_known"], True)
        self.assertFalse(self.f.qa_calls); self.assertFalse(self.approvals)
        self.assertEqual(self.state(record)["social_qa_status"], "not_performed_remaining_budget_blocked")
        self.f.tick(); self.assertEqual(len(self.native_image_calls), 1)

    def test_native_exactly_three_allows_one_social_with_actual_total_four(self):
        self.native_qa_cost, self.f.qa_cost = 1.7, 1.0
        record = self.run_slot()
        self.assertEqual(record["status"], "draft", record.get("error"))
        self.assertEqual(record["banner_cost_rub"], 4.0)
        self.assertEqual(len(self.f.qa_calls), 1)
        self.f.tick(); gate, after = self.f.gate(record)
        self.assertTrue(gate["pass"], gate)
        self.assertEqual(after["banner_cost_rub"], 4.0)
        self.assertEqual(len(self.f.qa_calls), 1)

    def test_native_three_then_actual_social_over_reserve_is_not_accepted(self):
        self.native_qa_cost, self.f.qa_cost = 1.7, 1.00004
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 4.00004)
        self.assertFalse(self.approvals)

    def test_social_quality_fail_does_not_change_native_pass_or_approve(self):
        self.f.qa_outputs.append({"pass": False, "main_object_ok": False})
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 2.1)
        self.assertFalse(record["vision_qa"]["pass"])
        self.assertEqual(self.state(record)["native_verdict"]["qa_status"], "PASS")
        self.assertFalse(self.approvals)
        self.assertTrue(all(not row["approved"] for rows in self.rows.values() for row in rows))

    def test_paid_image_exception_retains_intent_and_never_rebuys(self):
        self.native_create_exception = True
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertIs(record["banner_cost_known"], False)
        self.assertTrue(self.state(record)["image_intent"])
        self.assertEqual(self.usage[0]["cost_rub"], 1.3)
        self.f.tick(); self.assertEqual(len(self.native_image_calls), 1)
        self.assertFalse(self.native_qa_calls); self.assertFalse(self.f.qa_calls)

    def test_process_loss_leaves_running_claim_and_no_new_image_on_retry(self):
        self.native_process_loss = True
        with self.assertRaises(SystemExit): self.f.tick()
        claims = [json.loads(row.value) for (_, key), row in self.f.store.rows.items()
                  if key.startswith("social_final_payload_generation:")]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["state"], "running")
        intent = claims[0]["result"]["july_premium"]["social_native_v2"]["image_intent"]
        blocked = self.f.ns["autopost_projects"]("u2", owner=True, respect_time=True, only_id=self.f.project["id"])
        self.assertEqual(blocked["status"], "generation_blocked")
        unchanged = self.f.store.read("social_final_payload_generation:" + claims[0]["context"]["record_id"])
        self.assertEqual(unchanged, claims[0])
        self.assertTrue(intent)
        self.assertEqual((len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls)), (1, 0, 0))

    def test_approval_error_preserves_all_received_cost(self):
        self.native_approval_exception = True
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 2.1); self.assertIs(record["banner_cost_known"], True)
        self.assertEqual(len(record["delivery_qa_attempts"]), 1)
        self.f.tick(); self.assertEqual(len(self.native_image_calls), 1)

    def test_received_social_charge_survives_its_checkpoint_exception(self):
        original = self.f.ns["_social_final_payload_state"]
        failed = []
        def checkpoint(account, context, action, result=None):
            value = original(account, context, action, result)
            if action == "checkpoint" and result.get("final_payload_stage") == "qa_accounted" and not failed:
                failed.append(True)
                raise OSError("local checkpoint acknowledgement lost after received QA")
            return value
        self.f.ns["_social_final_payload_state"] = checkpoint
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 2.1)
        self.assertIs(record["banner_cost_known"], True)
        self.assertEqual(len(record["delivery_qa_attempts"]), 1)
        self.assertFalse(self.approvals)

    def test_unknown_social_provider_outcome_does_not_erase_native_receipts(self):
        def fail(): raise OSError("local unknown Social provider outcome")
        self.f.qa_hook = fail
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertIs(record["banner_cost_known"], False)
        state = self.state(record)
        self.assertEqual(state["native_total_rub"], 1.7)
        self.assertEqual(len(state["native_receipts"]), 2)
        self.assertFalse(self.approvals)

    def test_postapproval_read_error_preserves_paid_cost(self):
        self.fail_second_read = True
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 2.1); self.assertIs(record["banner_cost_known"], True)
        self.assertEqual(len(self.approvals), 1)

    def test_identity_changed_between_social_and_approval_is_blocked(self):
        self.drift_on_approval = True
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 2.1)
        self.assertEqual(len(record["delivery_qa_attempts"]), 1)

    def test_fraction_above_four_is_not_rounded_down_to_pass(self):
        self.f.qa_cost = 2.30004
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["banner_cost_rub"], 4.00004)
        self.assertFalse(self.approvals)

    def test_complete_generation_and_delivery_replay_need_no_extra_qa(self):
        record = self.run_slot()
        self.f.tick()
        gate, after = self.f.gate(record)
        self.assertTrue(gate["pass"], gate)
        self.assertEqual(after["banner_cost_rub"], 2.1)
        self.assertEqual((len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls)), (1, 1, 1))

    def test_repeat_native_current_snapshot_does_not_double_count(self):
        record = self.run_slot(); state = self.state(record)
        current = self.native.read_current_preview("u2", state["banner_id"], state["banner_version"])
        self.assertTrue(current["idempotency_replay"])
        before = copy.deepcopy(record)
        for _ in range(2): self.premium._native_v2_account("u2", record, current, state)
        self.assertEqual(record, before)
        self.assertEqual(record["banner_cost_rub"], 2.1)

    def test_native_receipt_cost_conflict_blocks_reconciliation(self):
        record = self.run_slot(); state = self.state(record)
        current = self.native.read_current_preview("u2", state["banner_id"], state["banner_version"])
        current["qa_cost_attempts"][0]["cost_rub"] = .5
        current["qa_cost_rub"] = .5
        current["total_cost_rub"] = current["budget"]["total_cost_rub"] = 1.8
        with self.assertRaisesRegex(ValueError, "NATIVE_RECEIPT_CONFLICT"):
            self.premium._native_v2_account("u2", record, current, state)
        self.assertEqual(record["banner_cost_rub"], 2.1)

    def test_later_native_receipt_adds_only_its_delta_and_replay_adds_nothing(self):
        record = self.run_slot(); state = self.state(record)
        row = self.rows[("u2", state["banner_id"])][0]
        rid = "fixture_native_late_receipt"
        provenance = {**row["provenance"], "intent_key": "fixture_distinct_native_qa_attempt"}
        self.usage.append(self.usage_row("banner_premium_vision_qa", rid, .35, provenance))
        row["qa_cost_attempts"].append({"attempt_key": "banner_premium_vision_qa:" + rid,
            "request_id": rid, "operation": "banner_premium_vision_qa", "cost_rub": .35,
            "cost_known": True, "allocation_slot": 1, "allocation_size": 1})
        current = self.native.read_current_preview("u2", state["banner_id"], state["banner_version"])
        self.assertEqual(current["total_cost_rub"], 2.05)
        self.assertIs(current["cost_reconciled"], True)
        self.premium._native_v2_account("u2", record, current, state)
        self.assertEqual(record["banner_cost_rub"], 2.45)
        self.premium._native_v2_account("u2", record, current, state)
        self.assertEqual(record["banner_cost_rub"], 2.45)
        self.assertEqual(len(state["native_receipts"]), 3)
        self.assertEqual(state["social_total_rub"], .4)

    def test_history_cannot_be_removed_from_native_scope(self):
        record = self.run_slot(); state = self.state(record)
        current = self.native.read_current_preview("u2", state["banner_id"], state["banner_version"])
        current["budget"]["request_ids"].remove("fixture_native_image_1")
        before = copy.deepcopy(record)
        with self.assertRaisesRegex(ValueError, "NATIVE_BUDGET_JOURNAL_INCOMPLETE"):
            self.premium._native_v2_account("u2", record, current, state)
        self.assertEqual(record, before)

    def test_historical_paid_result_is_not_upgraded_or_rechecked(self):
        original = self.run_slot()
        historic = copy.deepcopy(original)
        historic["july_premium"].pop("social_native_v2")
        counts = (len(self.creates), len(self.native_qa_calls), len(self.f.qa_calls))
        out = self.premium.generate_premium_social_banner(
            account_id="u2", project_id=self.f.project["id"], theme=self.f.theme,
            result=historic, project=self.f.project, content_brief=historic["content_brief"],
            visual_brief=historic["visual_brief"], quality_module=self.f.sq, banner_dir=str(self.f.root),
            final_qa_callback=lambda **k: self.fail("Historical QA must not be invoked"),
            final_payload_checkpoint=lambda: None)
        self.assertEqual(out["quality_status"], "banner_blocked")
        self.assertEqual(out["banner_cost_rub"], original["banner_cost_rub"])
        self.assertEqual(counts, (len(self.creates), len(self.native_qa_calls), len(self.f.qa_calls)))

    def test_new_lifecycle_cannot_import_an_untracked_native_replay(self):
        record = self.run_slot()
        state = self.state(record)
        # Simulate an untracked caller encountering an already paid native
        # artifact. The actual scheduler's durable claim blocks it earlier.
        empty = {k: copy.deepcopy(record[k]) for k in ("text", "final_payload_qa", "generation_key", "delivery_texts")}
        empty.update(banner_cost_rub=0.0, banner_cost_known=True)
        kwargs = copy.deepcopy(self.creates[0]); kwargs.pop("defer_vision_qa"); kwargs.pop("provenance")
        calls_before = (len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls))
        out = self.premium._generate_premium_native_v2(
            account_id="u2", result=empty, project=self.f.project, content_brief=record["content_brief"],
            visual_brief=record["visual_brief"], sq=self.f.sq, banner_dir=str(self.f.root), native=self.native,
            create_kwargs=kwargs, reservation=1.0, cap=4.0,
            final_qa_callback=lambda **k: self.fail("Untracked native replay must not buy Social QA"), checkpoint=lambda: None)
        self.assertIn("UNTRACKED_REPLAY", out["banner_error"])
        self.assertIs(out["banner_cost_known"], False)
        self.assertEqual(calls_before, (len(self.native_image_calls), len(self.native_qa_calls), len(self.f.qa_calls)))

    def test_unrecognized_native_version_never_falls_back_to_legacy(self):
        self.native.VISION_QA_VERSION = "unreviewed_future_version"
        record = self.run_slot()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertFalse(self.native_image_calls); self.assertFalse(self.f.qa_calls)


if __name__ == "__main__": unittest.main(verbosity=2)
