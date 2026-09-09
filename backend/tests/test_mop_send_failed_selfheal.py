import inspect

from app.api import messenger


def test_send_failed_policy_never_retries_external_access_or_tariff_errors():
    for err in (
        "Avito 403 — чат недоступен",
        "Avito 402 — нужна подписка API мессенджера",
        "Forbidden: no access",
        "subscription required",
    ):
        assert messenger._mop_send_failed_recovery_decision(
            send_error=err,
            failure_count=1,
            auto_send=True,
            owner_enabled=True,
            binding_enabled=True,
            schedule_active=True,
            entitlement_active=True,
        ) == "skip_external"


def test_send_failed_policy_requires_full_autosend_entitlement():
    kwargs = dict(
        send_error="timeout",
        failure_count=1,
        auto_send=True,
        owner_enabled=True,
        binding_enabled=True,
        schedule_active=True,
        entitlement_active=True,
    )
    for key in ("auto_send", "owner_enabled", "binding_enabled", "schedule_active", "entitlement_active"):
        case = dict(kwargs)
        case[key] = False
        assert messenger._mop_send_failed_recovery_decision(**case) == "skip_policy"


def test_send_failed_policy_is_bounded():
    base = dict(
        send_error="network timeout",
        auto_send=True,
        owner_enabled=True,
        binding_enabled=True,
        schedule_active=True,
        entitlement_active=True,
    )
    assert messenger._mop_send_failed_recovery_decision(
        failure_count=1, **base
    ) == "retry"
    assert messenger._mop_send_failed_recovery_decision(
        failure_count=2, **base
    ) == "retry"
    assert messenger._mop_send_failed_recovery_decision(
        failure_count=3, **base
    ) == "handoff"


def test_recovery_uses_canonical_do_send_and_is_wired_into_messenger_owner_loop():
    helper = inspect.getsource(messenger._recover_send_failed_autosend)
    loop = inspect.getsource(messenger._messenger_poll_loop)
    assert "_mc.do_send(" in helper
    assert "send_message(" not in helper
    assert "MOP_SEND_FAILED_SELFHEAL_V1" in helper
    assert "_recover_send_failed_autosend(active_mop_accounts" in loop
    assert '"mop_send_failed_selfheal"' in loop


def test_empty_active_set_is_noop():
    result = messenger._recover_send_failed_autosend(set(), limit=10)
    assert result["checked"] == 0
    assert result["retried"] == 0
    assert result["recovered"] == 0
