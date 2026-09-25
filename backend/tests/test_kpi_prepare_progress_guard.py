from pathlib import Path

import boris_guardian


ROOT = Path('/root/BORIS/backend')


def test_prepare_stall_counts_hourly_generation_not_fast_guardian_ticks():
    state = {}
    first = boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g1', progress_token='1/15', hard_after=4
    )
    repeat = boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g1', progress_token='1/15', hard_after=4
    )
    next_hour = boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g2', progress_token='1/15', hard_after=4
    )
    assert first['checks'] == 1
    assert repeat['checks'] == 1
    assert next_hour['checks'] == 2
    assert not next_hour['stalled']


def test_real_proposal_progress_resets_no_progress_counter():
    state = {}
    boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g1', progress_token='1/15', hard_after=4
    )
    boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g2', progress_token='1/15', hard_after=4
    )
    boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g3', progress_token='1/15', hard_after=4
    )
    progressed = boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g4', progress_token='2/15', hard_after=4
    )
    assert progressed['progressed'] is True
    assert progressed['checks'] == 1
    assert progressed['stalled'] is False


def test_parallel_content_disjointness_uses_pending_proposals_only():
    source = (ROOT / 'app/api/avito.py').read_text(encoding='utf-8')
    assert 'KPI_PARALLEL_PENDING_PROPOSAL_SCOPE_V1' in source
    start = source.index('KPI_PARALLEL_PENDING_PROPOSAL_SCOPE_V1')
    block = source[start:start+2200]
    assert '_pending_proposals' in block
    assert 'not p.get("apply_status")' in block
    assert 'str(p.get("item_id"))' in block
    assert '_active_iid in _pitems' in block


def test_fresh_prerequisite_stage_suppresses_false_prepare_stall_only_temporarily():
    now = boris_guardian.dt.datetime(2026, 9, 11, 14, 15, tzinfo=boris_guardian.dt.timezone.utc)
    fresh = {
        'stage': 'publish',
        'checked_at': '2026-09-11T14:10:54+00:00',
    }
    stale = {
        'stage': 'publish',
        'checked_at': '2026-09-11T12:00:00+00:00',
    }
    same_queue_stage = {
        'stage': 'proposal_apply',
        'checked_at': '2026-09-11T14:10:54+00:00',
    }
    assert boris_guardian._marketer_prepare_waits_on_fresh_prerequisite(fresh, now=now)
    assert not boris_guardian._marketer_prepare_waits_on_fresh_prerequisite(stale, now=now)
    assert not boris_guardian._marketer_prepare_waits_on_fresh_prerequisite(same_queue_stage, now=now)


def test_prerequisite_wait_clears_old_stall_debt_before_later_recheck():
    state = {}
    boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g1', progress_token='7/15', hard_after=4
    )
    boris_guardian._marketer_prepare_stall_tick(
        state, 'acc', 'op', 'apply_in_progress', generation='g2', progress_token='7/15', hard_after=4
    )
    assert any(k.startswith('marketer_prepare_pending:acc:') for k in state)
    boris_guardian._marketer_prepare_stall_clear(state, 'acc')
    assert not any(k.startswith('marketer_prepare_pending:acc:') for k in state)


def test_fresh_mapping_wait_is_expected_but_stale_mapping_wait_escalates():
    now = boris_guardian.dt.datetime(2026, 9, 11, 14, 30, tzinfo=boris_guardian.dt.timezone.utc)
    fresh = {
        'state': 'deferred',
        'stage': 'mapping_wait',
        'reason': 'canonical mapping retry owned by BORIS',
        'checked_at': '2026-09-11T14:20:00+00:00',
    }
    stale = dict(fresh, checked_at='2026-09-11T12:00:00+00:00')
    generic = dict(fresh, stage='non_money_cpl_content_fallback')
    assert boris_guardian._marketer_fresh_expected_wait(fresh, now=now)
    assert not boris_guardian._marketer_fresh_expected_wait(stale, now=now)
    assert not boris_guardian._marketer_fresh_expected_wait(generic, now=now)
