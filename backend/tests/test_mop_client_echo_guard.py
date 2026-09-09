from app.api import messenger


def test_exact_client_echo_is_blocked():
    history = "Клиент: Здравствуйте, хочу подобрать квартиру в Москве. С чего лучше начать?"
    reply = "Здравствуйте, хочу подобрать квартиру в Москве. С чего лучше начать?"
    problems = messenger._mop_output_policy_violations(reply, history)
    assert "client_echo" in problems


def test_near_contained_echo_is_blocked():
    history = "Клиент: Хочу подобрать квартиру в Москве для семьи"
    reply = "Хочу подобрать квартиру в Москве для семьи."
    problems = messenger._mop_output_policy_violations(reply, history)
    assert "client_echo" in problems


def test_normal_manager_reply_is_not_echo():
    history = "Клиент: Хочу подобрать квартиру в Москве для семьи"
    reply = "Начнём с основных параметров. Какой бюджет рассматриваете?"
    problems = messenger._mop_output_policy_violations(reply, history)
    assert "client_echo" not in problems


def test_maria_real_estate_echo_repair_is_useful():
    history = "Клиент: Здравствуйте, хочу подобрать квартиру в Москве. С чего лучше начать?"
    reply = messenger._mop_echo_repair_reply(history)
    assert "бюджет" in reply.lower()
    assert "телефон" not in reply.lower()
