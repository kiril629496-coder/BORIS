from pathlib import Path


ROOT = Path("/root/BORIS")
CRM = ROOT / "backend/app/api/crm.py"
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"
WORK = ROOT / "frontend/app/messenger/components/WorkDashboard.tsx"


def test_crm_backend_has_full_task_lifecycle():
    source = CRM.read_text(encoding="utf-8")
    for route in (
        '@router.get("/tasks")',
        '@router.post("/tasks")',
        '@router.post("/tasks/update")',
        '@router.post("/tasks/done")',
        '@router.post("/tasks/delete")',
    ):
        assert route in source


def test_messenger_frontend_uses_account_wide_tasks_and_status_normalization():
    source = FRONTEND.read_text(encoding="utf-8")
    assert "const [accountTasks, setAccountTasks]" in source
    assert "const loadAccountTasks = async ()" in source
    assert "/api/messenger-workspace/tasks?account_id=" in source
    assert "function taskStatusKey(" in source
    assert 'key === "today" ? "Сегодня"' in source
    assert 'key === "overdue" ? "Просрочено"' in source
    assert 'key === "planned" ? "Запланировано"' in source
    assert 'key === "done" ? "Выполнено"' in source


def test_messenger_frontend_supports_task_edit_done_delete():
    source = FRONTEND.read_text(encoding="utf-8")
    assert 'api("/api/messenger-workspace/tasks/update"' in source
    assert 'api("/api/messenger-workspace/tasks/done"' in source
    assert 'api("/api/messenger-workspace/tasks/delete"' in source
    assert "const deleteCrmTask = async" in source
    assert "Удалить задачу" in source
    assert "className={styles.taskStatus}" in source


def test_global_crm_and_work_show_urgent_account_tasks():
    source = FRONTEND.read_text(encoding="utf-8")
    work = WORK.read_text(encoding="utf-8")
    assert "Срочные задачи аккаунта" in source
    assert "Срочные CRM-задачи" in work
    assert 'accountTasks.filter(task => ["overdue","today"].includes(taskStatusKey(task)))' in source
    assert "openTaskClient(task)" in source
    assert "urgentTasks.map(task =>" in work
    assert "onOpenTask(task.id)" in work


def test_crm_mutations_emit_realtime_events():
    crm = (ROOT / "backend/app/api/crm.py").read_text(encoding="utf-8")
    messenger = (ROOT / "backend/app/api/messenger.py").read_text(encoding="utf-8")
    assert crm.count('"crm.task.changed"') >= 4
    assert '"action": "created"' in crm
    assert '"action": "updated"' in crm
    assert '"action": "done"' in crm
    assert '"action": "deleted"' in crm
    assert '"crm.stage.changed"' in messenger
