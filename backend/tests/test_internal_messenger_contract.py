from pathlib import Path

ROOT = Path('/root/BORIS/backend')
API = ROOT / 'app/api/internal_messenger.py'
MIGRATION = ROOT / 'migrations/060_internal_messenger.sql'
MAIN = ROOT / 'app/main.py'
NOTIFICATIONS = ROOT / 'app/services/lead_notifications.py'


def test_internal_messenger_contract_is_tenant_scoped_and_reuses_access():
    source = API.read_text()
    assert 'user_account_access' in source
    assert 'accounts' in source
    assert 'Cross-tenant access stays fail-closed' in source
    assert 'is_private_platform_owner(user)' in source
    assert 'role="owner" alone' in source
    assert 'is_own' in source
    assert 'internal_conversation_members' in source
    assert 'can_view' in source
    assert 'can_manage_team' in source
    assert 'customer Inbox/Avito state machine' in source


def test_internal_messenger_has_required_server_side_operations():
    source = API.read_text()
    for marker in (
        'def create_conversation', 'def list_conversations', 'def list_messages',
        'def send_message', 'def mark_read', 'def edit_message',
        'def delete_message', 'def react', 'def pin', 'def add_attachment',
        'def change_member_role', 'def remove_member', 'def transfer_ownership',
        'def read_receipts', 'def list_people', 'def update_conversation',
        'def upload_attachment', 'def download_attachment',
    ):
        assert marker in source
    assert 'Редактировать можно только свои сообщения' in source
    assert 'Удалять можно только свои сообщения' in source
    assert 'Только владелец или администратор группы' in source
    assert 'Только владелец группы может менять роли' in source
    assert 'Администратор не может удалить другого администратора' in source
    assert 'Сначала передайте владение группой' in source
    assert 'delivery_status' in source
    assert 'last_read_message_id' in source
    assert 'Только владелец или администратор группы может менять название' in source
    assert 'display_name' in source
    assert 'presence' in source
    assert 'def _record_system_event' in source
    assert 'Группа переименована' in source
    assert 'добавлен в группу' in source
    assert 'назначен администратором' in source
    assert 'удалён из группы' in source
    assert 'Владение группой передано' in source
    assert 'ATTACHMENT_MAX_BYTES' in source
    assert 'internal-message:' in source
    assert 'target_user_ids' in source
    assert 'notification_service.queue_event' in source


def test_migration_is_additive_and_contains_all_internal_entities():
    source = MIGRATION.read_text()
    assert 'CREATE TABLE IF NOT EXISTS' in source
    assert 'DROP TABLE' not in source.upper()
    for table in (
        'internal_conversations', 'internal_conversation_members',
        'internal_messages', 'internal_message_reactions',
        'internal_message_pins', 'internal_message_attachments',
    ):
        assert table in source
    assert "CHECK (kind IN ('DM','GROUP'))" in source
    assert "CHECK (role IN ('OWNER','ADMIN','MEMBER'))" in source


def test_main_wires_internal_router_without_replacing_customer_messenger():
    source = MAIN.read_text()
    assert 'from app.api.messenger import router as messenger_router' in source
    assert 'from app.api.internal_messenger import router as internal_messenger_router' in source
    assert 'app.include_router(messenger_router, dependencies=_protected)' in source
    assert 'app.include_router(internal_messenger_router)' in source


def test_internal_messenger_reuses_targeted_webpush_without_changing_default_delivery():
    source = NOTIFICATIONS.read_text()
    assert 'target_user_ids' in source
    assert 's.user_id' in source
    assert 'rows = [row for row in rows if int(row.get("user_id") or 0) in target_user_ids]' in source
    assert 'if "target_user_ids" in payload:' in source
    assert 'if target_user_ids:' not in source
