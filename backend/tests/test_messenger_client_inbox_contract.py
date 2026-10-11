from pathlib import Path

ROOT = Path('/root/BORIS')
API = ROOT / 'backend/app/api/messenger.py'
FRONTEND = ROOT / 'frontend/app/messenger/page.tsx'

def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]

def test_leads_by_stage_is_authenticated_and_account_scoped():
    source = API.read_text(encoding='utf-8')
    block = _block(source, '@router.get("/leads/by_stage")', '@router.get("/leads/stats")')
    assert 'user=_Dep_dr(_cu_dr)' in block
    assert '_account_access_allowed(account_id, user, need_reply=False)' in block
    assert 'raise HTTPException(status_code=403, detail="Аккаунт недоступен")' in block

def test_leads_by_stage_exposes_real_assignment_ownership_and_unread_projection():
    source = API.read_text(encoding='utf-8')
    block = _block(source, '@router.get("/leads/by_stage")', '@router.get("/leads/stats")')
    for marker in ('"stage": lead_stage or stage','"contact_name": contact_name or "Клиент Avito"','"assigned_to":','"assignee_name":','"owner_type": owner_type','"unread_count": unread_count'):
        assert marker in block
    assert 'human_takeover_state' in block

def test_client_inbox_filters_are_backed_by_real_fields():
    source = FRONTEND.read_text(encoding='utf-8')
    assert 'clientFilter' in source
    for marker in ('"unread"','"human"','"ai"','"mine"','"unassigned"','"overdue"'):
        assert marker in source
    assert 'lead.unread_count' in source
    assert 'lead.owner_type === "human"' in source
    assert 'String(lead.assigned_to || "") === String(currentUserId)' in source
    assert 'overdueChats.has(String(lead.chat_id))' in source
