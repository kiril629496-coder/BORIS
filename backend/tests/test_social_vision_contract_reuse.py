"""Actual accepted delivery-gate AST: contract epoch, reuse and spend boundaries.

Only IO boundaries are replaced. The editorial context/proof contract and
delivery-gate control flow execute unchanged. No DB or provider is called.
"""
import ast
import copy
import hashlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import social_quality as sq


RUNNER_SOURCE = Path(sq.__file__).resolve().parents[2] / "posting_runner.py"
FUNCTION_SHA256 = {
    "_social_delivery_public_texts": "5e0b7f4935da4c749320cf83b227f6ff5b5e9ce7be0025a65e6570be1c373eb8",
    "_social_delivery_quality_gate": "336a8070eaec06463f2142ba2e9106e178b56a368576b0822518a5f180d7da69",
}
NOW = datetime(2026, 10, 9, 4, 40, tzinfo=timezone.utc)
OCEAN = {
    "id": "a374ced25745", "name": "Океан Клиентов и Борис", "tz_offset": 3,
    "platforms": "both", "channel_tg": "@oko_marketing_i_rost", "vk_owner_id": -121265252,
}


class VisionContractReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = RUNNER_SOURCE.read_text()
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_SHA256]
        if len(nodes) != len(FUNCTION_SHA256):
            raise AssertionError("Reviewed gate functions are missing")
        for node in nodes:
            actual = hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest()
            if actual != FUNCTION_SHA256[node.name]:
                raise AssertionError("Reviewed gate changed: " + node.name)
        namespace = {"datetime": datetime, "timezone": timezone, "json": json}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(RUNNER_SOURCE), "exec"), namespace)
        cls.gate = staticmethod(namespace["_social_delivery_quality_gate"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.asset = Path(self.temporary.name) / "existing-banner.png"
        # Hashable fixture only: the Vision IO boundary is mocked explicitly.
        self.asset.write_bytes(b"existing immutable banner fixture; no image generation")
        self.asset_sha = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.record = {
            "id": "407e12016561", "project_id": OCEAN["id"], "status": "partial",
            "generation_key": "social-slot:u2:a374ced25745:20261008_19",
            "text": "Не смотрите на выручку в одиночку. Проверьте путь клиента.",
            "banner": str(self.asset), "banner_cost_known": True, "banner_cost_rub": 1.7188,
            "banner_attempt_costs": [1.3333], "banner_vision_qa_costs": [0.3855],
            "banner_attempts": 1,
            "ids": {"tg": {"chat_id": "@oko_marketing_i_rost", "message_id": 316}},
            "external_post_id": {"tg": {"chat_id": "@oko_marketing_i_rost", "message_id": 316}},
            "content_brief": {
                "policy_version": "SOCIAL_QUALITY_V1", "business": OCEAN["name"],
                "selected_theme": "Какие цифры проверять владельцу бизнеса",
                "current_date": "2026-10-08", "season": "осень-зима",
                "forbidden_mentions": [], "occasion": {"kind": "evergreen"},
            },
            "visual_brief": {
                "headline": "Не смотрите на выручку в одиночку",
                "main_object": "одна конкретная бизнес-сцена", "forbidden_objects": [],
            },
            "delivery_texts": {
                "tg": "Сохранённый TG: исходный текст и контакты.",
                "vk": "Сохранённый VK: исходный текст, контакты и #бизнесметрики.",
            },
            "lifecycle_history": [{"event": "original_tg_delivery", "message_id": 316}],
            "foreign_extension": {"keep": [1, 2, 3]},
        }
        for name, kwargs in (
            ("SessionLocal", {"side_effect": AssertionError("No DB in gate regression")}),
            ("recent_project_posts", {"return_value": []}),
            ("evaluate_copy", {"return_value": {"pass": True, "issues": []}}),
        ):
            boundary = patch.object(sq, name, **kwargs)
            boundary.start(); self.addCleanup(boundary.stop)
        self.media = types.ModuleType("app.services.media_semantic_validator")
        self.media._vision_estimate_kopeks = lambda: 100
        self.media._vision_chat_json = Mock(side_effect=AssertionError("No provider in gate regression"))
        media_patch = patch.dict(sys.modules, {"app.services.media_semantic_validator": self.media})
        media_patch.start(); self.addCleanup(media_patch.stop)
        self.real_refresh = sq.refresh_temporal_brief

    def verdict(self, passed):
        flags = sq.vision_required_flags(OCEAN, self.record["content_brief"], self.record["visual_brief"])
        verdict = {key: True for key in ("pass", *flags)}
        if not passed:
            verdict["pass"] = False
            verdict["semantic_match"] = False
        verdict.update({
            "qa_cost_rub": 0.3949, "qa_new_charge_rub": 0.3949,
            "qa_cost_known": True, "cache_hit": False,
            "qa_cost_evidence": {"source": "offline_fixture", "cost_rub": 0.3949},
            "issues": [] if passed else ["offline_actual_qa_failure"],
        })
        return verdict

    def run_gate(self, record, provider, *, legacy_contract=False, now=NOW):
        before = copy.deepcopy(record)

        def previous_refresh(brief, project, *, now=None):
            refreshed = self.real_refresh(brief, project, now=now)
            refreshed.pop("vision_qa_contract", None)
            return refreshed

        with patch.object(sq, "validate_banner_vision", provider):
            if legacy_contract:
                with patch.object(sq, "refresh_temporal_brief", side_effect=previous_refresh):
                    result = self.gate("u2", record, OCEAN, str(self.asset), now=now)
            else:
                result = self.gate("u2", record, OCEAN, str(self.asset), now=now)
        self.assertEqual(record, before, "Gate must not mutate an input record")
        self.assertEqual(hashlib.sha256(self.asset.read_bytes()).hexdigest(), self.asset_sha)
        updated = copy.deepcopy(record)
        updated.update(copy.deepcopy(result["record_updates"]))
        for key in (
            "ids", "external_post_id", "text", "banner", "generation_key", "status",
            "content_brief", "visual_brief", "delivery_texts", "lifecycle_history",
            "banner_attempt_costs", "banner_vision_qa_costs", "banner_attempts", "foreign_extension",
        ):
            self.assertEqual(updated[key], before[key], key)
        return result, updated

    def assert_old_contract_rechecked(self, prior_pass):
        old_provider = Mock(return_value=self.verdict(prior_pass))
        old_result, old_record = self.run_gate(self.record, old_provider, legacy_contract=True)
        old_provider.assert_called_once()
        self.assertEqual(old_result["pass"], prior_pass)
        self.assertAlmostEqual(old_record["banner_cost_rub"], 2.1137)
        old_attempts = copy.deepcopy(old_record["delivery_qa_attempts"])
        self.assertEqual(len(old_attempts), 1)
        new_provider = Mock(return_value=self.verdict(True))
        new_result, new_record = self.run_gate(old_record, new_provider)
        new_provider.assert_called_once()
        self.assertTrue(new_result["pass"])
        self.assertEqual(new_result["pending_platforms"], ["vk"])
        self.assertEqual(len(new_record["delivery_qa_attempts"]), 2)
        for intent, evidence in old_attempts.items():
            self.assertEqual(new_record["delivery_qa_attempts"][intent], evidence)
        self.assertAlmostEqual(new_record["banner_cost_rub"], 2.5086)
        self.assertLessEqual(new_record["banner_cost_rub"], sq.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB)
        if prior_pass:
            self.assertNotEqual(old_record["delivery_quality"]["vk"]["context_sha256"],
                                new_record["delivery_quality"]["vk"]["context_sha256"])

    def test_old_paid_failed_attempt_does_not_replay_after_contract_change(self):
        self.assert_old_contract_rechecked(False)

    def test_old_passed_delivery_proof_is_not_accepted_under_new_contract(self):
        self.assert_old_contract_rechecked(True)

    def assert_new_contract_reused(self, passed):
        first_provider = Mock(return_value=self.verdict(passed))
        first, record = self.run_gate(self.record, first_provider)
        first_provider.assert_called_once()
        self.assertEqual(first["pass"], passed)
        second_provider = Mock(side_effect=AssertionError("Unchanged proof must not buy QA again"))
        second, updated = self.run_gate(record, second_provider, now=NOW + timedelta(minutes=5))
        second_provider.assert_not_called()
        self.assertEqual(second["pass"], passed)
        self.assertEqual(updated["delivery_qa_attempts"], record["delivery_qa_attempts"])
        self.assertEqual(updated["banner_cost_rub"], record["banner_cost_rub"])
        self.assertEqual(len(updated["delivery_qa_attempts"]), 1)

    def test_new_successful_contract_reuses_proof_without_new_charge(self):
        self.assert_new_contract_reused(True)

    def test_new_failed_contract_reuses_failure_without_new_charge(self):
        self.assert_new_contract_reused(False)

    def test_actual_34908_spend_blocks_new_qa_at_the_existing_four_ruble_cap(self):
        record = copy.deepcopy(self.record)
        record["banner_cost_rub"] = 3.4908
        provider = Mock(side_effect=AssertionError("Reserve would exceed the four-ruble cap"))
        result, updated = self.run_gate(record, provider)
        provider.assert_not_called()
        self.assertFalse(result["pass"])
        self.assertIn("DELIVERY_QA_REMAINING_BUDGET_BLOCKED", result["qa_result"]["issues"])
        self.assertEqual(updated["banner_cost_rub"], 3.4908)
        self.assertNotIn("delivery_qa_attempts", updated)
        self.media._vision_chat_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
