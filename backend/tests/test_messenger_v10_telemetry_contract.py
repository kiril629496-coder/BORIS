from pathlib import Path

ROOT = Path(__file__).parents[1]
TELEMETRY = ROOT / "app/services/messenger_telemetry.py"
API = ROOT / "app/api/internal_messenger.py"
VOICE = ROOT / "app/services/messenger_voice.py"
VOICE_WORKER = ROOT / "app/services/messenger_voice_worker.py"
VIDEO = ROOT / "app/services/messenger_video.py"
VIDEO_WORKER = ROOT / "app/services/messenger_video_worker.py"
CALLS = ROOT / "app/services/messenger_calls.py"
FRONTEND = Path("/root/BORIS/frontend/app/messenger/page.tsx")
SW = Path("/root/BORIS/frontend/public/sw.js")

REQUIRED_EVENTS = {
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
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_v10_telemetry_event_contract_is_complete():
    telemetry = read(TELEMETRY)
    combined = "\n".join(read(p) for p in (API, VOICE, VOICE_WORKER, VIDEO, VIDEO_WORKER, CALLS, FRONTEND))
    for event in REQUIRED_EVENTS:
        assert f'"{event}"' in telemetry
        assert f'"{event}"' in combined
    assert '@router.post("/telemetry")' in read(API)
    assert "messenger_telemetry" in read(API)


def test_v10_telemetry_sanitizer_drops_private_content():
    import sys
    sys.path.insert(0, str(ROOT))
    from app.services.messenger_telemetry import sanitize_details

    result = sanitize_details({
        "call_type": "video",
        "duration_ms": 1234,
        "relay": True,
        "transport": "udp",
        "source": "browser",
        "text": "private message",
        "body": "private body",
        "transcript": "private transcript",
        "segments": [{"text": "secret"}],
        "sdp": "v=0...",
        "description": {"type": "offer", "sdp": "secret"},
        "candidate": "candidate:secret",
        "turn_secret": "secret",
        "token": "secret",
        "media_bytes": "secret",
        "audio": "secret",
        "video": "secret",
        "unknown": "secret",
    })
    assert result == {
        "call_type": "video",
        "duration_ms": 1234,
        "relay": True,
        "transport": "udp",
        "source": "browser",
    }


def test_v10_telemetry_workers_and_calls_emit_terminal_outcomes_only():
    voice_worker = read(VOICE_WORKER)
    video_worker = read(VIDEO_WORKER)
    calls = read(CALLS)
    assert '"stt_ready" if outcome == "ready" else "stt_no_speech"' in voice_worker
    assert '"stt_failed"' in voice_worker
    assert '"transcode_success"' in video_worker
    assert '"transcode_failed"' in video_worker
    for event in ("call_started", "call_answered", "call_rejected", "call_missed", "call_failed", "reconnect_attempt", "reconnect_success"):
        assert f'"{event}"' in calls


def test_v10_browser_telemetry_has_no_sensitive_payload_forwarding():
    frontend = read(FRONTEND)
    assert '"recordings_started"' in frontend
    assert '"video_started"' in frontend
    assert '"turn_relay_used"' in frontend
    assert "reportTurnRelayUsage" in frontend
    # WebRTC stats telemetry only reports safe categorical connection metadata.
    block = frontend[frontend.index("const reportTurnRelayUsage"):frontend.index("const prepareCallPeer")]
    for forbidden in ("localDescription", "remoteDescription", "candidate.toJSON", "sdp"):
        assert forbidden not in block


def test_v10_push_click_deeplink_evidence_is_privacy_safe():
    telemetry = read(TELEMETRY)
    frontend = read(FRONTEND)
    sw = read(SW)
    assert '"push_clicked"' in telemetry
    assert '"deeplink_opened"' in telemetry
    assert 'targetUrl.searchParams.set("push_click", "1")' in sw
    assert 'url.searchParams.get("push_click") !== "1"' in frontend
    assert 'url.searchParams.delete("push_click")' in frontend
    assert 'recordMessengerTelemetry("push_clicked", { source: "push" })' in frontend
    assert 'recordMessengerTelemetry("deeplink_opened", { source: "push" })' in frontend
    # Click evidence must never forward notification title/body or target URL into telemetry details.
    marker_block = frontend[frontend.index('url.searchParams.get("push_click")'):frontend.index("const currentAccountAccess")]
    for forbidden in ("notification", "title", "body", "token"):
        assert forbidden not in marker_block
