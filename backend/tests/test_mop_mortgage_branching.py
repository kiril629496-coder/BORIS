from app.api.messenger import (
    _mop_branch_next_question,
    _mop_client_meta_nonanswer,
    _mop_high_confidence_first_turn_reply,
    _mop_meta_nonanswer_reply,
    _mop_sales_branch,
)


def test_mortgage_first_turn_enters_mortgage_branch():
    reply = _mop_high_confidence_first_turn_reply(
        "добрый день нужна ипотека",
        "Клиент: добрый день нужна ипотека",
    )
    assert reply == (
        "Ипотека нужна уже под выбранную квартиру или сначала хотите "
        "получить предварительное одобрение?"
    )


def test_mortgage_after_room_moves_to_budget_not_dead_end():
    history = """Клиент: добрый день нужна ипотека
Мы: Ипотека нужна уже под выбранную квартиру или сначала хотите получить предварительное одобрение?
Клиент: уже под квартиру
Мы: Рассматриваете новостройку или вторичный рынок?
Клиент: новостойка
Мы: Сколько комнат рассматриваете?
Клиент: 1
"""
    assert _mop_sales_branch(history) == "mortgage"
    assert _mop_branch_next_question(history) == (
        "Понял, однокомнатная. Какой примерно бюджет покупки рассматриваете?"
    )


def test_meta_nonanswer_does_not_advance_sales_cycle():
    history = """Клиент: добрый день нужна ипотека
Мы: Ипотека нужна уже под выбранную квартиру или сначала хотите получить предварительное одобрение?
Клиент: янаписал
"""
    assert _mop_client_meta_nonanswer("янаписал") is True
    assert _mop_meta_nonanswer_reply(history) == (
        "Да, вижу — вам нужна ипотека. Уточню только один момент: "
        "квартира уже выбрана или сначала нужно предварительное одобрение?"
    )


def test_regular_short_answer_is_not_meta_nonanswer():
    assert _mop_client_meta_nonanswer("москва") is False
    assert _mop_client_meta_nonanswer("1") is False
    assert _mop_client_meta_nonanswer("новостойка") is False


def test_explicit_property_search_with_mortgage_payment_stays_property_search():
    history = "Клиент: Хочу подобрать квартиру в Москве, покупка будет в ипотеку."
    assert _mop_sales_branch(history) == "property_search"
