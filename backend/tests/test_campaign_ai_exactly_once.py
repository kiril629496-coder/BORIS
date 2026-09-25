import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.api import campaigns as C


class _Result:
    def scalar(self): return 0


class _DB:
    def execute(self, *args, **kwargs): return _Result()
    def close(self): pass


class CampaignAIExactlyOnceTests(unittest.TestCase):
    def test_transport_ambiguity_is_quarantined_and_second_call_never_rebuys(self):
        provider=Mock(side_effect=ConnectionError("lost after send"))
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
            "OPENAI_API_KEY":"test-key", "BORIS_CAMPAIGN_AI_PAID_CACHE_DIR":td,
        }):
            with patch.object(C,"SessionLocal",return_value=_DB()), \
                 patch("app.services.ai_budget.reserve",return_value={"status":"observe"}), \
                 patch("app.services.ai_budget.allow_next",return_value={"allowed":True}), \
                 patch("app.services.ai_budget.release",return_value={"status":"released"}), \
                 patch("app.services.ai_guard_audit.log_guard_event",return_value=True):
                kwargs=dict(account_id="acc-campaign",operation="campaign_reference_vision",model="gpt-5.4-mini",
                            input_payload=[{"role":"user","content":"test"}],max_output_tokens=100,
                            idempotency_key="owner-intent-1",timeout=10,requests_post=provider)
                with self.assertRaisesRegex(RuntimeError,"CAMPAIGN_AI_OUTCOME_UNCERTAIN"):
                    C._ff_guarded_openai_response(**kwargs)
                with self.assertRaisesRegex(RuntimeError,"CAMPAIGN_AI_OUTCOME_UNCERTAIN"):
                    C._ff_guarded_openai_response(**kwargs)
        self.assertEqual(provider.call_count,1)

    def test_source_has_durable_pre_provider_barrier_and_guard_observability(self):
        import inspect
        src=inspect.getsource(C._ff_guarded_openai_response)
        self.assertIn("provider_outcome_unknown",src)
        self.assertIn("CAMPAIGN_AI_OUTCOME_UNCERTAIN",src)
        self.assertIn("CAMPAIGN_AI_DAILY_CAP",src)
        self.assertIn("CAMPAIGN_AI_BURST_CAP",src)
        self.assertIn("Idempotency-Key",src)


if __name__ == "__main__":
    unittest.main()
