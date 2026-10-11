from pathlib import Path

ROOT=Path('/root/BORIS')
FRONTEND=ROOT/'frontend/app/messenger/page.tsx'
REPORTS=ROOT/'frontend/app/messenger/components/ReportsPanel.tsx'
REPORT_API=ROOT/'backend/app/api/messenger_reports.py'

def test_reports_use_canonical_business_evidence_through_readonly_facade():
    ui=REPORTS.read_text(encoding='utf-8')
    api=REPORT_API.read_text(encoding='utf-8')
    assert '/api/messenger-reports/' in ui
    assert 'daily_stats:' in api
    assert 'reactivation_candidates' in api
    assert 'marketing_experiment_journal:' in api
    assert 'guardian_balance_funding_health' in api
    assert '<ReportsPanel' in FRONTEND.read_text(encoding='utf-8')

def test_client_rows_expose_time_owner_and_unread_without_full_crm_stage():
    source=FRONTEND.read_text(encoding='utf-8')
    assert 'formatTime(lead.last_msg_at)' in source
    assert 'data-owner={lead.owner_type === "human" ? "human" : "ai"}' in source
    assert 'lead.assignee_name' in source
    assert 'lead.unread_count' in source
    assert '<em>{lead.stage || "новый"}</em>' not in source
    assert 'aria-label="Этап клиента"' not in source
