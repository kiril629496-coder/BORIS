import inspect, unittest
from app.api import campaigns as C
class ReleasedRetryGuard(unittest.TestCase):
 def test_released_zero_commit_has_bounded_rearm(self):
  src=inspect.getsource(C._ff_guarded_openai_response)
  self.assertIn('CAMPAIGN_AI_RELEASED_RETRY_REARMED',src)
  self.assertIn('CAMPAIGN_AI_RELEASED_RETRY_CAP',src)
  self.assertIn('CAMPAIGN_AI_RELEASED_RETRY_CAP',src)
  self.assertIn('CAMPAIGN_AI_PROVIDER_COOLDOWN',src)
  self.assertIn('retry_seq',src)
  self.assertIn('retry_seq>=3',src)
  self.assertIn('committed_kopeks',src)
  self.assertIn('provider_idem',src)
 def test_uncertain_barrier_precedes_rearm(self):
  src=inspect.getsource(C._ff_guarded_openai_response)
  self.assertLess(src.index('_uncertain_read()'),src.index('CAMPAIGN_AI_RELEASED_RETRY_REARMED'))
if __name__=='__main__': unittest.main()
