from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import late_day_spend_controller as late
from app.services.marketing_money_policy import pacing_budget_brake_policy


def test_daily_budget_pacing_contract_constants():
    assert late.BOOST_START_HOUR == 8
    assert late.PACING_MIN_DAILY_RATIO == 0.95
    assert late.PACING_TARGET_DAILY_RATIO == 0.98
    assert late.MAX_ITEMS_PER_ACCOUNT_CYCLE == 2
    assert late.MAX_PACING_ITEMS_PER_DAY == 2


def test_pacing_ratio_targets_98_not_over_100():
    msk = ZoneInfo('Europe/Moscow')
    noon = late._pacing_ratio(datetime(2026, 9, 14, 12, 0, tzinfo=msk))
    end = late._pacing_ratio(datetime(2026, 9, 14, 23, 59, 59, tzinfo=msk))
    assert 0.48 <= noon <= 0.50
    assert 0.979 <= end <= 0.98
    assert end < 1.0


def test_exploration_is_bounded_and_proven_loser_is_excluded():
    advice = {'recommendations': {'watching': [
        {'item_id': 1, 'bid_rub': 10, 'promotion_active': True,
         'contacts_7d': 0, 'views_7d': 5, 'contacts': 0},
        {'item_id': 2, 'bid_rub': 10, 'promotion_active': True,
         'contacts_7d': 0, 'views_7d': 25, 'contacts': 0},
    ]}}
    pool = late._eligible_pool(advice, allow_exploration=True)
    assert [x['id'] for x in pool] == [1]
    assert pool[0]['pacing_lane'] == 'exploration'


def test_negative_learning_exclusion_is_binding():
    advice = {'recommendations': {'raise': [
        {'item_id': 11, 'bid_rub': 20, 'promotion_active': True,
         'contacts_7d': 3, 'views_7d': 10, 'contacts': 1},
        {'item_id': 12, 'bid_rub': 20, 'promotion_active': True,
         'contacts_7d': 2, 'views_7d': 10, 'contacts': 1},
    ]}}
    pool = late._eligible_pool(advice, winner_ids={11, 12}, allow_exploration=True, exclude_ids={11})
    assert [x['id'] for x in pool] == [12]


def test_pacing_brake_keeps_small_provider_reserve():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    policy = pacing_budget_brake_policy(db, 'qa', 3000, 1000)
    assert 0.95 <= policy['cutoff_ratio'] <= 0.98
    assert policy['target_ratio'] == 0.98
    assert policy['minimum_acceptable_ratio'] == 0.95


def test_cpx_has_separate_pacing_money_guard_and_learning_lane():
    cpx = Path('app/api/cpx_advisor.py').read_text(encoding='utf-8')
    autonomy = Path('app/services/autonomy.py').read_text(encoding='utf-8')
    assert 'DAILY_BUDGET_PACING_MONEY_GUARD_V1' in cpx
    assert 'DAILY_BUDGET_PACING_SEPARATE_LEARNING_V1' in cpx
    assert 'and not _late_day_catchup' in cpx
    assert '_check_raise_allowed(account_id, source=ctx.get("source"))' in autonomy
