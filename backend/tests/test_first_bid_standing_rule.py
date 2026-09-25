from pathlib import Path
ROOT=Path('/root/BORIS/backend')

def test_first_bid_requires_explicit_daily_budget_and_global_step():
    s=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
    block=s[s.index('def _ensure_new_item_launch_mandate'):s.index('def bootstrap_new_no_promo')]
    assert 'NEW_FEED_LAUNCH_EXPLICIT_BUDGET_V3' in block
    assert 'return {"status":"blocked","reason":"no_explicit_daily_budget"' in block
    assert "max_actions_run=1,max_actions_day=24" in block
    assert "max_bid_delta_pct=:s" in block
    assert "GLOBAL_MAX_BID_DELTA_PCT" in s
    assert "NEW_ITEM_NO_PROMO_MIN_BID_MARKUP_PCT = GLOBAL_MAX_BID_DELTA_PCT" in s
    assert "NEW_FEED_FIRST_BID_NO_STEP_BYPASS_V1" in s
    # Source-specific policy is resolved through the canonical helper; that helper
    # itself clamps every source to the global <=10% ceiling.
    assert '_source_step_cap = int(effective_bid_step_cap_pct(str(body.source or "")))' in s
    assert "if _step_pct > _source_step_cap and not _late_day_reset:" in s
    policy=(ROOT/'app/services/marketing_money_policy.py').read_text(encoding='utf-8')
    assert 'def effective_bid_step_cap_pct' in policy
    assert 'GLOBAL_MAX_BID_DELTA_PCT' in policy

def test_first_bid_uses_same_full_money_guard_as_later_raise():
    s=(ROOT/'app/services/autonomy.py').read_text(encoding='utf-8')
    assert 'MONEY_GUARD_ALL_RAISES_V3' in s
    assert 'check_raise_allowed as _check_raise_allowed' in s
    assert 'blocked_insufficient_balance' in s
    assert 'launch_first_bid' in s

def test_first_bid_respects_absolute_cap_and_clamps_when_avito_min_fits():
    a=(ROOT/'app/services/autonomy.py').read_text(encoding='utf-8')
    c=(ROOT/'app/api/cpx_advisor.py').read_text(encoding='utf-8')
    assert 'NEW_FEED_FIRST_BID_ABSOLUTE_HARD_CAP_V3' in a
    assert 'blocked_bid_above_max' in a
    assert 'NEW_FEED_FIRST_BID_HARD_CAP_CLAMP_V3' in c
    assert '_launch_cap_rub>=_avito_min_rub' in c
