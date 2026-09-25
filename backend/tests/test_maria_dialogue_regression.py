from app.api import messenger as m


def test_maria_repeated_location_question_is_blocked_after_answer():
    history = "\n".join([
        "Мы: Хорошо, давайте тогда здесь. Подскажите, в каком районе или у какого метро рассматриваете квартиру?",
        "Клиент: М Печатники",
        "Клиент: 2 или 3 ка",
        "Мы: Принял, Печатники, 2- или 3-комнатная. Подскажите, в рамках какого бюджета рассматриваете покупку?",
        "Клиент: максимум 18",
        "Клиент: Желательно только печатники",
    ])
    problems = m._mop_output_policy_violations(
        "Какой район или метро рассматриваете?", history
    )
    assert "repeated_answered_question:location" in problems


def test_maria_chat_preference_blocks_phone_and_whatsapp_push():
    history = "\n".join([
        "Клиент: Здравствуйте вы занимаетесь военно-семейной ипотекой?",
        "Клиент: Нет, мне удобнее чат",
    ])
    assert m._mop_client_prefers_chat(history) is True
    problems = m._mop_output_policy_violations(
        "Напишите номер телефона или перейдём в WhatsApp.", history
    )
    assert "client_prefers_chat" in problems


def test_avito_assistant_generic_answer_does_not_silence_specific_mortgage_question():
    assert m._mop_avito_assistant_reply_is_sufficient(
        "Здравствуйте вы занимаетесь военно-семейной ипотекой?",
        "[Системное сообщение] Ассистент Авито ответил: Исполнитель имеет опыт 10 лет. Хотите записаться на консультацию?",
    ) is False
    assert m._mop_avito_assistant_reply_is_sufficient(
        "Да, хочу",
        "[Системное сообщение] Ассистент Авито ответил: По какому номеру с вами можно связаться?",
    ) is True
    assert m._mop_avito_assistant_reply_is_sufficient(
        "Нет, мне удобнее чат",
        "[Системное сообщение] Ассистент Авито ответил: Принято",
    ) is False


def test_vendor_spam_and_private_seller_search_are_not_buyer_leads():
    assert m._mop_nonbuyer_reason(
        "Мария, добрый день! Интересует ли вас доп заработок по продуктам страхования недвижимости?"
    ) == "vendor_solicitation"
    assert m._mop_nonbuyer_reason(
        "Делаем справки по форме банка. Справка 3000 руб, трудовая 5000 руб. Пишите в ватсап или телеграм."
    ) == "vendor_solicitation"
    assert m._mop_nonbuyer_reason(
        "Задача здесь - найти имеющих активную позицию частников! Поэтому спрашиваю компания или нет."
    ) == "searching_private_sellers"
    assert m._mop_nonbuyer_reason(
        "Здравствуйте, хочу подобрать квартиру в Печатниках до 18 млн."
    ) is None


def test_system_assistant_message_is_not_used_as_client_question():
    chat = {
        "last_message": {
            "direction": "in",
            "type": "system",
            "content": {"text": "[Системное сообщение] Ассистент Авито ответил: Принято"},
        },
        "messages": [
            {"direction": "in", "type": "text", "content": {"text": "Нет, мне удобнее чат"}},
            {"direction": "in", "type": "system", "content": {"text": "[Системное сообщение] Ассистент Авито ответил: Принято"}},
        ],
    }
    assert m._extract_question(chat) == "Нет, мне удобнее чат"
