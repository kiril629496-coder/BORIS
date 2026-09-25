import datetime as dt

import boris_guardian as guardian


def _health(stage, state='ok', minutes_ago=5):
    now=dt.datetime.now(dt.timezone.utc)
    return {
        'state': state,
        'stage': stage,
        'checked_at': (now-dt.timedelta(minutes=minutes_ago)).isoformat(),
        'reason': 'test',
    }, now


def test_effect_resolve_is_real_nonmoney_progress():
    health,now=_health('non_money:effect_resolve')
    assert guardian._marketer_fresh_meaningful_nonmoney(health,now=now)


def test_proposal_repair_scheduled_is_real_nonmoney_progress():
    health,now=_health('non_money:proposal_repair_scheduled')
    assert guardian._marketer_fresh_meaningful_nonmoney(health,now=now)


def test_no_action_is_not_progress():
    health,now=_health('non_money:no_action')
    assert not guardian._marketer_fresh_meaningful_nonmoney(health,now=now)


def test_deferred_state_is_not_successful_nonmoney_progress():
    health,now=_health('non_money:effect_resolve',state='deferred')
    assert not guardian._marketer_fresh_meaningful_nonmoney(health,now=now)


def test_stale_nonmoney_is_not_current_progress():
    health,now=_health('non_money:effect_resolve',minutes_ago=120)
    assert not guardian._marketer_fresh_meaningful_nonmoney(health,now=now)


def test_owner_alert_bullets_are_deduplicated_stably():
    assert guardian._unique_owner_alert_items(['x', 'x', '', 'y', 'x']) == ['x', 'y']


def test_deferred_unknown_stage_with_future_next_check_is_expected_wait():
    now=dt.datetime.now(dt.timezone.utc)
    health={
        'state':'deferred',
        'stage':'non_money_cpl_content_fallback',
        'reason':'non_money_money_mutation_fence',
        'checked_at':(now-dt.timedelta(minutes=2)).isoformat(),
        'next_check_at':(now+dt.timedelta(minutes=58)).isoformat(),
    }
    assert guardian._marketer_fresh_expected_wait(health,now=now)


def test_unknown_deferred_stage_without_retry_contract_is_not_expected_wait():
    now=dt.datetime.now(dt.timezone.utc)
    health={
        'state':'deferred',
        'stage':'unknown_future_stage',
        'reason':'something deferred',
        'checked_at':(now-dt.timedelta(minutes=2)).isoformat(),
    }
    assert not guardian._marketer_fresh_expected_wait(health,now=now)


def test_owner_warning_four_duplicate_events_debounce_to_one_send():
    state={}
    duplicated=['одно и то же временное ограничение'] * 4
    first=guardian._owner_warning_transition(state,duplicated,threshold=3)
    second=guardian._owner_warning_transition(state,duplicated,threshold=3)
    third=guardian._owner_warning_transition(state,duplicated,threshold=3)
    fourth=guardian._owner_warning_transition(state,duplicated,threshold=3)
    assert first['warnings'] == ['одно и то же временное ограничение']
    assert [first['action'],second['action'],third['action'],fourth['action']] == ['pending','pending','send','steady']
    assert state['last_warning_sig'] == 'одно и то же временное ограничение'


def test_owner_warning_recovery_is_emitted_once():
    state={'last_warning_sig':'x','pending_warning_sig':'','pending_warning_count':0}
    first=guardian._owner_warning_transition(state,[],threshold=3)
    second=guardian._owner_warning_transition(state,[],threshold=3)
    assert first['action'] == 'recovery'
    assert second['action'] == 'none'
    assert state['last_warning_sig'] == ''


def test_internal_marketer_retry_never_becomes_owner_warning():
    state={}
    retry='ИИ-маркетолог: поклиентский retry требуется: account: KPI 0/3'
    result=guardian._owner_warning_transition(state,[retry,retry,retry,retry],threshold=3)
    assert result['action'] == 'none'
    assert result['warnings'] == []
    assert state.get('pending_warning_count') == 0
