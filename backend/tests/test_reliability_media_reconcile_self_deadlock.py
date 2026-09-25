import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.services import reliability


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def query(self, *args, **kwargs):
        return _FakeQuery(self.rows)


def _heartbeat_row(age_seconds, state="degraded"):
    return SimpleNamespace(
        module="media",
        worker_id="published_campaign_self_heal",
        account_id="*",
        state=state,
        details_json={"unresolved": 5, "paid_deferred": 5},
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


class MediaReconcileReliabilityTests(unittest.TestCase):
    def test_daily_media_self_heal_is_not_stale_after_ten_hours(self):
        result = reliability.heartbeat_health(_FakeDB([_heartbeat_row(10 * 3600)]))
        self.assertEqual(result["stale"], 0)
        worker = result["workers"][0]
        self.assertEqual(worker["state"], "degraded")

    def test_daily_media_self_heal_becomes_stale_after_26_hour_sla(self):
        result = reliability.heartbeat_health(_FakeDB([_heartbeat_row(27 * 3600)]))
        self.assertEqual(result["stale"], 1)
        self.assertEqual(result["workers"][0]["state"], "stale")
        self.assertEqual(result["state"], "critical")

    def test_media_reconcile_can_recover_its_own_stale_heartbeat(self):
        capacity = {
            "signals": [
                {"key": "stale_workers", "state": "warning"},
            ]
        }
        own_stale = {
            "workers": [
                {
                    "module": "media",
                    "worker_id": "published_campaign_self_heal",
                    "state": "stale",
                }
            ]
        }
        with (
            patch.object(reliability, "capacity_health", return_value=capacity),
            patch.object(reliability, "heartbeat_health", return_value=own_stale),
        ):
            result = reliability.admission_control(object(), "media_reconcile")
        self.assertTrue(result["allow"])
        self.assertEqual(result["reason"], "capacity_available")

    def test_other_stale_worker_still_blocks_media_reconcile(self):
        capacity = {
            "signals": [
                {"key": "stale_workers", "state": "warning"},
            ]
        }
        other_stale = {
            "workers": [
                {
                    "module": "messenger",
                    "worker_id": "poller",
                    "state": "stale",
                }
            ]
        }
        with (
            patch.object(reliability, "capacity_health", return_value=capacity),
            patch.object(reliability, "heartbeat_health", return_value=other_stale),
        ):
            result = reliability.admission_control(object(), "media_reconcile")
        self.assertFalse(result["allow"])
        self.assertIn("stale_workers", result["reason"])

    def test_real_capacity_pressure_still_blocks_even_with_own_stale(self):
        capacity = {
            "signals": [
                {"key": "stale_workers", "state": "warning"},
                {"key": "memory_used", "state": "warning"},
            ]
        }
        own_stale = {
            "workers": [
                {
                    "module": "media",
                    "worker_id": "published_campaign_self_heal",
                    "state": "stale",
                }
            ]
        }
        with (
            patch.object(reliability, "capacity_health", return_value=capacity),
            patch.object(reliability, "heartbeat_health", return_value=own_stale),
        ):
            result = reliability.admission_control(object(), "media_reconcile")
        self.assertFalse(result["allow"])
        self.assertIn("memory_used", result["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
