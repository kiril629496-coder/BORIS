import unittest, inspect
import posting_runner as pr
class AllOwnerReadiness(unittest.TestCase):
 def test_expected_owner_projects_are_not_name_whitelisted(self):
  s=inspect.getsource(pr.social_owner_pre_slot_readiness_health)
  self.assertIn('project.get("expected_auto_generation") is not True',s)
  self.assertNotIn('pname not in owner_names',s)
 def test_provider_cooldown_is_deferred_external(self):
  s=inspect.getsource(pr.autopost_projects)
  self.assertIn('PROJECT_DEFERRED_EXTERNAL',s)
  self.assertIn('CAMPAIGN_AI_PROVIDER_COOLDOWN:',s)
  self.assertIn('deferred_external',s)
if __name__=='__main__': unittest.main()
