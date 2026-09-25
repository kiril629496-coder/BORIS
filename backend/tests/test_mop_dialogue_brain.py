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
