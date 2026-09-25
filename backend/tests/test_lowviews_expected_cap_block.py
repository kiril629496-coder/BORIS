from pathlib import Path


def test_lowviews_atomic_capacity_block_is_normal_policy_result():
    src = Path('/root/BORIS/backend/app/api/cpx_advisor.py').read_text(encoding='utf-8')
    assert 'LOWVIEWS_ATOMIC_CAP_POLICY_BLOCK_V1' in src
    assert 'blocked_first_bid_cross_source_hourly_atomic' in src
    assert 'first_bid_cross_source_hourly_limit' in src
    # Unknown exceptions must still escape instead of being hidden.
    marker = src.index('LOWVIEWS_ATOMIC_CAP_POLICY_BLOCK_V1')
    window = src[marker:marker + 1800]
    assert 'raise' in window
