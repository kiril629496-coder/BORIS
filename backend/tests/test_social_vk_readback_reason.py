"""Actual VK normalizer + reconciliation/receipt flow, with HTTP/DB replaced."""
import ast
import copy
import hashlib
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from app.services.social_delivery_receipts import DeliveryIntent, guarded_delivery
from app.services.social_delivery_reconcile import reconcile_vk


RUNNER_SOURCE = Path(__file__).resolve().parents[1] / "posting_runner.py"
API_FAILURE_AST_SHA256 = "4915ba7cfa08c54670c3e183ee93a1acfcd1f7dbc92b24d4cb1923ecd8b8bbfa"
SECRET = "UNIT_ONLY_SECRET_MUST_NEVER_ENTER_RECEIPT"


class MemoryJournal:
    """Only the persistence boundary; the real guarded_delivery runs unchanged."""
    def __init__(self):
        self.rows = {}

    def __call__(self, intent):
        owner = self

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def load(self):
                return copy.deepcopy(owner.rows.get(intent.key))

            def save(self, value):
                owner.rows[intent.key] = copy.deepcopy(value)

            def destination_admission(self):
                raise AssertionError("Uncertain readback must not reach send admission")

        return Session()


class VkReadbackReasonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = RUNNER_SOURCE.read_text()
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_vk_api_failure")
        if hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() != API_FAILURE_AST_SHA256:
            raise AssertionError("Reviewed canonical VK normalizer changed")
        cls.normalizer_code = compile(ast.Module(body=[node], type_ignores=[]), str(RUNNER_SOURCE), "exec")

    def setUp(self):
        self.backoff = Mock(side_effect=lambda seconds: seconds)
        namespace = {
            "_set_social_vk_rate_backoff": self.backoff,
            "observe_suppressed": Mock(side_effect=AssertionError("Unexpected normalizer suppression")),
            "sys": sys,
        }
        exec(self.normalizer_code, namespace)
        self.normalizer = namespace["_vk_api_failure"]
        self.intent = DeliveryIntent("u2", "vk", "-239695377", "wall_post", "Existing post", "", {
            "post_id": "5c71018d7c61", "project_id": "f938caa3a944", "protocol": None,
            "created_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            "legacy_error": "VK live delivery paced: next rescue pass",
        })
        self.receipt = {"context": copy.deepcopy(self.intent.context), "origin": "legacy"}

    def session(self, payload):
        return types.SimpleNamespace(get=Mock(return_value=types.SimpleNamespace(
            json=lambda: copy.deepcopy(payload))))

    def payload(self, code):
        return {"error": {"error_code": code, "error_msg": SECRET,
                          "request_params": [{"key": "access_token", "value": SECRET}]},
                "raw_secret": SECRET}

    def assert_safe(self, result):
        serialized = json.dumps(result, ensure_ascii=False)
        for forbidden in (SECRET, "error_msg", "request_params", "access_token", "raw_secret"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "vk_readback_rejected")
        self.assertTrue(set(result.get("api_failure", {})) <= {
            "code", "stage", "reason", "retryable_external", "dependency_state", "retry_after_seconds",
        })

    def call(self, payload, callback):
        session = self.session(payload)
        result = reconcile_vk(self.intent, self.receipt, token=SECRET,
                              session=session, on_api_failure=callback)
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], "https://api.vk.com/method/wall.get")
        return result

    def test_flood_9_and_29_keep_exact_normalized_code_and_existing_backoff(self):
        for code, seconds in ((9, 3600), (29, 900)):
            with self.subTest(code=code):
                self.backoff.reset_mock()
                result = self.call(self.payload(code), self.normalizer)
                self.assert_safe(result)
                self.assertEqual(result["api_failure"], {
                    "code": code, "stage": "wall.get", "reason": "vk_rate_limited",
                    "retryable_external": True, "dependency_state": "waiting_external",
                    "retry_after_seconds": seconds,
                })
                self.backoff.assert_called_once_with(seconds)

    def test_auth_5_and_permission_15_are_distinguishable_without_invented_retry(self):
        for code in (5, 15):
            with self.subTest(code=code):
                result = self.call(self.payload(code), self.normalizer)
                self.assert_safe(result)
                self.assertEqual(result["api_failure"], {"code": code, "stage": "wall.get"})
        self.backoff.assert_not_called()

    def test_unknown_provider_error_does_not_leak_its_raw_message_or_parameters(self):
        for raw_code, expected_code in ((999, 999), (SECRET, 0)):
            with self.subTest(expected_code=expected_code):
                result = self.call(self.payload(raw_code), self.normalizer)
                self.assert_safe(result)
                self.assertEqual(result["api_failure"], {"code": expected_code, "stage": "wall.get"})
        self.backoff.assert_not_called()

    def test_none_and_non_dict_callback_results_keep_original_generic_result(self):
        for value in (None, SECRET, [SECRET], 9, True, object()):
            with self.subTest(value_type=type(value).__name__):
                callback = Mock(return_value=value)
                result = self.call(self.payload(9), callback)
                self.assertEqual(result, {"status": "unavailable", "reason": "vk_readback_rejected"})
                callback.assert_called_once()
                self.assert_safe(result)

    def test_numeric_strings_normalize_but_boolean_strings_do_not(self):
        result = self.call(self.payload(29), Mock(return_value={
            "code": " 29 ", "stage": "wall.get", "reason": "vk_rate_limited",
            "retry_after_seconds": "900", "retryable_external": "true",
            "dependency_state": "waiting_external", "error": SECRET,
        }))
        self.assert_safe(result)
        self.assertEqual(result["api_failure"], {
            "code": 29, "stage": "wall.get", "reason": "vk_rate_limited",
            "retry_after_seconds": 900, "dependency_state": "waiting_external",
        })
        self.assertIs(type(result["api_failure"]["code"]), int)
        self.assertIs(type(result["api_failure"]["retry_after_seconds"]), int)

    def test_wrong_types_and_unrecognized_text_are_omitted_without_coercion(self):
        for wrong_integer in (True, -1, 9.5, float("nan"), "NaN", SECRET, 2**40):
            with self.subTest(value_type=type(wrong_integer).__name__):
                result = self.call(self.payload(9), Mock(return_value={
                    "code": wrong_integer, "retry_after_seconds": wrong_integer,
                    "stage": "wall.get:" + SECRET, "reason": SECRET,
                    "dependency_state": {"secret": SECRET}, "retryable_external": False,
                    "error": SECRET, "request_params": {"access_token": SECRET},
                }))
                self.assert_safe(result)
                self.assertEqual(result["api_failure"], {"retryable_external": False})

    def test_absent_error_or_callback_retains_existing_generic_behavior(self):
        callback = Mock(side_effect=AssertionError("No API error to normalize"))
        result = self.call({"response": {"items": "invalid"}, "raw_secret": SECRET}, callback)
        self.assertEqual(result, {"status": "unavailable", "reason": "vk_readback_rejected"})
        callback.assert_not_called()
        self.assertEqual(self.call(self.payload(9), None), result)

    def test_callback_exception_semantics_remain_unchanged(self):
        callback = Mock(side_effect=RuntimeError("offline callback failure"))
        with self.assertRaisesRegex(RuntimeError, "offline callback failure"):
            self.call(self.payload(9), callback)
        callback.assert_called_once()

    def test_active_cooldown_makes_no_http_or_callback_call(self):
        session = self.session(self.payload(9))
        callback = Mock(side_effect=AssertionError("No callback during cooldown"))
        result = reconcile_vk(self.intent, self.receipt, token=SECRET, session=session,
                              cooldown_remaining=lambda: 100, on_api_failure=callback)
        self.assertEqual(result, {"status": "unavailable", "reason": "vk_cooldown"})
        session.get.assert_not_called(); callback.assert_not_called()

    def test_successful_readback_has_no_new_diagnostics_or_changed_result(self):
        now = int(datetime.now(timezone.utc).timestamp())
        item = {"id": 321, "owner_id": -239695377, "date": now, "text": self.intent.text, "attachments": []}
        callback = Mock(side_effect=AssertionError("No API failure on success"))
        result = self.call({"response": {"count": 1, "items": [item]}}, callback)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["result"]["post_id"], 321)
        self.assertEqual(result["provider_published_at"], now)
        self.assertNotIn("api_failure", result)
        callback.assert_not_called(); self.backoff.assert_not_called()

    def test_actual_receipt_saves_sanitized_reason_without_resend_or_extra_readback(self):
        journal = MemoryJournal()
        session = self.session(self.payload(9))
        send = Mock(side_effect=AssertionError("Readback rejection must not publish"))
        prepare = Mock(side_effect=AssertionError("Readback rejection must not prepare a send"))

        def readback(intent, receipt):
            return reconcile_vk(intent, receipt, token=SECRET, session=session,
                                on_api_failure=self.normalizer)

        first = guarded_delivery(self.intent, send, prepare=prepare,
                                 reconcile=readback, journal_factory=journal)
        saved = copy.deepcopy(journal.rows[self.intent.key])
        evidence = saved["last_reconciliation"]
        self.assert_safe(evidence)
        self.assertEqual(evidence["api_failure"]["code"], 9)
        self.assertEqual(saved["status"], "uncertain")
        self.assertIn("next_reconciliation_at", saved)
        self.assertTrue(first["delivery_outcome_uncertain"])
        self.assertEqual(first["reason"], "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN")
        again = guarded_delivery(self.intent, send, prepare=prepare,
                                 reconcile=readback, journal_factory=journal)
        self.assertTrue(again["delivery_outcome_uncertain"])
        self.assertEqual(journal.rows[self.intent.key], saved)
        session.get.assert_called_once(); self.backoff.assert_called_once_with(3600)
        send.assert_not_called(); prepare.assert_not_called()
        self.assertNotIn(SECRET, json.dumps(saved, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
