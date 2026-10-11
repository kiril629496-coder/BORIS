from pathlib import Path

ROOT = Path("/root/BORIS")
BACKEND = (ROOT / "backend/app/api/messenger.py").read_text(encoding="utf-8")
FRONTEND = (ROOT / "frontend/app/messenger/page.tsx").read_text(encoding="utf-8")


def test_client_search_is_server_scoped_and_tenant_guarded():
    assert 'def leads_by_stage_endpoint(account_id: str, stage: str, q: str = ""' in BACKEND
    assert "_account_access_allowed(account_id, user, need_reply=False)" in BACKEND
    assert "COALESCE(l.contact_name,'') ILIKE :like" in BACKEND
    assert "CAST(l.avito_chat_id AS TEXT) ILIKE :like" in BACKEND
    assert "COALESCE(l.item_title,'') ILIKE :like" in BACKEND
    assert "messenger_messages sm" in BACKEND
    assert "sm.account_id=l.account_id" in BACKEND
    assert "sm.avito_chat_id=l.avito_chat_id" in BACKEND


def test_client_search_frontend_uses_debounce_and_stale_request_guard():
    assert "const inboxRequestSeq = useRef(0);" in FRONTEND
    assert "const requestId = ++inboxRequestSeq.current;" in FRONTEND
    assert "requestId !== inboxRequestSeq.current" in FRONTEND
    assert "window.setTimeout(() => void loadInbox(query.trim()), 300)" in FRONTEND
    assert "&q=${encodeURIComponent(searchTerm)}" in FRONTEND
