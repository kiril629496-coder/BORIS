import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = Path(os.environ.get("BORIS_FRONTEND_ROOT", "/root/BORIS/frontend")).resolve()
WORKSPACE = (BACKEND_ROOT / "app/api/messenger_workspace.py").read_text(encoding="utf-8")
LEGAL = (BACKEND_ROOT / "app/api/legal.py").read_text(encoding="utf-8")
LEGAL_SERVICE = (BACKEND_ROOT / "app/services/messenger_legal.py").read_text(encoding="utf-8")
LEGAL_PAGE = (FRONTEND_ROOT / "app/messenger/legal/page.tsx").read_text(encoding="utf-8")
MESSENGER = (FRONTEND_ROOT / "app/messenger/page.tsx").read_text(encoding="utf-8")
MAIN = (BACKEND_ROOT / "app/main.py").read_text(encoding="utf-8")


def test_clickwrap_is_explicit_unchecked_and_exact_version_bound():
    assert 'useState(false)' in LEGAL_PAGE
    assert 'type="checkbox"' in LEGAL_PAGE
    assert 'disabled={!checked || accepting || !documents.length}' in LEGAL_PAGE
    assert 'accepted: true' in LEGAL_PAGE
    assert 'document_type: doc.document_type' in LEGAL_PAGE
    assert 'version: doc.version' in LEGAL_PAGE
    assert 'hash: doc.hash' in LEGAL_PAGE


def test_backend_requires_exact_current_documents_and_reaccepts_on_hash_change():
    assert "submitted != expected" in LEGAL
    assert "legal_documents_changed" in LEGAL
    assert "require_acceptance" in LEGAL
    assert "messenger_legal_acceptance_required" in LEGAL_SERVICE
    assert "document_hash" in LEGAL_SERVICE
    assert "document_version" in LEGAL_SERVICE


def test_messenger_workspace_facade_is_registered_and_every_route_is_legal_gated():
    assert "app.include_router(messenger_workspace_router)" in MAIN
    assert 'prefix="/api/messenger-workspace"' in WORKSPACE
    assert WORKSPACE.count("Depends(require_messenger_legal_user)") >= 14


def test_facade_reuses_shared_business_logic_without_copying_provider_send():
    for marker in (
        "inbox_api.inbox_accounts",
        "messenger_api.leads_by_stage_endpoint",
        "messenger_api.approve_draft",
        "export_api.export_dialog",
        "crm_api.list_tasks",
        "crm_api.create_task",
        "inbox_api.inbox_take",
        "inbox_api.inbox_release",
        "inbox_api.inbox_mop_handoff",
        "inbox_api.inbox_send",
    ):
        assert marker in WORKSPACE
    assert "send_message(" not in WORKSPACE


def test_messenger_frontend_redirects_428_to_legal_and_uses_workspace_facade():
    assert "response.status === 428" in MESSENGER
    assert 'window.location.replace("/messenger/legal")' in MESSENGER
    assert "/api/messenger-workspace/accounts" in MESSENGER
    assert "/api/messenger-workspace/leads" in MESSENGER
    assert "/api/messenger-workspace/pending-drafts" in MESSENGER
    assert "/api/messenger-workspace/dialog" in MESSENGER
    assert "/api/messenger-workspace/tasks" in MESSENGER
    assert "/api/messenger-workspace/send" in MESSENGER
    assert "/api/messenger-workspace/ownership/" in MESSENGER


def test_messenger_frontend_no_longer_calls_shared_work_routes_directly():
    forbidden = (
        "/api/inbox/accounts",
        "/api/messenger/leads/by_stage",
        "/api/messenger/pending_drafts",
        "/api/messenger/draft/",
        "/api/inbox/export/dialog",
        "/api/crm/tasks",
        "/api/inbox/send",
        "/api/inbox/take",
        "/api/inbox/release",
        "/api/inbox/mop-handoff",
    )
    for route in forbidden:
        assert route not in MESSENGER
