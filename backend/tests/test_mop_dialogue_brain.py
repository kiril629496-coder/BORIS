from app.services.mop_dialogue_brain import (
    _sanitize_question,
    niche_key,
    NICHE_DEFAULT_QUESTIONS,
)


def test_dialogue_brain_sanitizes_sensitive_fragments():
    q = _sanitize_question(
        "Позвоните +7 999 123-45-67, цена 125 000 руб, сайт https://example.com — какой вариант нужен?"
    )
    assert "+7 999" not in q
    assert "125 000" not in q
    assert "https://example.com" not in q
    assert "[телефон]" in q
    assert "[сумма]" in q
    assert "[ссылка]" in q


def test_dialogue_brain_rejects_useless_short_questions():
    assert _sanitize_question("Почему?") == ""
    assert _sanitize_question("ru Почему?") == ""
    assert _sanitize_question("Чем могу помочь?") == ""


def test_niche_detection_for_real_client_categories():
    assert niche_key("Брокер Мария", "", "Одобрение ипотеки") == "ipoteka_nedvizhimost"
    assert niche_key("Плитка МО", "Тротуарная плитка", "") == "plitka"
    assert niche_key("Динара", "Экипировка для водных видов спорта", "") == "water_gear"
    assert niche_key("Георгий", "Продажа электровелосипедов", "") == "electric_bikes"
    assert niche_key("Тимофей", "Строительные товары и материалы", "") == "building_materials"


def test_every_known_niche_has_nonempty_safe_starter():
    for key in (
        "bytovki", "ipoteka_nedvizhimost", "plitka", "mebel", "steklo",
        "shtukaturka", "beton_stroitelstvo", "almaznoe", "evakuatory",
        "avto", "dveri", "angary", "nerudnye", "water_gear",
        "electric_bikes", "building_materials", "marketing",
    ):
        assert NICHE_DEFAULT_QUESTIONS.get(key)
        assert all(str(x).strip().endswith("?") for x in NICHE_DEFAULT_QUESTIONS[key])


def test_dialogue_brain_dynamic_facts_never_become_literal_response_patterns():
    from app.services.mop_dialogue_brain import response_pattern_safe
    assert response_pattern_safe("Цена 85 000 руб, есть в наличии.") is False
    assert response_pattern_safe("Завтра есть свободное время в 9:30.") is False
    assert response_pattern_safe("Наш телефон +7 999 123-45-67") is False
    assert response_pattern_safe("Да есть") is False
    assert response_pattern_safe("Подскажите, в каком городе нужен проект?") is True
    assert response_pattern_safe("Передам вопрос менеджеру, он уточнит детали.") is True


def test_dialogue_brain_strips_factual_preamble_from_learned_question():
    from app.services.mop_dialogue_brain import _behavioral_question_tail, _sanitize_question
    q = _behavioral_question_tail(_sanitize_question(
        "Культуры, 63Ж График работы: с 10:00 до 19:00 ежедневно Вас Аренда или Продажа интересует?"
    ))
    assert q == "Аренда или Продажа интересует?"


def test_dialogue_context_uses_account_intent_profile_without_cross_account_literals(monkeypatch):
    from app.services import mop_dialogue_brain as brain

    account_payload = {
        "summary": {"messages": 100, "chats": 20, "human_replies": 60, "mop_replies_excluded": 10},
        "style_profile": {
            "preferred_length": "short",
            "typical_sentences": 1,
            "question_rate_pct": 20,
            "cta_rate_pct": 35,
            "first_turn_greeting_rate_pct": 70,
            "followup_greeting_rate_pct": 5,
        },
        "intent_profiles": [{
            "intent": "price",
            "label": "Цена/расчёт",
            "support": 20,
            "preferred_length": "short",
            "question_rate_pct": 10,
            "cta_rate_pct": 60,
            "handoff_rate_pct": 55,
        }],
        "recommended_sequence": [{"tag": "budget", "label": "Бюджет/сумма", "support": 9}],
        "question_templates": [],
        "response_patterns": [],
        "handoff_patterns": [],
    }
    niche_payload = {
        "accounts": 3,
        "recommended_sequence": [{"tag": "location", "label": "География/адрес"}],
        "response_patterns": [{"response": "FOREIGN_LITERAL_MUST_NOT_LEAK"}],
    }

    class _Result:
        def first(self):
            return ("Компания", "Мебель", "")

    class _Db:
        def execute(self, *args, **kwargs):
            return _Result()
        def close(self):
            pass
        def rollback(self):
            pass

    monkeypatch.setattr(brain, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(brain, "ensure_tables", lambda db: None)
    monkeypatch.setattr(
        brain,
        "_payload",
        lambda db, table, key_col, key: account_payload if table == brain.ACCOUNT_TABLE else niche_payload,
    )
    monkeypatch.setattr(brain, "niche_key", lambda *args: "mebel")

    ctx = brain.dialogue_context("acc", current_client_text="Сколько будет стоить?", max_chars=6000)
    assert "Текущий тип обращения клиента: Цена/расчёт." in ctx
    assert "CTA ~60%" in ctx
    assert "передача человеку ~55%" in ctx
    assert "human_handoff=true" in ctx
    assert "FOREIGN_LITERAL_MUST_NOT_LEAK" not in ctx


def test_dialogue_brain_builder_excludes_mop_self_training_and_worker_refresh_is_singleton():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    runner = (root / "mop_dialogue_brain_runner.py").read_text(encoding="utf-8")
    worker = (root / "boris_background_worker.py").read_text(encoding="utf-8")
    assert "outgoing_text_hash" in runner
    assert "mop_generated" in runner
    assert "response_pattern_safe" in runner
    assert "MOP_REPLIES_EXCLUDED" in runner
    assert "def _mop_dialogue_learning_loop" in worker
    assert '("mop_dialogue_learning", _mop_dialogue_learning_loop)' in worker
    assert "BORIS_MOP_DIALOGUE_BRAIN_INTERVAL_SEC" in worker
