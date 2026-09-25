import inspect
import unittest

from app.services import jobs
from app.services import control_plane_adapters as adapters


class CampaignDeliverSafetyContractTests(unittest.TestCase):
    def test_superseded_campaign_is_fail_closed_before_external_delivery(self):
        src = inspect.getsource(jobs._run_campaign_deliver)
        self.assertIn("CAMPAIGN_DELIVER_SUPERSEDED_FAILCLOSED_V1", src)
        self.assertIn("publication_superseded_noop", src)
        self.assertIn('"external_action":False', src)
        self.assertIn('"completed"', src)

    def test_expected_empty_scope_waits_bounded_materialization(self):
        src = inspect.getsource(jobs._run_campaign_deliver)
        self.assertIn("CAMPAIGN_DELIVER_MATERIALIZE_BARRIER_V1", src)
        self.assertIn("_materialize_wait_count", src)
        self.assertIn("_expected > 0 and _wait < 12", src)
        self.assertIn("awaiting_materialization", src)
        self.assertIn("retry_after_seconds", src)
        self.assertIn("campaign items not materialized after bounded wait", src)

    def test_supervisor_requires_explicit_superseded_replacement_receipt(self):
        src = inspect.getsource(adapters._feed_factory_state)
        self.assertIn("FEED_FAILED_SUPERSEDED_RESOLUTION_PROOF_V1", src)
        self.assertIn("recovered_by_superseded_replacement", src)
        self.assertIn("replacement_campaign_id", src)
        self.assertIn("publication_receipt", src)
        self.assertIn("avito_autoload_item_report", src)
        self.assertIn("success_added", src)
        self.assertIn("replacement_avito_item_id", src)
        self.assertIn("source_ff=dict(source_settings.get('feed_factory') or {})", src)
        self.assertIn("source_settings.get('resolution') or source_ff.get('resolution')", src)


if __name__ == "__main__":
    unittest.main()
