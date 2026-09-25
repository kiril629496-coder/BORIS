import os
import tempfile
import unittest
from unittest.mock import patch

from app.services.category_resolver import _category_paid_json


class _Result:
    def scalar(self): return 0


class _DB:
    def execute(self, *args, **kwargs): return _Result()
    def close(self): pass


class CategoryPaidSafetyTests(unittest.TestCase):
    def test_ambiguous_transport_is_quarantined_and_not_rebought(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
            "OPENAI_API_KEY":"test-key", "BORIS_CATEGORY_PAID_CACHE_DIR":td,
        }):
            with patch("app.db.session.SessionLocal", return_value=_DB()), \
                 patch("app.services.ai_budget.reserve", return_value={"status":"observe"}), \
                 patch("app.services.ai_budget.allow_next", return_value={"allowed":True}), \
                 patch("app.services.ai_budget.release", return_value={"status":"released"}), \
                 patch("app.services.ai_guard_audit.log_guard_event", return_value=True), \
                 patch("requests.post", side_effect=ConnectionError("lost after send")) as provider:
                with self.assertRaisesRegex(RuntimeError, "CATEGORY_OUTCOME_UNCERTAIN"):
                    _category_paid_json("acc-category", "category_guess", "find category", 200, 30)
                with self.assertRaisesRegex(RuntimeError, "CATEGORY_OUTCOME_UNCERTAIN"):
                    _category_paid_json("acc-category", "category_guess", "find category", 200, 30)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(len([x for x in os.listdir(td) if x.endswith(".uncertain")]), 1)

    def test_source_has_pre_provider_barrier_and_human_guard_codes(self):
        import inspect
        from app.services import category_resolver as C
        src=inspect.getsource(C._category_paid_json)
        self.assertIn("provider_in_progress",src)
        self.assertIn("CATEGORY_DAILY_CAP",src)
        self.assertIn("CATEGORY_BURST_CAP",src)
        self.assertIn("Idempotency-Key",src)


if __name__ == "__main__":
    unittest.main()
