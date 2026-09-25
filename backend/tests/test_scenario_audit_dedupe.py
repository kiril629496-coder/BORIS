from app.api.avito import _audit_should_append


def test_exact_consecutive_scenario_step_is_suppressed():
    log = [{
        "ts": "2026-09-09T20:00:00",
        "actor": "boris",
        "action": "scenario_step",
        "details": "Состояние не изменилось",
    }]
    entry = {
        "ts": "2026-09-09T20:30:00",
        "actor": "boris",
        "action": "scenario_step",
        "details": "Состояние не изменилось",
    }
    assert _audit_should_append(log, entry) is False


def test_changed_scenario_step_is_preserved():
    log = [{
        "actor": "boris",
        "action": "scenario_step",
        "details": "Ждём результат",
    }]
    entry = {
        "actor": "boris",
        "action": "scenario_step",
        "details": "Публикация подтверждена",
    }
    assert _audit_should_append(log, entry) is True


def test_same_details_for_real_business_action_are_not_suppressed():
    log = [{
        "actor": "boris_auto",
        "action": "cpx_apply_bid",
        "details": "ставка изменена",
    }]
    entry = {
        "actor": "boris_auto",
        "action": "cpx_apply_bid",
        "details": "ставка изменена",
    }
    assert _audit_should_append(log, entry) is True
