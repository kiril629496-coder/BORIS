from pathlib import Path


ROOT = Path("/root/BORIS")
API = ROOT / "backend/app/api/internal_messenger.py"
TEAM = ROOT / "backend/app/api/team.py"


def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_team_role_permission_matrix_is_explicit():
    source = TEAM.read_text(encoding="utf-8")
    assert '"OWNER": {"can_view": True, "can_reply": True' in source
    assert '"MANAGER_LEAD": {"can_view": True, "can_reply": True' in source
    assert '"MANAGER": {"can_view": True, "can_reply": True' in source
    assert '"VIEWER": {"can_view": True, "can_reply": False' in source
    assert '"VIEWER": {"can_view": True, "can_reply": False, "can_manage_ads": False, "can_view_reports": True, "can_manage_team": False}' in source


def test_internal_write_operations_require_can_reply():
    source = API.read_text(encoding="utf-8")
    assert 'def _require_reply(' in source
    assert 'detail="Роль только для просмотра: отправка и изменения недоступны"' in source

    create = _block(source, "def create_conversation(", '@router.patch("/conversations/{conversation_id}")')
    send = _block(source, "def send_message(", '@router.post("/conversations/{conversation_id}/read")')
    edit = _block(source, "def edit_message(", '@router.delete("/messages/{message_id}")')
    delete = _block(source, "def delete_message(", '@router.post("/messages/{message_id}/reactions")')
    react = _block(source, "def react(", '@router.delete("/messages/{message_id}/reactions/{reaction}")')
    remove_reaction = _block(source, "def remove_reaction(", '@router.post("/messages/{message_id}/pin")')
    upload = _block(source, "async def upload_attachment(", '@router.post("/messages/{message_id}/attachments")')
    attach = _block(source, "def add_attachment(", '@router.get("/attachments/{attachment_id}")')

    for block in (create, send, edit, delete, react, remove_reaction, upload, attach):
        assert "_require_reply(" in block


def test_group_chat_admin_uses_chat_role_and_reply_permission_not_company_team_admin():
    source = API.read_text(encoding="utf-8")
    assert 'def _require_team_manage(' in source  # still used by company-level/pin controls

    create = _block(source, "def create_conversation(", '@router.patch("/conversations/{conversation_id}")')
    rename = _block(source, "def update_conversation(", '@router.get("/conversations/{conversation_id}/members")')
    add_member = _block(source, "def add_member(", '@router.patch("/conversations/{conversation_id}/members/{member_user_id}")')
    role = _block(source, "def change_member_role(", '@router.delete("/conversations/{conversation_id}/members/{member_user_id}")')
    remove = _block(source, "def remove_member(", '@router.post("/conversations/{conversation_id}/transfer-ownership")')
    transfer = _block(source, "def transfer_ownership(", '@router.get("/conversations/{conversation_id}/messages")')
    pin = _block(source, "def pin(", '@router.delete("/messages/{message_id}/pin")')
    unpin = _block(source, "def unpin(", '@router.post("/conversations/{conversation_id}/attachments/upload")')

    assert "_require_reply(" in create
    assert "_require_team_manage(" not in create
    for block in (rename, add_member, role, remove, transfer):
        assert "_require_reply(" in block
        assert "_require_team_manage(" not in block
    for block in (pin, unpin):
        assert "_require_team_manage(" in block


def test_read_only_endpoints_do_not_require_reply_permission():
    source = API.read_text(encoding="utf-8")
    list_messages = _block(source, "def list_messages(", '@router.post("/conversations/{conversation_id}/messages")')
    receipts = _block(source, "def read_receipts(", '@router.patch("/messages/{message_id}")')
    download = _block(source, "def download_attachment(", "return FileResponse(")

    assert "_require_reply(" not in list_messages
    assert "_require_reply(" not in receipts
    assert "_require_reply(" not in download
