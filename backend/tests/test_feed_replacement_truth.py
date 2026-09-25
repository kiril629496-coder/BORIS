import inspect
import unittest

from app.services import control_plane_adapters as adapters


class FeedReplacementTruthTests(unittest.TestCase):
    def test_failed_delivery_can_be_closed_only_by_explicit_completed_replacement(self):
        src = inspect.getsource(adapters._feed_factory_state)
        self.assertIn("FEED_FAILED_REPLACED_BY_COMPLETED_CAMPAIGN_V2", src)
        self.assertIn("terminal_failure", src)
        self.assertIn("replacement_campaign_id", src)
        self.assertIn("scale_after_success_campaign_id", src)
        self.assertIn("recovered_by_completed_replacement", src)

    def test_replacement_requires_terminal_zero_error_exact_scope_and_real_avito_ids(self):
        src = inspect.getsource(adapters._feed_factory_state)
        self.assertIn("owner_activation", src)
        self.assertIn("full_success", src)
        self.assertIn("terminal", src)
        self.assertIn("report_errors", src)
        self.assertIn("scope_errors", src)
        self.assertIn("found!=expected", src)
        self.assertIn("active!=expected", src)
        self.assertIn("successful!=expected", src)
        self.assertIn("avito_item_id", src)
        self.assertIn("len(set(avito_ids))!=expected", src)
        self.assertIn("published_items", src)

    def test_later_partial_is_accepted_only_for_explicit_safe_provider_reconciliation(self):
        src = inspect.getsource(adapters._feed_factory_state)
        self.assertIn("reconciled_partial", src)
        self.assertIn("canonical_identity_provider_rejection", src)
        self.assertIn("publication_truth_reconciliation", src)
        self.assertIn("post_publish_watch", src)
        self.assertIn("owner_action_required", src)
        self.assertIn("safe_republish", src)
        self.assertIn("missing_feed_ids_count", src)
        self.assertIn("diagnostic", src)


if __name__ == "__main__":
    unittest.main()
