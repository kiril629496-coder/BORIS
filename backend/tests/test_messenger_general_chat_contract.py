from pathlib import Path

ROOT = Path("/root/BORIS")
API = ROOT / "backend/app/api/internal_messenger.py"
TEAM = ROOT / "backend/app/api/team.py"
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"
CSS = ROOT / "frontend/app/messenger/messenger.module.css"


def test_general_chat_backend_is_single_account_scoped_and_auto_reconciled():
    source = API.read_text()
    assert 'GENERAL_CHAT_TITLE = "Общий чат BORIS"' in source
    assert 'def ensure_general_conversation_membership' in source
    assert 'pg_advisory_xact_lock' in source
    assert 'boris_general_chat:' in source
    assert 'ON CONFLICT (conversation_id,user_id) DO UPDATE SET role=EXCLUDED.role' in source
    assert 'COALESCE(u.is_active,TRUE)=TRUE' in source
    assert "COALESCE(u.status,'active')<>'blocked'" in source
    assert '@router.post("/general/ensure")' in source
    assert '"is_general": True' in source
    assert '"is_general": bool(' in source


def test_general_chat_is_not_manually_managed_like_a_normal_group():
    source = API.read_text()
    assert 'Общий чат управляется составом команды и не переименовывается вручную' in source
    assert 'Участники общего чата синхронизируются из команды автоматически' in source
    assert 'Роли общего чата синхронизируются из ролей команды' in source
    assert 'Владение общим чатом закреплено за владельцем аккаунта' in source


def test_team_lifecycle_updates_general_chat_membership():
    source = TEAM.read_text()
    assert source.count('ensure_general_conversation_membership') >= 3
    assert 'for acc in targets:' in source
    assert 'ensure_general_conversation_membership(db, row["account_id"])' in source


def test_general_chat_is_first_class_in_messenger_ui():
    source = FRONTEND.read_text()
    css = CSS.read_text()
    assert 'generalReadyAccount' in source
    assert '/api/internal-messenger/general/ensure' in source
    assert 'Общий чат подключается. Пока доступен текущий список внутренних чатов.' in source
    assert 'void ensureGeneral(); }, 5000' in source
    assert 'isGeneral: Boolean(thread.is_general)' in source
    assert 'thread.is_general ? "Общий чат"' in source
    assert 'internalSelected?.isGeneral ? "Вся команда"' in source
    assert 'Написать всей команде…' in source
    assert 'Состав этого чата синхронизируется автоматически' in source
    assert 'styles.generalRow' in source
    assert '.generalRow' in css
    assert '.generalAvatar' in css
    assert '.generalInfo' in css


def test_internal_messages_expose_real_author_label():
    source = API.read_text()
    assert 'AS author_label' in source
    assert '"author_label": r["author_label"]' in source
