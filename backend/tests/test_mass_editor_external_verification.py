import unittest
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.api import mass_editor as me


class ExternalVerificationTests(unittest.TestCase):
    def test_verify_job_is_read_recoverable(self):
        job = {
            "mode": "verify",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "route_recovery_attempts": 0,
        }
        self.assertEqual(me._isolated_network_recovery_policy(job), "resume_read")

    def test_stale_plain_scan_is_terminal_after_24h(self):
        job = {
            "mode": "scan",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
            "route_recovery_attempts": 0,
        }
        self.assertEqual(me._isolated_network_recovery_policy(job), "skip")

    def test_parent_completes_only_after_all_external_items_verified(self):
        parent = {
            "job_id": "parent1", "status": "waiting_external_verification", "verified": 0,
            "autoload": {"external_verified": 0},
        }
        writes = {}
        def fake_get(account_id, key):
            if key == "mass_editor_job:parent1":
                return dict(parent)
            return None
        def fake_put(account_id, key, value):
            writes[key] = dict(value)
        child = {
            "job_id": "verify1", "parent_job_id": "parent1", "mode": "verify",
            "status": "completed", "total": 1, "verified": 1, "failed": 0, "error": None,
        }
        with patch.object(me, "_db_get", side_effect=fake_get), patch.object(me, "_db_put", side_effect=fake_put):
            me._sync_external_verification_parent("acc", child)
        result = writes["mass_editor_job:parent1"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["autoload"]["external_verified"], 1)

    def test_parent_stays_waiting_when_external_browser_is_blocked(self):
        parent = {
            "job_id": "parent1", "status": "waiting_external_verification", "verified": 0,
            "autoload": {"external_verified": 0},
        }
        writes = {}
        with patch.object(me, "_db_get", return_value=dict(parent)), patch.object(me, "_db_put", side_effect=lambda a, k, v: writes.__setitem__(k, dict(v))):
            me._sync_external_verification_parent("acc", {
                "job_id": "verify1", "parent_job_id": "parent1", "mode": "verify",
                "status": "blocked_network_route", "total": 1, "verified": 0, "failed": 0,
                "error": "route unavailable",
            })
        result = writes["mass_editor_job:parent1"]
        self.assertEqual(result["status"], "waiting_external_verification")
        self.assertEqual(result["verified"], 0)
        self.assertEqual(result["external_verification"]["status"], "blocked_network_route")

    def test_external_verification_job_is_read_only(self):
        writes = {}
        with patch.object(me, "_db_put", side_effect=lambda a, k, v: writes.__setitem__(k, dict(v))):
            job = me._queue_external_verification_job("acc", "parent", {
                "items": [{"id": 123, "title": "x", "status": "active", "changes": {"custom:Бренд": "HAG"}}],
                "rules": {"custom:Бренд": {"mode": "fixed", "value": "HAG"}},
            })
        self.assertEqual(job["mode"], "verify")
        self.assertTrue(job["read_only"])
        self.assertFalse(job["confirmed"])
        self.assertEqual(job["executor"], "isolated_browser")
        self.assertEqual(job["items"][0]["changes"]["custom:Бренд"], "HAG")

    def test_provider_probe_skipped_when_no_blocked_jobs(self):
        previous = {"state": "avito_blocked", "checked_at": time.time() - 20}
        with patch.object(me, "_current_network_blocked_isolated_job_count", return_value=0), \
             patch.object(me, "_db_get", return_value=previous), \
             patch.object(me.asyncio, "run") as run_mock:
            result = me._probe_and_recover_network_blocked_jobs()
        self.assertTrue(result["skipped_no_blocked_jobs"])
        self.assertEqual(result["blocked_jobs"], 0)
        run_mock.assert_not_called()

    def test_avito_blocked_provider_has_five_minute_backoff(self):
        previous = {"state": "avito_blocked", "checked_at": time.time() - 30}
        with patch.object(me, "_current_network_blocked_isolated_job_count", return_value=2), \
             patch.object(me, "_db_get", return_value=previous), \
             patch.object(me.asyncio, "run") as run_mock:
            result = me._probe_and_recover_network_blocked_jobs()
        self.assertTrue(result["skipped_avito_block_backoff"])
        self.assertEqual(result["blocked_jobs"], 2)
        self.assertGreater(result["retry_after_seconds"], 0)
        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
