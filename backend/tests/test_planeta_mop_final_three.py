from unittest.mock import patch

from app.api import messenger
from app.crm import bridge
from app.services import sales_ai_router


def test_system_policy_handoffs_are_not_sticky_chat_ownership():
    assert not messenger._mop_handoff_event_is_sticky(
        "handed_to_human", "system", "", "нет свежего однозначного подтверждённого ценового факта",
        {"policy_version": "MOP_STRUCTURED_HANDOFF_V1"},
    )
    assert not messenger._mop_handoff_event_is_sticky(
        "handed_to_human", "system", "", "mop_text_provider_no_vision",
        {"policy_version": "MOP_STRUCTURED_HANDOFF_V1"},
    )


def test_real_human_and_phone_handoffs_stay_sticky():
    assert messenger._mop_handoff_event_is_sticky(
        "handed_to_human", "system", "", "получен телефон клиента",
        {"policy_version": "MOP_STRUCTURED_HANDOFF_V1"},
    )
    assert messenger._mop_handoff_event_is_sticky(
        "handed_to_human", "user", "42", "manager take", {}
    )
    assert messenger._mop_handoff_event_is_sticky(
        "handed_to_human", "system", "", "client_explicitly_requested_human",
        {"policy_version": "MOP_STRUCTURED_HANDOFF_V1"},
    )


def test_planeta_knowledge_rejects_prompt_and_foreign_vertical():
    raw = '{"direction":"30 объявлений по каждой категории и каждому направлению бизнеса, которые уже есть у нас","category_path":"Готовый бизнес и оборудование > Оборудование для бизнеса > Промышленное > Насосы и компрессоры > Насосы"}'
    allowed, reason = messenger._planeta_feed_profile_decision(
        "feed_direction_profile:30 объявлений по каждой категории", raw
    )
    assert not allowed
    assert reason == "prompt_like_direction"

    kitchen = '{"direction":"Кухни на заказ","category_path":"Для дома и дачи > Мебель и интерьер > Кухонные гарнитуры > Кухни"}'
    allowed, reason = messenger._planeta_feed_profile_decision(
        "feed_direction_profile:кухни на заказ", kitchen
    )
    assert allowed
    assert reason is None


def test_crm_meaningful_first_inquiry():
    assert not bridge._meaningful_inbound_text("Здравствуйте")
    assert not bridge._meaningful_inbound_text("Цена")
    assert not bridge._meaningful_inbound_text("Актуально?")
    assert bridge._meaningful_inbound_text("Нужна кухня в Ижевске")
    assert bridge._meaningful_inbound_text("Есть размеры кухни, нужен расчет")


def test_openai_internal_expired_budget_uses_bounded_retry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reserve_ids = []
    allow_ids = []
    posted = {}

    from app.services import ai_budget

    def fake_reserve(**kw):
        reserve_ids.append(kw["run_id"])
        return {"status": "exists" if len(reserve_ids) == 1 else "reserved"}

    def fake_allow(run_id):
        allow_ids.append(run_id)
        if len(allow_ids) == 1:
            return {"allowed": False, "reason": "expired"}
        return {"allowed": True}

    class Resp:
        status_code = 200
        headers = {"x-request-id": "req-test"}
        def json(self):
            return {
                "output": [{"content": [{"type": "output_text", "text": "Готово"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    def fake_post(self, url, **kwargs):
        posted["headers"] = kwargs.get("headers") or {}
        return Resp()

    monkeypatch.setattr(ai_budget, "reserve", fake_reserve)
    monkeypatch.setattr(ai_budget, "allow_next", fake_allow)
    monkeypatch.setattr(ai_budget, "commit", lambda *a, **k: {"status": "committed"})
    monkeypatch.setattr(ai_budget, "release", lambda *a, **k: {"status": "released"})
    monkeypatch.setattr(ai_budget, "stop", lambda *a, **k: None)
    monkeypatch.setattr("requests.Session.post", fake_post)
    monkeypatch.setattr(sales_ai_router, "dependency_call", lambda dep, fn, **kw: fn())
    monkeypatch.setattr("app.usage.log_usage", lambda **kw: 0.01)

    out = sales_ai_router._openai_default_call(
        account_id="planetazayavki_65985",
        operation="mop_messenger_reply",
        prompt="Ответь клиенту",
        model="gpt-test",
        max_output_tokens=32,
        idempotency_key="mop-draft:11529",
        module="mop",
        timeout=5,
    )
    assert out["text"] == "Готово"
    assert len(reserve_ids) == 2
    assert reserve_ids[1] == reserve_ids[0] + ":retry1"
    assert allow_ids == reserve_ids
    assert posted["headers"]["Idempotency-Key"].startswith("sales-ai:")


def test_chat_lock_ignores_inherited_nonsticky_price_handoff():
    class Result:
        def __init__(self, first):
            self._first = first
        def mappings(self):
            return self
        def first(self):
            return self._first

    class Db:
        def execute(self, *args, **kwargs):
            return Result({
                "id": 20,
                "sent_at": None,
                "updated_at": None,
                "handoff_event": "handed_to_human",
                "handoff_actor_type": "system",
                "handoff_actor_id": "chat_handoff_lock",
                "handoff_payload": "prior chat-level human handoff is still unresolved",
                "handoff_metadata": {"policy_version": "MOP_CHAT_HANDOFF_LOCK_V1"},
                "handoff_event_at": None,
                "root_event": "handed_to_human",
                "root_actor_type": "system",
                "root_actor_id": None,
                "root_payload": "нет свежего однозначного подтверждённого ценового факта",
                "root_metadata": {"policy_version": "MOP_STRUCTURED_HANDOFF_V1"},
                "root_at": None,
                "returned_at": None,
            })

    assert messenger._mop_unresolved_chat_handoff(Db(), "acc", "chat") is None


def test_generation_budget_expired_becomes_waiting_external():
    from app import mop_core

    class ScalarResult:
        def scalar(self):
            return 0

    class Db:
        def execute(self, *args, **kwargs):
            return ScalarResult()

    with patch.object(mop_core, "set_status", return_value=({"id": 1}, "analyzing")) as set_status:
        status = mop_core.generation_failed(
            Db(), 1,
            "[Ошибка генерации: SalesAIBudgetBlocked: openai internal ai budget blocked: expired]",
        )
    assert status == "waiting_external"
    assert set_status.call_args.args[2] == "waiting_external"


def test_crm_after_engaged_trigger_respects_meaningful_gate():
    class Result:
        def mappings(self):
            return self
        def first(self):
            return {"has_phone": False, "stage": None, "msg_count": 1}

    class Db:
        def execute(self, *args, **kwargs):
            return Result()

    with patch.object(bridge, "_account_sales_settings", return_value={"deal_trigger": "after_engaged"}),          patch.object(bridge, "_latest_mop_qualification", return_value={}),          patch.object(bridge, "_persist_qualification_to_lead", return_value=None):
        allow_greeting, _, _ = bridge._deal_gate(
            Db(), "planetazayavki_65985", "chat-1",
            [{"direction": "in", "body": "Здравствуйте"}],
        )
        allow_real, _, maturity = bridge._deal_gate(
            Db(), "planetazayavki_65985", "chat-2",
            [{"direction": "in", "body": "Нужна кухня в Ижевске"}],
        )
    assert allow_greeting is False
    assert allow_real is True
    assert maturity["meaningful_first"] is True
