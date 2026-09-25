import unittest
from unittest.mock import MagicMock, patch
import cpx_cap_reconciler as cap

class CpxCapSafetyLowerLaneTests(unittest.TestCase):
    def test_contract_marks_lane_as_lower_only(self):
        db=MagicMock()
        client=MagicMock()
        client.__enter__.return_value=client
        client.__exit__.return_value=False
        initial=[
            {"itemID":101,"actionTypeID":5,"manualPromotion":{},"autoPromotion":{"enabled":True}},
            {"itemID":202,"actionTypeID":5,"manualPromotion":{"bidPenny":25000},"autoPromotion":{}},
        ]
        verified=[
            {"itemID":101,"actionTypeID":5,"manualPromotion":{},"autoPromotion":{}},
            {"itemID":202,"actionTypeID":5,"manualPromotion":{"bidPenny":15000},"autoPromotion":{}},
        ]
        ok=MagicMock(status_code=200,text="")
        with patch.object(cap,"account_throttle_remaining",return_value=0),              patch.object(cap,"_token",return_value="qa-token"),              patch.object(cap.httpx,"Client",return_value=client),              patch.object(cap,"_active_item_ids",return_value=[101,202]),              patch.object(cap,"_bulk_promotions",side_effect=[initial,verified]),              patch.object(cap,"_owner_manual_bid_overrides",return_value={}),              patch.object(cap,"_get_bid_detail",return_value={"manual":{"minBidPenny":10000}}),              patch.object(cap,"_remove",return_value=ok) as remove,              patch.object(cap,"_set_manual",return_value=ok) as set_manual,              patch.object(cap,"_audit_log"),              patch.object(cap,"_save_runtime"):
            out=cap.reconcile_account(db,"qa_lower_only",150.0,True)
        self.assertEqual(out["safety_lane"],"lower_only")
        self.assertFalse(out["can_increase_spend"])
        self.assertEqual(out["status"],"pass")
        self.assertEqual(out["removed"],1)
        self.assertEqual(out["lowered"],1)
        remove.assert_called_once()
        set_manual.assert_called_once_with(client,{"Authorization":"Bearer qa-token"},202,5,150)

    def test_transport_timeout_is_deferred_not_fatal(self):
        db=MagicMock()
        client=MagicMock()
        client.__enter__.return_value=client
        client.__exit__.return_value=False
        with patch.object(cap,"account_throttle_remaining",return_value=0), \
             patch.object(cap,"_token",return_value="qa-token"), \
             patch.object(cap.httpx,"Client",return_value=client), \
             patch.object(cap,"_active_item_ids",side_effect=cap.httpx.ConnectTimeout("timed out")), \
             patch.object(cap,"_save_runtime"):
            out=cap.reconcile_account(db,"qa_transport",150.0,True)
        self.assertEqual(out["status"],"deferred")
        self.assertEqual(out["failed"],0)
        self.assertTrue(any("provider_deferred:ConnectTimeout" in str(x) for x in out["errors"]))
        self.assertFalse(out["can_increase_spend"])


if __name__=="__main__":
    unittest.main()
