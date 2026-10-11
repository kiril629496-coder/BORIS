"""Actual Social + native33/native412 flow; all paid/DB IO is local fixtures.

The optional baseline root only selects immutable source for before/fix-after
proof. No implementation helper, copy evaluator, QA normalizer, native receipt,
native finalization or durable claim function is replaced by the test adapter.
"""
from __future__ import annotations
import copy
from datetime import timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

from PIL import Image

BACKEND = Path(os.environ.get("SOCIAL_COPY_REPAIR_BASELINE_ROOT", Path(__file__).resolve().parents[1]))
ASIDE = "За окном снова лето."


def load_local(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CopyDeletionRepairTests(unittest.TestCase):
    def setUp(self):
        self.first_override = {}
        self.second_override = {}
        self.proposal_mutator = lambda proposal: proposal
        self.before_response = None
        self.native_kind = None

    def setup_flow(self, native=None, aside=ASIDE, project="ocean"):
        self.native_kind = native
        if native == "412":
            module = load_local("copy_repair_native412_fixture", BACKEND / "tests/test_social_native_dual_api.py")
            self.dual = module.SocialNativeDualApiTests("test_native_and_social_actual_verdicts_both_pass_with_unique_cost")
            self.dual.setUp(); self.addCleanup(self.dual.doCleanups)
            self.f = self.dual.f
        else:
            module = load_local("copy_repair_actual_flow_fixture", BACKEND / "tests/test_social_final_payload_flow.py")
            self.f = module.FinalPayloadFlowTests("test_initial_final_qa_is_single_paid_proof_and_delivery_reuses_it")
            self.f.setUp(); self.addCleanup(self.f.doCleanups)
            if native == "33":
                self.setup_native33()
            elif project == "dushi":
                self.f.project = copy.deepcopy(self.f.fixtures["projects"]["f938caa3a944"])
                source = self.f.fixtures["posts"]["dushi"]
                self.f.theme, self.f.raw_text = source["theme"], source["text"]
                self.f.project.update(themes=[self.f.theme], mode="auto", image_source="ai", allow_hashtags=True,
                                      paid_auto_generation=True, expected_auto_generation=True)
                self.f.store.write("posting_projects", [self.f.project])
        header, rest = self.f.raw_text.split("\n\n", 1)
        self.f.raw_text = header + "\n\n" + aside + "\n\n" + rest
        self.original_source = self.f.raw_text
        self.aside = aside
        original = self.f.provider_qa

        def qa(account, operation, content, **kwargs):
            out, meta = original(account, operation, content, **kwargs)
            number = len(self.f.qa_calls)
            if number == 1:
                snapshots = [json.loads(row.value)["result"] for (_, key), row in self.f.store.rows.items()
                             if key.startswith("social_final_payload_generation:")]
                self.first_checkpoint = copy.deepcopy(snapshots[0])
                prompt = content[0]["text"]
                marker = "\nCOPY DELETION CONTEXT:\n"
                context = json.loads(prompt.split(marker, 1)[1]) if marker in prompt else {
                    "version": "SOCIAL_OPTIONAL_COPY_DELETION_V1",
                    "body_sha256": hashlib.sha256(self.original_source.encode()).hexdigest(),
                    "units": [{"unit_sha256": hashlib.sha256(aside.encode()).hexdigest(), "kind": "season"}],
                }
                proposal = {"version": context["version"], "body_sha256": context["body_sha256"],
                            "units": [{k: u[k] for k in ("unit_sha256", "kind")} for u in context["units"][:2]],
                            "image_occasion_ok": True, "business_facts_ok": True}
                out.update({"pass": False, "copy_season_ok": False,
                            "issues": ["Unsupported optional season assertion in caption; the image is seasonally valid."],
                            "copy_deletion_proposal": self.proposal_mutator(proposal), **self.first_override})
                if aside == "Сегодня праздник." and not self.first_override:
                    out.update(copy_season_ok=True, occasion_ok=False)
            elif number == 2:
                out.update(self.second_override)
            if self.before_response:
                self.before_response(number, out, meta)
            return out, meta

        self.f.provider_qa = qa
        if native != "412":
            sys.modules["app.services.media_semantic_validator"]._vision_chat_json = qa
        self.addCleanup(lambda: setattr(self.f.sq.datetime, "instant", module.NOW if hasattr(module, "NOW") else self.f.ed.datetime.instant))
        return self.f

    def setup_native33(self):
        f = self.f
        workspace = f.root
        f.root = workspace / "banners"
        f.root.mkdir()
        f.ns["BANNER_DIR"] = str(f.root)
        f.project = copy.deepcopy(f.fixtures["projects"]["270dbf472f7f"])
        source = f.fixtures["posts"]["manicure"]
        f.theme, f.raw_text = source["theme"], source["text"]
        f.project.update(themes=[f.theme], allow_hashtags=True, image_source="ai", mode="auto",
                         platforms="vk", channel_tg="", paid_auto_generation=True, expected_auto_generation=True)
        f.store.write("posting_projects", [f.project])
        source_path = BACKEND / "tests/fixtures/native_banner_33c5.py"
        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(),
                         "33c5ce15194fedc92fbf777e477c2baf4fbe700fd1a5a7660c4da70f53a91460")
        native = f.load_module("app.services.banner_full_ai_premium", source_path)
        self.native33, self.native33_rows, self.native33_image_calls, self.native33_applies = native, {}, [], []
        native.IMAGES_ROOT, native.LOCK_ROOT = workspace / "images", workspace / "native_locks"
        native._load_versions = lambda a, b: copy.deepcopy(self.native33_rows.get((a, b), []))
        native._save_versions = lambda a, b, rows: self.native33_rows.update({(a, b): copy.deepcopy(rows)})
        native._load_cost_circuit = lambda: {}
        native._trip_cost_circuit = lambda **kwargs: {"fixture_circuit": kwargs}
        native.SessionLocal = lambda: self.fail("native33 must never query a real DB")
        native.estimate_cost_rub = lambda: f.image_cost
        native._technical_image_qa = lambda *a: {"passed": True, "issues": []}
        reference = f.root / "native33_reference.png"
        Image.new("RGB", (512, 512), (140, 40, 180)).save(reference, compress_level=0)
        native.OWNER_REFERENCE_IMAGES = {"manicure": str(reference), "pedicure": str(reference)}
        native.OWNER_REFERENCE_VERSION = "FIXTURE_JULY_OWNER_REFERENCE"

        def image(**kwargs):
            self.native33_image_calls.append(copy.deepcopy(kwargs))
            Image.new("RGB", (1024, 1024), (40, 60, 80)).save(kwargs["save_path"], compress_level=0)
            return {"ok": True, "cost_rub": f.image_cost, "request_id": "fixture_native33_image_1"}

        native._generate_image_core = image
        apply = native._apply_vision_verdict
        def applied(**kwargs):
            self.native33_applies.append(copy.deepcopy(kwargs)); return apply(**kwargs)
        native._apply_vision_verdict = applied
        f.load_module("app.services.social_premium_banner", BACKEND / "app/services/social_premium_banner.py")

    def run_flow(self):
        self.f.tick(); return self.f.post()

    def image_count(self):
        if self.native_kind == "412": return len(self.dual.native_image_calls)
        if self.native_kind == "33": return len(self.native33_image_calls)
        return len(self.f.image_calls)

    def assert_success(self, record, total):
        self.assertEqual(record["status"], "draft", record)
        self.assertEqual(len(self.f.qa_calls), 2)
        self.assertEqual(self.image_count(), 1)
        self.assertEqual(len(self.f.text_calls), 1)
        self.assertEqual(record["text"], self.original_source.replace(self.aside + "\n\n", "", 1))
        self.assertEqual(record["copy_deletion"]["attempts"], 1)
        self.assertEqual(record["copy_deletion"]["state"], "passed")
        self.assertEqual(len(record["delivery_qa_attempts"]), 2)
        self.assertAlmostEqual(record["banner_cost_rub"], total, places=6)
        self.assertEqual(record["id"], record["final_payload_qa"]["record_id"])
        self.assertIn(self.aside.lower(), record["visual_brief"]["post_visual_source_text"])
        original = record["copy_deletion"]["original"]
        self.assertIs(original["failed_verdict"]["pass"], False)
        for p, caption in record["delivery_texts"].items():
            self.assertEqual(caption, original["delivery_texts"][p].replace(self.aside + "\n\n", "", 1))
            self.assertEqual(record["delivery_quality"][p]["public_text_sha256"], self.f.ed._digest(caption))
        prompts = [q["content"] for q in self.f.qa_calls]
        self.assertEqual(prompts[0][1:], prompts[1][1:], "the actual image/reference bytes changed")
        self.assertTrue(all(q["max_tokens"] == 700 and q["operation"] == "social_banner_vision_qa_v1" for q in self.f.qa_calls))
        self.assertFalse(self.f.public_calls)

    def test_ordinary_first_copy_failure_deletes_only_aside_and_reuses_final_proof(self):
        self.setup_flow(); record = self.run_flow(); self.assert_success(record, 2.1)
        before = copy.deepcopy(record)
        self.f.tick(); gate, after = self.f.gate(record)
        self.assertTrue(gate["pass"], gate)
        self.assertEqual(after["delivery_quality"], before["delivery_quality"])
        self.assertEqual(len(self.f.qa_calls), 2); self.assertEqual(self.image_count(), 1)

    def test_native33_actual_callback_keeps_both_qa_in_native_row(self):
        self.setup_flow("33"); record = self.run_flow(); self.assert_success(record, 2.1)
        row = next(iter(self.native33_rows.values()))[0]
        self.assertEqual(len(self.native33_applies), 1)
        self.assertAlmostEqual(row["qa_cost_rub"], .8)
        self.assertAlmostEqual(row["total_cost_rub"], 2.1)
        self.assertEqual(row["preview_status"], "DRAFT_PREVIEW")
        self.assertEqual(len(row["vision_qa"]["social_editorial"]["final_payload_state"]["delivery_qa_attempts"]), 2)

    def test_native412_actual_callback_accounts_all_social_before_approve(self):
        self.setup_flow("412"); record = self.run_flow(); self.assert_success(record, 2.5)
        state = record["july_premium"]["social_native_v2"]
        self.assertAlmostEqual(state["native_total_rub"], 1.7)
        self.assertAlmostEqual(state["social_total_rub"], .8)
        self.assertEqual(len(state["social_receipts"]), 2)
        self.assertEqual(state["native_verdict"]["qa_status"], "PASS")
        self.assertEqual(len(self.dual.approvals), 1)
        self.assertEqual(len(self.dual.native_qa_calls), 1)
        self.f.tick(); gate, after = self.f.gate(record)
        self.assertTrue(gate["pass"], gate); self.assertEqual(len(self.f.qa_calls), 2)

    def test_second_actual_failure_keeps_both_costs_and_does_not_rebuy_image(self):
        self.setup_flow(); self.second_override = {"pass": False, "object_ok": False, "issues": ["Actual image object mismatch"]}
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["copy_deletion"]["state"], "failed")
        self.assertEqual(len(self.f.qa_calls), 2); self.assertEqual(self.image_count(), 1)
        self.assertAlmostEqual(record["banner_cost_rub"], 2.1)
        self.assertFalse(record["vision_qa"]["image_repair_allowed"])
        self.f.tick(); self.assertEqual(len(self.f.qa_calls), 2)

    def test_no_remaining_full_reserve_keeps_original_failed_copy_and_cost(self):
        self.setup_flow(); self.f.image_cost = 3.0
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["text"], self.original_source)
        self.assertEqual(len(self.f.qa_calls), 1); self.assertEqual(self.image_count(), 1)
        self.assertAlmostEqual(record["banner_cost_rub"], 3.4)
        self.assertEqual(record["copy_deletion"]["blocked_reason"], "COPY_DELETION_REMAINING_BUDGET_BLOCKED")

    def test_replacement_or_wrong_body_hash_is_not_a_deletion_proposal(self):
        self.setup_flow()
        self.proposal_mutator = lambda p: {**p, "body_sha256": "0" * 64, "replacement_text": "invented replacement"}
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked"); self.assertEqual(record["text"], self.original_source)
        self.assertEqual(len(self.f.qa_calls), 1); self.assertAlmostEqual(record["banner_cost_rub"], 1.7)

    def test_image_service_or_schema_failure_is_not_repaired(self):
        self.setup_flow(); self.first_override = {"service_ok": False, "exact_text_ok": "true"}
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["text"], self.original_source); self.assertEqual(len(self.f.qa_calls), 1)

    def test_native412_actual_native_failure_never_invokes_social_or_approve(self):
        self.setup_flow("412"); self.dual.native_reject = True
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked"); self.assertFalse(self.f.qa_calls)
        self.assertFalse(self.dual.approvals); self.assertEqual(self.image_count(), 1)
        self.assertAlmostEqual(record["banner_cost_rub"], 1.7)

    def test_dushi_relative_date_repair_preserves_contact_tags_and_own_style(self):
        self.setup_flow(aside="Сегодня праздник.", project="dushi")
        record = self.run_flow(); self.assert_success(record, 2.1)
        self.assertEqual(record["content_brief"]["occasion"], {"kind": "evergreen"})
        self.assertNotIn("NAIL/REFERENCE PREMIUM STYLE RULES", self.f.qa_calls[1]["content"][0]["text"])
        for platform in record["delivery_texts"]:
            self.assertIn(self.f.project["contact_" + platform], record["delivery_texts"][platform])

    def test_backend_never_enumerates_service_numbers_contacts_urls_or_merged_copy(self):
        self.setup_flow()
        samples = ["Сегодня 9 октября.", "Сегодня праздник маникюра.", "Сегодня скидка 20 процентов.",
                   "Сегодня один мастер работает.", "За окном снова лето. Запишитесь к мастеру.",
                   "За окном снова лето: https://example.test", "За окном снова лето. #маникюр",
                   "За окном снова лето. Телефон: +7 999 111-22-33.", "Сегодня праздник у Анны.",
                   "За окном снова лето, а педикюр стоит тысячу рублей."]
        for aside in samples:
            with self.subTest(aside=aside):
                text = self.original_source.replace(ASIDE, aside)
                captions = {p: self.f.ns["_project_delivery_text_tracked"](text, self.f.project, p,
                    tracking_id="qa_fixture_post", final_payload=True) for p in ("tg", "vk")}
                manifest = self.f.sq.copy_deletion_manifest(text, captions,
                    {"occasion": {"kind": "evergreen"}, "selected_theme": self.f.theme}, {"headline": self.f.theme})
                self.assertIsNone(manifest)

    def test_two_asides_over_one_quarter_are_not_deleted(self):
        aside = ASIDE + "\n\nНа улице по-прежнему солнечно."
        self.setup_flow(aside=aside)
        self.f.raw_text = ("<b>Как проверить спрос на товар</b>\n\n" + aside + "\n\n"
            "Проверьте спрос до закупки товара: сравните фотографии, заголовки и вопросы покупателей. "
            "Отдельно учтите города, время ответа и входящие обращения.\n\n"
            "#авито #объявления #маркетинг #продажи #товар #спрос")
        self.original_source = self.f.raw_text
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["text"], self.first_checkpoint["text"])
        self.assertEqual(len(self.f.qa_calls), 1)
        self.assertEqual(record["copy_deletion"]["blocked_reason"], "COPY_DELETION_TOO_MUCH_COPY")

    def test_missing_proposal_retains_first_fail_on_repeated_scheduler(self):
        self.setup_flow(); self.proposal_mutator = lambda proposal: None
        record = self.run_flow(); self.f.tick()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["text"], self.original_source)
        self.assertEqual(len(self.f.qa_calls), 1); self.assertEqual(self.image_count(), 1)
        self.assertAlmostEqual(record["banner_cost_rub"], 1.7)

    def test_image_date_or_business_fact_failure_rejects_proposal(self):
        self.setup_flow(); self.proposal_mutator = lambda p: {**p, "image_occasion_ok": False, "business_facts_ok": False}
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked"); self.assertEqual(record["text"], self.original_source)
        self.assertEqual(len(self.f.qa_calls), 1)

    def test_native33_second_fail_keeps_both_costs_and_native_failed(self):
        self.setup_flow("33"); self.second_override = {"pass": False, "spelling_ok": False}
        record = self.run_flow()
        row = next(iter(self.native33_rows.values()))[0]
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(row["preview_status"], "QA_FAILED")
        self.assertAlmostEqual(row["qa_cost_rub"], .8); self.assertAlmostEqual(row["total_cost_rub"], 2.1)
        self.assertEqual(len(self.f.qa_calls), 2); self.assertEqual(self.image_count(), 1)

    def test_native412_second_fail_preserves_native_pass_without_approval(self):
        self.setup_flow("412"); self.second_override = {"pass": False, "object_ok": False}
        record = self.run_flow(); state = record["july_premium"]["social_native_v2"]
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(state["native_verdict"]["qa_status"], "PASS")
        self.assertAlmostEqual(state["social_total_rub"], .8); self.assertAlmostEqual(record["banner_cost_rub"], 2.5)
        self.assertEqual(len(self.f.qa_calls), 2); self.assertFalse(self.dual.approvals)

    def test_unknown_second_cost_keeps_first_receipt_and_blocks(self):
        self.setup_flow()
        def unknown(number, out, meta):
            if number == 2: meta.pop("cost_rub", None)
        self.before_response = unknown
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertIs(record["banner_cost_known"], False)
        self.assertEqual(len(record["delivery_qa_attempts"]), 2)
        self.assertAlmostEqual(record["copy_deletion"]["original"]["banner_cost_rub"], 1.7)
        self.assertEqual(len(self.f.qa_calls), 2); self.assertEqual(self.image_count(), 1)

    def test_actual_second_cost_over_cap_is_retained_and_not_accepted(self):
        self.setup_flow()
        def expensive(number, out, meta):
            if number == 2: meta["cost_rub"] = 2.31
        self.before_response = expensive
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertAlmostEqual(record["banner_cost_rub"], 4.01)
        self.assertEqual(len(self.f.qa_calls), 2); self.assertEqual(self.image_count(), 1)

    def test_fractional_actual_cost_is_not_rounded_down_by_final_checkpoint(self):
        self.setup_flow(); self.f.qa_cost = .333333
        record = self.run_flow(); self.assert_success(record, 1.966666)

    def claim(self):
        entries = [json.loads(row.value) for (_, key), row in self.f.store.rows.items()
                   if key.startswith("social_final_payload_generation:")]
        self.assertEqual(len(entries), 1)
        return entries[0]

    def test_checkpoint_cannot_lower_cost_known_state_or_drop_native_receipts(self):
        self.setup_flow("412"); self.run_flow()
        previous = self.claim()["result"]
        changes = {
            "total": lambda r: r.update(banner_cost_rub=1.0),
            "known": lambda r: r.update(banner_cost_known=False),
            "qa_costs": lambda r: r.update(banner_vision_qa_costs=[]),
            "qa_receipts": lambda r: r.update(delivery_qa_attempts={}),
            "native_receipts": lambda r: r["july_premium"]["social_native_v2"].update(native_receipts={}),
            "native_budget": lambda r: r["july_premium"]["social_native_v2"].update(native_budget={}),
        }
        for kind, change in changes.items():
            with self.subTest(kind=kind):
                candidate = copy.deepcopy(previous); change(candidate)
                with self.assertRaisesRegex(RuntimeError, "SOCIAL_COPY_DELETION_"):
                    self.f.ns["_social_copy_deletion_checkpoint"](previous, candidate)

    def test_cas_race_does_not_overwrite_newer_claim_or_buy_corrective_qa(self):
        self.setup_flow()
        original = self.f.ns["_social_final_payload_state"]
        seen = []
        def race(account, context, action, result=None, **kwargs):
            if action == "copy_delete":
                key = "social_final_payload_generation:" + context["record_id"]
                newer = self.f.store.read(key)
                newer["result"]["concurrent_note"] = "newer durable writer"
                self.f.store.write(key, newer); seen.append(copy.deepcopy(newer))
            return original(account, context, action, result, **kwargs)
        self.f.ns["_social_final_payload_state"] = race
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_COPY_DELETION_CAS_CONFLICT"):
            self.f.tick()
        self.assertEqual(self.claim(), seen[0])
        self.assertEqual(self.claim()["state"], "running")
        self.assertEqual(len(self.f.qa_calls), 1); self.assertEqual(self.image_count(), 1)

    def interruption(self, where):
        self.setup_flow()
        original = self.f.ns["_social_final_payload_state"]
        def interrupted(account, context, action, result=None, **kwargs):
            if action == "copy_delete" and where == "before_cas": raise SystemExit(17)
            value = original(account, context, action, result, **kwargs)
            if action == "copy_delete" and where == "after_cas": raise SystemExit(17)
            return value
        self.f.ns["_social_final_payload_state"] = interrupted
        if where == "second_qa":
            self.before_response = lambda n, out, meta: (_ for _ in ()).throw(SystemExit(17)) if n == 2 else None
        with self.assertRaises(SystemExit): self.f.tick()
        durable = self.claim()
        self.assertEqual(durable["state"], "running")
        self.assertEqual(len(durable["result"]["delivery_qa_attempts"]), 1)
        self.assertAlmostEqual(durable["result"]["banner_cost_rub"], 1.7)
        count = len(self.f.qa_calls)
        self.f.ns["_social_final_payload_state"] = original
        self.before_response = None
        self.f.tick()
        self.assertEqual(self.claim(), durable)
        self.assertEqual(len(self.f.qa_calls), count); self.assertEqual(self.image_count(), 1)
        return durable

    def test_interruption_before_cas_keeps_first_fail_without_new_spend(self):
        durable = self.interruption("before_cas")
        self.assertEqual(durable["result"]["copy_deletion"]["attempts"], 0)
        self.assertEqual(durable["result"]["text"], self.original_source)

    def test_interruption_after_cas_keeps_corrected_copy_and_used_attempt(self):
        durable = self.interruption("after_cas")
        self.assertEqual(durable["result"]["copy_deletion"]["attempts"], 1)
        self.assertEqual(durable["result"]["final_payload_stage"], "copy_deleted")

    def test_interruption_during_corrective_qa_is_reconciliation_not_a_free_retry(self):
        durable = self.interruption("second_qa")
        self.assertEqual(durable["result"]["copy_deletion"]["attempts"], 1)
        self.assertEqual(durable["result"]["final_payload_stage"], "qa_request")
        self.assertEqual(len(self.f.qa_calls), 2)

    def test_local_day_rollover_after_first_qa_prevents_correction_and_payment(self):
        self.setup_flow()
        def rollover(number, out, meta):
            if number == 1: self.f.sq.datetime.instant += timedelta(days=1)
        self.before_response = rollover
        self.f.tick()
        state = self.claim()["result"]
        self.assertEqual(state["copy_deletion"]["blocked_reason"], "COPY_DELETION_DAY_CHANGED")
        self.assertEqual(state["text"], self.original_source)
        self.assertEqual(len(self.f.qa_calls), 1)

    def test_native412_full_remaining_reserve_is_required_before_corrective_qa(self):
        self.setup_flow("412"); self.dual.image_actual = 2.3
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["text"], self.original_source)
        self.assertEqual(record["copy_deletion"]["blocked_reason"], "COPY_DELETION_REMAINING_BUDGET_BLOCKED")
        self.assertEqual(len(self.f.qa_calls), 1); self.assertFalse(self.dual.approvals)
        self.assertAlmostEqual(record["banner_cost_rub"], 3.1)

    def test_reference_change_after_first_response_does_not_buy_corrective_qa(self):
        self.setup_flow("412")
        def change_reference(number, out, meta):
            if number == 1:
                path = self.dual.native.OWNER_REFERENCE_IMAGES["manicure"]
                Image.new("RGB", (512, 512), (2, 3, 4)).save(path, compress_level=0)
        self.before_response = change_reference
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["copy_deletion"]["blocked_reason"], "COPY_DELETION_REFERENCE_CHANGED")
        self.assertEqual(len(self.f.qa_calls), 1); self.assertFalse(self.dual.approvals)

    def test_backend_rejects_duplicate_unknown_and_more_than_two_unit_ids(self):
        self.setup_flow()
        self.run_flow()
        original = self.f.post()["copy_deletion"]["original"]
        manifest = original["manifest"]
        context = {"version": "SOCIAL_OPTIONAL_COPY_DELETION_V1", "mode": "proposal",
                   "body_sha256": manifest["body_sha256"], "units": manifest["units"]}
        valid = original["failed_verdict"]["copy_deletion_proposal"]
        variants = [dict(valid, units=valid["units"] * 2), dict(valid, units=valid["units"] * 3),
                    dict(valid, units=[{"unit_sha256": "0" * 64, "kind": "season"}])]
        for variant in variants:
            proposal, error = self.f.sq.normalize_copy_deletion_proposal(variant, context)
            self.assertIsNone(proposal); self.assertEqual(error, "COPY_DELETION_PROPOSAL_INVALID")

    def test_corrupted_paid_qa_input_contract_blocks_instead_of_repairing_copy(self):
        self.setup_flow()
        actual = self.f.sq.validate_banner_vision
        def corrupted(*args, **kwargs):
            verdict = actual(*args, **kwargs)
            verdict["qa_input_sha256"] = "corrupted_receipt_input"
            return verdict
        self.f.sq.validate_banner_vision = corrupted
        record = self.run_flow()
        self.assertEqual(record["status"], "quality_blocked")
        self.assertEqual(record["text"], self.original_source)
        self.assertEqual(len(self.f.qa_calls), 1); self.assertEqual(self.image_count(), 1)
        self.assertIn("FINAL_PAYLOAD_QA_INPUT_UNVERIFIED", record["vision_qa"]["issues"])
        self.assertAlmostEqual(record["banner_cost_rub"], 1.7)


if __name__ == "__main__": unittest.main(verbosity=2)
