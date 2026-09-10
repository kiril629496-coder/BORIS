# -*- coding: utf-8 -*-
import inspect
import unittest

from app.services import owner_outreach_policy as policy
from app.services import email_queue
from app.services import owner_outreach_volume_safety as volume
from app.services import prospect_campaigns as prospect


class OwnerOutreachPolicyRootTest(unittest.TestCase):
    def test_hard_ceiling_is_20(self):
        self.assertEqual(policy.OWNER_OUTREACH_HARD_MAX_DAILY, 20)

    def test_ramp_is_10_then_15_then_20(self):
        self.assertEqual(
            [policy.canonical_daily_cap(x) for x in range(8)],
            [10, 10, 10, 10, 10, 10, 15, 20],
        )
        self.assertEqual(policy.canonical_daily_cap(30), 20)
        self.assertEqual(policy.canonical_policy_state(5)["next_cap"], 15)
        self.assertEqual(policy.canonical_policy_state(6)["next_cap"], 20)
        self.assertIsNone(policy.canonical_policy_state(7)["next_cap"])

    def test_prospect_runtime_uses_external_policy(self):
        self.assertEqual(prospect.OWNER_OUTREACH_MAX_DAILY, 20)
        state_src = inspect.getsource(prospect._owner_daily_state)
        self.assertIn("canonical_policy_state(age)", state_src)
        self.assertIn("computed_cap=min(configured_cap,canonical_cap)", state_src)
        self.assertIn('policy_state["next_cap"]', state_src)

    def test_db_volume_guard_mirrors_same_10_15_20_policy(self):
        self.assertEqual(
            [volume.canonical_cap(x) for x in range(8)],
            [10, 10, 10, 10, 10, 10, 15, 20],
        )
        snapshot = volume._snapshot_guard_sql()
        queue = volume._queue_guard_sql()
        for src in (snapshot, queue):
            self.assertIn("age_days < 6", src)
            self.assertIn("THEN 10", src)
            self.assertIn("age_days < 7", src)
            self.assertIn("THEN 15", src)
            self.assertIn("ELSE 20", src)
        ensure_src = inspect.getsource(volume.ensure)
        self.assertIn("trg_owner_daily_limit_guard_v1", ensure_src)
        self.assertIn("trg_zz_owner_daily_limit_guard_v2", ensure_src)
        self.assertIn("DROP TRIGGER IF EXISTS", ensure_src)

    def test_pre_smtp_guard_has_independent_canonical_cap(self):
        src = inspect.getsource(prospect.owner_queue_pre_send_guard)
        self.assertIn("_backend_volume_cap", src)
        self.assertIn("OWNER_OUTREACH_HARD_MAX_DAILY", src)
        self.assertIn("successful + max(1,inflight_position) > effective_cap", src)
        self.assertIn("owner_daily_cap_reached_pre_smtp", src)

    def test_daily_snapshot_is_created_by_volume_preflight(self):
        ensure_src = inspect.getsource(volume.ensure)
        self.assertIn("_ensure_active_daily_snapshots", ensure_src)
        helper_src = inspect.getsource(volume._ensure_active_daily_snapshots)
        self.assertIn("c.status='active'", helper_src)
        self.assertIn("c.account_id='__owner_outreach__'", helper_src)
        self.assertIn("ON CONFLICT(owner_id,service_date) DO NOTHING", helper_src)
        self.assertIn("canonical_cap(age)", helper_src)
        self.assertIn("activity_today", helper_src)
        self.assertIn("cap=min(cap,OWNER_OUTREACH_STAGE1_DAILY)", helper_src)

    def test_email_worker_runs_volume_preflight_before_campaign_feeder(self):
        import email_queue_runner
        src = inspect.getsource(email_queue_runner.main)
        self.assertLess(
            src.index("owner_outreach_volume_safety.ensure()"),
            src.index("prospect_campaigns.tick_all"),
        )

    def test_email_queue_defers_exact_daily_cap_guard_without_smtp_or_attempt(self):
        claim_src = inspect.getsource(email_queue._claim)
        batch_src = inspect.getsource(email_queue.process_batch)
        defer_src = inspect.getsource(email_queue._defer_owner_daily_cap)
        self.assertIn("EMAIL_OWNER_DAILY_CAP_DEFER_V1", claim_src)
        self.assertIn("OwnerDailyVolumeDeferred", claim_src)
        self.assertIn("daily_cap_deferred", batch_src)
        self.assertIn("continue", batch_src)
        self.assertIn("interval '1 day 9 hours'", defer_src)
        self.assertIn("status='queued'", defer_src)
        self.assertIn("deferred_daily_cap", defer_src)

    def test_volume_health_exposes_snapshot_readiness_without_false_owner_alarm(self):
        src = inspect.getsource(volume.health)
        self.assertIn("missing_active_snapshots", src)
        self.assertIn("snapshot_ready", src)
        self.assertIn('"healthy": ok', src)

    def test_daily_cap_is_persisted_fail_lower_not_raise(self):
        src = inspect.getsource(prospect._lock_owner_daily_cap)
        self.assertIn("prospect_owner_daily_limits", src)
        self.assertIn("LEAST(prospect_owner_daily_limits.daily_cap,EXCLUDED.daily_cap)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
