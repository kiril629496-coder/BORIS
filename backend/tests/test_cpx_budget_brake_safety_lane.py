import inspect
import unittest
from unittest.mock import MagicMock, patch
import cpx_budget_brake as brake

class CpxBudgetBrakeSafetyLaneTests(unittest.TestCase):
    def test_budget_guard_above_limit_preserves_live_promotion(self):
        db=MagicMock()
        with patch.object(brake,"effective_daily_budget_limit",return_value=100.0), \
             patch.object(brake,"adaptive_budget_brake_policy",return_value={"cutoff_ratio":0.90,"cutoff_pct":90.0,"reserve_rub":10.0}), \
             patch.object(brake,"_latest_spending",return_value={"status":"ok","spent_today_rub":120.0,"age_seconds":10,"spending_date":"fixture"}), \
             patch.object(brake,"_shared_throttle_guard") as throttle, \
             patch.object(brake,"_token") as token, \
             patch.object(brake,"_inventory") as inventory, \
             patch.object(brake,"_bulk_promotions") as bulk, \
             patch.object(brake,"_remove") as remove, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_brake",True)
        self.assertEqual(out["status"],"growth_frozen_existing_preserved")
        self.assertEqual(out["policy_version"],"KPI_CONTINUITY_BUDGET_GUARD_V1")
        self.assertTrue(out["existing_promotions_preserved"])
        self.assertFalse(out["can_increase_spend"])
        self.assertTrue(out["budget_exceeded"])
        self.assertEqual(out["removed"],0)
        throttle.assert_not_called(); token.assert_not_called(); inventory.assert_not_called(); bulk.assert_not_called(); remove.assert_not_called()

    def test_explicit_zero_budget_preserves_existing_promotion(self):
        db=MagicMock()
        with patch.object(brake,"effective_daily_budget_limit",return_value=0.0), \
             patch.object(brake,"_configured_daily_budget_state",return_value={"present":True,"value":0.0}), \
             patch.object(brake,"_latest_spending",return_value={"status":"ok","spent_today_rub":199.0,"age_seconds":10,"spending_date":"fixture"}), \
             patch.object(brake,"_shared_throttle_guard"), \
             patch.object(brake,"_token",return_value="qa-token"), \
             patch.object(brake,"_inventory",return_value=([101],{"source":"qa","complete":True})), \
             patch.object(brake,"_bulk_promotions",return_value=[{"itemID":101,"manualPromotion":{"bidPenny":9900},"autoPromotion":{}}]), \
             patch.object(brake,"_remove") as remove, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_zero_budget",True)
        self.assertEqual(out["status"],"zero_budget_growth_blocked_existing_preserved")
        self.assertEqual(out["paid_promotions_before"],1)
        self.assertEqual(out["paid_promotions_after"],1)
        self.assertEqual(out["removed"],0)
        self.assertFalse(out["can_increase_spend"])
        remove.assert_not_called()

    def test_budget_guard_starts_growth_freeze_at_ninety_percent_reserve(self):
        db=MagicMock()
        with patch.object(brake,"effective_daily_budget_limit",return_value=100.0), \
             patch.object(brake,"adaptive_budget_brake_policy",return_value={"cutoff_ratio":0.90,"cutoff_pct":90.0,"reserve_rub":10.0}), \
             patch.object(brake,"_latest_spending",return_value={"status":"ok","spent_today_rub":90.0,"age_seconds":10,"spending_date":"fixture"}), \
             patch.object(brake,"_shared_throttle_guard") as throttle, \
             patch.object(brake,"_remove") as remove, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_brake",True)
        self.assertEqual(out["status"],"growth_frozen_existing_preserved")
        self.assertTrue(out["early_cutoff"])
        self.assertFalse(out["budget_exceeded"])
        self.assertEqual(out["brake_start_pct"],90.0)
        self.assertEqual(out["removed"],0)
        throttle.assert_not_called(); remove.assert_not_called()

    def test_budget_brake_does_not_touch_promotion_below_reserve(self):
        db=MagicMock()
        with patch.object(brake,"effective_daily_budget_limit",return_value=100.0), patch.object(brake,"adaptive_budget_brake_policy",return_value={"cutoff_ratio":0.90,"cutoff_pct":90.0,"reserve_rub":10.0}), \
             patch.object(brake,"_latest_spending",return_value={
                 "status":"ok","spent_today_rub":89.9,"age_seconds":10,"spending_date":"fixture"
             }), \
             patch.object(brake,"_shared_throttle_guard") as throttle, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_brake",True)
        self.assertEqual(out["status"],"within_budget")
        throttle.assert_not_called()

    def test_stale_same_day_cutoff_freezes_growth_without_provider_write(self):
        from datetime import datetime, timezone
        db=MagicMock(); today=datetime.now(timezone.utc).date().isoformat()
        with patch.object(brake,"effective_daily_budget_limit",return_value=100.0), \
             patch.object(brake,"adaptive_budget_brake_policy",return_value={"cutoff_ratio":0.90,"cutoff_pct":90.0,"reserve_rub":10.0}), \
             patch.object(brake,"_latest_spending",return_value={"status":"stale","spent_today_rub":95.0,"age_seconds":901,"spending_date":today}), \
             patch.object(brake,"_shared_throttle_guard") as throttle, \
             patch.object(brake,"_remove") as remove, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_brake",True)
        self.assertEqual(out["status"],"growth_frozen_existing_preserved")
        self.assertTrue(out["stale_monotonic_overlimit_proof"])
        self.assertTrue(out["early_cutoff"])
        throttle.assert_not_called(); remove.assert_not_called()

    def test_above_cutoff_needs_no_provider_io_to_freeze_growth(self):
        db=MagicMock()
        with patch.object(brake,"effective_daily_budget_limit",return_value=100.0), \
             patch.object(brake,"adaptive_budget_brake_policy",return_value={"cutoff_ratio":0.90,"cutoff_pct":90.0,"reserve_rub":10.0}), \
             patch.object(brake,"_latest_spending",return_value={"status":"ok","spent_today_rub":95.0,"age_seconds":10,"spending_date":"fixture"}), \
             patch.object(brake,"_inventory",side_effect=AssertionError("provider inventory must not be touched")) as inventory, \
             patch.object(brake,"_remove",side_effect=AssertionError("promotion must not be removed")) as remove, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_brake",True)
        self.assertEqual(out["status"],"growth_frozen_existing_preserved")
        self.assertFalse(out["can_increase_spend"])
        inventory.assert_not_called(); remove.assert_not_called()

    def test_stale_below_cutoff_stays_fail_closed_without_provider_write(self):
        from datetime import datetime, timezone
        db=MagicMock()
        today=datetime.now(timezone.utc).date().isoformat()
        with patch.object(brake,"effective_daily_budget_limit",return_value=100.0), patch.object(brake,"adaptive_budget_brake_policy",return_value={"cutoff_ratio":0.90,"cutoff_pct":90.0,"reserve_rub":10.0}), \
             patch.object(brake,"_latest_spending",return_value={
                 "status":"stale","spent_today_rub":89.9,"age_seconds":901,"spending_date":today
             }), \
             patch.object(brake,"_shared_throttle_guard") as throttle, \
             patch.object(brake,"_save_runtime"):
            out=brake.brake_account(db,"qa_brake",True)
        self.assertEqual(out["status"],"spend_stale")
        throttle.assert_not_called()

    def test_active_path_returns_before_legacy_remove_loop(self):
        src=inspect.getsource(brake.brake_account)
        marker=src.index('out["policy_version"] = "KPI_CONTINUITY_BUDGET_GUARD_V1"')
        active_return=src.index('return out', marker)
        legacy_remove=src.index('rr=_remove(', active_return)
        self.assertLess(active_return,legacy_remove)


if __name__=="__main__":
    unittest.main()
