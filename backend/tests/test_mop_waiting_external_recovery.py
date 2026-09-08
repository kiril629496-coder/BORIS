import inspect
import unittest

from app import mop_core
from app.api import mop_training
from app.api import messenger as messenger_api
from app.api.messenger import _waiting_external_403_decision
from app.mop_core import waiting_external_local_decision, _handoff_topic_resolved


class WaitingExternal403RecoveryTests(unittest.TestCase):
    def test_same_incoming_resumes(self):
        r={"status":"ok","messages":{"messages":[
            {"id":"m1","created":20,"direction":"in","type":"text","content":{"text":"hello"}},
        ]}}
        self.assertEqual(_waiting_external_403_decision("m1",r),"resume")

    def test_newer_incoming_supersedes_even_if_provider_order_is_mixed(self):
        r={"status":"ok","messages":{"messages":[
            {"id":"m1","created":20,"direction":"in","type":"text"},
            {"id":"m2","created":30,"direction":"in","type":"text"},
        ]}}
        self.assertEqual(_waiting_external_403_decision("m1",r),"superseded")

    def test_newer_outgoing_means_answered(self):
        r={"status":"ok","messages":{"messages":[
            {"id":"m1","created":20,"direction":"in","type":"text"},
            {"id":"o1","created":40,"direction":"out","type":"text"},
        ]}}
        self.assertEqual(_waiting_external_403_decision("m1",r),"answered")

    def test_explicit_403_stays_external(self):
        self.assertEqual(
            _waiting_external_403_decision("m1",{"status":"error","message":"Avito 403 — нет доступа к этому чату"}),
            "still_external",
        )

    def test_subscription_402_is_external_dependency(self):
        self.assertEqual(
            _waiting_external_403_decision("m1",{"status":"error","message":"Avito API error 402: Перейдите на подписку с API мессенджера"}),
            "subscription_required",
        )

    def test_unknown_provider_failure_retries_later(self):
        self.assertEqual(
            _waiting_external_403_decision("m1",{"status":"error","message":"timeout"}),
            "retry_later",
        )

    def test_state_machine_allows_recovered_draft_and_post_send_handoff(self):
        self.assertTrue(mop_core.can_go("waiting_external","draft_ready"))
        self.assertTrue(mop_core.can_go("sent","human_required"))

    def test_local_waiting_external_decision_is_provider_free_and_deterministic(self):
        self.assertEqual(waiting_external_local_decision("m1", "m1", "in"), "unchanged")
        self.assertEqual(waiting_external_local_decision("m1", "o2", "out"), "answered_externally")
        self.assertEqual(waiting_external_local_decision("m1", "m2", "in"), "superseded")
        self.assertEqual(waiting_external_local_decision("m1", "x2", "unknown"), "unchanged")

    def test_handoff_topic_resolution_is_narrow(self):
        self.assertTrue(_handoff_topic_resolved(
            "availability_requires_human",
            "Коллега уточнил, сейчас есть несколько вариантов, включая Toyota Camry.",
        ))
        self.assertTrue(_handoff_topic_resolved(
            "нужно подтвердить актуальную цену",
            "Стоимость составит 85 000 руб.",
        ))
        self.assertTrue(_handoff_topic_resolved(
            "индивидуальный расчет доставки",
            "Доставка рассчитана: 12 000 ₽.",
        ))
        self.assertFalse(_handoff_topic_resolved(
            "messenger_channel_unconfirmed",
            "Продолжим здесь в Авито.",
        ))
        self.assertFalse(_handoff_topic_resolved(
            "client_requested_human",
            "Уточню и вернусь к вам.",
        ))

    def test_local_provider_retry_has_bounded_backoff_and_no_provider_call(self):
        src=inspect.getsource(mop_core.recover_waiting_external_provider)
        self.assertIn("MOP_LOCAL_PROVIDER_RETRY_BACKOFF_V1", src)
        self.assertIn("_local_backoff=(2*60,5*60,10*60,20*60,30*60)", src)
        self.assertIn("_local_backoff=(5*60,15*60,30*60,60*60,120*60)", src)
        self.assertIn("backoff_sec=_local_backoff", src)
        self.assertIn("returned_to_ai", src)
        self.assertNotIn("ollama", src.lower())
        self.assertNotIn("openai", src.lower())

    def test_training_uses_shared_sales_ai_router_without_legacy_direct_ollama(self):
        src=inspect.getsource(mop_training._ai_json)
        self.assertIn("MOP_TRAINING_SALES_AI_ROUTER_V1", src)
        self.assertIn("sales_ai_router", src)
        self.assertIn('expect_json=True', src)
        self.assertIn('local_format=_mop_training_output_schema(operation)', src)
        self.assertIn('router_policy', src)
        self.assertNotIn("http://127.0.0.1:11434/api/generate", src)
        self.assertNotIn("boris_mop_local.lock", src)

    def test_mop_generation_uses_shared_sales_ai_router_and_exactly_once_cache(self):
        src=inspect.getsource(messenger_api.generate_ai_draft_reply)
        self.assertIn("MOP_SALES_AI_ROUTER_V1", src)
        self.assertIn("MOP_ROUTED_PROVIDER_USAGE_V1", src)
        self.assertIn("sales_ai_router", src)
        self.assertIn('operation="mop_messenger_reply"', src)
        self.assertIn("idempotency_key=_router_intent", src)
        self.assertIn("pg_advisory_lock", src)
        self.assertIn("_mop_paid_cache_get", src)
        self.assertIn("_mop_paid_cache_put", src)
        self.assertIn('provider_request_id = str((usage or {}).get("request_id")', src)
        self.assertIn("ambiguous-%s:mop:", src)
        self.assertNotIn("_giga.chat(", src)
        self.assertNotIn("gigachat_provider_error_no_blind_replay", src)

    def test_mop_provider_outage_degrades_to_safe_reply_not_502_or_owner_task(self):
        src=inspect.getsource(messenger_api.generate_ai_draft_reply)
        self.assertIn("MOP_AI_EMERGENCY_REPLY_V2", src)
        self.assertIn('"provider":"deterministic_emergency"', src)
        self.assertIn('"provider_outage_deferred":True', src)
        self.assertIn('"provider_outage_reason":"all_sales_ai_providers_unavailable"', src)
        self.assertIn('"human_handoff":False', src)
        self.assertIn('"handoff_reason":None', src)
        self.assertIn('"fallback_chain":_provider_chain', src)
        self.assertIn("provider_outage_safe_continuation", src)
        self.assertIn("Точную стоимость без подтверждённых условий не буду придумывать.", src)
        self.assertNotIn("зафиксирую детали и передам специалисту", src)

if __name__ == "__main__":
    unittest.main()
