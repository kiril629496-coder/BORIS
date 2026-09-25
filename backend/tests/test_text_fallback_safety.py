import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import gigachat_pool as G


@contextmanager
def _guard(value):
    yield value


class _Response:
    def __init__(self, request_id="req-provider-1"):
        self.headers={"x-request-id":request_id}
    def raise_for_status(self):
        return None
    def json(self):
        return {"usage":{"prompt_tokens":10,"completion_tokens":5},"choices":[{"message":{"content":"ok"}}]}


class TextFallbackSafetyTests(unittest.TestCase):
    def test_openai_fallback_clamps_attempts_to_one(self):
        msg=SimpleNamespace(role="user",content="hello")
        with patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard({"allowed":True})), \
             patch("proxy_pool.get_intl_requests_proxies",return_value={}), \
             patch("app.services.reliability.dependency_call",side_effect=RuntimeError("network")) as provider:
            with self.assertRaises(Exception):
                G._openai_fallback_completion([msg],attempts=9,account_id="acc-a",operation="qa")
        self.assertEqual(provider.call_count,1)

    def test_openai_text_burst_cap_stops_before_provider(self):
        msg=SimpleNamespace(role="user",content="hello")
        with patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard({"allowed":False,"used":20,"cap":20})), \
             patch("app.services.reliability.dependency_call") as provider:
            with self.assertRaisesRegex(RuntimeError,"OPENAI_TEXT_BURST_CAP"):
                G._openai_fallback_completion([msg],account_id="acc-a",operation="qa")
        provider.assert_not_called()

    def test_openai_text_daily_cap_is_distinct_and_stops_before_provider(self):
        msg=SimpleNamespace(role="user",content="hello")
        guard={"allowed":False,"code":"OPENAI_TEXT_DAILY_CAP","used":2,"cap":20,"daily_used":100,"daily_cap":100}
        with patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard(guard)), \
             patch("app.services.reliability.dependency_call") as provider:
            with self.assertRaisesRegex(RuntimeError,"OPENAI_TEXT_DAILY_CAP:100/100"):
                G._openai_fallback_completion([msg],account_id="acc-a",operation="qa")
        provider.assert_not_called()

    def test_provider_request_id_is_used_for_usage(self):
        msg=SimpleNamespace(role="user",content="hello")
        with patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard({"allowed":True})), \
             patch("proxy_pool.get_intl_requests_proxies",return_value={}), \
             patch("app.services.reliability.dependency_call",return_value=_Response("req-real-123")), \
             patch("app.usage.log_usage",return_value=0.1) as usage:
            out=G._openai_fallback_completion([msg],account_id="acc-a",operation="qa")
        self.assertEqual(out,"ok")
        self.assertEqual(usage.call_args.kwargs.get("request_id"),"req-real-123")

    def test_campaign_single_paid_attempt_bypasses_gigachat_and_calls_openai_once(self):
        msg=SimpleNamespace(role="user",content="hello")
        events=[]
        with patch.object(G,"RateLimitedGigaChat",side_effect=AssertionError("GigaChat must not run")), \
             patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard({"allowed":True})), \
             patch("proxy_pool.get_intl_requests_proxies",return_value={}), \
             patch("app.services.ai_budget.reserve",return_value={"status":"reserved"}), \
             patch("app.services.ai_budget.allow_next",return_value={"allowed":True}), \
             patch("app.services.ai_budget.commit",side_effect=lambda run_id,rid: events.append(("commit",rid)) or {"status":"committed"}), \
             patch("app.services.ai_budget.release",side_effect=lambda run_id: events.append(("release",run_id)) or {"status":"released"}), \
             patch("app.usage.log_usage",side_effect=lambda *a,**kw: events.append(("usage",kw.get("request_id"))) or 0.1), \
             patch("app.services.reliability.dependency_call",return_value=_Response("req-campaign-exact")) as provider:
            out=G.chat_with_fallback([msg],account_id="acc-a",operation="генерация объявлений",single_paid_attempt=True)
        self.assertEqual(out,"ok")
        self.assertEqual(provider.call_count,1)
        self.assertIn(("usage","req-campaign-exact"),events)
        self.assertIn(("commit","req-campaign-exact"),events)
        self.assertTrue(any(e[0]=="release" for e in events))

    def test_campaign_single_paid_attempt_marks_missing_provider_id_ambiguous(self):
        msg=SimpleNamespace(role="user",content="hello")
        events=[]
        with patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard({"allowed":True})), \
             patch("proxy_pool.get_intl_requests_proxies",return_value={}), \
             patch("app.services.ai_budget.reserve",return_value={"status":"reserved"}), \
             patch("app.services.ai_budget.allow_next",return_value={"allowed":True}), \
             patch("app.services.ai_budget.commit",side_effect=lambda run_id,rid: events.append(("commit",rid)) or {"status":"committed"}), \
             patch("app.services.ai_budget.release",return_value={"status":"released"}), \
             patch("app.usage.log_usage",side_effect=lambda *a,**kw: events.append(("usage",kw.get("request_id"))) or 0.1), \
             patch("app.services.reliability.dependency_call",return_value=_Response("")) as provider:
            out=G.chat_with_fallback([msg],account_id="acc-a",operation="генерация объявлений",single_paid_attempt=True)
        self.assertEqual(out,"ok")
        self.assertEqual(provider.call_count,1)
        rid=[e[1] for e in events if e[0]=="usage"][0]
        self.assertTrue(rid.startswith("ambiguous-text:acc-a:"))
        self.assertIn(("commit",rid),events)

    def test_same_paid_intent_cannot_rebuy_after_ambiguous_transport_failure(self):
        msg=SimpleNamespace(role="user",content="hello")
        state={"reserve":0,"allow":0}
        def _reserve(**kw):
            state["reserve"] += 1
            return {"status":"reserved" if state["reserve"] == 1 else "exists",
                    "run_status":"released" if state["reserve"] > 1 else "reserved"}
        def _allow(run_id):
            state["allow"] += 1
            return {"allowed": state["allow"] == 1, "reason":"run_released" if state["allow"] > 1 else None}
        with patch.object(G,"_openai_text_burst_guard",lambda account_id:_guard({"allowed":True})), \
             patch("proxy_pool.get_intl_requests_proxies",return_value={}), \
             patch("app.services.ai_budget.reserve",side_effect=_reserve) as reserve, \
             patch("app.services.ai_budget.allow_next",side_effect=_allow), \
             patch("app.services.ai_budget.release",return_value={"status":"released"}), \
             patch("app.services.reliability.dependency_call",side_effect=RuntimeError("connection lost after send")) as provider:
            with self.assertRaises(Exception):
                G._openai_fallback_completion([msg],account_id="acc-a",operation="qa",
                                              request_id="job:7:item:9",financial_guard=True)
            with self.assertRaisesRegex(RuntimeError,"CAMPAIGN_TEXT_BUDGET_NOT_ALLOWED"):
                G._openai_fallback_completion([msg],account_id="acc-a",operation="qa",
                                              request_id="job:7:item:9",financial_guard=True)
        self.assertEqual(provider.call_count,1)
        run_ids=[c.kwargs.get("run_id") for c in reserve.call_args_list]
        self.assertEqual(run_ids[0],run_ids[1])
        self.assertTrue(run_ids[0].startswith("campaign-text-intent:"))
        first_headers=provider.call_args.kwargs.get("headers") or {}
        self.assertEqual(first_headers.get("Idempotency-Key"),"job:7:item:9")


if __name__ == "__main__":
    unittest.main()
