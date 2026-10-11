from app.api.messenger import _mop_client_visible_text, _normalize_mop_structured
from app.services.sales_ai_router import _json_value


def _wrapped(reply="Чистый ответ клиенту"):
    prefix = "**JSON-" + "ОТВЕТ:** "
    body = (
        '{ "reply_text": "' + reply +
        '", "human_handoff": false, "handoff_reason": null }'
    )
    return prefix + body


def test_prefixed_json_is_unwrapped_before_client_send():
    raw = _wrapped()
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Чистый ответ клиенту"


def test_router_accepts_prefixed_json_object():
    obj = _json_value(_wrapped())
    assert obj["reply_text"] == "Чистый ответ клиенту"
    assert obj["human_handoff"] is False


def test_mop_normalizer_never_returns_service_envelope_as_reply():
    reply, analysis, error = _normalize_mop_structured(_wrapped())
    assert reply == "Чистый ответ клиенту"
    assert "JSON-" not in reply
    assert '"reply_text"' not in reply


def test_unparseable_service_envelope_fails_closed():
    raw = ("**JSON-" + "ОТВЕТ:** ") + '{ "human_handoff": false }'
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == ""


def test_markdown_service_fields_are_unwrapped():
    raw = (
        "**reply_text**:«Подскажите, актуален ли расчёт?» "
        "**human_handoff**:false **handoff_reason**:null"
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Подскажите, актуален ли расчёт?"


def test_markdown_italic_service_fields_are_unwrapped():
    raw = (
        "**reply_text**:*«Нужен ли монтаж?»* "
        "**human_handoff**:*false* **handoff_reason**:*null*"
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Нужен ли монтаж?"


def test_plain_text_with_metadata_suffix_is_cleaned():
    raw = (
        "Когда вам удобно организовать замер? "
        "**human_handoff**: false **handoff_reason**: null"
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Когда вам удобно организовать замер?"


def test_training_messages_supply_latest_client_question():
    from app.api.messenger import _extract_question
    chat = {
        "_training_messages": [
            {"role": "client", "text": "Здравствуйте"},
            {"role": "mop", "text": "Добрый день"},
            {"role": "client", "text": "Сколько будет стоить окно?"},
        ]
    }
    assert _extract_question(chat) == "Сколько будет стоить окно?"


def test_loose_markdown_reply_text_is_unwrapped():
    raw = (
        '**reply_text**:«Подскажите, пожалуйста, актуально ли вам рассчитать стоимость окна?» '
        '**human_handoff**:false'
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Подскажите, пожалуйста, актуально ли вам рассчитать стоимость окна?"


def test_loose_markdown_reply_text_with_italic_metadata_is_unwrapped():
    raw = (
        '**reply_text**:*«Чтобы точнее рассчитать стоимость, нужен ли вам монтаж?»* '
        '**human_handoff**:*false* **handoff_reason**:*null*'
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Чтобы точнее рассчитать стоимость, нужен ли вам монтаж?"


def test_plain_text_followed_by_service_metadata_is_unwrapped():
    raw = (
        'Подскажите, пожалуйста, когда вам удобнее организовать замер?» '
        '**human_handoff**: false **handoff_reason**: null'
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Подскажите, пожалуйста, когда вам удобнее организовать замер?"


def test_short_style_reactivation_price_followup_is_compact():
    from app.reactivation_draft import _short_style_reactivation_reply
    text = _short_style_reactivation_reply("price_requested")
    assert text == "Подскажите, расчёт ещё актуален? Если да, продолжим."
    assert len(text) < 70


def test_training_messages_supply_last_client_question():
    from app.api.messenger import _extract_question
    chat = {
        "_training_messages": [
            {"role": "client", "text": "Сколько стоит окно?"},
            {"role": "mop", "text": "Вам с монтажом?"},
            {"role": "client", "text": "Без монтажа"},
        ]
    }
    assert _extract_question(chat) == "Без монтажа"


def test_loose_markup_with_invisible_unicode_is_cleaned():
    raw = (
        "**reply_text**:️«Подскажите, расчёт ещё актуален?» "
        "**human_handoff**:‌false **handoff_reason**:‌null"
    )
    text, had = _mop_client_visible_text(raw)
    assert had is True
    assert text == "Подскажите, расчёт ещё актуален?"


def test_short_style_reactivation_price_reply_is_compact():
    from app.reactivation_draft import _short_style_reactivation_reply
    text = _short_style_reactivation_reply("price_requested")
    assert len(text) <= 70
    assert "расчёт" in text.lower()
    assert "смет" not in text.lower()
    assert "хотим уточнить" not in text.lower()


def test_short_style_reactivation_no_reply_is_compact_and_static():
    from app.reactivation_draft import _short_style_reactivation_reply
    text = _short_style_reactivation_reply("no_reply")
    low = text.lower()
    assert len(text) <= 70
    assert "актуал" in low
    assert "менеджер" not in low
    assert "5–7" not in text
    assert "октябр" not in low
