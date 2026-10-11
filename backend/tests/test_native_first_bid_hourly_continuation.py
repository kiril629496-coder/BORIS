from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPX = (ROOT / "app/api/cpx_advisor.py").read_text()


def _block(src, start, end):
    return src[src.index(start):src.index(end)]


def test_native_first_bid_is_hourly_not_daily_cap2():
    block = _block(
        CPX,
        "def _native_provider_probe_first_bid_preflight",
        "def _provider_alias_recent_receipt",
    )
    assert "NATIVE_PROVIDER_PROBE_FIRST_BID_HOURLY_CONTINUATION_V2" in block
    assert "NATIVE_PROVIDER_PROBE_FIRST_BID_DAILY_CAP2_V1" not in block
    assert "_native_daily_cap = 2" not in block
    assert "native_first_bid_daily_cap" not in block
    assert "native_first_bid_unresolved_receipt" in block
    assert "_raise_measurement_cap" in block
    assert "prior_first_activations_today" in block


def test_lowviews_primary_lane_keeps_atomic_hourly_refusal_handling():
    block = _block(
        CPX,
        "def hourly_account_low_views_ramp",
        "def profitable_day_push",
    )
    assert 'source="account_low_views_ramp"' in block
    assert "blocked_first_bid_cross_source_hourly_atomic" in block
    assert "first_bid_cross_source_hourly_limit" in block


def test_money_safety_guards_remain_in_apply_path():
    block = _block(CPX, "def apply_one", "def _balance_unknown")
    assert "daily_budget" in block
    assert "max_cpl" in block
    assert "hard" in block.lower()
    assert "receipt" in block.lower()
