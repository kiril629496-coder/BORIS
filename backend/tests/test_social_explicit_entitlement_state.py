import unittest
import posting_runner as pr
class SocialExplicitEntitlementStateTest(unittest.TestCase):
    def test_explicit_expired_state_overrides_future_paid_date(self):
        self.assertFalse(pr._sub_active({"paid_at":"2026-09-05","period_days":30,"status":"expired_owner_confirmed"}))
    def test_normal_active_subscription_remains_active(self):
        self.assertTrue(pr._sub_active({"paid_at":"2026-09-05","period_days":30}))
if __name__=='__main__': unittest.main()
