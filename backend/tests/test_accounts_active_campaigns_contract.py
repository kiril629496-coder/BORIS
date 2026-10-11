from pathlib import Path

ROOT = Path("/root/BORIS")
ACCOUNTS = ROOT / "backend/app/api/accounts.py"
CAMPAIGN_PAGE = ROOT / "frontend/app/dashboard/campaign/page.tsx"


def test_active_campaign_summary_is_cross_account_read_only_and_hides_internal_fixtures():
    src = ACCOUNTS.read_text(encoding="utf-8")
    assert "@router.get(\"/active-campaigns\")" in src
    assert "BackgroundJob" in src
    assert "job_execution_clock" in src
    assert "campaign_id" in src
    assert "qa|test|demo|fixture|sandbox|mock" in src
    assert "active_statuses" in src
    assert "campaign_prepare" in src
    assert "classify_campaign_runtime_health" in src
    assert "health_state" in src or "**health" in src
    assert "waiting_ratio" in (ROOT / "backend/app/services/campaign_runtime_health.py").read_text(encoding="utf-8")
    assert "no_progress_seconds" in (ROOT / "backend/app/services/campaign_runtime_health.py").read_text(encoding="utf-8")
    assert "sla_state" in (ROOT / "backend/app/services/campaign_runtime_health.py").read_text(encoding="utf-8")


def test_campaign_overview_has_one_global_active_request_and_account_navigation():
    src = CAMPAIGN_PAGE.read_text(encoding="utf-8")
    assert "/api/accounts/active-campaigns" in src
    assert "campaign-global-active" in src
    assert "globalActiveCampaigns" in src
    assert "setSelectedAccount(aid)" in src
    assert "duration_active_seconds" in src
    assert "duration_waiting_seconds" in src
    assert "health_state" in src
    assert "Есть задержка" in src
    assert "Требует внимания" in src
    assert "healthReasons[0]" in src
    assert "Активные кампании по всем аккаунтам" in src
