from pathlib import Path

ROOT = Path("/root/BORIS")
API = ROOT / "backend/app/api/messenger_reports.py"
MAIN = ROOT / "backend/app/main.py"
PAGE = ROOT / "frontend/app/messenger/page.tsx"
REPORTS = ROOT / "frontend/app/messenger/components/ReportsPanel.tsx"
SETTINGS = ROOT / "frontend/app/messenger/components/SettingsPanel.tsx"
CSS = ROOT / "frontend/app/messenger/messenger.module.css"


def test_report_facade_routes_are_scoped_and_legal_gated():
    src = API.read_text(encoding="utf-8")
    assert 'APIRouter(prefix="/api/messenger-reports"' in src
    for route in (
        '@router.get("/summary")',
        '@router.get("/finance")',
        '@router.get("/advertising")',
        '@router.get("/reactivation")',
        '@router.get("/ab-tests")',
        '@router.get("/ads/{item_id}/experiments")',
        '@router.get("/ask")',
        '@router.post("/share")',
    ):
        assert route in src
    assert "require_messenger_legal_user" in src
    assert "messenger_reports_router" in MAIN.read_text(encoding="utf-8")


def test_reports_are_fact_first_and_do_not_refresh_or_mutate_business_modules():
    src = API.read_text(encoding="utf-8")
    for marker in (
        "daily_stats:",
        "guardian_balance_funding_health",
        "reactivation_candidates",
        "reactivation_messages",
        "marketing_experiment_journal:",
    ):
        assert marker in src
    for forbidden in (
        "requests.",
        "httpx.",
        "openai",
        "collect_stats(",
        "reactivation_run",
        "campaign_activate",
    ):
        assert forbidden not in src.lower()


def test_finance_is_fail_closed_and_never_invents_avito_advance():
    src = API.read_text(encoding="utf-8")
    assert '"can_view_finance": bool(overrides.get("can_view_finance", False))' in src
    assert '"advance_rub": None' in src
    assert '"advance_status": "provider_not_exposed"' in src
    assert "Отдельная сумма аванса не предоставляется Avito API" in src


def test_ab_results_never_promote_insufficient_measurement_to_winner_or_loser():
    src = API.read_text(encoding="utf-8")
    assert '"classification": (classification or "measured") if measured else "insufficient_data"' in src
    assert 'x["confidence"] == "measured"' in src
    assert '"confidence": "insufficient_data"' in src
    assert "не объявляются победителями" in src


def test_report_share_reuses_canonical_internal_messenger_send():
    src = API.read_text(encoding="utf-8")
    assert 'from app.api.internal_messenger import MessageCreate, send_message' in src
    assert 'send_message(int(body.conversation_id)' in src
    assert "internal_conversations c JOIN internal_conversation_members m" in src


def test_reports_ui_is_tenant_bound_and_clears_on_account_switch():
    src = REPORTS.read_text(encoding="utf-8")
    assert "result.account_id" not in src  # local variable is named r; guard below is authoritative
    assert "r.account_id!==scoped" in src
    assert 'setReport(null);setError("");setAnswer("");setQuestion("");setDestinations([]);setDestination(null)' in src
    assert "[accountId]" in src
    assert "/api/messenger-reports/" in src


def test_more_menu_places_reports_between_tasks_and_team():
    src = PAGE.read_text(encoding="utf-8")
    tasks = src.index("Задачи и напоминания")
    reports = src.index("Отчёты и сводки")
    team = src.index("Команда и доступы")
    assert tasks < reports < team
    assert "<ReportsPanel" in src


def test_settings_v2_replaces_old_giant_blocks_and_ios_tip_is_conditional():
    page = PAGE.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "<SettingsPanel" in page
    assert "settingsGridV2" in settings
    assert "settingsSegmented" in settings
    assert "accentChoices" in settings
    assert "themePreview" in settings
    assert "Push-уведомления" in settings
    assert "Приложение BORIS Messenger" in settings
    assert "Доступ к аккаунтам" in settings
    assert "showIosTip&&" in settings
    assert "BORIS_MESSENGER_V63_SETTINGS_REPORTS" in css


def test_reports_surface_has_required_business_sections_and_morning_report():
    src = REPORTS.read_text(encoding="utf-8")
    for marker in (
        "Утренний отчёт",
        "Спросить BORIS",
        "Кошелёк Avito",
        "Реклама",
        "Реактивация",
        "A/B-тесты",
        "Недостаточно данных",
        "Отправить",
    ):
        assert marker in src


def test_reports_support_today_custom_range_history_and_deeplinks():
    api = API.read_text(encoding="utf-8")
    reports = REPORTS.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "def _resolve_period" in api
    assert "date_from: Optional[str]" in api
    assert "date_to: Optional[str]" in api
    assert "Период отчёта не может превышать 60 дней" in api
    assert "https://boris-ai.pro/messenger?section=reports&report=" in api
    assert "Сегодня" in reports
    assert "Свой период" in reports
    assert "customPeriod" in reports
    assert "История тестов" in reports
    assert "/ads/" in reports and "/experiments" in reports
    assert 'requested === "reports"' in page
    assert 'window.addEventListener("popstate", applySectionFromUrl)' in page
    assert "BORIS_MESSENGER_V63_REPORTS_RANGE_HISTORY" in css


def test_ab_report_includes_prepared_title_hypotheses_without_faking_measurement():
    api = API.read_text(encoding="utf-8")
    reports = REPORTS.read_text(encoding="utf-8")
    assert "def _prepared_title_hypotheses" in api
    assert '"kind": "Заголовок"' in api
    assert '"confidence": "not_measured"' in api
    assert '"prepared_hypotheses": prepared[:80]' in api
    assert '"prepared_hypotheses": prepared' in api
    assert "Подготовлено · ещё не измерялось" in reports
    assert "prepared_hypotheses" in reports
    assert "variant_a?.title" in reports
    assert "variant_b?.title" in reports


def test_reports_use_per_user_application_rate_limits_instead_of_nginx_ip_bucket():
    api = API.read_text(encoding="utf-8")
    assert "messenger_report_read" in api
    assert "messenger_report_share" in api
    assert "f\"user:{int(user.id)}\"" in api
    assert api.count("_rate_report(user)") >= 7
    assert "_rate_report(user, share=True)" in api
