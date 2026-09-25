from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_first_bid_uses_owner_standing_rule_then_later_raises_use_full_budget_guard():
    advisor=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
    autonomy=(ROOT/'app/services/autonomy.py').read_text(encoding='utf-8')
    assert 'source=\'new_feed_launch\'' in advisor or 'source="new_feed_launch"' in advisor
    assert 'FIRST_ACTIVATION_ONLY_V1' in advisor
    # Current production policy is stricter than the old standing-rule marker:
    # first activation requires explicit owner-authorized daily budget provenance.
    assert 'NEW_FEED_LAUNCH_EXPLICIT_BUDGET_V3' in advisor
    assert 'MONEY_BUDGET_OWNER_PROVENANCE_GUARD_V1' in advisor
    # All raises, including launch, now use the canonical full money guard.
    launch=autonomy[autonomy.index('MONEY_GUARD_ALL_RAISES_V3'):]
    assert 'check_raise_allowed as _check_raise_allowed' in launch
    assert 'blocked_money_guard_unavailable' in launch
    assert 'blocked_insufficient_balance' in launch
    assert 'daily_budget_enforcement"] = "FULL"' in launch


def test_first_bid_never_exceeds_account_hard_cap():
    autonomy=(ROOT/'app/services/autonomy.py').read_text(encoding='utf-8')
    advisor=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
    assert 'NEW_FEED_FIRST_BID_ABSOLUTE_HARD_CAP_V3' in autonomy
    block=autonomy[autonomy.index('NEW_FEED_FIRST_BID_ABSOLUTE_HARD_CAP_V3'):]
    assert 'blocked_bid_above_max' in block
    assert 'launch_floor_over_normal_cap' not in block
    assert 'NEW_FEED_FIRST_BID_HARD_CAP_CLAMP_V3' in advisor



def test_removed_promotion_is_not_reactivated_as_new():
    advisor=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
    block=advisor[advisor.index('FIRST_ACTIVATION_ONLY_V1'):advisor.index('All DB-derived selection data')]
    assert 'cpx_applied' in block
    assert 'ever_raised' in block
    assert 'no_first_activation_candidates' in block


def test_rollback_has_bounded_snapshot_selfheal():
    avito=(ROOT/'app/api/avito.py').read_text(encoding='utf-8')
    assert 'ROLLBACK_SNAPSHOT_SELFHEAL_V1' in avito
    block=avito[avito.index('ROLLBACK_SNAPSHOT_SELFHEAL_V1'):avito.index('STALE_ROLLBACK_AUTORETRY_V1')]
    assert '_kpi_latest_live_snapshot_item' in block
    assert '_retry_count < 3' in block
    assert 'stale_snapshot_mismatch_autoretry' in block
    assert 'proof_source' in block


def test_runner_escalates_first_bid_failures():
    runner=(ROOT/'kpi_goal_runner.py').read_text(encoding='utf-8')
    assert 'FIRST_BID_INCIDENT' in runner
    assert 'new_feed_first_bid' in runner
    assert 'external_wait' in runner


def test_runner_prioritizes_existing_lifecycle_before_new_feed_money_work():
    runner=(ROOT/'kpi_goal_runner.py').read_text(encoding='utf-8')
    assert 'LIFECYCLE_PRIORITY_V1' in runner
    lifecycle=runner.index('LIFECYCLE_PRIORITY_V1')
    bootstrap=runner.index('Feed promotion bootstrap applies')
    assert lifecycle < bootstrap
    block=runner[lifecycle:bootstrap]
    assert 'has_active_obligation' in block
    assert 'call_tick(client, account_id, allow_stale_non_money=True)' in block
    assert 'LIFECYCLE_INCIDENT' in block
    assert '"blocked", "error", "failed", "provider_stalled"' in block
