import inspect, unittest
import posting_runner as pr

class SocialPregenerationCatchupWindow(unittest.TestCase):
    def test_window_is_minute_precise_and_keeps_due_slot_retryable(self):
        src=inspect.getsource(pr.pregenerate_upcoming_all)
        self.assertIn('slot_local=local_now.replace(hour=h,minute=0,second=0,microsecond=0)',src)
        self.assertIn('window_start=slot_local-timedelta(hours=int(lead_hours))',src)
        self.assertIn('due=[x for x in pending if x[0] <= local_now]',src)
        self.assertIn('if _posting_generation_exists(account_id,_candidate_key)',src)
        self.assertIn('newer_window_open=any(',src)
        self.assertIn('if slot_local <= local_now and newer_window_open:',src)
        self.assertNotIn('current_hour < h <= current_hour + int(lead_hours)',src)

    def test_pregeneration_remains_moderate_only(self):
        src=inspect.getsource(pr.pregenerate_upcoming_all)
        self.assertIn('(proj.get("mode") or "auto") != "moderate"',src)
        self.assertIn('pre_generate_only=True',src)

if __name__=='__main__': unittest.main()
