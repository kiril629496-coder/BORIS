import inspect
import unittest

from app.api import tasks as T


class PipelineDraftPaidSafetyTest(unittest.TestCase):
    def test_pipeline_text_provider_retry_has_stable_idempotency(self):
        src = inspect.getsource(T._run_pipeline_texts)
        self.assertIn("PIPELINE_TEXT_ECONOMIC_INTENT_V1", src)
        self.assertIn('pipeline-text:{_pipeline_identity}:{_chunk_start}:{this_chunk}', src)
        self.assertIn('"idempotency_key": _text_intent', src)

    def test_pipeline_drafts_are_deterministic_and_atomic(self):
        src = inspect.getsource(T._run_pipeline_texts)
        self.assertIn('"id": f"pipeline-{_pipeline_identity}-{i}"', src)
        self.assertIn("_append_drafts(account_id, charged_drafts)", src)
        self.assertNotIn("_save_drafts(account_id, existing_drafts + new_drafts)", src)

    def test_pipeline_tariff_is_child_exactly_once_before_append(self):
        src = inspect.getsource(T._run_pipeline_texts)
        self.assertIn("PIPELINE_DRAFT_TARIFF_EXACTLY_ONCE_V1", src)
        self.assertIn('f"pipeline-draft:{account_id}:{_did}"', src)
        self.assertIn('idempotency_key=_bill_key', src)
        self.assertLess(src.index("_pipeline_bill_consume"), src.index("_append_drafts(account_id, charged_drafts)"))


if __name__ == "__main__":
    unittest.main()
