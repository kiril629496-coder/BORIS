import unittest
from pathlib import Path
class PostPublishSingleOwner(unittest.TestCase):
 def test_direct_loop_not_registered(self):
  s=Path('boris_background_worker.py').read_text()
  main=s[s.index('def main'):]
  self.assertNotIn('(\"campaign_post_publish_watch\", _campaign_post_publish_watch_loop)',main)
  jobs=Path('app/services/jobs.py').read_text()
  self.assertIn('system:campaign_post_publish_watch:v1',jobs)
  self.assertIn('kind="campaign_post_publish_watch"',jobs)
if __name__=='__main__': unittest.main()
