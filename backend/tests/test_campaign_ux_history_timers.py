from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_mass_editor_blocked_task_timer_is_frozen_and_humanized():
    src=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    block=src[src.index("MASS_EDITOR_TASK_TIMERS_V1"):src.index('@router.get("/ab-tests")')]
    assert 'status.startswith("blocked")' in block
    assert '"waiting_external","pending_external_verification","needs_action"' in block
    assert "end_dt=finished_dt or (updated_dt if paused and updated_dt else now)" in block
    assert '"Нужно подключить Avito" if status=="blocked_credentials"' in block
    assert '"Нужно действие" if status.startswith("blocked")' in block

def test_campaign_frontend_history_contains_real_task_timing_and_attention_filter():
    page=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert 'data-testid="campaign-history-task-timing"' in page
    assert "Время выполнения задач" in page
    assert "Реальные серверные задачи" in page
    assert '["attention","Нужно действие"]' in page
    assert 'st.startsWith("blocked")' in page

def test_account_picker_is_searchable_keyboard_command_picker():
    picker=(ROOT.parent/"frontend/app/components/shell/AccountSelector.tsx").read_text(encoding="utf-8")
    css=(ROOT.parent/"frontend/app/components/shell/shell.module.css").read_text(encoding="utf-8")
    assert 'aria-label="Поиск аккаунта"' in picker
    assert '["work","Рабочие"]' in picker and '["qa","QA"]' in picker
    assert 'event.key==="ArrowDown"' in picker
    assert 'event.key==="Enter"' in picker
    assert "data-focused" in picker
    assert '.accountPickerItem[data-focused="1"]' in css

def test_ab_cards_link_directly_to_human_edit_surfaces():
    panel=(ROOT.parent/"frontend/app/dashboard/campaign/AvitoABTestsPanel.tsx").read_text(encoding="utf-8")
    for label in ("Открыть карточку","Редактировать текст","Фото и баннер","История изменений"):
        assert label in panel
    assert "cleanClientText(x.description)" in panel
