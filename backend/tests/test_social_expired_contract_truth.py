import inspect
import unittest
import posting_runner as pr

class SocialExpiredContractTruthTest(unittest.TestCase):
    def test_expired_service_is_not_scheduler_contract_blocker(self):
        src = inspect.getsource(pr.social_scheduler_contract_health)
        self.assertIn('and not _sub_active(proj.get("subscription"))', src)
        self.assertNotIn('"reason": "subscription_expired"', src)

    def test_runtime_still_rejects_explicit_expired_entitlement(self):
        self.assertFalse(pr._sub_active({
            "plan":"post_both", "paid_at":"2026-08-02", "period_days":30,
            "status":"expired_owner_confirmed",
        }))

if __name__ == '__main__': unittest.main()
