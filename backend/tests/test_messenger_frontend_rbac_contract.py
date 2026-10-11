from pathlib import Path


ROOT = Path("/root/BORIS")
INBOX = ROOT / "backend/app/api/inbox_slots.py"
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"


def test_inbox_accounts_exposes_account_capabilities():
    source = INBOX.read_text(encoding="utf-8")
    for marker in (
        '"role": access_role',
        '"can_reply": can_reply',
        '"can_manage_team": can_manage_team',
        '"can_view_reports": can_view_reports',
    ):
        assert marker in source
    assert "SELECT role,can_reply,can_manage_team,can_view_reports" in source


def test_frontend_uses_fail_closed_account_capabilities():
    source = FRONTEND.read_text(encoding="utf-8")
    assert 'const currentAccountRole = String(currentAccountAccess?.role || "").toUpperCase();' in source
    assert 'const canReply = currentAccountRole !== "VIEWER" && Boolean(currentAccountAccess?.can_reply);' in source
    assert 'const canManageTeam = currentAccountRole !== "VIEWER" && Boolean(currentAccountAccess?.can_manage_team || currentAccountRole === "OWNER");' in source
    assert 'disabled={!canReply} aria-expanded={ownershipMenuOpen}>Управление</button>' in source
    assert 'void take("take")' in source
    assert 'void take("human")' in source
    assert 'void take("release")' in source
    assert 'void take("mop")' in source
    assert 'placeholder={canReply ? "Ответ сотрудника…" : "Режим просмотра"}' in source
    assert 'disabled={!canReply || attachmentUploading' in source
    assert 'newChatMode === "group"' in source
    assert 'disabled={!canReply || chatCreating' in source
    assert 'newGroupMemberIds.length' in source
    assert '"Создать группу"' in source
    assert 'disabled={!canManageTeam || !inviteName.trim() || !invitePhone.trim()}>Создать приглашение</button>' in source


def test_frontend_hides_team_navigation_without_permission():
    source = FRONTEND.read_text(encoding="utf-8")
    assert 'canManageTeam && <button className={`${styles.moreMenuItem}' in source
    assert 'setSection("team")' in source
    assert 'Команда и доступы' in source
    assert 'setSection("team")' in source
    assert '["team","Команда"' not in source
