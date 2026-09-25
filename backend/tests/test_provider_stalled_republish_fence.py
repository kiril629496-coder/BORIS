from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProviderStalledRepublishFenceTest(unittest.TestCase):
    def test_publish_provider_stalled_is_terminal_for_generic_republish(self):
        src = (ROOT / 'app/api/avito.py').read_text(encoding='utf-8')
        self.assertIn('PUBLISH_PROVIDER_STALLED_REPUBLISH_FENCE_V2', src)
        start = src.index('def kpi_publish_changes(')
        end = src.index('\ndef ', start + 10)
        fn = src[start:end]
        self.assertIn('op.get("publish_provider_stalled_at")', fn)
        self.assertIn('_publish_retry_count >= 3', fn)
        self.assertIn('"stale_live_mismatch_autoretry"', fn)
        self.assertIn('"avito_publish_not_applying_after_bounded_retries"', fn)
        self.assertIn('"status": "provider_stalled"', fn)
        self.assertIn('"stop_retry_and_rotate_hypothesis"', fn)
        # The fence must run before the generic feed_applied/publish_failed lane
        # can issue another external provider call.
        self.assertLess(
            fn.index('PUBLISH_PROVIDER_STALLED_REPUBLISH_FENCE_V2'),
            fn.index('if status not in (\n            "feed_applied"'),
        )


if __name__ == '__main__':
    unittest.main()
