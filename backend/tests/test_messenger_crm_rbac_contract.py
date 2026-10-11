from pathlib import Path


ROOT = Path("/root/BORIS")
CRM = ROOT / "backend/app/api/crm.py"
MESSENGER = ROOT / "backend/app/api/messenger.py"


def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_crm_mutations_require_write_permission():
    source = CRM.read_text(encoding="utf-8")
    assert "def _write(" in source
    assert "can_reply=TRUE" in source
    assert 'detail="Роль только для просмотра: изменение CRM недоступно"' in source

    create = _block(source, "def create_task(", "class TaskAction")
    done = _block(source, "def done_task(", '@router.post("/tasks/update")')
    update = _block(source, "def update_task(", '@router.post("/tasks/delete")')
    delete = _block(source, "def delete_task(", '@router.get("/today")')
    for block in (create, done, update, delete):
        assert "_write(" in block


def test_crm_reads_remain_available_to_viewers():
    source = CRM.read_text(encoding="utf-8")
    list_tasks = _block(source, "def list_tasks(", '@router.get("/badges")')
    badges = _block(source, "def badges(", "class TaskIn")
    assert "_own(" in list_tasks and "_write(" not in list_tasks
    assert "_own(" in badges and "_write(" not in badges


def test_sales_dialog_stage_change_returns_real_403_when_forbidden():
    source = MESSENGER.read_text(encoding="utf-8")
    block = _block(source, "def sales_dialog_state_set(", "return {")
    assert "_account_access_allowed(account_id, user, need_reply=True)" in block
    assert 'raise HTTPException(status_code=403, detail="Аккаунт недоступен для изменения CRM")' in block
