"""Local, bounded media normalization for BORIS Messenger video."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

MAX_BYTES = 150 * 1024 * 1024
MAX_VIDEO_MS = 5 * 60 * 1000
MAX_NOTE_MS = 2 * 60 * 1000
MIMES = {"video/webm", "video/mp4", "video/quicktime", "video/x-m4v"}

class VideoError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.TimeoutExpired as exc:
        raise VideoError("video_processing_timeout") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VideoError("video_processing_failed") from exc

def _packet_duration_seconds(path: Path) -> float:
    """Fallback for MediaRecorder WebM files that omit container duration.

    Chrome/Chromium can emit valid live-style WebM with video/audio streams,
    width/height and packet timestamps but no format/stream duration metadata.
    Read packet timestamps only when the normal metadata path is missing.
    """
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,duration_time",
        "-of", "csv=p=0", str(path),
    ], 30)
    end_s = 0.0
    for raw in result.stdout.splitlines():
        parts = [part.strip() for part in raw.split(",")]
        if not parts or not parts[0]:
            continue
        try:
            pts = float(parts[0])
        except (TypeError, ValueError):
            continue
        duration = 0.0
        if len(parts) > 1 and parts[1]:
            try:
                duration = max(0.0, float(parts[1]))
            except (TypeError, ValueError):
                duration = 0.0
        end_s = max(end_s, pts + duration)
    return end_s


def probe(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], 30)
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        raise VideoError("video_probe_failed") from exc
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise VideoError("video_stream_missing")
    duration_s = float((payload.get("format") or {}).get("duration") or video.get("duration") or 0)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if duration_s <= 0:
        duration_s = _packet_duration_seconds(path)
    if duration_s <= 0 or width <= 0 or height <= 0:
        raise VideoError("video_metadata_invalid")
    return {
        "duration_ms": max(1, int(round(duration_s * 1000))),
        "width": width,
        "height": height,
        "codec": str(video.get("codec_name") or ""),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }

def normalize(source: Path, playback: Path, thumbnail: Path, *, kind: str) -> dict:
    if not source.is_file() or source.stat().st_size <= 0 or source.stat().st_size > MAX_BYTES:
        raise VideoError("video_size_limit")
    meta = probe(source)
    limit = MAX_NOTE_MS if kind == "video_note" else MAX_VIDEO_MS
    if meta["duration_ms"] > limit:
        raise VideoError("video_duration_limit")
    playback.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    playback.unlink(missing_ok=True)
    thumbnail.unlink(missing_ok=True)
    scale = "scale=min(1280\\,iw):-2"
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
        "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-fps_mode", "vfr",
        "-movflags", "+faststart", "-f", "mp4", str(playback),
    ], 600)
    if not playback.is_file() or playback.stat().st_size <= 0 or playback.stat().st_size > MAX_BYTES:
        raise VideoError("video_output_invalid")
    seek = min(max(meta["duration_ms"] / 2000.0, 0.2), 2.0)
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{seek:.3f}", "-i", str(playback), "-frames:v", "1",
        "-vf", "scale=min(480\\,iw):-2", "-q:v", "4", "-c:v", "mjpeg",
        "-f", "mjpeg", str(thumbnail),
    ], 60)
    if not thumbnail.is_file() or thumbnail.stat().st_size <= 0:
        raise VideoError("thumbnail_failed")
    final = probe(playback)
    return {
        "duration_ms": final["duration_ms"],
        "width": final["width"],
        "height": final["height"],
        "playback_size_bytes": playback.stat().st_size,
        "playback_mime": "video/mp4",
    }
