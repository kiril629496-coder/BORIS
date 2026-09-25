import unittest, inspect
import posting_runner as pr
class HashtagHealthContract(unittest.TestCase):
 def test_quality_health_respects_allow_hashtags(self):
  src=inspect.getsource(pr.social_scheduler_contract_health)
  self.assertIn('not bool(proj.get("allow_hashtags"))',src)
  self.assertIn('bad_hash',src)
if __name__=='__main__': unittest.main()
