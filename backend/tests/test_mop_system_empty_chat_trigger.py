import inspect

from app.api import messenger


def test_avito_system_empty_chat_trigger_recognizes_real_notice():
    assert messenger._is_system_empty_chat_nudge(
        "[Системное сообщение] ✏️ Пользователь создал чат, но пока ничего не написал."
    )
    assert messenger._is_system_empty_chat_nudge(
        "[Системное сообщение] ✏️ Пользователь создал чат, но\u00a0пока ничего не\u00a0написал."
    )


def test_avito_system_empty_chat_trigger_ignores_other_system_notices():
    assert not messenger._is_system_empty_chat_nudge(
        "[Системное сообщение] Покупатель ознакомился со скидкой 2425 ₽ "
        "и теперь может оформить заказ Авито Доставкой."
    )
    assert not messenger._is_system_empty_chat_nudge(
        "[Системное сообщение] Клиент посмотрел номер телефона."
    )
    assert not messenger._is_system_empty_chat_nudge(
        "Пользователь создал чат, но пока ничего не написал."
    )


def test_new_contour_empty_chat_marks_all_terminal_success_results():
    src = inspect.getsource(messenger._process_account_messenger_check)
    assert "_system_empty_chat = _is_system_empty_chat_nudge" in src
    assert '"empty:%s" % chat_id' in src
    for result in (
        "card_sent",
        "auto_sent",
        "auto_sent_handoff",
        "avito_assistant_already_replied",
        "no_reply_required",
    ):
        assert f'"{result}"' in src
    assert '"empty_chat_greeted"' in src
