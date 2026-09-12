import unittest
from unittest.mock import patch

import app.services.email_worker_guard as guard


class EmailWorkerGuardSystemdTest(unittest.TestCase):
    def test_guard_has_no_cron_repair_contract(self):
        self.assertFalse(hasattr(guard, "CRON_LINE"))
        self.assertFalse(hasattr(guard, "_repair_cron"))
        self.assertEqual(guard.TIMER_UNIT, "boris-email-queue.timer")
        self.assertEqual(guard.SERVICE_UNIT, "boris-email-queue.service")

    @patch.object(guard, "_heartbeat_age_seconds", return_value=15.0)
    @patch.object(guard, "_timer_state", return_value=(True, True))
    @patch.object(guard, "_active_outreach", return_value=1)
    def test_healthy_timer_is_canonical(self, _active, _timer, _age):
        result = guard.ensure()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["timer_enabled"])
        self.assertTrue(result["timer_active"])
        self.assertFalse(result["timer_repaired"])
        self.assertFalse(result["service_triggered"])

    @patch.object(guard, "_heartbeat_age_seconds", return_value=20.0)
    @patch.object(guard, "_repair_timer", return_value=True)
    @patch.object(guard, "_timer_state", side_effect=[(False, False), (True, True)])
    @patch.object(guard, "_active_outreach", return_value=1)
    def test_missing_timer_is_repaired_via_systemd(self, _active, _timer, _repair, _age):
        result = guard.ensure()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["timer_repaired"])
        self.assertTrue(result["timer_enabled"])
        self.assertTrue(result["timer_active"])

    @patch.object(guard, "_trigger_service_once", return_value=True)
    @patch.object(guard, "_heartbeat_age_seconds", side_effect=[700.0, 1.0])
    @patch.object(guard, "_timer_state", return_value=(True, True))
    @patch.object(guard, "_active_outreach", return_value=1)
    def test_stale_heartbeat_triggers_canonical_service(self, _active, _timer, _age, _trigger):
        result = guard.ensure(max_heartbeat_age_seconds=300)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["service_triggered"])
        self.assertLessEqual(result["heartbeat_age_seconds"], 1.0)


if __name__ == "__main__":
    unittest.main()
