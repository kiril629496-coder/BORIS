import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
import posting_runner as pr

class SocialTransportSubscriptionGapTest(unittest.TestCase):
    def test_cached_health_propagates_subscription_gaps_without_making_transport_red(self):
        original=pr.SessionLocal
        hb=SimpleNamespace(
            state="ok",
            last_seen_at=datetime.now(timezone.utc),
            details_json={
                "ok":True,
                "projects":[],
                "blocked":[],
                "blocked_count":0,
                "transport_errors":0,
                "subscription_gap_count":1,
                "subscription_gaps":[{
                    "account_id":"mbroker_27859",
                    "reason":"active_subscription_without_posting_project",
                }],
                "owner_action_required":False,
                "checked_at":datetime.now(timezone.utc).isoformat(),
            },
        )
        class Q:
            def filter(self,*a,**k): return self
            def order_by(self,*a,**k): return self
            def first(self): return hb
        class DB:
            def query(self,*a,**k): return Q()
            def close(self): pass
        try:
            pr.SessionLocal=lambda: DB()
            out=pr.social_transport_readiness_cached_health()
        finally:
            pr.SessionLocal=original
        self.assertTrue(out["ok"])
        self.assertEqual(out["subscription_gap_count"],1)
        self.assertEqual(out["subscription_gaps"][0]["account_id"],"mbroker_27859")
        self.assertFalse(out["owner_action_required"])

if __name__=="__main__":
    unittest.main()
