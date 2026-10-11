from pathlib import Path

ROOT = Path("/root/BORIS")
INBOX = ROOT / "backend/app/api/inbox_slots.py"
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"


def _block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_client_send_http_failures_are_not_masked_as_200():
    source = INBOX.read_text(encoding="utf-8")
    block = _block(source, "def inbox_send(", "class InboxReadRequest")
    assert 'raise HTTPException(status_code=400, detail="Пустое сообщение")' in block
    assert 'raise HTTPException(status_code=403, detail="Нельзя отправить через чужой или неподключённый аккаунт")' in block
    assert 'raise HTTPException(status_code=403, detail="Нет права отвечать в этом аккаунте")' in block
    assert 'raise HTTPException(status_code=402, detail="Подписка на аккаунт неактивна — отправка недоступна")' in block
    assert 'raise HTTPException(status_code=409' in block
    assert 'raise HTTPException(status_code=502' in block


def test_client_send_success_exposes_real_sent_state_and_provider_identity():
    source = INBOX.read_text(encoding="utf-8")
    block = _block(source, "def inbox_send(", "class InboxReadRequest")
    assert '"delivery_status": "sent"' in block
    assert '"provider_message_id": provider_message_id' in block
    assert '"avito_message": immediate if isinstance(immediate, dict) else None' in block
    assert 'if isinstance(immediate, dict) and immediate.get("id") is not None' in block


def test_idempotency_remains_optional_for_legacy_callers_but_messenger_reuses_key():
    backend = INBOX.read_text(encoding="utf-8")
    frontend = FRONTEND.read_text(encoding="utf-8")
    assert 'idempotency_key: str = ""' in backend
    assert 'const key = sendKey.current || crypto.randomUUID();' in frontend
    assert 'sendKey.current = key;' in frontend
    assert 'sendKey.current = null;' in frontend
    assert 'Текст сохранён — можно повторить отправку.' in frontend


def test_messenger_optimistic_send_reconciles_without_fake_delivered_status():
    source = FRONTEND.read_text(encoding="utf-8")
    assert 'optimistic?: boolean' in source
    assert 'id: providerId || `local-send:${key}`' in source
    assert 'delivery_status: result.delivery_status || "sent"' in source
    assert 'const optimistic = current.filter(message => message.optimistic);' in source
    assert '"Отправлено"' in source
    # Client-side send must not claim provider delivery when only POST success is known.
    send_block = _block(source, "const send = async () => {", "useEffect(() => {")
    assert "Доставлено" not in send_block


def test_client_send_emits_realtime_message_and_human_mode_events():
    source = INBOX.read_text(encoding="utf-8")
    block = _block(source, "def inbox_send(", "class InboxReadRequest")
    assert '"client.message.changed"' in block
    assert '"client.ai_mode.changed"' in block
    assert '"action": "sent"' in block
    assert '"action": "human"' in block


def test_client_draft_survives_reload_and_offline_send_is_blocked():
    source = FRONTEND.read_text(encoding="utf-8")
    assert '"boris_messenger_client_draft:" + accountId + ":" + selected.chat_id' in source
    assert 'localStorage.getItem(draftKey) || ""' in source
    assert 'localStorage.setItem(draftKey, value)' in source
    assert 'localStorage.removeItem("boris_messenger_client_draft:" + accountId + ":" + selected.chat_id)' in source
    assert 'if (!navigator.onLine || realtimeState === "offline")' in source
    assert 'Нет соединения с BORIS. Текст сохранён' in source
    assert 'realtimeState === "offline" ? "Нет связи"' in source
