from app.services import jobs


def test_prepare_only_guard_recognizes_reverse_negation_prompt():
    prompt = (
        "\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e "
        "\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u0442\u044c \u0438 \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c "
        "\u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438 \u0438 \u043c\u0435\u0434\u0438\u0430 \u0432\u043d\u0443\u0442\u0440\u0438 BORIS; "
        "\u0432\u043d\u0435\u0448\u043d\u044e\u044e \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u044e \u0432 Avito "
        "\u043d\u0430 \u044d\u0442\u043e\u043c \u044d\u0442\u0430\u043f\u0435 \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u0442\u044c."
    )
    ff = {"order_contract": {"original_user_prompt": prompt}}
    assert jobs._campaign_owner_prepare_only_contract(ff) is True


def test_prepare_only_guard_prefers_durable_policy():
    ff = {
        "publication_policy": {
            "mode": "prepare_only",
            "automatic_external_write": False,
            "requires_real_owner_activation": True,
        },
        "order_contract": {"original_user_prompt": "ordinary request"},
    }
    assert jobs._campaign_owner_prepare_only_contract(ff) is True


def test_prepare_only_guard_allows_normal_order_without_negative_clause():
    ff = {"order_contract": {"original_user_prompt": "publish after review"}}
    assert jobs._campaign_owner_prepare_only_contract(ff) is False
