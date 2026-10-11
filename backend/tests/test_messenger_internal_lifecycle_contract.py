from pathlib import Path

ROOT = Path("/root/BORIS")
API = ROOT / "backend/app/api/internal_messenger.py"
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"
CSS = ROOT / "frontend/app/messenger/messenger.module.css"


def test_normal_group_can_be_left_but_general_chat_cannot():
    source = API.read_text(encoding="utf-8")
    assert '@router.post("/conversations/{conversation_id}/leave")' in source
    assert 'detail="Общий чат компании покинуть нельзя"' in source
    assert 'detail="Личный чат нельзя покинуть как группу"' in source
    assert 'detail="Владелец должен сначала передать владение группой"' in source
    assert '"left": True' in source


def test_internal_chat_has_cancel_edit_reply_preview_and_pinned_navigation():
    source = FRONTEND.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "const cancelInternalEdit = () =>" in source
    assert "Редактирование сообщения" in source
    assert "internalReplyMessage" in source
    assert "Ответ:" in source
    assert "internalPinnedMessages" in source
    assert "Закреплённые" in source
    assert "scrollIntoView" in source
    assert ".pinnedStrip" in css


def test_frontend_exposes_leave_only_for_non_general_non_owner_group():
    source = FRONTEND.read_text(encoding="utf-8")
    assert "const leaveInternalGroup = async () =>" in source
    assert 'internalSelected.kind !== "Группа" || internalSelected.isGeneral' in source
    assert 'myInternalRole !== "OWNER"' in source
    assert "Покинуть группу" in source


def test_internal_history_uses_cursor_pagination_and_load_older_ui():
    api = (ROOT / "backend/app/api/internal_messenger.py").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend/app/messenger/page.tsx").read_text(encoding="utf-8")
    assert "before_id: int = Query(default=0, ge=0)" in api
    assert "ORDER BY m.id DESC LIMIT :fetch_limit" in api
    assert '"has_more": has_more' in api
    assert '"next_before_id": int(rows[0]["id"]) if rows else before_id' in api
    assert "loadOlderInternalMessages" in frontend
    assert "Загрузить более ранние сообщения" in frontend


def test_internal_history_scroll_contract():
    source = FRONTEND.read_text(encoding="utf-8")
    for marker in ("internalHistoryRef", "internalNearBottomRef", "internalInitialScrollRef", "internalRestoreScrollRef"):
        assert marker in source
