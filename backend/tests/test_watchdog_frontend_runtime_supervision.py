import unittest
from unittest.mock import patch

from app.ext_api import watchdog


class Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def base_report():
    return {
        "text": "base",
        "system": "GREEN",
        "false_green": False,
        "stalled": [],
        "clients": [],
        "master": {},
        "queue": {"duplicates": 0, "anomaly": False},
        "delta": 0,
        "coverage_ok": True,
    }


class FrontendRuntimeSupervisionTests(unittest.TestCase):
    def test_healthy_timer_does_not_mutate(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(tuple(cmd))
            action = cmd[1]
            if action == "is-active":
                return Proc(0, "active\n")
            if action == "is-enabled":
                return Proc(0, "enabled\n")
            raise AssertionError(cmd)

        with patch.object(watchdog.subprocess, "run", side_effect=fake_run):
            rep = watchdog.frontend_runtime_supervision("unit.timer")

        self.assertTrue(rep["ok"])
        self.assertFalse(rep["healed"])
        self.assertEqual(rep["actions"], [])
        self.assertEqual(
            [call[1] for call in calls],
            ["is-active", "is-enabled", "is-active", "is-enabled"],
        )

    def test_stopped_disabled_timer_self_heals(self):
        state = {"active": False, "enabled": False}

        def fake_run(cmd, **kwargs):
            action = cmd[1]
            if action == "is-active":
                return Proc(0, "active\n") if state["active"] else Proc(3, "inactive\n")
            if action == "is-enabled":
                return Proc(0, "enabled\n") if state["enabled"] else Proc(1, "disabled\n")
            if action == "enable":
                state["enabled"] = True
                return Proc()
            if action == "start":
                state["active"] = True
                return Proc()
            raise AssertionError(cmd)

        with patch.object(watchdog.subprocess, "run", side_effect=fake_run):
            rep = watchdog.frontend_runtime_supervision("unit.timer")

        self.assertTrue(rep["ok"])
        self.assertTrue(rep["healed"])
        self.assertEqual(rep["actions"], ["enabled", "started"])
        self.assertTrue(rep["active_after"])
        self.assertTrue(rep["enabled_after"])

    def test_unrecovered_timer_failure_is_reported(self):
        def fake_run(cmd, **kwargs):
            action = cmd[1]
            if action == "is-active":
                return Proc(3, "inactive\n")
            if action == "is-enabled":
                return Proc(1, "disabled\n")
            if action in ("enable", "start"):
                return Proc(1, stderr="permission denied")
            raise AssertionError(cmd)

        with patch.object(watchdog.subprocess, "run", side_effect=fake_run):
            rep = watchdog.frontend_runtime_supervision("unit.timer")

        self.assertFalse(rep["ok"])
        self.assertFalse(rep["healed"])
        self.assertEqual(len(rep["errors"]), 2)

    def test_once_escalates_only_unrecovered_timer_and_healthy_signature_is_compatible(self):
        healthy = base_report()
        healthy["frontend_runtime_supervision"] = {"ok": True}
        legacy = base_report()
        self.assertEqual(
            watchdog._notify_signature(healthy),
            watchdog._notify_signature(legacy),
        )

        with (
            patch.object(watchdog, "beat", return_value=None),
            patch.object(watchdog, "snapshot_report", side_effect=base_report),
            patch.object(
                watchdog,
                "frontend_runtime_supervision",
                return_value={"ok": False, "healed": False, "actions": [], "errors": ["x"]},
            ),
        ):
            rep = watchdog.once(send=False)

        self.assertEqual(rep["system"], "RED")
        self.assertIn("не смог автоматически восстановить", rep["text"])
        self.assertIn("frontend runtime timer не восстановлен", rep["acted"])
        self.assertIn(
            '"frontend_runtime_timer_ok": false',
            watchdog._notify_signature(rep),
        )

    def test_recover_owner_watchdog_restarts_and_verifies_heartbeat(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(tuple(cmd))
            if cmd[1] == "restart":
                return Proc()
            if cmd[1] == "is-active":
                return Proc(0, "active\n")
            raise AssertionError(cmd)

        with (
            patch.object(watchdog.subprocess, "run", side_effect=fake_run),
            patch.object(watchdog, "missed_heartbeat", return_value=(False, 0)),
        ):
            rep = watchdog.recover_owner_watchdog("boris-watchdog.service", wait_seconds=1)

        self.assertTrue(rep["ok"])
        self.assertEqual(calls[0], ("systemctl", "restart", "--no-block", "boris-watchdog.service"))
        self.assertIn(("systemctl", "is-active", "boris-watchdog.service"), calls)

    def test_check_returns_nonzero_when_watchdog_cannot_recover(self):
        with (
            patch.object(watchdog, "missed_heartbeat", return_value=(True, 99)),
            patch.object(
                watchdog,
                "recover_owner_watchdog",
                return_value={"ok": False, "error": "restart_failed"},
            ),
            patch.object(watchdog, "_runtime_error_should_notify", return_value=False),
            patch("sys.argv", ["watchdog", "check"]),
        ):
            rc = watchdog.main()

        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
