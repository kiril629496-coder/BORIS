import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services.campaign_post_publish_watch import _update_watch, _diagnose_missing_provider_status


class CampaignPostPublishWatchTests(unittest.TestCase):
    def setUp(self):
        # These are historical state-machine fixtures. Freeze the canonical
        # marketing business day to the fixture date so current wall-clock time
        # cannot turn valid Sep-4 snapshots into `waiting_fresh_stats` years/days later.
        self._business_day = patch(
            "app.services.campaign_post_publish_watch.marketing_today_iso",
            return_value="2026-09-04",
        )
        self._business_day.start()
        self.addCleanup(self._business_day.stop)

    def test_publication_day_baselines_first_safe_snapshot(self):
        published = datetime(2026, 9, 4, 9, 54, tzinfo=timezone.utc)
        collected = datetime(2026, 9, 4, 10, 41, tzinfo=timezone.utc)
        now = datetime(2026, 9, 4, 10, 50, tzinfo=timezone.utc)
        watch = _update_watch(
            {"campaign_id": 144, "account_id": "a"},
            metrics={"matched": 36, "active": 36, "canonical": 36, "views": 27, "contacts": 0},
            collected_at=collected, published_at=published, expected=36, now=now,
        )
        self.assertEqual(watch["status"], "observing")
        self.assertEqual(watch["views_since_baseline"], 0)
        self.assertEqual(watch["contacts_since_baseline"], 0)
        self.assertEqual(watch["days"]["2026-09-04"]["baseline_views"], 27)
        self.assertFalse(watch["owner_action_required"])

    def test_same_day_delta_is_measured_without_prepublication_traffic(self):
        published = datetime(2026, 9, 4, 9, 54, tzinfo=timezone.utc)
        first = datetime(2026, 9, 4, 10, 41, tzinfo=timezone.utc)
        later = datetime(2026, 9, 4, 11, 10, tzinfo=timezone.utc)
        watch = _update_watch(
            {"campaign_id": 144, "account_id": "a"},
            metrics={"matched": 36, "active": 36, "canonical": 36, "views": 27, "contacts": 0},
            collected_at=first, published_at=published, expected=36,
            now=datetime(2026, 9, 4, 10, 50, tzinfo=timezone.utc),
        )
        watch = _update_watch(
            watch,
            metrics={"matched": 36, "active": 36, "canonical": 36, "views": 35, "contacts": 2},
            collected_at=later, published_at=published, expected=36,
            now=datetime(2026, 9, 4, 11, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(watch["views_since_baseline"], 8)
        self.assertEqual(watch["contacts_since_baseline"], 2)
        self.assertEqual(watch["status"], "observing")

    def test_scope_loss_is_detected_and_kept_ownerless_for_retry(self):
        published = datetime(2026, 9, 4, 9, 54, tzinfo=timezone.utc)
        collected = datetime(2026, 9, 4, 10, 41, tzinfo=timezone.utc)
        watch = _update_watch(
            {"campaign_id": 144, "account_id": "a"},
            metrics={"matched": 36, "active": 35, "canonical": 36, "views": 27, "contacts": 0},
            collected_at=collected, published_at=published, expected=36,
            now=datetime(2026, 9, 4, 10, 50, tzinfo=timezone.utc),
        )
        self.assertEqual(watch["status"], "degraded")
        self.assertEqual(watch["degraded_streak"], 1)
        self.assertEqual(watch["self_heal"], "automatic_recheck_no_owner_action")
        self.assertFalse(watch["owner_action_required"])

    def test_missing_identity_requires_confirmation_then_diagnosis_without_republish(self):
        published = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
        first_now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        watch = _update_watch(
            {"campaign_id": 225, "account_id": "a", "expected_feed_ids": ["a", "b"]},
            metrics={"matched": 1, "active": 1, "canonical": 1, "views": 0, "contacts": 0,
                     "items": {"a": {"status": "active"}}},
            collected_at=first_now, published_at=published, expected=2, now=first_now,
        )
        self.assertEqual(watch["diagnosis_stage"], "confirming_inventory_loss")
        self.assertEqual(watch["diagnostic"], "confirm_inventory_loss_next_stats_cycle")
        self.assertFalse(watch["safe_republish"])
        later = datetime(2026, 9, 4, 11, 5, tzinfo=timezone.utc)
        watch = _update_watch(
            watch, metrics={"matched": 1, "active": 1, "canonical": 1, "views": 0, "contacts": 0,
                            "items": {"a": {"status": "active"}}},
            collected_at=later, published_at=published, expected=2, now=later,
        )
        self.assertEqual(watch["diagnosis_stage"], "persistent_inventory_loss_needs_read_only_provider_diagnosis")
        self.assertEqual(watch["diagnostic"], "read_only_provider_diagnosis_due")
        self.assertEqual(watch["self_heal"], "diagnose_before_any_republish")
        self.assertFalse(watch["safe_republish"])

    def test_next_complete_day_starts_at_zero_and_accumulates(self):
        published = datetime(2026, 9, 4, 9, 54, tzinfo=timezone.utc)
        watch = _update_watch(
            {"campaign_id": 144, "account_id": "a"},
            metrics={"matched": 36, "active": 36, "canonical": 36, "views": 27, "contacts": 0},
            collected_at=datetime(2026, 9, 4, 10, 41, tzinfo=timezone.utc),
            published_at=published, expected=36,
            now=datetime(2026, 9, 4, 10, 50, tzinfo=timezone.utc),
        )
        watch = _update_watch(
            watch,
            metrics={"matched": 36, "active": 36, "canonical": 36, "views": 39, "contacts": 1},
            collected_at=datetime(2026, 9, 4, 23, 50, tzinfo=timezone.utc),
            published_at=published, expected=36,
            now=datetime(2026, 9, 4, 23, 55, tzinfo=timezone.utc),
        )
        watch = _update_watch(
            watch,
            metrics={"matched": 36, "active": 36, "canonical": 36, "views": 5, "contacts": 1},
            collected_at=datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc),
            published_at=published, expected=36,
            now=datetime(2026, 9, 5, 1, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(watch["days"]["2026-09-05"]["baseline_views"], 0)
        self.assertEqual(watch["views_since_baseline"], 17)
        self.assertEqual(watch["contacts_since_baseline"], 2)


class CampaignProviderDiagnosisTests(unittest.TestCase):
    def test_missing_without_provider_id_fails_closed(self):
        class R:
            feed_identity = "ci-x"
            avito_item_id = None
        out = _diagnose_missing_provider_status("a", [R()], ["ci-x"], now=datetime(2026,9,4,12,tzinfo=timezone.utc))
        self.assertEqual(out["status"], "unresolved")
        self.assertFalse(out["safe_republish"])
        self.assertFalse(out["owner_action_required"])

    def test_recent_same_scope_diagnosis_is_reused_without_provider_call(self):
        now = datetime(2026,9,4,12,10,tzinfo=timezone.utc)
        prev = {"status":"confirmed","checked_at":"2026-09-04T12:05:00+00:00","feed_ids":["ci-x"],
                "safe_republish":False,"classification":"provider_blocked"}
        class R:
            feed_identity = "ci-x"
            avito_item_id = "123"
        self.assertEqual(_diagnose_missing_provider_status("a", [R()], ["ci-x"], now=now, previous=prev), prev)


class CampaignPostPublishWorkerWireTests(unittest.TestCase):
    def test_background_jobs_is_single_owner_of_campaign_watch(self):
        with open("/root/BORIS/backend/boris_background_worker.py", encoding="utf-8") as fh:
            worker = fh.read()
        with open("/root/BORIS/backend/app/services/jobs.py", encoding="utf-8") as fh:
            jobs = fh.read()
        self.assertIn("POST_PUBLISH_WATCH_SINGLE_OWNER_V2", worker)
        # The old direct 15-minute thread may remain as emergency code, but it
        # must not be started alongside the durable queue owner.
        self.assertNotIn('("campaign_post_publish_watch", _campaign_post_publish_watch_loop)', worker)
        self.assertIn("POST_PUBLISH_WATCH_SCHEDULER_V1", jobs)
        self.assertIn('_watch_fingerprint = "system:campaign_post_publish_watch:v1"', jobs)
        self.assertIn('kind="campaign_post_publish_watch", status="queued"', jobs)
        self.assertIn('db.execute(_t("select pg_advisory_xact_lock', jobs)


if __name__ == "__main__":
    unittest.main()
