import unittest
from datetime import datetime, timezone

from app.api.messenger import _manager_paid_period_state


class ManagerPaidPeriodStateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 6, 19, 50, 0, tzinfo=timezone.utc)

    def test_legacy_naive_utc_date_is_supported(self):
        alive, days, invalid = _manager_paid_period_state("2026-09-11T08:18:32.143763", self.now)
        self.assertTrue(alive)
        self.assertEqual(days, 5)
        self.assertFalse(invalid)

    def test_timezone_aware_date_is_supported(self):
        alive, days, invalid = _manager_paid_period_state("2026-09-20T16:24:47.520046+00:00", self.now)
        self.assertTrue(alive)
        self.assertEqual(days, 14)
        self.assertFalse(invalid)

    def test_z_suffix_is_supported(self):
        alive, days, invalid = _manager_paid_period_state("2026-09-07T19:50:00Z", self.now)
        self.assertTrue(alive)
        self.assertEqual(days, 1)
        self.assertFalse(invalid)

    def test_expired_date_is_inactive(self):
        alive, days, invalid = _manager_paid_period_state("2026-09-06T19:49:59+00:00", self.now)
        self.assertFalse(alive)
        self.assertEqual(days, 0)
        self.assertFalse(invalid)

    def test_malformed_date_fails_closed(self):
        alive, days, invalid = _manager_paid_period_state("not-a-date", self.now)
        self.assertFalse(alive)
        self.assertEqual(days, 0)
        self.assertTrue(invalid)

    def test_missing_period_preserves_legacy_no_period_semantics(self):
        alive, days, invalid = _manager_paid_period_state(None, self.now)
        self.assertTrue(alive)
        self.assertEqual(days, 0)
        self.assertFalse(invalid)


if __name__ == "__main__":
    unittest.main()
