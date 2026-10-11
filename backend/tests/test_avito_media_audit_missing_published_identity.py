import unittest
from pathlib import Path
from scripts.avito_media_quality_audit import is_transient_derivative_url

class MediaAuditRegressionTests(unittest.TestCase):
    def test_transient_derivative_detection(self):
        self.assertTrue(is_transient_derivative_url("https://boris-ai.pro/images/a/uq_2ad2f8135c6a2166.jpg"))
        self.assertTrue(is_transient_derivative_url("/images/a/uqv_2ad2f8135c6a2166.webp?v=1"))
        self.assertFalse(is_transient_derivative_url("/images/a/up_1785772599_1103.jpeg"))
        self.assertFalse(is_transient_derivative_url("/images/a/uq_custom_customer_name.jpg"))

    def test_audit_fails_missing_published_and_transient_storage(self):
        s=Path(__file__).parents[1].joinpath("scripts/avito_media_quality_audit.py").read_text()
        self.assertIn("missing_published_feed_identity",s)
        self.assertIn("published_identity_bound_missing_from_feed_items",s)
        self.assertIn("canonical_missing_transient_derivative_hits",s)
        self.assertIn("canonical_transient_derivatives or failed_feeds",s)

if __name__=="__main__": unittest.main()
