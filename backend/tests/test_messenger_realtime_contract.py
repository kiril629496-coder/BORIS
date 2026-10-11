from pathlib import Path

ROOT = Path("/root/BORIS")
API = ROOT / "backend/app/api/internal_messenger.py"
TEAM = ROOT / "backend/app/api/team.py"
MIGRATION = ROOT / "backend/migrations/061_internal_messenger_realtime.sql"
FRONTEND = ROOT / "frontend/app/messenger/page.tsx"
CSS = ROOT / "frontend/app/messenger/messenger.module.css"


def test_realtime_migration_is_additive_and_cursor_scoped():
    source = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS internal_messenger_events" in source
    assert "BIGSERIAL PRIMARY KEY" in source
    assert "account_id VARCHAR NOT NULL" in source
    assert "payload_json JSONB" in source
    assert "ix_internal_messenger_events_account_id" in source
    assert "DROP TABLE" not in source.upper()


def test_backend_exposes_authenticated_resumable_sse():
    source = API.read_text()
    assert '@router.get("/events/stream")' in source
    assert 'last-event-id' in source
    assert 'resume_after=max' in source.replace(" ", "") or 'resume_after = max' in source
    assert 'StreamingResponse' in source
    assert '"X-Accel-Buffering": "no"' in source
    assert 'heartbeat_at' in source
    assert '_access(session, user, account_id)' in source
    assert 'retry: 2000' in source
    assert 'def _messenger_event_feed' in source
    assert 'def emit_messenger_event' in source


def test_internal_actions_emit_durable_events_before_commit():
    source = API.read_text()
    for marker in (
        "internal.conversation.created",
        "internal.conversation.updated",
        "internal.conversation.member_added",
        "internal.conversation.member_removed",
        "internal.conversation.role_changed",
        "internal.message.created",
        "internal.message.updated",
        "internal.message.deleted",
        "internal.message.reaction_changed",
        "internal.message.pin_changed",
        "internal.message.read",
    ):
        assert marker in source
    assert "INSERT INTO internal_messenger_events" in source


def test_team_lifecycle_emits_team_changed():
    source = TEAM.read_text()
    assert source.count('"team.changed"') >= 5
    for action in ("invite_created", "invite_accepted", "access_revoked", "role_changed", "invite_revoked"):
        assert action in source


def test_frontend_realtime_has_cursor_reconnect_multitab_and_polling_fallback():
    source = FRONTEND.read_text()
    css = CSS.read_text()
    assert "/api/internal-messenger/events/stream" in source
    assert "boris_messenger_event_cursor:" in source
    assert "BroadcastChannel" in source
    assert 'headers.set("Accept", "text/event-stream")' in source
    assert "THREAD_LIST_RECONCILIATION_V1" in source
    assert "MESSAGE_HISTORY_RECONCILIATION_V1" in source
    assert 'document.visibilityState === "visible"' in source
    assert 'setRealtimeState("reconnecting")' in source
    assert 'setRealtimeState("offline")' in source
    assert "REALTIME_POLLING_FALLBACK_V1" in source
    assert '/api/internal-messenger/events?account_id=' in source
    assert "fallbackHealthy = true" in source
    assert "streamController.abort()" in source
    assert "3500" in source
    assert 'data-state={realtimeState}' in source
    assert "Онлайн" in source and "Нет связи" in source
    assert '.statusDot[data-state="offline"]' in css
