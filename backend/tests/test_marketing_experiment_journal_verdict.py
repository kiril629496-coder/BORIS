from app.services.marketing_experiment_journal import _performance_verdict


def _v(**kw):
    base=dict(
        entitlement={"state":"active"}, target=3, business_leads=0, views=10,
        spend=100, cpl=None, red_cpl=600, budget=0, budget_authorized=False,
        content_only=False, balance_health={}, marketer_health={}, title_health={},
    )
    base.update(kw)
    return _performance_verdict(**base)


def test_zero_unauthorized_budget_precedes_wallet_warning():
    out=_v(balance_health={"owner_action_required":True,"reason":"avito_balance_below_one_red_cpl"})
    assert out["status"]=="KPI_BLOCKED_EXTERNAL"
    assert out["blocker"]=="daily_budget_not_authorized"
    assert out["owner_action_required"] is True
    assert out["next_action"]=="authorize_daily_budget_then_auto_resume"


def test_real_wallet_block_stays_wallet_action_when_budget_authorized():
    out=_v(budget=3000,budget_authorized=True,
           balance_health={"owner_action_required":True,"reason":"avito_balance_below_one_red_cpl"})
    assert out["blocker"]=="avito_balance_below_one_red_cpl"
    assert out["next_action"]=="fund_external_avito_wallet_then_auto_resume"


def test_positive_unproven_budget_plus_low_wallet_surfaces_both_owner_steps():
    out=_v(budget=1000,budget_authorized=False,
           balance_health={"owner_action_required":True,"reason":"avito_balance_below_one_red_cpl"})
    assert out["status"]=="KPI_BLOCKED_EXTERNAL"
    assert out["blocker"]=="daily_budget_owner_provenance_missing_and_avito_balance_low"
    assert out["owner_action_required"] is True
    assert out["next_action"]=="confirm_daily_budget_and_fund_external_avito_wallet_then_auto_resume"


def test_expensive_leads_without_owner_block_remain_autonomous():
    out=_v(budget=1700,budget_authorized=True,business_leads=1,spend=700,cpl=700,
           balance_health={"owner_action_required":False})
    assert out["status"]=="KPI_EXPENSIVE_LEADS"
    assert out["owner_action_required"] is False
    assert out["next_action"]=="cut_losers_reallocate_and_repair_conversion"
