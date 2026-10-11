from app.api import messenger

def test_planeta_project_parameters_bypass_memory_lookup():
    assert messenger._mop_planeta_project_parameter_update(
        "Слева первый шкаф обязательно 800мм. Мойка есть уже. Плита 600мм от правого края. Остальное не важно"
    ) is True
    assert messenger._mop_planeta_project_parameter_update("3 метра в длину и ширина 6 метров") is True
    assert messenger._mop_planeta_project_parameter_update("Есть ли это в наличии сейчас?") is False
    assert messenger._mop_planeta_project_parameter_update("Сколько будет стоить шкаф 800мм?") is False

def test_known_kitchen_type_blocks_redundant_product_type_question():
    history = (
        "Клиент: Здравствуйте, кухонный гарнитур угловой интересует\n"
        "Мы: Подскажите город.\n"
        "Клиент: Ижевск\n"
        "Мы: Есть примерные размеры?\n"
        "Клиент: 175 на 190"
    )
    problems = messenger._mop_output_policy_violations(
        "Какой тип мебели нужен?", history
    )
    assert "repeated_answered_question:product_type" in problems

def test_unknown_product_type_question_is_allowed_when_client_never_named_product():
    history = "Клиент: Здравствуйте, хочу заказать проект"
    problems = messenger._mop_output_policy_violations(
        "Какой тип мебели нужен?", history
    )
    assert "repeated_answered_question:product_type" not in problems
