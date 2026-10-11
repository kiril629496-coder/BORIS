"""Independent local fault-injection tests for final-payload Premium QA.

Adapted from the independent review harness to the exact candidate callback.
The candidate owner authorized this LOCAL execution; no live services are used.  No application import,
database, provider, public publication, or production filesystem is used.

The Premium orchestration, strict flags, delivery-proof reuse and native
_apply_vision_verdict are extracted from the actual source with AST.  Only I/O
boundaries are replaced.  All images and native storage are disposable fixtures.
"""
from __future__ import annotations

import ast
from collections import deque
from copy import deepcopy
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BACKEND = Path(__file__).resolve().parents[1]
SERVICES = BACKEND / "app/services"
PREMIUM_SOURCE = SERVICES / "social_premium_banner.py"
CONTRACT_SOURCE = SERVICES / "social_editorial_contract.py"
SQ_SOURCE = SERVICES / "social_quality.py"
# This suite exercises the unchanged legacy adapter. The dual-API suite below
# separately executes the approved identity/receipt native source.
NATIVE_SOURCE = BACKEND / "tests/fixtures/native_banner_33c5.py"
CONTRACT_SHA256 = "da752096d760f8778849a5c4cabd519001b91187501bc544f3abfc1bcb1abd2e"

# This is fixture input, not a fallback permission to promote historical QA.
FINAL_PAYLOAD_METADATA = {"final_payload_qa": {"version": "SOCIAL_FINAL_PAYLOAD_QA_V1", "record_id": "qa_fixture_record", "generation_key": "social-slot:qa_account:qa_manicure_project:20261009_09"}}


def _definitions(source, namespace, *, wanted=None):
    """Execute source functions and literal constants, never its app imports."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (wanted is None or node.name in wanted):
            body.append(node)
        elif isinstance(node, ast.Assign):
            try:
                ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            body.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 str(source), "exec"), namespace)
    return namespace


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PremiumFinalPayloadCostTest(unittest.TestCase):
    def setUp(self):
        self.assertEqual(_sha(NATIVE_SOURCE), "33c5ce15194fedc92fbf777e477c2baf4fbe700fd1a5a7660c4da70f53a91460")
        self.assertEqual(hashlib.sha256(CONTRACT_SOURCE.read_bytes()).hexdigest(), CONTRACT_SHA256,
                         "staged editorial contract differs from the reviewed dependency")
        self.temp = tempfile.TemporaryDirectory(prefix="premium-final-payload-review-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference_path = self.root / "owner_reference_fixture.png"
        self.reference_path.write_bytes(b"local fixture owner reference; no provider")
        self.fixed_now = datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)
        self.project = {
            "id": "qa_manicure_project", "name": "Маникюр Евпатория",
            "platforms": "vk", "vk_owner_id": -99, "tz_offset": 3,
            "owner_reference_niche": "manicure",
        }
        self.brief = {
            "project_id": self.project["id"], "current_date": "2026-10-09",
            "season": "осень-зима", "selected_theme": "аккуратная форма осенью",
            "occasion": {"kind": "evergreen"},
            "forbidden_mentions": ["море", "купание", "пляж"],
        }
        self.visual = {
            "headline": "Аккуратная форма для осеннего маникюра",
            "main_object": "ухоженные руки", "forbidden_objects": ["пляж"],
            "post_visual_source_text": "Законченная полезная мысль об осеннем маникюре.",
        }
        self.native_rows = {}
        self.create_calls = []
        self.native_apply_calls = []
        self.native_save_calls = []
        self.asset_copy_calls = []
        self.final_callback_calls = []
        self.actual_qa_calls = []
        self.callback_responses = deque()
        self.image_cost = 1.30
        self.qa_cost = 0.40
        self.image_estimate = 1.30
        self.qa_reservation = 1.0
        self.native_reject_after_qa = False
        self.native_save_error = False
        self.asset_copy_error = False
        self.remove_asset_after_qa = False
        self.mutate_asset_after_qa = False
        self.unknown_callback_cost = False
        self.active_result = None
        self.caught_errors = []

        pure = _definitions(CONTRACT_SOURCE, {
            "copy": copy, "hashlib": hashlib, "json": json, "re": re,
            "date": date, "datetime": datetime, "timedelta": timedelta,
            "timezone": timezone,
        })
        flags = _definitions(SQ_SOURCE, {}, wanted={"vision_required_flags"})
        self.ensure_delivery_qa = pure["ensure_delivery_qa"]
        self.strict_qa_flags = pure["strict_qa_flags"]
        self.required_flags = flags["vision_required_flags"]
        self.social_flags = flags["SOCIAL_VISION_REQUIRED_FLAGS"]
        self.native_flags = flags["NATIVE_PREMIUM_REQUIRED_FLAGS"]
        self.sq = SimpleNamespace(
            POLICY_VERSION="qa_fixture_social_policy",
            BANNER_ACCEPTED_TOTAL_COST_CAP_RUB=4.0,
            BANNER_REGEN_MAX=1,
            visual_brief_prompt=lambda visual: json.dumps(visual, ensure_ascii=False, sort_keys=True),
            vision_required_flags=self.required_flags,
            vision_reference_context=self._reference,
            validate_banner_vision=Mock(side_effect=self._legacy_qa),
        )
        self.native_apply = _definitions(NATIVE_SOURCE, {
            "_version_row": self._native_version_row,
            "_save_versions": self._native_save_versions,
            "MAX_COST_RUB": 4.0,
            "_trip_cost_circuit": lambda **kwargs: {"fixture_cost_circuit": kwargs},
            "_now_iso": lambda: self.fixed_now.isoformat(),
        }, wanted={"_apply_vision_verdict"})["_apply_vision_verdict"]
        namespace = _definitions(PREMIUM_SOURCE, {
            "copy": copy, "hashlib": hashlib, "json": json, "math": math,
            "Path": Path, "file_sha256": _sha,
            "persist_banner_asset": self._persist_asset,
            "strict_qa_flags": self.strict_qa_flags,
        })
        self.generate = namespace["generate_premium_social_banner"]

        app = ModuleType("app")
        app.__path__ = []
        services = ModuleType("app.services")
        services.__path__ = []
        native = ModuleType("app.services.banner_full_ai_premium")
        native.create_preview = self._create_preview
        native.estimate_cost_rub = lambda: self.image_estimate
        native._apply_vision_verdict = self._apply_native
        validator = ModuleType("app.services.media_semantic_validator")
        validator._vision_estimate_kopeks = lambda: self.qa_reservation * 100
        assets = ModuleType("app.services.social_media_assets")
        assets.file_sha256 = _sha
        assets.persist_banner_asset = self._persist_asset
        editorial = ModuleType("app.services.social_editorial_contract")
        editorial.strict_qa_flags = self.strict_qa_flags
        editorial.ensure_delivery_qa = self.ensure_delivery_qa
        editorial.project_local_now = pure["project_local_now"]
        app.services = services
        services.banner_full_ai_premium = native
        services.media_semantic_validator = validator
        services.social_media_assets = assets
        services.social_editorial_contract = editorial
        self.modules = patch.dict(sys.modules, {
            "app": app, "app.services": services,
            "app.services.banner_full_ai_premium": native,
            "app.services.media_semantic_validator": validator,
            "app.services.social_media_assets": assets,
            "app.services.social_editorial_contract": editorial,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def _reference(self, *args):
        return {
            "required": True, "ok": True, "niche": "manicure",
            "version": "qa_fixture_owner_reference", "path": str(self.reference_path),
            "sha256": _sha(self.reference_path),
        }

    def new_result(self, **overrides):
        marker = deepcopy(FINAL_PAYLOAD_METADATA)
        value = {
            "id": "qa_fixture_record", "text": "Законченная полезная мысль об осеннем маникюре.",
            "generation_key": "social-slot:qa_account:qa_manicure_project:20261009_09",
            "delivery_protocol": "SOCIAL_RECEIPT_V1", "ids": {},
            "delivery_texts": {"vk": "Аккуратная форма для осеннего маникюра.\n#маникюр #евпатория"},
            "content_brief": deepcopy(self.brief),
            "banner_cost_rub": 0.0, "banner_cost_known": True,
            **marker,
        }
        value.update(overrides)
        return value

    def verdict(self, *, passed=True, cost=None, **overrides):
        amount = self.qa_cost if cost is None else cost
        required = tuple(dict.fromkeys((*self.social_flags, *self.native_flags)))
        verdict = {
            "policy_version": self.sq.POLICY_VERSION,
            "pass": passed, **{flag: True for flag in required},
            "qa_cost_rub": amount, "qa_cost_known": True,
            "qa_new_charge_rub": amount, "cache_hit": False,
            "provider_request_id": "qa_fixture_provider_response",
            "request_id": "qa_fixture_provider_response",
            "qa_cost_evidence": {"source": "fixture_provider_meta", "cost_rub": amount,
                                 "request_id": "qa_fixture_provider_response"},
            "reference": self._reference(), "issues": [],
            "premium_verdict": {"passed": passed, **{flag: True for flag in self.native_flags}},
        }
        verdict.update(overrides)
        return verdict

    def _legacy_qa(self, *args):
        return self.verdict()

    def _create_preview(self, **kwargs):
        self.create_calls.append(deepcopy(kwargs))
        key = kwargs["idempotency_key"]
        if key in self.native_rows:
            return {**deepcopy(self.native_rows[key][0]), "idempotency_replay": True}
        image = self.root / (hashlib.sha256(key.encode()).hexdigest() + ".png")
        image.write_bytes(b"disposable fixture image, never sent to a provider:" + key.encode())
        row = {
            "status": "ok", "banner_id": key, "version": 1,
            "idempotency_key": key, "absolute_path": str(image),
            "actual_cost_rub": self.image_cost, "qa_cost_rub": 0.0,
            "total_cost_rub": self.image_cost, "cost_reconciled": True,
            "cost_sla_pass": True, "qa_passed": False,
            "preview_status": "VISION_QA_PENDING", "technical_qa": {"passed": True},
            "engine": "full_ai_premium", "engine_source": "qa_fixture_native_july",
            "provider_request_id": "qa_fixture_image_receipt",
            "qa_contract": {
                "exact_title": kwargs["exact_title"],
                "exact_subtitle": kwargs["exact_subtitle"],
                "advantages": deepcopy(kwargs["advantages"]),
                "owner_reference_sha256": _sha(self.reference_path),
            },
        }
        self.native_rows[key] = [deepcopy(row)]
        return row

    def _native_version_row(self, account_id, banner_id, version):
        rows = deepcopy(self.native_rows.get(banner_id, []))
        return rows, next((row for row in rows if row["version"] == version), None)

    def _native_save_versions(self, account_id, banner_id, versions):
        self.native_save_calls.append((account_id, banner_id, deepcopy(versions)))
        if self.native_save_error:
            raise OSError("fixture native storage write failed after paid QA")
        self.native_rows[banner_id] = deepcopy(versions)

    def _apply_native(self, **kwargs):
        self.native_apply_calls.append(deepcopy(kwargs))
        if self.native_reject_after_qa:
            self.native_rows[kwargs["banner_id"]][0]["technical_qa"]["passed"] = False
        return self.native_apply(**kwargs)

    def _persist_asset(self, account_id, path, *, banner_dir):
        self.asset_copy_calls.append((account_id, path, banner_dir))
        if self.asset_copy_error:
            raise OSError("fixture canonical asset write failed after paid QA")
        folder = Path(banner_dir)
        self.assertTrue(folder.is_relative_to(self.root))
        folder.mkdir(parents=True, exist_ok=True)
        digest = _sha(path)
        output = folder / (digest + ".png")
        output.write_bytes(Path(path).read_bytes())
        return {"path": str(output), "filename": output.name, "sha256": digest}

    def _actual_final_qa(self, *, path, **request):
        result = self.active_result
        attempts = result.setdefault("delivery_qa_attempts", {})
        intent = hashlib.sha256(json.dumps({
            "context": request["context_sha256"], "outputs": request["public_texts"],
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        saved = attempts.get(intent)
        if isinstance(saved, dict) and isinstance(saved.get("verdict"), dict):
            return deepcopy(saved["verdict"])
        total = result.get("banner_cost_rub")
        if (type(total) not in (int, float) or not math.isfinite(total)
                or result.get("banner_cost_known") is not True
                or total + self.qa_reservation > 4.0):
            return {"pass": False, "cost_guard_ok": False,
                    "issues": ["fixture_remaining_budget_closed"]}
        self.actual_qa_calls.append(deepcopy(request))
        verdict = deepcopy(self.callback_responses.popleft() if self.callback_responses else self.verdict())
        amount = verdict.get("qa_cost_rub")
        if self.unknown_callback_cost:
            amount = None
            verdict.update(qa_cost_rub=None, qa_cost_known=False, qa_new_charge_rub=None)
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
            result.update(banner_cost_rub=None, banner_cost_known=False)
            return {**verdict, "pass": False, "cost_guard_ok": False,
                    "issues": ["fixture_actual_cost_unknown"]}
        result["banner_cost_rub"] = round(total + amount, 6)
        result["banner_cost_known"] = True
        result.setdefault("banner_vision_qa_costs", []).append(amount)
        verdict["cost_guard_ok"] = result["banner_cost_rub"] <= 4.0
        if not verdict["cost_guard_ok"]:
            verdict["pass"] = False
        attempts[intent] = {
            "cost_rub": amount, "new_charge_rub": amount,
            "cost_evidence": deepcopy(verdict["qa_cost_evidence"]),
            "verdict": deepcopy(verdict),
        }
        if self.remove_asset_after_qa:
            Path(path).unlink()
        elif self.mutate_asset_after_qa:
            Path(path).write_bytes(b"fixture bytes changed after the actual QA response")
        return verdict

    def _final_qa(self, *, path, visual_brief):
        result = self.active_result
        self.final_callback_calls.append({
            "path": path, "visual_brief": deepcopy(visual_brief),
            "cost_before": result.get("banner_cost_rub"),
            "known_before": result.get("banner_cost_known"),
        })
        checked = deepcopy(visual_brief)
        checked["current_date"] = self.brief["current_date"]
        checked["owner_reference"] = {key: self._reference()[key]
                                      for key in ("niche", "version", "sha256")}
        public_texts = deepcopy(result["delivery_texts"])
        request = self.ensure_delivery_qa(
            record=result, project=self.project, brief=deepcopy(self.brief),
            visual_brief=checked, banner_sha256=_sha(path), public_texts=public_texts,
            required_flags=self.required_flags(self.project, self.brief, checked),
            qa_callback=lambda **kwargs: self._actual_final_qa(path=path, **kwargs),
            now=self.fixed_now,
        )
        result["delivery_quality"] = deepcopy(request["delivery_quality"])
        result["delivery_copy_quality"] = {"vk": {"pass": True, "source": "fixture_copy_boundary"}}
        verdict = request.get("qa_result")
        if not isinstance(verdict, dict) and request.get("pass") is True:
            verdict = (request["delivery_quality"].get("vk") or {}).get("verdict")
        if not isinstance(verdict, dict):
            return {"pass": False, "issues": list(request.get("blockers") or ["fixture_no_actual_proof"]),
                    "cost_guard_ok": False, "image_repair_allowed": False}
        return deepcopy(verdict)

    def call(self, result=None, *, legacy=False, capture_exception=False):
        self.active_result = result if result is not None else self.new_result()
        kwargs = dict(
            account_id="qa_account", project_id=self.project["id"],
            theme=self.brief["selected_theme"], result=self.active_result,
            project=deepcopy(self.project), content_brief=deepcopy(self.brief),
            visual_brief=deepcopy(self.visual), quality_module=self.sq,
            banner_dir=str(self.root / "canonical_assets"),
        )
        if not legacy:
            kwargs["final_qa_callback"] = self._final_qa
        try:
            return self.generate(**kwargs)
        except Exception as exc:
            if not capture_exception:
                raise
            self.caught_errors.append(type(exc).__name__ + ":" + str(exc))
            return self.active_result

    def assertBlockedWithPaidCost(self, out, amount):
        self.assertFalse(out.get("banner"), "a blocked paid attempt returned a publishable asset")
        self.assertNotEqual(out.get("quality_status"), "pass")
        self.assertIs(out.get("banner_cost_known"), True)
        self.assertAlmostEqual(out["banner_cost_rub"], amount, places=6)
        self.assertTrue(out.get("delivery_qa_attempts"), "the paid QA evidence was lost")

    def test_first_final_qa_sees_full_native_contract_and_is_charged_once(self):
        out = self.call()
        self.assertEqual(out["quality_status"], "pass")
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.sq.validate_banner_vision.assert_not_called()
        self.assertEqual(len(self.native_apply_calls), 1)
        self.assertAlmostEqual(out["banner_cost_rub"], 1.70, places=6)
        self.assertAlmostEqual(sum(out["banner_attempt_costs"]), 1.30, places=6)
        self.assertAlmostEqual(sum(out["banner_vision_qa_costs"]), 0.40, places=6)
        seen = self.final_callback_calls[0]
        self.assertEqual(seen["cost_before"], 1.30)
        self.assertIs(seen["known_before"], True)
        self.assertIn("premium_contract", seen["visual_brief"])
        self.assertIn("Красиво в вашем ритме", seen["visual_brief"]["allowed_secondary_text"])
        self.assertTrue(out["delivery_quality"].get("vk"))
        self.assertEqual(out["july_premium"]["preview_status"], "DRAFT_PREVIEW")

    def test_native_replay_reuses_final_proof_without_double_count(self):
        first = self.call()
        proof = deepcopy(first["delivery_quality"])
        second = self.call()
        self.assertEqual(self.create_calls[0]["idempotency_key"], self.create_calls[1]["idempotency_key"])
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.assertEqual(second["delivery_quality"], proof)
        self.assertAlmostEqual(second["banner_cost_rub"], 1.70, places=6)
        self.assertEqual(first["banner_asset"], second["banner_asset"])

    def test_native_replay_with_less_than_reservation_remaining_needs_no_new_budget(self):
        self.qa_cost = 2.40
        first = self.call()
        self.assertEqual(first["quality_status"], "pass")
        self.assertAlmostEqual(first["banner_cost_rub"], 3.70, places=6)
        second = self.call()
        self.assertEqual(second["quality_status"], "pass")
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.assertAlmostEqual(second["banner_cost_rub"], 3.70, places=6)

    def test_changed_final_copy_keeps_old_native_qa_cost_and_requires_actual_new_qa(self):
        self.call()
        changed = self.new_result(delivery_texts={"vk": "Новый согласованный совет об осенней форме. #маникюр #евпатория"})
        out = self.call(changed)
        self.assertEqual(out["quality_status"], "pass")
        self.assertEqual(len(self.actual_qa_calls), 2)
        self.assertAlmostEqual(out["banner_cost_rub"], 2.10, places=6)
        native = self.native_rows[self.create_calls[-1]["idempotency_key"]][0]
        self.assertAlmostEqual(native["qa_cost_rub"], 0.80, places=6)
        self.assertAlmostEqual(native["total_cost_rub"], 2.10, places=6)

    def test_legacy_raw_copy_native_pass_is_not_a_final_payload_proof(self):
        legacy = self.call(legacy=True)
        self.assertEqual(legacy["quality_status"], "pass")
        self.assertEqual(self.sq.validate_banner_vision.call_count, 1)
        # An explicitly NEW lifecycle has a distinct identity/seed. The old
        # raw proof is not transplanted; normal scheduler enrollment forbids
        # applying this new marker to historical plans (tested separately).
        out = self.call()
        self.assertEqual(out["quality_status"], "pass")
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.assertNotEqual(self.create_calls[0]["idempotency_key"], self.create_calls[1]["idempotency_key"])
        self.assertAlmostEqual(out["banner_cost_rub"], 1.70, places=6)

    def test_saved_final_fail_is_reused_without_rebuy_or_pass_promotion(self):
        self.callback_responses.append(self.verdict(passed=False, service_ok=False,
                                                   issues=["fixture_actual_service_mismatch"]))
        first = self.call()
        second = self.call()
        self.assertBlockedWithPaidCost(first, 1.70)
        self.assertBlockedWithPaidCost(second, 1.70)
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.assertIs(second["vision_qa"]["pass"], False)

    def test_native_technical_failure_cannot_be_overridden_by_final_social_pass(self):
        self.native_reject_after_qa = True
        out = self.call()
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.assertEqual(len(self.native_apply_calls), 1)
        self.assertIs(out["vision_qa"]["pass"], False)
        self.assertEqual(out["july_premium"]["preview_status"], "QA_FAILED")

    def test_non_boolean_required_flag_remains_blocked_with_actual_cost(self):
        self.callback_responses.append(self.verdict(exact_text_ok="true"))
        out = self.call()
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertIs(out["vision_qa"]["pass"], False)

    def test_missing_actual_premium_verdict_remains_blocked_with_actual_cost(self):
        self.callback_responses.append(self.verdict(premium_verdict=None))
        out = self.call()
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertEqual(self.native_apply_calls, [])

    def test_existing_failed_attempt_cost_is_retained_before_next_image(self):
        self.sq.BANNER_REGEN_MAX = 2
        result = self.new_result(banner_cost_rub=1.20, banner_attempts=1,
                                 banner_attempt_costs=[1.0], banner_vision_qa_costs=[0.20])
        out = self.call(result)
        self.assertEqual(out["quality_status"], "pass")
        self.assertAlmostEqual(out["banner_cost_rub"], 2.90, places=6)
        self.assertAlmostEqual(sum(out["banner_attempt_costs"]), 2.30, places=6)
        self.assertAlmostEqual(sum(out["banner_vision_qa_costs"]), 0.60, places=6)
        self.assertEqual(len(self.create_calls), 1)

    def test_native_save_failure_after_actual_qa_preserves_the_charge(self):
        self.native_save_error = True
        out = self.call(capture_exception=True)
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertEqual(len(self.actual_qa_calls), 1)

    def test_missing_native_version_after_paid_recheck_is_blocked_with_charge(self):
        first = self.call()
        self.assertEqual(first["quality_status"], "pass")
        # Exercise the actual native function's explicit None return: a stale
        # replay row must not remain accepted if its version cannot be loaded
        # while persisting a newly paid final-caption QA.
        self.native_apply.__globals__["_version_row"] = lambda *args: ([], None)
        changed = self.new_result(delivery_texts={
            "vk": "Новый согласованный совет об осенней форме. #маникюр #евпатория",
        })
        out = self.call(changed)
        self.assertBlockedWithPaidCost(out, 2.10)
        self.assertEqual(len(self.actual_qa_calls), 2)
        self.assertEqual(len(self.native_apply_calls), 2)
        stored = self.native_rows[self.create_calls[-1]["idempotency_key"]][0]
        self.assertAlmostEqual(stored["qa_cost_rub"], 0.40, places=6)
        self.assertAlmostEqual(stored["total_cost_rub"], 1.70, places=6)

    def test_canonical_copy_failure_after_actual_qa_preserves_the_charge(self):
        self.asset_copy_error = True
        out = self.call(capture_exception=True)
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertEqual(len(self.actual_qa_calls), 1)

    def test_disappearing_image_after_actual_qa_preserves_the_charge(self):
        self.remove_asset_after_qa = True
        out = self.call(capture_exception=True)
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertEqual(len(self.actual_qa_calls), 1)

    def test_mutated_image_after_actual_qa_is_blocked_without_losing_charge(self):
        self.mutate_asset_after_qa = True
        out = self.call(capture_exception=True)
        self.assertBlockedWithPaidCost(out, 1.70)
        self.assertEqual(len(self.actual_qa_calls), 1)

    def test_unknown_callback_cost_stops_without_free_acceptance(self):
        self.unknown_callback_cost = True
        out = self.call(capture_exception=True)
        self.assertFalse(out.get("banner"))
        self.assertNotEqual(out.get("quality_status"), "pass")
        self.assertIs(out.get("banner_cost_known"), False)
        self.assertIsNone(out.get("banner_cost_rub"))
        self.assertEqual(len(self.actual_qa_calls), 1)
        self.assertEqual(len(self.create_calls), 1)

    def test_remaining_budget_blocks_before_another_paid_image(self):
        out = self.call(self.new_result(banner_cost_rub=3.0, banner_attempts=1,
                                       banner_attempt_costs=[2.6], banner_vision_qa_costs=[0.4]))
        self.assertFalse(out.get("banner"))
        self.assertAlmostEqual(out["banner_cost_rub"], 3.0, places=6)
        self.assertEqual(self.create_calls, [])
        self.assertEqual(self.actual_qa_calls, [])

    def test_missing_visual_source_blocks_before_any_paid_native_call(self):
        self.visual.pop("post_visual_source_text")
        with self.assertRaisesRegex(ValueError, "PREMIUM_FINAL_VISUAL_SOURCE_REQUIRED"):
            self.call()
        self.assertEqual(self.create_calls, []); self.assertEqual(self.actual_qa_calls, [])

    def test_native_description_limit_never_silently_crops_new_semantic_brief(self):
        self.visual["post_visual_source_text"] = "Полный смысловой текст. " * 150
        out = self.call()
        self.assertEqual(out["quality_status"], "banner_blocked")
        self.assertEqual(out["banner_error"], "PREMIUM_FINAL_VISUAL_BRIEF_TOO_LONG")
        self.assertEqual(out["banner_cost_rub"], 0.0)
        self.assertEqual(self.create_calls, []); self.assertEqual(self.actual_qa_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
