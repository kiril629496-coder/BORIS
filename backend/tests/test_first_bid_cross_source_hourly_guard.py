from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / 'migrations' / '043_cpx_autonomous_first_bid_cross_source_guard.sql').read_text()


def test_cross_source_first_bid_guard_is_atomic_and_hourly():
    assert 'CPX_AUTONOMOUS_FIRST_BID_CROSS_SOURCE_HOURLY_V1' in SQL
    assert "pg_advisory_xact_lock(hashtext('cpx-action-cap:' || NEW.account_id))" in SQL
    assert "date_trunc('hour',r.created_at)=date_trunc('hour',now())" in SQL
    assert "blocked_first_bid_cross_source_hourly_atomic" in SQL


def test_guard_covers_autonomous_zero_bid_lanes_but_excludes_budget_resume():
    for source in (
        'feed20', 'account_low_views_ramp', 'reach_rescue', 'cpl_headroom_push',
        'kpi_behind_push', 'new_feed_hourly_ramp', 'profitable_day_push',
        'late_day_spend_catchup', 'kpi_gap_boost',
    ):
        assert f"'{source}'" in SQL
    guard = SQL.split('CPX_AUTONOMOUS_FIRST_BID_CROSS_SOURCE_HOURLY_V1', 1)[1].split('-- CPX_MOSCOW_MONEY_DAY_V1', 1)[0]
    source_list = guard.split("COALESCE(NEW.source,'') IN (", 1)[1].split(") THEN", 1)[0]
    assert "'budget_resume'" not in source_list
    assert "NEW.action='raise'" in guard
    assert 'COALESCE(NEW.old_bid_penny,0)=0' in guard
