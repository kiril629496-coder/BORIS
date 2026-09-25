import inspect, unittest
from app.api import campaigns
class IntentLifetimeRetryCap(unittest.TestCase):
 def test_retry_cap_is_total_per_deterministic_intent_not_rolling(self):
  src=inspect.getsource(campaigns._ff_guarded_openai_response)
  self.assertIn('if retry_seq>=3:',src)
  self.assertIn('CAMPAIGN_AI_RELEASED_RETRY_CAP',src)
  self.assertIn('"retry_total":retry_seq',src)
  self.assertNotIn('if retry_recent>=3:',src)
if __name__=='__main__': unittest.main()
