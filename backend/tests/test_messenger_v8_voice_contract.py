from pathlib import Path

ROOT = Path(__file__).parents[1]
API = ROOT / "app/api/internal_messenger.py"
VOICE = ROOT / "app/services/messenger_voice.py"
MEDIA = ROOT / "app/services/messenger_voice_media.py"
IDEMP = ROOT / "app/services/messenger_idempotency.py"
WORKSPACE = ROOT / "app/api/messenger_workspace.py"


def test_v8_internal_message_idempotency_is_transactional_and_scoped():
    api = API.read_text(encoding="utf-8")
    idem = IDEMP.read_text(encoding="utf-8")
    assert "client_message_id" in api
    assert "messenger_idempotency.begin" in api
    assert "messenger_idempotency.finish" in api
    assert "pg_advisory_xact_lock" in idem
    assert "internal_message_receipts" in idem
    assert "conversation_id=:c AND user_id=:u AND client_message_id=:k" in idem
    assert "409" in idem


def test_v8_voice_routes_require_membership_and_server_side_reply_permission():
    api = API.read_text(encoding="utf-8")
    voice = VOICE.read_text(encoding="utf-8")
    for marker in (
        '"/voice/capabilities"',
        '"/voice/{asset_id}"',
        '"/messages/{message_id}/transcription"',
        '"/messages/{message_id}/transcription/retry"',
        'attachment_kind == "voice"',
        'body.message_type == "voice"',
    ):
        assert marker in api
    assert "_require_member" in voice
    assert "_require_reply" in voice
    assert "uploader_user_id" in voice
    assert "Голосовое сообщение не найдено" in voice


def test_v8_voice_media_is_bounded_and_private():
    media = MEDIA.read_text(encoding="utf-8")
    voice = VOICE.read_text(encoding="utf-8")
    assert "MAX_BYTES = 25 * 1024 * 1024" in media
    assert "MAX_SECONDS = 600" in media
    for mime in ("audio/webm", "audio/ogg", "audio/mp4", "audio/aac", "audio/mpeg"):
        assert mime in media
    assert "parse_range" in media
    assert '"Cache-Control": "private, no-store"' in voice
    assert '"X-Content-Type-Options": "nosniff"' in voice
    assert "Range" not in voice or "parse_range" in voice


def test_v8_voice_stt_and_push_are_async_and_targeted():
    api = API.read_text(encoding="utf-8")
    voice = VOICE.read_text(encoding="utf-8")
    assert "background_jobs" in voice
    assert "messenger_voice_transcription" in voice
    assert "ON CONFLICT(fingerprint)" in voice
    assert "notification_service.queue_event" in api
    assert "target_user_ids" in api
    assert "Голосовое сообщение" in api
    assert "MESSENGER_LOCAL_TRANSCRIPTION_ENABLED" in api


def test_v8_voice_native_stream_uses_short_lived_session_bound_ticket():
    api = API.read_text(encoding="utf-8")
    voice = VOICE.read_text(encoding="utf-8")
    workspace = WORKSPACE.read_text(encoding="utf-8")
    assert '"/voice/{asset_id}/playback-ticket"' in api
    assert "VOICE_STREAM_COOKIE" in api
    assert '"/api/messenger-workspace/voice/{asset_id}/stream"' in voice
    assert "user_from_playback_ticket" in voice
    assert "validate_session" in voice
    assert '"sid": session_id' in voice
    assert '"sev": epoch' in voice
    assert '"asset": str(row["id"])' in voice
    assert "authorize(db, user, asset_id=asset_id)" in voice
    assert '@router.get("/voice/{asset_id}/stream")' in workspace
    assert "playback_ticket_response" in workspace
    assert 'request.headers.get("range")' in workspace
    assert "voice_stream_router" not in api


def test_v8_voice_capabilities_expose_runtime_contract_version():
    api = API.read_text(encoding="utf-8")
    voice = VOICE.read_text(encoding="utf-8")
    assert "VOICE_RUNTIME_CONTRACT_VERSION" in voice
    assert "MESSENGER_VOICE_RUNTIME_20261004_R10" in voice
    assert '"runtime_contract": voice_service.VOICE_RUNTIME_CONTRACT_VERSION' in api
