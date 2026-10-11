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
        self.assertIn("human_takeover_state", src)
        self.assertIn('"provider_access_restored"', src)
        self.assertIn('"human_required"', src)
        self.assertNotIn(',"returned_to_ai",', src)
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

    def test_maria_old_template_is_blocked_after_phone_received(self):
        history = "Клиент: Добрый день\nКлиент: 89995213979"
        reply = (
            "Добрый день! Меня зовут Борис, я помощник Марии Каргальцевой. "
            "Напишите, пожалуйста, ваш номер телефона и удобное время для звонка."
        )
        problems = messenger_api._mop_output_policy_violations(reply, history)
        self.assertIn("internal_or_invented_identity", problems)
        self.assertIn("phone_already_received", problems)

    def test_phone_request_is_allowed_before_client_phone_exists(self):
        history = "Клиент: Добрый день, хочу рефинансировать ипотеку"
        reply = "Напишите, пожалуйста, ваш номер телефона для короткого созвона."
        problems = messenger_api._mop_output_policy_violations(reply, history)
        self.assertNotIn("phone_already_received", problems)

    def test_repeat_specialist_promise_is_blocked(self):
        history = (
            "Клиент: Ищу квартиру в Печатниках\n"
            "Мы: Специалист подготовит варианты под ваши критерии и пришлет подборку."
        )
        reply = "Специалист уже отбирает варианты и скоро пришлет подборку."
        problems = messenger_api._mop_output_policy_violations(reply, history)
        self.assertIn("repeated_handoff_promise", problems)

class ProactiveExternalReconciliationTests(unittest.TestCase):
    class _Row:
        def __init__(self, **values):
            self._mapping = values

    class _Result:
        def __init__(self, rows=None, scalar_value=None, first_value=None):
            self._rows = rows or []
            self._scalar = scalar_value
            self._first = first_value

        def fetchall(self):
            return self._rows

        def scalar(self):
            return self._scalar

        def first(self):
            return self._first

    class _DB:
        def __init__(self, event_id, account_id="account-a", source_account="account-a"):
            self.event_id = event_id
            self.account_id = account_id
            self.source_account = source_account
            self.calls = []
            self.closed = False

        def execute(self, statement, params):
            sql = str(statement)
            self.calls.append((sql, dict(params)))
            if "FROM mop_drafts" in sql:
                return ProactiveExternalReconciliationTests._Result(rows=[
                    ProactiveExternalReconciliationTests._Row(
                        id=7, status="draft_ready", avito_message_id=self.event_id
                    )
                ])
            if "avito_created_at FROM messenger_messages" in sql:
                source_id = messenger_api._reconciliation_source_message_id(self.event_id)
                found = params["a"] == self.source_account and params["m"] == source_id
                return ProactiveExternalReconciliationTests._Result(
                    scalar_value=100 if found else None
                )
            if "SELECT 1 FROM messenger_messages" in sql:
                return ProactiveExternalReconciliationTests._Result(first_value=(1,))
            raise AssertionError(sql)

        def close(self):
            self.closed = True

    def _run(self, event_id, account_id="account-a", source_account="account-a"):
        db = self._DB(event_id, account_id, source_account)
        transitions = []
        cards = []

        def set_status(_db, draft_id, new_status, previous, reason, **kwargs):
            transitions.append((draft_id, new_status, previous, reason, kwargs))
            return ({"id": draft_id}, "draft_ready")

        from unittest.mock import patch
        with patch("app.db.session.SessionLocal", return_value=db),              patch("app.mop_core.set_status", side_effect=set_status),              patch("app.mop_core.push_card",
                   side_effect=lambda _db, draft_id: cards.append(draft_id)):
            result = messenger_api._close_answered_externally(account_id, "chat-1")
        return result, db, transitions, cards

    def test_supported_proactive_ids_use_canonical_source_and_close(self):
        for event_id, source_id in (
            ("system:viewed_phone:source:1", "source:1"),
            ("system:empty_chat:source-2", "source-2"),
        ):
            with self.subTest(event_id=event_id):
                result, db, transitions, cards = self._run(event_id)
                self.assertEqual(result, 7)
                self.assertTrue(transitions)
                self.assertEqual(cards, [7])
                lookup = [params for sql, params in db.calls
                          if "avito_created_at FROM messenger_messages" in sql]
                self.assertEqual(lookup, [{"a": "account-a", "m": source_id}])

    def test_ordinary_unknown_and_malformed_ids_remain_unchanged(self):
        for message_id in (
            "ordinary-id",
            "system:unknown:source-3",
            "system:viewed_phone:",
        ):
            with self.subTest(message_id=message_id):
                self.assertEqual(
                    messenger_api._reconciliation_source_message_id(message_id),
                    message_id,
                )

    def test_other_account_source_cannot_close_draft(self):
        result, _db, transitions, cards = self._run(
            "system:viewed_phone:source-4",
            account_id="account-b",
            source_account="account-a",
        )
        self.assertIsNone(result)
        self.assertEqual(transitions, [])
        self.assertEqual(cards, [])


if __name__ == "__main__":
    unittest.main()
