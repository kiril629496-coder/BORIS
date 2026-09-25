import unittest
from unittest.mock import patch
import posting_runner as pr

class SocialProviderRetryCapDeferralTest(unittest.TestCase):
    def test_retry_cap_is_deferred_not_project_error(self):
        projects=[{"id":"p1","name":"QA"}]
        class Row: value='[{"id":"p1","name":"QA"}]'
        class Q:
            def filter(self,*a,**k): return self
            def first(self): return Row()
        class DB:
            def query(self,*a,**k): return Q()
            def close(self): pass
        with patch.object(pr,'SessionLocal',return_value=DB()), patch.object(pr,'_autopost_projects_impl',side_effect=RuntimeError('CAMPAIGN_AI_RELEASED_RETRY_CAP:3')):
            out=pr.autopost_projects('qa')
        self.assertTrue(out['ok'])
        self.assertEqual(len(out['deferred_external']),1)
        self.assertEqual(out['deferred_external'][0]['dependency_state'],'waiting_external')
        self.assertFalse(out['deferred_external'][0]['paid_retry_performed'])
if __name__=='__main__': unittest.main()
