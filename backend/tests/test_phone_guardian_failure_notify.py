import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phone-guardian-failure-notify.py"
SPEC = importlib.util.spec_from_file_location("phone_guardian_failure_notify", SCRIPT)
N = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(N)


def facts(*, supervisor_result="exit-code", contract_ok=True):
    return {
        "checked_at": "2026-09-06T14:20:00+00:00",
        "supervisor": {
            "active_state": "failed",
            "result": supervisor_result,
            "exec_main_status": "2",
            "unit_file_state": "static",
        },
        "supervisor_timer": {
            "active_state": "active",
            "result": "success",
            "exec_main_status": "0",
            "unit_file_state": "enabled",
        },
        "guardian": {
            "active_state": "failed",
            "result": "exit-code",
            "exec_main_status": "2",
            "unit_file_state": "static",
        },
        "guardian_timer": {
            "active_state": "active",
            "result": "success",
            "exec_main_status": "0",
            "unit_file_state": "enabled",
        },
        "contract": {"ok": contract_ok, "code": "pass" if contract_ok else "fail", "rc": 0 if contract_ok else 3},
    }


class PhoneGuardianFailureNotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (N.STATE_DIR, N.STATE, N.HEALTH, N.JOURNAL)
        N.STATE_DIR = root
        N.STATE = root / "failure_state.json"
        N.HEALTH = root / "last_healthy.json"
        N.JOURNAL = root / "failure_alerts.jsonl"

    def tearDown(self):
        N.STATE_DIR, N.STATE, N.HEALTH, N.JOURNAL = self.old
        self.tmp.cleanup()

    def test_first_two_failures_are_silent_then_third_escalates_once(self):
        sender = Mock(return_value=(True, [{"ok": True, "message_id": 7}]))
        one = N.process_failure(facts(), now=1000, send_func=sender)
        two = N.process_failure(facts(), now=1060, send_func=sender)
        three = N.process_failure(facts(), now=1120, send_func=sender)

        self.assertEqual(one["streak"], 1)
        self.assertEqual(two["streak"], 2)
        self.assertEqual(one["status"], "retrying_before_escalation")
        self.assertEqual(two["status"], "retrying_before_escalation")
        self.assertFalse(one["owner_action_required"])
        self.assertFalse(two["owner_action_required"])

        self.assertEqual(three["streak"], 3)
        self.assertEqual(three["status"], "sent")
        self.assertTrue(three["sent"])
        self.assertTrue(three["owner_action_required"])
        sender.assert_called_once()

    def test_success_resets_streak_immediately(self):
        N.process_failure(facts(), now=1000, send_func=Mock())
        N.process_failure(facts(), now=1060, send_func=Mock())
        marked = N.mark_healthy(now=1100)
        state = json.loads(N.STATE.read_text())
        after = N.process_failure(facts(), now=1160, send_func=Mock())

        self.assertEqual(marked["streak"], 0)
        self.assertEqual(state["streak"], 0)
        self.assertEqual(after["streak"], 1)
        self.assertEqual(after["status"], "retrying_before_escalation")

    def test_same_successful_alert_is_deduped_for_six_hours(self):
        sender = Mock(return_value=(True, [{"ok": True}]))
        N.process_failure(facts(), now=1000, send_func=sender)
        N.process_failure(facts(), now=1060, send_func=sender)
        third = N.process_failure(facts(), now=1120, send_func=sender)
        fourth = N.process_failure(facts(), now=1180, send_func=sender)

        self.assertEqual(third["status"], "sent")
        self.assertEqual(fourth["status"], "deduped")
        self.assertEqual(sender.call_count, 1)

    def test_recovered_then_new_persistent_incident_alerts_again(self):
        sender = Mock(return_value=(True, [{"ok": True}]))
        N.process_failure(facts(), now=1000, send_func=sender)
        N.process_failure(facts(), now=1060, send_func=sender)
        first_alert = N.process_failure(facts(), now=1120, send_func=sender)
        N.mark_healthy(now=1200)
        N.process_failure(facts(), now=1260, send_func=sender)
        N.process_failure(facts(), now=1320, send_func=sender)
        second_alert = N.process_failure(facts(), now=1380, send_func=sender)

        self.assertEqual(first_alert["status"], "sent")
        self.assertEqual(second_alert["status"], "sent")
        self.assertEqual(sender.call_count, 2)

    def test_failed_delivery_is_rate_limited_but_supervisor_streak_continues(self):
        sender = Mock(return_value=(False, [{"ok": False, "description": "offline"}]))
        N.process_failure(facts(), now=1000, send_func=sender)
        N.process_failure(facts(), now=1060, send_func=sender)
        third = N.process_failure(facts(), now=1120, send_func=sender)
        fourth = N.process_failure(facts(), now=1180, send_func=sender)
        later = N.process_failure(facts(), now=1120 + N.DELIVERY_RETRY_SECONDS + 1, send_func=sender)

        self.assertEqual(third["status"], "send_failed")
        self.assertEqual(fourth["status"], "delivery_retry_deferred")
        self.assertGreaterEqual(later["streak"], 5)
        self.assertEqual(later["status"], "send_failed")
        self.assertEqual(sender.call_count, 2)

    def test_long_gap_starts_new_streak(self):
        N.process_failure(facts(), now=1000, send_func=Mock())
        later = N.process_failure(
            facts(),
            now=1000 + N.STREAK_WINDOW_SECONDS + 1,
            send_func=Mock(),
        )
        self.assertEqual(later["streak"], 1)

    def test_fingerprint_changes_when_failure_class_changes(self):
        self.assertNotEqual(
            N.fingerprint(facts()),
            N.fingerprint(facts(supervisor_result="timeout")),
        )

    def test_message_contains_no_client_or_secret_material(self):
        msg = N.message(facts(contract_ok=False), 3)
        self.assertIn("3", msg)
        self.assertIn("safety-contract: FAIL", msg)
        self.assertNotIn("password", msg.lower())
        self.assertNotIn("token", msg.lower())


if __name__ == "__main__":
    unittest.main()
