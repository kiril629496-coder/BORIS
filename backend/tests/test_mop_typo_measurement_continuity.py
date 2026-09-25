from app.api.messenger import (
    _mop_measurement_intent,
    _mop_dimensions_answered,
    _mop_output_policy_violations,
    _mop_measurement_repair_reply,
)


def test_measurement_typo_is_understood():
    assert _mop_measurement_intent("нет я наверное нужен замерик") is True
    assert _mop_measurement_intent("наверное нужен замершик") is True
    assert _mop_measurement_intent("нужен замерщик") is True
    assert _mop_measurement_intent("заморозка пока не нужна") is False


def test_dimensions_question_is_closed_by_measurement_intent():
    assert _mop_dimensions_answered("размеров не знаю, нужен замер") is True
    assert _mop_dimensions_answered("нет я наверное нужен замерик") is True


def test_repeated_dimensions_question_is_blocked_after_typo_intent():
    history = """Клиент: добрый день нужна прямая кухня до 2 метров
Мы: В каком городе нужен проект?
Клиент: Уфа
Мы: Есть ли примерные размеры помещения или зоны?
Клиент: нет я наверное нужен замерик"""
    violations = _mop_output_policy_violations("Есть ли примерные размеры помещения или зоны?", history)
    assert "measurement_required_no_dimensions" in violations
    assert "repeated_answered_question:dimensions" in violations


def test_measurement_repair_moves_to_address():
    history = """Клиент: добрый день нужна прямая кухня до 2 метров
Мы: В каком городе нужен проект?
Клиент: Уфа
Мы: Есть ли примерные размеры помещения или зоны?
Клиент: нет я наверное нужен замерик"""
    reply = _mop_measurement_repair_reply(history)
    assert "нужен замерщик" in reply.lower()
    assert "адрес" in reply.lower()
    assert "размер" in reply.lower()


def test_any_answer_closes_same_semantic_question():
    from app.api.messenger import _mop_question_was_answered, _mop_emergency_next_question
    qs=[
        'В каком городе нужен проект?',
        'Есть ли примерные размеры помещения или зоны?',
        'Что важнее по комплектации и хранению?',
        'Есть ли ориентир по стилю/цвету и бюджету?',
    ]
    history='''Клиент: нужна кухня\nМы: В каком городе нужен проект?\nКлиент: Уфа\nМы: Есть ли примерные размеры помещения или зоны?\nКлиент: нет'''
    assert _mop_question_was_answered(history,'Есть ли примерные размеры помещения или зоны?') is True
    assert _mop_emergency_next_question(qs,history)=='Что важнее по комплектации и хранению?'


def test_short_negative_or_unknown_answer_closes_question():
    from app.api.messenger import _mop_question_was_answered
    assert _mop_question_was_answered('''Мы: Есть ли ориентир по бюджету?\nКлиент: нет''','Есть ли ориентир по бюджету?') is True
    assert _mop_question_was_answered('''Мы: Что важнее по комплектации и хранению?\nКлиент: не знаю''','Что важнее по комплектации и хранению?') is True


def test_explicit_clarification_does_not_close_question():
    from app.api.messenger import _mop_question_was_answered
    assert _mop_question_was_answered('''Мы: Подскажите бюджет?\nКлиент: не понял вопрос''','Подскажите бюджет?') is False
