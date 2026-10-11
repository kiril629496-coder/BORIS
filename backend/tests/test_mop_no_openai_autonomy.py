from unittest.mock import patch

from app.api import messenger
from app.services import sales_ai_router


def test_explicit_human_request_is_deterministic_and_narrow():
    positives = [
        "Позовите менеджера",
        "Я хочу поговорить с человеком",
        "Соедините меня со специалистом",
        "Можно живого оператора?",
        "Хватит бота, дайте человека",
        "Подключите сотрудника",
        "Переключите на менеджера",
        "Хочу общаться с живым человеком",
    ]
    negatives = [
        "Менеджер свяжется завтра?",
        "Подскажите стоимость",
        "Мне нужен менеджер проекта для компании",
        "Когда специалист сможет приехать?",
    ]
    assert all(messenger._mop_explicit_human_request(x) for x in positives)
    assert not any(messenger._mop_explicit_human_request(x) for x in negatives)


def test_mop_provider_order_never_contains_openai():
    env = {
        "OPENAI_API_KEY": "present-but-forbidden-for-mop",
        "GIGACHAT_KEY": "configured",
        "BORIS_SALES_OPENAI_ENABLED": "1",
        "BORIS_MOP_GIGACHAT_ENABLED": "1",
        "BORIS_MOP_DEEPSEEK_ENABLED": "0",
        "BORIS_MOP_GEMINI_ENABLED": "0",
        "BORIS_SALES_LOCAL_ENABLED": "1",
    }
    with patch.dict("os.environ", env, clear=False):
        order = sales_ai_router._mop_provider_order("acct")
    assert order == ["gigachat", "ollama"]
    assert "openai" not in order


def test_non_mop_provider_policy_keeps_existing_openai_semantics():
    with patch.dict("os.environ", {
        "OPENAI_API_KEY": "configured",
        "BORIS_SALES_OPENAI_ENABLED": "1",
        "BORIS_SALES_DEEPSEEK_ENABLED": "0",
        "BORIS_SALES_GEMINI_ENABLED": "0",
        "BORIS_SALES_LOCAL_ENABLED": "0",
    }, clear=False), patch.object(sales_ai_router, "_openai_billing_blocked", return_value=False), patch.object(sales_ai_router, "_circuit_blocks", return_value=False):
        assert sales_ai_router.provider_order("acct", module="sales") == ["openai"]


def test_mop_generate_routes_with_module_specific_order():
    seen = {}
    def fake_giga(**kwargs):
        seen.update(kwargs)
        return {"text": '{"reply_text":"По делу.","human_handoff":false,"handoff_reason":null}', "usage": {}}

    with patch.object(sales_ai_router, "_mop_provider_order", return_value=["gigachat"]):
        out = sales_ai_router.generate_text(
            account_id="acct",
            operation="mop_messenger_reply",
            prompt="reply",
            local_prompt="reply",
            module="mop",
            timeout=8,
            expect_json=True,
            gigachat_call=fake_giga,
        )
    assert out["provider"] == "gigachat"
    assert out["json"]["reply_text"] == "По делу."
    assert seen["operation"] == "mop_messenger_reply"


def test_planeta_mop_is_exact_openai_only_exception(monkeypatch):
    env = {
        "OPENAI_API_KEY": "configured",
        "BORIS_SALES_OPENAI_ENABLED": "1",
        "GIGACHAT_KEY": "configured-but-must-not-fallback",
        "BORIS_MOP_GIGACHAT_ENABLED": "1",
        "BORIS_SALES_GEMINI_ENABLED": "1",
        "BORIS_SALES_LOCAL_ENABLED": "1",
    }
    with patch.dict("os.environ", env, clear=False),          patch.object(sales_ai_router, "_openai_billing_blocked", return_value=False),          patch.object(sales_ai_router, "_circuit_blocks", return_value=False):
        order = sales_ai_router._mop_provider_order("planetazayavki_65985")
    assert order == ["openai"]


def test_planeta_mop_fails_closed_instead_of_falling_back_when_openai_unavailable(monkeypatch):
    env = {
        "OPENAI_API_KEY": "configured",
        "BORIS_SALES_OPENAI_ENABLED": "1",
        "GIGACHAT_KEY": "configured",
        "BORIS_MOP_GIGACHAT_ENABLED": "1",
        "BORIS_SALES_GEMINI_ENABLED": "1",
        "BORIS_SALES_LOCAL_ENABLED": "1",
    }
    with patch.dict("os.environ", env, clear=False),          patch.object(sales_ai_router, "_openai_billing_blocked", return_value=True),          patch.object(sales_ai_router, "_circuit_blocks", return_value=False):
        order = sales_ai_router._mop_provider_order("planetazayavki_65985")
    assert order == []


def test_global_production_contract_requires_scoped_planeta_openai_policy_marker():
    from pathlib import Path
    source = Path("/root/BORIS/backend/production_contract_runner.py").read_text(encoding="utf-8")
    assert '"mop_router_scoped_openai_policy"' in source
    assert '"PLANETA_OPENAI_MOP_V1"' in source
    assert '"mop_router_no_openai"' not in source
