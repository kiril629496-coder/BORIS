import json
import unittest
from unittest.mock import MagicMock
from app.services.marketing_money_policy import adaptive_budget_brake_policy

class AdaptiveBudgetBrakePolicyTests(unittest.TestCase):
    def _db(self, completed):
        db=MagicMock()
        rows=[]
        for i,v in enumerate(completed,1):
            rows.append((f"daily_spending:2026-09-0{i}", json.dumps({"status":"ok","date":f"2026-09-0{i}","all_spend_rub":v})))
        db.execute.return_value.fetchall.return_value=rows
        return db

    def test_minimum_reserve_is_ten_percent_on_slow_account(self):
        # The minimum-reserve contract must be deterministic. Passing non-zero
        # same-day spend makes the intentionally adaptive four-hour burn reserve
        # depend on the current Moscow clock and can legitimately exceed 10%.
        p=adaptive_budget_brake_policy(self._db([300,400,500]),"slow",4000,0)
        self.assertEqual(p["cutoff_pct"],90.0)
        self.assertEqual(p["reserve_rub"],400.0)

    def test_historical_burn_expands_reserve(self):
        p=adaptive_budget_brake_policy(self._db([5494.4,2120.5,3382.4]),"fast",3000,0)
        self.assertLessEqual(p["cutoff_pct"],70.0)
        self.assertGreaterEqual(p["reserve_rub"],900.0)

    def test_reserve_never_exceeds_half_budget(self):
        p=adaptive_budget_brake_policy(self._db([50000]),"burst",3000,0)
        self.assertEqual(p["cutoff_pct"],50.0)
        self.assertEqual(p["reserve_rub"],1500.0)

    def test_no_limit_is_fail_closed_shape(self):
        p=adaptive_budget_brake_policy(self._db([]),"none",0,0)
        self.assertEqual(p["cutoff_ratio"],0.0)
        self.assertEqual(p["reserve_ratio"],1.0)

if __name__=='__main__': unittest.main()
