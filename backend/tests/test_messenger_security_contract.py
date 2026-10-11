from pathlib import Path

ROOT = Path("/root/BORIS")
API = ROOT / "backend/app/api/internal_messenger.py"
CRM = ROOT / "backend/app/api/crm.py"


def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_internal_resources_are_tenant_and_membership_scoped():
    source = API.read_text(encoding="utf-8")
    assert "def _require_member(" in source
    assert 'raise HTTPException(status_code=404, detail="Чат не найден")' in source
    assert 'raise HTTPException(status_code=403, detail="Чат недоступен для этого аккаунта")' in source
    download = _block(source, "def download_attachment(", '@router.post("/conversations/{conversation_id}/leave")')
    assert "_require_member(db, int(row[\"conversation_id\"]), user)" in download
    assert "_attachment_storage_path(str(row[\"storage_ref\"]), int(row[\"conversation_id\"]))" in download


def test_attachment_upload_blocks_path_traversal_and_executable_names():
    source = API.read_text(encoding="utf-8")
    upload = _block(source, "async def upload_attachment(", '@router.post("/messages/{message_id}/attachments")')
    attach = _block(source, "def add_attachment(", '@router.get("/attachments/{attachment_id}")')
    assert 'Path(str(file.filename or "file")).name' in upload
    assert "ATTACHMENT_DENIED_EXTENSIONS" in upload
    assert "ATTACHMENT_MAX_BYTES" in upload
    assert 'status_code=413' in upload
    assert "ATTACHMENT_DENIED_EXTENSIONS" in attach
    assert "_attachment_storage_path(body.storage_ref, conversation_id)" in attach


def test_crm_reads_require_account_access_and_mutations_require_reply():
    source = CRM.read_text(encoding="utf-8")
    assert "def _own(" in source
    assert "def _write(" in source
    create = _block(source, "def create_task(", "class TaskAction")
    done = _block(source, "def done_task(", '@router.post("/tasks/update")')
    update = _block(source, "def update_task(", '@router.post("/tasks/delete")')
    delete = _block(source, "def delete_task(", '@router.get("/today")')
    for block in (create, done, update, delete):
        assert "_write(" in block
