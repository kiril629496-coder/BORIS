import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phone-guardian-supervisor.py"
SPEC = importlib.util.spec_from_file_location("phone_guardian_supervisor", SCRIPT)
S = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(S)


def escalation(ok=True):
    return {
        "ok": ok,
        "script_present": ok,
        "alert_file_exact": ok,
        "dropin_file_exact": ok,
        "on_failure_loaded": ok,
        "post_loaded": ok,
        "alert_loaded": ok,
    }


def contract(ok=True):
    return {"ok": ok, "rc": 0 if ok else 3, "result": "PASS" if ok else "FAIL"}


def dropin(ok=True):
    return {"ok": ok, "file_exact": ok, "loaded": ok}


def timer(ok=True):
    return {"active": ok, "enabled": ok, "last_trigger": "now", "raw": {}}


def service(ok=True, in_progress=False):
    return {
        "success": ok,
        "in_progress": in_progress,
        "active_state": "inactive" if ok else ("activating" if in_progress else "failed"),
        "result": "success" if ok else "exit-code",
        "exec_main_status": "0" if ok else "2",
        "last_finished": "now",
        "raw": {},
    }


class PhoneGuardianSupervisorTests(unittest.TestCase):
    def setUp(self):
        self._escalation_patcher = patch.object(S, "_escalation_state", return_value=escalation())
        self._escalation_patcher.start()
        self.addCleanup(self._escalation_patcher.stop)
        self._preflight_patcher = patch.object(
            S, "_preflight_dropin_state",
            return_value={"ok": True, "script_present": True, "file_exact": True, "loaded": True},
        )
        self._preflight_patcher.start()
        self.addCleanup(self._preflight_patcher.stop)

    def test_healthy_is_noop(self):
        with patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service()), \
             patch.object(S, "_run") as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["errors"], [])
        run.assert_not_called()

    def test_atomic_systemd_write_permission_error_is_reported_not_raised(self):
        with patch.object(S.tempfile, "mkstemp", side_effect=PermissionError("denied")):
            self.assertFalse(
                S._atomic_write_text(
                    Path("/etc/systemd/system/boris-telephony-qa-never-written.service"),
                    "[Service]\n",
                )
            )

    def test_missing_escalation_wiring_is_restored_before_guardian_work(self):
        with patch.object(S, "_escalation_state", side_effect=[
                escalation(False), escalation(True), escalation(True)
             ]), \
             patch.object(S, "_restore_escalation_wiring", return_value=True) as restore, \
             patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service()), \
             patch.object(S, "_run") as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertIn("restored_supervisor_escalation", out["actions"])
        restore.assert_called_once_with()
        run.assert_not_called()

    def test_contract_failure_is_fail_closed_and_never_restarts(self):
        with patch.object(S, "_contract_state", return_value=contract(False)), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service(False)), \
             patch.object(S, "_run") as run:
            out = S.supervise()
        self.assertEqual(out["status"], "blocked_contract")
        self.assertIn("telephony_guardian_contract_failed", out["errors"])
        run.assert_not_called()

    def test_disabled_timer_is_enabled_and_verified(self):
        with patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", side_effect=[timer(False), timer(False), timer(True)]), \
             patch.object(S, "_service_state", return_value=service()), \
             patch.object(S, "_run", return_value={"ok": True, "rc": 0, "stdout": "", "stderr": ""}) as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertIn("enabled_guardian_timer", out["actions"])
        self.assertTrue(any(c.args[0][:3] == ["systemctl", "enable", "--now"] for c in run.call_args_list))

    def test_failed_oneshot_is_retried_once_and_verified(self):
        with patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", side_effect=[service(False), service(False), service(True)]), \
             patch.object(S, "_run", return_value={"ok": True, "rc": 0, "stdout": "", "stderr": ""}) as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertIn("retried_guardian_service", out["actions"])
        starts = [c for c in run.call_args_list if c.args[0] == ["systemctl", "start", S.GUARDIAN_SERVICE]]
        self.assertEqual(len(starts), 1)

    def test_wrong_phone_preflight_dropin_is_restored_and_verified(self):
        bad = {"ok": False, "script_present": True, "file_exact": False, "loaded": False}
        good = {"ok": True, "script_present": True, "file_exact": True, "loaded": True}
        with patch.object(S, "_preflight_dropin_state", side_effect=[bad, good, good]), \
             patch.object(S, "_restore_preflight_dropin", return_value=True) as restore, \
             patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service()), \
             patch.object(S, "_run") as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertIn("restored_phone_bytecode_preflight", out["actions"])
        restore.assert_called_once_with()
        run.assert_not_called()

    def test_missing_phone_preflight_script_keeps_supervisor_degraded(self):
        bad = {"ok": False, "script_present": False, "file_exact": False, "loaded": False}
        with patch.object(S, "_preflight_dropin_state", return_value=bad), \
             patch.object(S, "_restore_preflight_dropin", return_value=False), \
             patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service()), \
             patch.object(S, "_run"):
            out = S.supervise()
        self.assertEqual(out["status"], "degraded")
        self.assertIn("phone_preflight_restore_failed", out["errors"])

    def test_missing_dropin_is_restored_before_runtime_retry(self):
        with patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", side_effect=[dropin(False), dropin(True)]), \
             patch.object(S, "_atomic_write_dropin", return_value=True) as write, \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service()), \
             patch.object(S, "_run", return_value={"ok": True, "rc": 0, "stdout": "", "stderr": ""}) as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertIn("restored_contract_dropin", out["actions"])
        write.assert_called_once_with()
        self.assertTrue(any(c.args[0] == ["systemctl", "daemon-reload"] for c in run.call_args_list))

    def test_running_guardian_is_not_raced(self):
        with patch.object(S, "_contract_state", return_value=contract()), \
             patch.object(S, "_dropin_state", return_value=dropin()), \
             patch.object(S, "_timer_state", return_value=timer()), \
             patch.object(S, "_service_state", return_value=service(False, in_progress=True)), \
             patch.object(S, "_run") as run:
            out = S.supervise()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["actions"], [])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
