import inspect
import unittest

from app.api import parser as P


class ParsedDraftPaidSafetyTest(unittest.TestCase):
    def test_parsed_drafts_have_deterministic_identity(self):
        src = inspect.getsource(P.parsed_products_to_drafts)
        self.assertIn("_draft_fp", src)
        self.assertIn('f"parsed_{req.account_id}_{idx}_{_draft_fp}"', src)
        self.assertNotIn('f"parsed_{req.account_id}_{idx}_{int(_time.time())}"', src)

    def test_parsed_drafts_charge_child_intents_before_atomic_append(self):
        src = inspect.getsource(P.parsed_products_to_drafts)
        self.assertIn("PARSED_DRAFT_TARIFF_EXACTLY_ONCE_V1", src)
        self.assertIn('f"parsed-draft:{req.account_id}:{_did}"', src)
        self.assertIn("idempotency_key=_billing_key", src)
        self.assertIn("PARSED_DRAFT_APPEND_ATOMIC_V1", src)
        self.assertLess(src.index("_billing_consume"), src.index("_append_drafts(req.account_id, charged_drafts)"))


if __name__ == "__main__":
    unittest.main()
