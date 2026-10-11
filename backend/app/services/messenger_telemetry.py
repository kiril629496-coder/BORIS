"""Privacy-safe operational telemetry for BORIS Messenger media and calls."""
from __future__ import annotations

from typing import Any

from app.services.messenger_security_audit import append_security_audit

EVENTS = frozenset({
    "recordings_started",
    "recordings_sent",
    "voice_failed",
    "stt_ready",
    "stt_no_speech",
    "stt_failed",
    "video_started",
    "video_uploaded",
    "transcode_success",
    "transcode_failed",
    "call_started",
    "call_answered",
    "call_rejected",
    "call_missed",
    "call_failed",
    "reconnect_attempt",
    "reconnect_success",
    "turn_relay_used",
    "push_clicked",
    "deeplink_opened",
})

# Never retain conversation content or connection secrets in telemetry.
FORBIDDEN_KEYS = frozenset({
    "text", "body", "message", "transcript", "transcription", "segments",
    "sdp", "description", "offer", "answer", "candidate", "candidates",
    "turn_secret", "turn_password", "password", "token", "authorization",
    "audio", "video", "blob", "bytes", "media", "media_bytes",
})

ALLOWED_DETAIL_KEYS = frozenset({
    "media_kind",
    "call_type",
    "outcome",
    "error_code",
    "reason",
    "elapsed_ms",
    "duration_ms",
    "size_bytes",
    "width",
    "height",
    "attempt",
    "transport",
    "relay",
    "network",
    "source",
})


def sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for raw_key, raw_value in (details or {}).items():
        key = str(raw_key).strip().lower()
        if key in FORBIDDEN_KEYS or key not in ALLOWED_DETAIL_KEYS:
            continue
        if isinstance(raw_value, bool):
            clean[key] = raw_value
        elif isinstance(raw_value, int):
            clean[key] = max(-10**12, min(raw_value, 10**12))
        elif isinstance(raw_value, float):
            clean[key] = max(-10**12, min(raw_value, 10**12))
        elif isinstance(raw_value, str):
            clean[key] = raw_value[:96]
    return clean


def record(
    db,
    event: str,
    *,
    user_id: int | None = None,
    account_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    name = str(event).strip()
    if name not in EVENTS:
        raise ValueError("unsupported_messenger_telemetry_event")
    return append_security_audit(
        db,
        action="telemetry:" + name,
        user_id=user_id,
        account_id=account_id,
        entity_type=entity_type,
        entity_id=entity_id,
        result="ok",
        details=sanitize_details(details),
    )
