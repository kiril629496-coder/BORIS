from pathlib import Path
import subprocess

from app.services import messenger_video_media as media


def test_normalize_forces_vfr_for_mediarecorder_webm(monkeypatch, tmp_path: Path):
    source = tmp_path / "physical.webm"
    playback = tmp_path / "playback.mp4"
    thumbnail = tmp_path / "thumb.jpg"
    source.write_bytes(b"physical-webm")

    calls = []

    def fake_probe(path: Path):
        if path == source:
            return {
                "duration_ms": 84035,
                "width": 1280,
                "height": 720,
                "codec": "vp8",
                "has_audio": True,
            }
        return {
            "duration_ms": 84017,
            "width": 1280,
            "height": 720,
            "codec": "h264",
            "has_audio": True,
        }

    def fake_run(args, timeout):
        calls.append(list(args))
        if args[0] == "ffmpeg" and "-frames:v" not in args:
            playback.write_bytes(b"mp4-data")
        elif args[0] == "ffmpeg":
            thumbnail.write_bytes(b"jpg")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(media, "probe", fake_probe)
    monkeypatch.setattr(media, "_run", fake_run)

    result = media.normalize(source, playback, thumbnail, kind="video")

    transcode = calls[0]
    assert transcode[transcode.index("-fps_mode") + 1] == "vfr"
    assert result["duration_ms"] == 84017
    assert result["width"] == 1280
    assert result["height"] == 720
    assert result["playback_mime"] == "video/mp4"
