import inspect

from app.services import mop_dialogue_brain as brain
import mop_dialogue_brain_runner as runner
from app.api import messenger


def test_response_sanitizer_removes_contacts_money_and_long_numbers():
    text = "Звоните +7 (812) 642-02-62, цена 85 000 за наличные, сайт https://example.com"
    out = brain._sanitize_response(text)
    assert "+7 (812)" not in out
    assert "85 000" not in out
    assert "https://example.com" not in out
    assert "[ссылка]" in out
    assert "[число]" in out or "[сумма]" in out


def test_dynamic_business_facts_are_not_reusable_response_patterns():
    assert not brain.response_pattern_safe("Завтра есть свободное время в 14:00")
    assert not brain.response_pattern_safe("Есть в наличии")
    assert not brain.response_pattern_safe("Цена [сумма]")
    assert not brain.response_pattern_safe("Наш номер [телефон]")
    assert brain.response_pattern_safe("Подскажите, какой вариант вам ближе?")


def test_client_intent_classifier_routes_common_sales_turns():
    assert brain.classify_client_intent("Сколько будет стоить?")[0] == "price"
    assert brain.classify_client_intent("Есть ли сейчас в наличии?")[0] == "availability"
    assert brain.classify_client_intent("Можно подключить менеджера?")[0] == "human_request"
    assert brain.classify_client_intent("Вот фото, хочу примерно так")[0] == "photo"


def test_runner_v3_excludes_mop_outgoing_from_human_learning():
    src = inspect.getsource(runner.build)
    assert "mop_account_dialogue_brain_v3" in src
    assert "outgoing_text_hash" in src
    assert "mop_generated" in src
    assert "MOP_REPLIES_EXCLUDED" in src
    assert "response_pattern_safe" in src


def test_live_mop_uses_current_client_text_for_adaptive_retrieval():
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    assert "current_client_text=str(_question or" in src
    assert "max_chars=5200" in src


def test_existing_messenger_poller_owns_dialogue_learning_refresh():
    src = inspect.getsource(messenger._messenger_poll_loop)
    assert "MOP_DIALOGUE_ADAPTIVE_LEARNING_V3" in src
    assert "BORIS_MOP_DIALOGUE_BRAIN_INTERVAL_SEC" in src
    assert "mop_dialogue_brain_runner" in src
    assert "worker_pool.submit(_refresh_dialogue_learning)" in src


def test_v3_build_persists_intent_profiles_and_live_context_consumes_them():
    build_src = inspect.getsource(runner.build)
    ctx_src = inspect.getsource(brain.dialogue_context)
    assert '"intent_profiles"' in build_src
    assert "question_rate_pct" in build_src
    assert "cta_rate_pct" in build_src
    assert "handoff_rate_pct" in build_src
    assert "intent_profile" in ctx_src
    assert "Поведенческий профиль" in ctx_src


def test_question_learning_keeps_only_behavioral_tail_after_contact_preamble():
    raw = "Наши контакты: +7 (812) 642-02-62 manager@example.com. Вас аренда или продажа интересует?"
    sanitized = brain._sanitize_question(raw)
    out = brain._behavioral_question_tail(sanitized)
    assert "812" not in out
    assert "example.com" not in out
    assert out.lower().startswith("вас аренда")
