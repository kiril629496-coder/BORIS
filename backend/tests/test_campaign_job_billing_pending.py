import inspect
import unittest
from app.services import jobs


class CampaignJobBillingPendingTests(unittest.TestCase):
    def test_prepare_stops_after_materialize_billing_pending(self):
        src=inspect.getsource(jobs._run_campaign_prepare)
        materialize=src[src.find('ff_workflow_materialize'):src.find('campaign_preview')]
        self.assertIn('_mat_status == "billing_pending"', materialize)
        self.assertIn('"retry"', materialize)
        self.assertIn('_mat_status == "billing_limit"', materialize)

    def test_prepare_stops_after_banner_billing_pending(self):
        src=inspect.getsource(jobs._run_campaign_prepare)
        banner=src[src.find('ff_workflow_banners'):src.find('internal_validation')]
        self.assertIn('banner_result.get("status")=="billing_pending"', banner)
        self.assertIn('pending_charge', banner)
        self.assertIn('"retry"', banner)

    def test_dedicated_banner_job_keeps_item_pending_until_billing_settles(self):
        src=inspect.getsource(jobs._run_campaign_banners)
        self.assertIn('result.get("status")=="billing_pending"', src)
        self.assertIn('_campaign_checkpoint(db,job,payload,"billing_pending"', src)
        self.assertIn('result.get("status")=="billing_limit"', src)


if __name__=='__main__':
    unittest.main()
