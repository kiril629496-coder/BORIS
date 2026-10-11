from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "app" / "api" / "inbox_slots.py"
MOP = ROOT / "app" / "mop_core.py"


def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_all_ownership_mutations_require_can_reply():
    source = INBOX.read_text(encoding="utf-8")
    take = _block(source, "def inbox_take(", '@router.post("/release")')
    release = _block(source, "def inbox_release(", '@router.post("/mop-handoff")')
    handoff = _block(source, "def inbox_mop_handoff(", "class InboxSendRequest")
    for block in (take, release, handoff):
        assert 'has_permission(db, user, req.account_id, "can_reply")' in block


def test_take_and_return_use_canonical_ownership_recorders_without_early_commit():
    inbox = INBOX.read_text(encoding="utf-8")
    mop = MOP.read_text(encoding="utf-8")
    take = _block(inbox, "def inbox_take(", '@router.post("/release")')
    handoff = _block(inbox, "def inbox_mop_handoff(", "class InboxSendRequest")

    assert "record_human_takeover(" in take
    assert "commit=False" in take
    assert take.find("record_human_takeover(") < take.find("db.commit()")
    assert "record_return_to_ai(" in handoff
    assert "commit=False" in handoff
    assert "def record_human_takeover(" in mop and "commit: bool = True" in mop
    assert "def record_return_to_ai(" in mop and "commit: bool = True" in mop


def test_release_only_clears_assignment_and_does_not_resume_mop():
    source = INBOX.read_text(encoding="utf-8")
    release = _block(source, "def inbox_release(", '@router.post("/mop-handoff")')
    assert "assigned_user_id = NULL" in release
    assert "record_return_to_ai(" not in release
    assert '"draft_ready"' not in release
    assert "/mop-handoff?action=mop" in release
    assert "db.commit()" in release


def test_ownership_http_status_contract_is_explicit():
    source = INBOX.read_text(encoding="utf-8")
    take = _block(source, "def inbox_take(", '@router.post("/release")')
    release = _block(source, "def inbox_release(", '@router.post("/mop-handoff")')
    handoff = _block(source, "def inbox_mop_handoff(", "class InboxSendRequest")

    for block in (take, release, handoff):
        assert 'raise HTTPException(status_code=403, detail="Действие недоступно для этой роли")' in block
        assert 'raise HTTPException(status_code=404, detail="Аккаунт не найден или не подключён")' in block

    assert 'raise HTTPException(status_code=409, detail="Диалог ещё не заведён в работу")' in take
    assert 'status_code=409,' in take
    assert 'detail=f"Диалог уже ведёт {assigned_name}"' in take
    assert 'raise HTTPException(status_code=409, detail="Диалог ведёт другой сотрудник")' in release
    assert 'raise HTTPException(status_code=400, detail="action must be human or mop")' in handoff
    assert 'raise HTTPException(status_code=409, detail="Для диалога пока нет MOP draft")' in handoff


def test_ownership_actions_emit_messenger_realtime_events():
    source = INBOX.read_text(encoding="utf-8")
    take = _block(source, "def inbox_take(", '@router.post("/release")')
    release = _block(source, "def inbox_release(", '@router.post("/mop-handoff")')
    handoff = _block(source, "def inbox_mop_handoff(", "class InboxSendRequest")
    assert '"client.assignment.changed"' in take
    assert '"client.ai_mode.changed"' in take
    assert '"client.assignment.changed"' in release
    assert '"client.ai_mode.changed"' in handoff
    assert '"action": "human"' in handoff
    assert '"action": "mop"' in handoff


def test_human_send_preserves_ai_pause_until_explicit_return_to_boris():
    source = INBOX.read_text(encoding="utf-8")
    send = _block(source, "def inbox_send(", '@router.post("/mark_read")')
    assert "HUMAN_SEND_OWNERSHIP_SEPARATION_V1" in send
    assert "UPDATE mop_drafts SET status = 'human_required'" in send
    assert "sent_at = NULL" in send
    assert "UPDATE mop_drafts SET status = 'sent'" not in send
    assert "record_human_takeover(" in send


def test_ownership_failures_are_real_http_errors_not_http_200_error_payloads():
    source = INBOX.read_text(encoding="utf-8")
    take = _block(source, "def inbox_take(", '@router.post("/release")')
    handoff = _block(source, "def inbox_mop_handoff(", "class InboxSendRequest")

    assert 'return {"status": "error", "message": "Не удалось зафиксировать ownership диалога"}' not in take
    assert 'raise HTTPException(status_code=500, detail="Не удалось зафиксировать ownership диалога")' in take

    assert 'return {"status":"error","message":"Не удалось передать ownership человеку"}' not in handoff
    assert 'return {"status":"error","message":"Не удалось вернуть ownership МОПу"}' not in handoff
    assert 'return {"status":"error","message":"Не удалось изменить ownership диалога"}' not in handoff
    assert 'raise HTTPException(status_code=500, detail="Не удалось передать ownership человеку")' in handoff
    assert 'raise HTTPException(status_code=500, detail="Не удалось вернуть ownership BORIS")' in handoff
    assert 'raise HTTPException(status_code=500, detail="Не удалось изменить ownership диалога")' in handoff
    assert 'except HTTPException:' in handoff


def test_handoff_without_mop_state_is_explicit_conflict_not_fake_success():
    source = INBOX.read_text(encoding="utf-8")
    handoff = _block(source, "def inbox_mop_handoff(", "class InboxSendRequest")
    assert 'raise HTTPException(status_code=409, detail="Для диалога пока нет MOP draft")' in handoff
    assert 'raise HTTPException(status_code=409, detail="Текущее состояние BORIS не позволяет передать диалог человеку")' in handoff
    assert '"status":"noop"' not in handoff
