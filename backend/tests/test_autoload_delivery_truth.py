import unittest
from app.api.avito import _autoload_scope_delivery_effect


class AutoloadDeliveryTruthTests(unittest.TestCase):
    def test_all_success_skipped_is_unchanged_not_new_publication(self):
        rows=[{"avito_status":"active","section":{"slug":"success_skipped"}} for _ in range(36)]
        out=_autoload_scope_delivery_effect(rows,expected_total=36,full_success=True,partial=False)
        self.assertEqual(out["state"],"unchanged")
        self.assertEqual(out["unchanged_items"],36)
        self.assertEqual(out["changed_or_created_items"],0)
        self.assertFalse(out["proves_current_upload_changed_content"])

    def test_non_skipped_active_rows_are_change_evidence_for_current_upload(self):
        rows=[{"avito_status":"active","section":{"slug":"success"}} for _ in range(5)]
        out=_autoload_scope_delivery_effect(rows,expected_total=5,full_success=True,partial=False)
        self.assertEqual(out["state"],"changed_or_created")
        self.assertEqual(out["changed_or_created_items"],5)
        self.assertTrue(out["proves_current_upload_changed_content"])

    def test_mixed_success_is_explicit(self):
        rows=[
            {"avito_status":"active","section":{"slug":"success_skipped"}},
            {"avito_status":"active","section":{"slug":"successful"}},
        ]
        out=_autoload_scope_delivery_effect(rows,expected_total=2,full_success=True,partial=False)
        self.assertEqual(out["state"],"changed_or_created_mixed")
        self.assertEqual(out["unchanged_items"],1)
        self.assertEqual(out["changed_or_created_items"],1)

    def test_partial_never_claims_current_upload_change_proof(self):
        rows=[
            {"avito_status":"active","section":{"slug":"successful"}},
            {"avito_status":"rejected","section":{"slug":"error_rejected"}},
        ]
        out=_autoload_scope_delivery_effect(rows,expected_total=2,full_success=False,partial=True)
        self.assertEqual(out["state"],"partial")
        self.assertFalse(out["proves_current_upload_changed_content"])


if __name__=="__main__":
    unittest.main()
