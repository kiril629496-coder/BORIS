from pathlib import Path


def test_stale_non_money_skips_bid_bootstrap_and_ramp():
    source = (Path(__file__).resolve().parents[1] / "app/api/avito.py").read_text(encoding="utf-8")
    start = source.index("def kpi_goal_tick(account_id: str, allow_stale_non_money: bool = False):")
    end = source.index("\n@router.", start)
    block = source[start:end]

    # Current contract: both stale-fact protection and provider-day freshness
    # converge into one gate. Bid bootstrap/ramp are reachable only when NEW
    # work is allowed and the caller is not in stale non-money mode.
    bootstrap = block.index("bootstrap_new_no_promo_batch(account_id, max_items=5)")
    ramp = block.index("hourly_new_feed_low_views_ramp(account_id, max_items=1)")
    kpi_check = block.index("check = kpi_check(account_id, refresh_live=not allow_stale_non_money)")
    gate_start = block.rfind("if (", 0, bootstrap)
    guarded = block[gate_start:kpi_check]

    assert gate_start < bootstrap < kpi_check
    assert gate_start < ramp < kpi_check
    assert "_funding_new_work_allowed" in guarded
    assert "not allow_stale_non_money" in guarded
    assert "_bid_autopilot_enabled_kgt" in guarded
    assert "not _content_only_placement_kgt" in guarded
    assert "bootstrap_new_no_promo_batch(account_id, max_items=5)" in guarded
    assert "hourly_new_feed_low_views_ramp(account_id, max_items=1)" in guarded


def test_content_only_package_skips_money_and_inventory_growth():
    source = (Path(__file__).resolve().parents[1] / "app/api/avito.py").read_text(encoding="utf-8")
    start = source.index("def kpi_goal_tick(account_id: str, allow_stale_non_money: bool = False):")
    end = source.index("\n@router.", start)
    block = source[start:end]

    assert "if _funding_new_work_allowed and not _content_only_placement_kgt:" in block
    assert "and _bid_autopilot_enabled_kgt" in block
    assert "and not _content_only_placement_kgt" in block
    assert "_kpi_additive_growth_tick(account_id)" in block

    orchestrator_start = source.index("def kpi_orchestrate(")
    orchestrator_end = source.index("\n@router.", orchestrator_start)
    orchestrator = source[orchestrator_start:orchestrator_end]
    assert '"expand_inventory"' in orchestrator
    assert "_content_only_placement_account" in orchestrator
