from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[1]
API = ROOT / "app/api/internal_messenger.py"
WORKSPACE = ROOT / "app/api/messenger_workspace.py"
VIDEO = ROOT / "app/services/messenger_video.py"
MEDIA = ROOT / "app/services/messenger_video_media.py"
WORKER = ROOT / "app/services/messenger_video_worker.py"
CALLS = ROOT / "app/services/messenger_calls.py"
MAINT = ROOT / "app/services/messenger_call_maintenance.py"

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def test_v9_backend_video_private_bounded_worker_backed():
    api, video, media, worker, workspace = map(read, (API, VIDEO, MEDIA, WORKER, WORKSPACE))
    assert "MESSENGER_VIDEO_RUNTIME_20261005_V2" in video
    assert "MAX_BYTES = 150 * 1024 * 1024" in media
    assert "MAX_VIDEO_MS = 5 * 60 * 1000" in media
    assert "MAX_NOTE_MS = 2 * 60 * 1000" in media
    assert '"-f", "mp4"' in media
    assert '"-f", "mjpeg"' in media
    assert "_require_member" in video and "_require_reply" in video
    assert "validate_session" in video
    assert "messenger_video_processing" in video
    assert "pg_try_advisory_lock" in worker
    assert "processing_lease_until" in worker
    assert "actual_kopeks=0" in worker
    assert '"/video/{asset_id}/playback-ticket"' in api
    assert '@router.get("/video/{asset_id}/stream")' in workspace
    assert '@router.get("/video/{asset_id}/thumbnail")' in workspace
    assert '"Cache-Control":"private, no-store"' in video
    assert "parse_range" in video
    assert ":video-original" not in video
    assert ":video-playback" not in video
    assert ":video-thumb" not in video
    assert 'f"internal:{conversation_id}:{uuid4().hex}"' in video

def test_v9_backend_calls_signaling_security_and_timeouts():
    api, calls, maint = map(read, (API, CALLS, MAINT))
    assert "MESSENGER_CALLS_RUNTIME_20261005_V4" in calls
    for marker in (
        "client_call_id", "_require_member", "_require_reply", "internal_call_events",
        "internal.call.invite", "MESSENGER_TURN_SECRET", "MESSENGER_TURN_URLS",
        "hmac.new", "sha1", "CALL_RING_TIMEOUT_SECONDS",
        "CALL_HEARTBEAT_TIMEOUT_SECONDS", "heartbeat_timeout",
        "pg_advisory_xact_lock", "hashtextextended",
        "caller_user_id", "peer_user_id",
        "Этот сигнал может отправить только инициатор звонка",
        "expire_all", "heartbeat", "return {\"status\":\"ok\",\"heartbeat\":True}",
    ):
        assert marker in calls or marker in api
    assert "RTCPeerConnection" not in calls
    assert "payload_json='{}'::jsonb" in calls
    assert 'calls_service.enabled("MESSENGER_TURN_ENABLED")' in api
    assert "db.commit()" in api
    assert "pg_try_advisory_lock" in maint
    assert "calls.expire_all" in maint
    assert "old_events_deleted" in maint

def test_v9_backend_video_normalization_real_ffmpeg(tmp_path):
    sys.path.insert(0, str(ROOT))
    from app.services.messenger_video_media import normalize, probe
    source = tmp_path / "source.webm"
    playback = tmp_path / "playback.mp4"
    thumbnail = tmp_path / "thumb.jpg"
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000",
        "-t", "1.2", "-c:v", "libvpx-vp9", "-c:a", "libopus", str(source),
    ], check=True, timeout=60)
    result = normalize(source, playback, thumbnail, kind="video")
    assert playback.is_file() and playback.stat().st_size > 0
    assert thumbnail.is_file() and thumbnail.stat().st_size > 0
    assert result["playback_mime"] == "video/mp4"
    meta = probe(playback)
    assert 800 <= meta["duration_ms"] <= 1800
    assert meta["width"] == 320 and meta["height"] == 240


def test_v9_backend_video_live_webm_without_duration_metadata(tmp_path):
    """Chrome MediaRecorder can emit valid live WebM without container duration."""
    sys.path.insert(0, str(ROOT))
    from app.services.messenger_video_media import normalize, probe

    source = tmp_path / "live.webm"
    playback = tmp_path / "live.mp4"
    thumbnail = tmp_path / "live.jpg"
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=650:sample_rate=48000",
        "-t", "1.4", "-c:v", "libvpx", "-c:a", "libopus",
        "-f", "webm", "-live", "1", str(source),
    ], check=True, timeout=60)

    # The regression fixture intentionally has no normal duration metadata.
    raw = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(source),
    ], check=True, capture_output=True, text=True, timeout=30)
    payload = __import__("json").loads(raw.stdout)
    video = next(s for s in payload["streams"] if s.get("codec_type") == "video")
    assert not (payload.get("format") or {}).get("duration")
    assert not video.get("duration")

    meta = probe(source)
    assert 900 <= meta["duration_ms"] <= 2200
    assert meta["width"] == 320 and meta["height"] == 240

    result = normalize(source, playback, thumbnail, kind="video_note")
    assert result["playback_mime"] == "video/mp4"
    assert playback.is_file() and playback.stat().st_size > 0
    assert thumbnail.is_file() and thumbnail.stat().st_size > 0
