from pathlib import Path

AVITO = Path(__file__).resolve().parents[1] / "app" / "api" / "avito.py"


def _segment(source: str, start: str, end: str) -> str:
    assert start in source
    assert end in source
    return source.split(start, 1)[1].split(end, 1)[0]


def test_legacy_republish_is_additive_and_never_expires_source():
    source = AVITO.read_text(encoding="utf-8")
    segment = _segment(source, "def republish_apply", "class KpiSettingsRequest")
    assert "OWNER_GROWTH_NO_SHRINK_V1" in segment
    assert 'it["date_end"] =' not in segment
    assert '"removed": 0' in segment
    assert '"policy": "additive_no_shrink"' in segment
    assert "no_safe_additive_source" in segment


def test_legacy_autonomous_kpi_delegates_to_canonical_goal_runner():
    source = AVITO.read_text(encoding="utf-8")
    segment = _segment(source, "def kpi_autopilot_run", '@router.get("/kpi_check")')
    assert "KPI_LEGACY_AUTOPILOT_CANONICAL_DELEGATE_V1" in segment
    assert 'if autopilot_mode in {"always_auto", "goal_auto"}' in segment
    assert "canonical = kpi_goal_tick(account_id)" in segment


def test_kpi_gap_policy_never_recommends_shrinking_inventory():
    source = AVITO.read_text(encoding="utf-8")
    segment = _segment(source, "def kpi_check", "@router.")
    assert "canonical_goal_recovery" in segment
    assert "не уменьшая активный рекламный пул" in segment
    assert "BORIS сохраняет действующие объявления" in segment
    assert "проверить и снять неэффективные" not in segment
    assert "а не увеличивать количество" not in segment


def test_kpi_daily_controls_use_moscow_business_day_not_server_utc_date():
    source = AVITO.read_text(encoding="utf-8")
    assert "KPI_MOSCOW_DAY_CLOCK_V1" in source
    assert "return marketing_today()" in source
    forbidden = (
        "_date_kc.today()", "_date_krc.today()", "_date_kbl.today()",
        "_date_kbr.today()", "_date_kbc.today()", "_date_kbrl.today()",
        "_date_kbs.today()", "_date_kair.today()", "_date_kaic.today()",
        "_date_kairl.today()", "_date_kes.today()", "_date_inv.today()",
        "_date_identity.today()", "_date_krg.today()",
        "_date_title_cohort.today()", "_date_kd.today()", "_date_amw.today()",
    )
    assert not [token for token in forbidden if token in source]


def test_inventory_expansion_is_wired_prepare_only_and_not_unsupported():
    source = AVITO.read_text(encoding="utf-8")
    handler = _segment(source, "def _kpi_exec_expand_inventory", "def _kpi_executor_registry")
    registry = _segment(source, "def _kpi_executor_registry", "def _kpi_exec_increase_reach")
    goal = _segment(source, "def kpi_goal_tick", '@router.get("/kpi_goal_state")')
    assert "KPI_INVENTORY_EXPANSION_PREPARE_V1" in handler
    assert "avito.publish_feed_additive" in handler
    assert '"changed_feed": False' in handler
    assert "feed_row.value" not in handler
    assert '"expand_inventory": {' in registry
    assert '"handler": "_kpi_exec_expand_inventory"' in registry
    assert "KPI_GOAL_INVENTORY_PREPARE_WIRE_V1" in goal
    assert '"stage": "inventory_expand_prepare"' in goal
