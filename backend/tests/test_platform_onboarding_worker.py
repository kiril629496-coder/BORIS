from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tools import platform_onboarding_worker as worker


class PlatformOnboardingWorkerTests(unittest.TestCase):
    def test_human_checkpoint_due_after_one_hour(self):
        now = datetime.now(timezone.utc)
        row = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(hours=1, minutes=5)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertTrue(worker._verification_due(row, now))

    def test_fresh_human_checkpoint_is_not_reprobed(self):
        now = datetime.now(timezone.utc)
        row = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(minutes=30)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertFalse(worker._verification_due(row, now))

    def test_terminal_or_nonhuman_checkpoint_is_not_probed(self):
        now = datetime.now(timezone.utc)
        terminal = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(hours=3)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=True):
            self.assertFalse(worker._verification_due(terminal, now))

        nonhuman = {
            "status": "verification_required",
            "checkpoint": "account_creation_unverified",
            "updated_at": (now - timedelta(hours=3)).isoformat(),
        }
        with patch.object(worker.marketplace, "registration_is_terminally_blocked", return_value=False):
            self.assertFalse(worker._verification_due(nonhuman, now))

    def test_active_client_demand_sorts_first(self):
        reg = {
            "urgent": {"updated_at": "2026-09-01T00:00:00+00:00"},
            "later": {"updated_at": "2026-08-01T00:00:00+00:00"},
        }
        priority = {
            "urgent": {"urgent_for_active_projects": True, "relevant_projects": 1, "potential_slots": 1},
            "later": {"urgent_for_active_projects": False, "relevant_projects": 0, "potential_slots": 0},
        }
        items = [{"platform": "later", "priority": 1}, {"platform": "urgent", "priority": 99}]
        items.sort(key=lambda x: worker._priority_key(x, priority, reg))
        self.assertEqual([x["platform"] for x in items], ["urgent", "later"])

    def test_active_project_scope_excludes_reserve_until_client_onboarding_is_clear(self):
        candidates = [
            {"platform": "client_forum"},
            {"platform": "reserve_forum"},
        ]
        priority = {
            "client_forum": {
                "urgent_for_active_projects": True,
                "relevant_projects": 1,
                "potential_slots": 1,
            }
        }
        scoped = worker._scope_candidates_to_active_projects(candidates, priority)
        self.assertEqual([x["platform"] for x in scoped], ["client_forum"])
        self.assertEqual(worker._scope_candidates_to_active_projects(candidates, {}), candidates)

    def test_worker_verify_command_never_submits(self):
        class Proc:
            returncode = 0
            stdout = '{"ok": true, "status": "verification_required", "checkpoint": "captcha_required"}'
            stderr = ""

        with patch.object(worker.subprocess, "run", return_value=Proc()) as run:
            result = worker._verify("example")

        argv = run.call_args.args[0]
        self.assertIn("verify-registration", argv)
        self.assertNotIn("--submit", argv)
        self.assertFalse(result["submitted"])


    def test_transport_failure_preserves_human_checkpoint_and_refreshes_timestamp(self):
        class Proc:
            returncode = 1
            stdout = ""
            stderr = "Page.goto: Timeout 45000ms exceeded"

        current = {
            "platform": "captcha_forum",
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "email": "worker@example.com",
            "account_url": "https://forum.example/register",
        }
        with (
            patch.object(worker.subprocess, "run", return_value=Proc()),
            patch.object(worker.marketplace, "registration_plan", return_value=[current]),
            patch.object(worker.marketplace, "upsert_registration") as upsert,
            patch.object(worker.marketplace, "record_attempt") as record,
        ):
            result = worker._verify("captcha_forum")

        self.assertEqual(result["returncode"], 1)
        self.assertIn("Timeout", result["error"])
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[:2], ("captcha_forum", "verification_required"))
        self.assertEqual(upsert.call_args.kwargs["checkpoint"], "captcha_required")
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["error_code"], "verification_transport_error")

    def test_timeout_refreshes_checkpoint_instead_of_monopolising_queue(self):
        current = {
            "platform": "slow_forum",
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "email": "worker@example.com",
            "account_url": "https://forum.example/register",
        }
        with (
            patch.object(worker.subprocess, "run", side_effect=worker.subprocess.TimeoutExpired(cmd=["verify"], timeout=70)),
            patch.object(worker.marketplace, "registration_plan", return_value=[current]),
            patch.object(worker.marketplace, "upsert_registration") as upsert,
            patch.object(worker.marketplace, "record_attempt") as record,
        ):
            result = worker._verify("slow_forum")

        self.assertEqual(result["returncode"], 124)
        self.assertEqual(result["status"], "verification_required")
        self.assertEqual(result["checkpoint"], "captcha_required")
        self.assertEqual(result["error"], "verification_timeout")
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["checkpoint"], "captcha_required")
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["error_code"], "verification_transport_error")

    def test_transient_rule_audit_due_uses_short_guardian_interval(self):
        now = datetime.now(timezone.utc)
        row = {
            "checked_at": (now - worker.guardian.TRANSIENT_AUDIT_RETRY_INTERVAL - timedelta(minutes=1)).isoformat(),
            "decision": "review",
            "errors": [{"error": "Temporary failure in name resolution"}],
        }
        with (
            patch.object(worker.platform_rules, "latest", return_value=row),
            patch.object(worker.guardian, "_audit_has_transient_transport_error", return_value=True),
        ):
            self.assertTrue(worker._transient_rule_audit_due("forum", now))

    def test_worker_retries_transient_rule_audit_and_marks_recovery(self):
        now = datetime.now(timezone.utc)
        previous = {
            "checked_at": (now - timedelta(hours=3)).isoformat(),
            "decision": "review",
            "errors": [{"error": "Page.goto: net::ERR_NAME_NOT_RESOLVED"}],
        }
        recovered = {
            "checked_at": now.isoformat(),
            "decision": "allowed",
            "errors": [],
        }
        with (
            patch.object(worker.marketplace, "list_platforms", return_value=[{"key": "forum"}]),
            patch.object(worker.platform_rules, "latest", return_value=previous),
            patch.object(worker.platform_rules, "inspect_platform", return_value=recovered) as inspect,
            patch.object(worker.guardian, "_audit_has_transient_transport_error", side_effect=lambda row: bool(row.get("errors"))),
        ):
            result = worker._retry_transient_rule_audits(now)

        inspect.assert_called_once_with("forum")
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["progressed"], ["forum"])
        self.assertFalse(result["attempts"][0]["transient_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
