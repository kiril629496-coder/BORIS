import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class NewFeedBootstrapScopeTests(unittest.TestCase):
    def test_first_activation_bootstrap_uses_current_canonical_feed_identity(self):
        src=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
        start=src.index('def bootstrap_new_no_promo')
        end=src.index('# NEW_FEED_HOURLY_LOW_VIEWS_RAMP_V3', start)
        block=src[start:end]
        self.assertIn('CANONICAL_FEED_BID_SCOPE_V1', block)
        self.assertIn('_load_feed_items(account_id)', block)
        self.assertNotIn('CampaignItem.source == "feed_factory"', block)
        self.assertIn('CampaignItem.status == "published"', block)
        self.assertIn('CampaignItem.avito_item_id.isnot(None)', block)
        self.assertIn('published_identity_bound', block)
        self.assertIn('FIRST_ACTIVATION_ONLY_V1', block)

    def test_hourly_ramp_uses_same_canonical_feed_scope(self):
        src=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
        start=src.index('def hourly_new_feed_low_views_ramp')
        block=src[start:start+9000]
        self.assertIn('_load_feed_items(account_id)', block)
        self.assertNotIn('CampaignItem.source == "feed_factory"', block)
        self.assertIn('CampaignItem.status == "published"', block)
        self.assertIn('published_identity_bound', block)

if __name__ == '__main__':
    unittest.main()
