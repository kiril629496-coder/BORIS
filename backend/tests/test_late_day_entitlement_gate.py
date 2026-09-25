import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import late_day_spend_controller as late

class LateDayEntitlementGateTests(unittest.TestCase):
    def test_unknown_entitlement_blocks_before_any_money_request(self):
        db=MagicMock()
        cfg={"account_id":"qa_unknown","target_leads_per_day":3}
        now=datetime(2026,9,5,20,0,tzinfo=ZoneInfo("Europe/Moscow"))
        with patch.object(late,"get_goal_auto_accounts",return_value=[cfg]),              patch.object(late,"SessionLocal",return_value=db),              patch.object(late,"marketing_service_entitlement",return_value={
                 "state":"unknown","source":"no_explicit_current_paid_period","until":None
             }),              patch.object(late,"_load") as load,              patch.object(late,"_post") as post:
            out=late.boost_evening(now,account_filter="qa_unknown")
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["status"],"blocked_entitlement")
        self.assertEqual(out[0]["reason"],"no_active_boris_marketer_entitlement")
        self.assertFalse(out[0]["changed_avito"])
        self.assertEqual(out[0]["applied"],0)
        load.assert_not_called()
        post.assert_not_called()
        db.close.assert_called_once()

if __name__=="__main__":
    unittest.main()
