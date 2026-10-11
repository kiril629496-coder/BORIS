from pathlib import Path

ROOT = Path("/root/BORIS")
PAGE = (ROOT / "frontend/app/messenger/page.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/app/messenger/messenger.module.css").read_text(encoding="utf-8")


def test_v4_removes_giant_chat_and_client_heroes_from_dom():
    assert "<div className={styles.internalHead}>" not in PAGE
    assert "Синий рабочий контур" not in PAGE
    assert "BORIS_MESSENGER_DESIGN_V4" in CSS
    assert ".internalHead{display:none!important}" in CSS


def test_v4_uses_app_rail_and_mobile_bottom_nav():
    assert "styles.navIcon" in PAGE
    assert "styles.navLabel" in PAGE
    assert "<MessageCircle size={19}" in PAGE
    assert "<UsersRound size={19}" in PAGE
    assert "<Ellipsis size={20}" in PAGE
    assert "Задачи и напоминания" in PAGE
    assert "Команда и доступы" in PAGE
    assert "Уведомления и приложение" in PAGE
    assert "@media(min-width:1025px)" in CSS
    assert "width:86px" in CSS
    assert "flex-direction:column" in CSS
    assert "@media(max-width:1024px)" in CSS


def test_v4_chat_is_compact_messenger_workspace():
    for marker in ("styles.chatListHead", "styles.chatListTools", "styles.internalHistory", "styles.internalComposer"):
        assert marker in PAGE
    assert 'thread.is_general ? "Общий чат"' in PAGE
    assert 'thread.isGeneral ? "👥"' in PAGE
    assert "max-width:70%" in CSS
    assert ".internalBubble:hover .messageActionList" in CSS
    assert ".generalInfo{display:none!important}" in CSS


def test_v4_team_is_members_first_with_invite_drawer():
    for marker in ("styles.teamWorkspace", "styles.teamMembersList", "styles.teamMemberRow", "styles.inviteBackdrop", "styles.inviteDrawer", "styles.roleCards"):
        assert marker in PAGE
    assert "＋ Пригласить" in PAGE
    assert "Старший менеджер" in PAGE
    assert "Клиенты, CRM и управление командой" in PAGE
    assert "Добавьте первого сотрудника" in PAGE
    assert "styles.teamToolbar" in PAGE


def test_v4_does_not_persist_action_or_technical_errors_in_footer():
    assert '<footer className={styles.footer}><span>{status}</span>' not in PAGE
    assert "Синхронизация активна" in PAGE
    assert r"team\.manage" in PAGE  # sanitizer may recognize backend legacy text
    assert "У вашей роли нет доступа к этому разделу." in PAGE


def test_account_switch_clears_tenant_bound_state():
    assert "setLeads([])" in PAGE
    assert "setInternalThreads([])" in PAGE
    assert "setTeamMembers([])" in PAGE
    assert "setAccountTasks([])" in PAGE
