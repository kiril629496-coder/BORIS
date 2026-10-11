from pathlib import Path

ROOT = Path("/root/BORIS")
API = (ROOT / "backend/app/api/internal_messenger.py").read_text(encoding="utf-8")
PAGE = (ROOT / "frontend/app/messenger/page.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/app/messenger/messenger.module.css").read_text(encoding="utf-8")


def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_private_platform_owner_is_the_only_cross_tenant_internal_messenger_exception():
    access = _block(API, "def _access(", "def _require_reply(")
    assert "is_private_platform_owner(user)" in access
    assert "SELECT 1 FROM accounts WHERE account_id=:a LIMIT 1" in access
    assert 'role="owner" alone' in access
    assert 'getattr(user, "role"' not in access


def test_chat_creation_is_serialized_tenant_checked_and_deduplicated():
    create = _block(API, "def create_conversation(", '@router.patch("/conversations/{conversation_id}")')
    assert "pg_advisory_xact_lock(hashtext(:k))" in create
    assert "COALESCE(u.is_active,TRUE)=TRUE" in create
    assert "COALESCE(u.status,'active')<>'blocked'" in create
    assert "len(target_ids) != 1" in create
    assert "count(*) FROM internal_conversation_members mx" in create
    assert "=2 " in create
    assert "created_at>=now()-interval '30 seconds'" in create
    assert "member_user_ids" in create
    assert 'action="conversation_create"' in create
    assert "_require_team_manage(" not in create


def test_new_chat_ui_is_searchable_segmented_and_not_native_select():
    start = PAGE.index("{newChatOpen &&")
    end = PAGE.index('<input className={styles.search} value={internalQuery}', start)
    creator = PAGE[start:end]
    assert "<select" not in creator
    for marker in (
        'newChatMode === "dm"',
        'newChatMode === "group"',
        "newChatSearchRef",
        "newChatCandidates",
        "newGroupMemberIds",
        "selectedGroupPeople",
        "chatCreating",
        "Начать диалог",
        "Создать группу",
        "Пока некого добавить в чат",
        "Добавить сотрудника",
    ):
        assert marker in creator
    assert "data-new-chat-trigger" in PAGE
    assert "internalComposerRef" in PAGE
    assert 'event.key === "Enter" && !event.shiftKey' in PAGE


def test_more_menu_is_native_style_navigation_not_legacy_grey_button_stack():
    for marker in (
        "<MessageCircle size={19}",
        "<UsersRound size={19}",
        "<Ellipsis size={20}",
        "<CalendarCheck2 size={18}",
        "<Bell size={18}",
        "<ShieldCheck size={18}",
        "<ExternalLink size={18}",
        "Мои задачи и дедлайны",
        "Сотрудники и роли",
        "Push и установка",
        "Устройства и соглашения",
        "styles.moreMenuX",
        "styles.moreMenuSeparator",
    ):
        assert marker in PAGE
    assert "moreMenuClose" not in PAGE
    assert ".mobileMoreSheet .moreMenuItem" in CSS
    assert ".moreMenuSeparator" in CSS


def test_chat_creator_and_more_menu_have_desktop_and_mobile_surfaces():
    for marker in (
        "BORIS_MESSENGER_CHAT_CREATION_V62",
        ".newChatSegments",
        ".newChatPeople",
        ".newChatChips",
        ".newChatSubmit",
        "@media(max-width:1024px)",
        "bottom:calc(76px + env(safe-area-inset-bottom))",
    ):
        assert marker in CSS


def test_group_chat_roles_are_independent_from_company_team_admin_permission():
    rename = _block(API, "def update_conversation(", '@router.get("/conversations/{conversation_id}/members")')
    add_member = _block(API, "def add_member(", '@router.patch("/conversations/{conversation_id}/members/{member_user_id}")')
    role = _block(API, "def change_member_role(", '@router.delete("/conversations/{conversation_id}/members/{member_user_id}")')
    remove = _block(API, "def remove_member(", '@router.post("/conversations/{conversation_id}/transfer-ownership")')
    transfer = _block(API, "def transfer_ownership(", '@router.get("/conversations/{conversation_id}/messages")')
    for block in (rename, add_member, role, remove, transfer):
        assert "_require_reply(" in block
        assert "_require_team_manage(" not in block
    assert 'canReply && myInternalRole === "OWNER"' in PAGE
    assert 'canReply && myInternalRole === "ADMIN"' in PAGE
    assert "canManageTeam && <button className={" in PAGE
    assert "Команда и доступы" in PAGE


def test_general_chat_keeps_strict_platform_owner_membership_for_support_workspace():
    ensure = _block(API, "def ensure_general_conversation(", '@router.get("/conversations")')
    sync = _block(API, "def ensure_general_conversation_membership(", "def _record_system_event(")
    assert "is_private_platform_owner(user)" in ensure
    assert "role='ADMIN'" in ensure
    assert "PLATFORM_OWNER_ID" in sync
    assert "user_id<>:platform_owner" in sync
