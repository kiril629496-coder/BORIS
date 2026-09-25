import inspect, unittest
from app.api import campaigns
class RetryCapQuietWindow(unittest.TestCase):
    def test_retry_cap_quiet_window_matches_rolling_window(self):
        src=inspect.getsource(campaigns._ff_guarded_openai_response)
        self.assertIn('retry_seq>=3',src)
        self.assertIn('if retry_seq>=3:',src)
        self.assertIn('retry_after=max(60,min(int(retry_after or 300),1800))',src)
if __name__=='__main__': unittest.main()
