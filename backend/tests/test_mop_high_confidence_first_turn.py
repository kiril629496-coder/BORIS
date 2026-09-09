from app.api import messenger


def test_real_estate_first_turn_fastpath():
    q = "Здравствуйте, хочу подобрать квартиру в Москве. С чего лучше начать?"
    h = "Клиент: " + q
    r = messenger._mop_high_confidence_first_turn_reply(q, h)
    assert r
    assert "бюджет" in r.lower()
    assert "телефон" not in r.lower()


def test_mortgage_first_turn_fastpath():
    q = "Добрый день, интересует ипотека"
    h = "Клиент: " + q
    r = messenger._mop_high_confidence_first_turn_reply(q, h)
    assert r
    assert "ипотеки" in r.lower()


def test_fastpath_not_used_after_manager_reply():
    q = "Хочу подобрать квартиру в Москве"
    h = "Клиент: Хочу квартиру\nМы: Какой район?\nКлиент: " + q
    assert messenger._mop_high_confidence_first_turn_reply(q, h) is None


def test_generate_path_places_learning_gate_before_high_confidence_fastpath():
    import inspect
    src = inspect.getsource(messenger.generate_ai_draft_reply)
    gate = src.index("_learning_relevant_now = _mop_confirmed_learning_relevant(")
    fast = src.index("_high_confidence_reply = (")
    assert gate < fast
    fast_block = src[fast:fast+800]
    assert "if _learning_relevant_now" in fast_block
