import inspect, unittest
from pathlib import Path
class MediaAuditMissingPublishedIdentityTests(unittest.TestCase):
    def test_audit_fails_missing_published_bound_identity(self):
        s=Path('scripts/avito_media_quality_audit.py').read_text()
        self.assertIn('missing_published_feed_identity',s)
        self.assertIn('published_identity_bound_missing_from_feed_items',s)
        block=s[s.index('if not feed_item:'):s.index('try:',s.index('if not feed_item:'))]
        self.assertIn('item.status',block)
        self.assertIn('campaign_feed_drift.append',block)
        self.assertIn('continue',block)
if __name__=='__main__': unittest.main()
