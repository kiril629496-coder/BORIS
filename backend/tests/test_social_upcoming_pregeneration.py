import inspect
import unittest
import posting_runner as pr

class SocialUpcomingPregenerationContract(unittest.TestCase):
    def test_future_override_is_only_allowed_for_pregeneration_of_moderate_projects(self):
        src=inspect.getsource(pr._autopost_projects_impl)
        self.assertIn('pre_generate_only',src)
        self.assertIn('(proj.get("mode") or "auto") != "moderate"',src)
        self.assertIn('SOCIAL_SLOT_OVERRIDE_IN_FUTURE',src)

    def test_fleet_pregeneration_is_entitlement_and_idempotency_guarded(self):
        src=inspect.getsource(pr.pregenerate_upcoming_all)
        self.assertIn('(proj.get("mode") or "auto") != "moderate"',src)
        self.assertIn('_sub_active(sub)',src)
        self.assertIn('_posting_generation_exists',src)
        self.assertIn('pre_generate_only=True',src)

    def test_background_worker_calls_pregeneration_before_normal_generation(self):
        import boris_background_worker as bw
        src=inspect.getsource(bw._social_posting_loop)
        self.assertLess(src.index('pregenerate_upcoming_all(lead_hours=2)'), src.index('autopost_all(respect_time=True)'))

if __name__=='__main__': unittest.main()
