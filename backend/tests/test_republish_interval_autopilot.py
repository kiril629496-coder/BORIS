from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AVITO = ROOT / "backend" / "app" / "api" / "avito.py"
MARKETING = ROOT / "frontend" / "app" / "dashboard" / "marketing" / "page.tsx"
SIDEBAR = ROOT / "frontend" / "app" / "components" / "shell" / "Sidebar.tsx"


def _segment(source: str, start: str, end: str) -> str:
    assert start in source
    assert end in source
    return source.split(start, 1)[1].split(end, 1)[0]


def test_republish_schedule_is_explicit_opt_in_and_bounded():
    source = AVITO.read_text(encoding="utf-8")
    settings = _segment(source, "class RepublishSettingsRequest", '@router.post("/delete_republish_settings")')
    assert "schedule_enabled: bool = False" in settings
    assert "interval_days: int = 7" in settings
    assert "max_items_per_cycle: int = 3" in settings
    assert "min(30, max(3" in settings
    assert "min(5, max(1" in settings


def test_republish_cycle_uses_existing_safe_feed_pipeline():
    source = AVITO.read_text(encoding="utf-8")
    cycle = _segment(source, "def republish_cycle_run", '@router.post("/delete_republish_settings")')
    assert "REPUBLISH_INTERVAL_AUTOPILOT_V1" in cycle
    assert "republish_check(account_id)" in cycle
    assert "republish_apply(RepublishApplyRequest(" in cycle
    assert "feed_send_to_avito(account_id=account_id)" in cycle
    assert '"changed_feed": changed_feed' in cycle
    assert '"changed_avito": changed_avito' in cycle


def test_goal_runner_gives_due_republish_one_transition():
    source = AVITO.read_text(encoding="utf-8")
    goal = _segment(source, "def kpi_goal_tick", '@router.get("/kpi_goal_state")')
    assert "REPUBLISH_INTERVAL_AUTOPILOT_V1" in goal
    assert "republish_cycle_run(account_id)" in goal
    assert '"stage": "republish_cycle"' in goal
    assert goal.index("REPUBLISH_INTERVAL_AUTOPILOT_V1") < goal.index("KPI_ADDITIVE_GROWTH_BEFORE_CONTENT_WAIT_V1")


def test_marketing_ui_exposes_interval_and_action_plan():
    source = MARKETING.read_text(encoding="utf-8")
    assert "Перепубликация объявлений" in source
    assert "Повторный заброс в тот же спрос" in source
    assert 'min={3} max={30}' in source
    assert "/api/avito/republish_settings" in source
    assert "/api/avito/set_republish_settings" in source
    assert "Что BORIS делает каждые" in source


def test_development_link_is_owner_only_in_sidebar():
    source = SIDEBAR.read_text(encoding="utf-8")
    import re, json
    keys = set(json.loads(re.search(r"OWNER_ONLY_NAV_KEYS = new Set\((\[[^;]+?\])\)", source).group(1)))
    assert {"journal", "ux-tester", "reliability"}.issubset(keys)
    assert "group.items.filter(item => platformOwner || !OWNER_ONLY_NAV_KEYS.has(item.key))" in source
    for platform_owner in (False, True):
        for key in ("journal", "ux-tester", "reliability"):
            assert (platform_owner or key not in keys) is platform_owner
