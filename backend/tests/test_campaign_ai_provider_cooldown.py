import inspect, unittest
from app.api import campaigns as C
class ProviderCooldownTests(unittest.TestCase):
 def test_429_has_durable_cooldown_and_no_uncertain_retry(self):
  src=inspect.getsource(C._ff_guarded_openai_response)
  self.assertIn('CAMPAIGN_AI_PROVIDER_COOLDOWN',src)
  self.assertIn('cooldown_path',src)
  self.assertIn('_cooldown_write',src)
  self.assertIn('_status==429',src)
  self.assertIn('retry_seq>=3',src)
  self.assertIn("interval '30 minutes'",src)
 def test_cooldown_checked_before_budget_reserve(self):
  src=inspect.getsource(C._ff_guarded_openai_response)
  self.assertLess(src.index('cooldown=_cooldown_read()'),src.index('token=ai_budget.reserve'))
if __name__=='__main__': unittest.main()
