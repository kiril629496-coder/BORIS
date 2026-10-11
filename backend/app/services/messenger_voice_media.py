"""Bounded local audio processing. No network providers or automatic downloads."""
from __future__ import annotations

import array
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from typing import Protocol

MAX_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 600
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_PATH = Path("/root/BORIS/models/faster-whisper/models--Systran--faster-whisper-small/snapshots") / MODEL_REVISION
MODEL_VERSION = "faster-whisper-1.2.1:small:" + MODEL_REVISION + ":cpu:int8"
MIMES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/aac", "audio/mpeg", "audio/wav", "audio/x-wav"}


class VoiceError(Exception):
    """Only this stable code may leave the processing boundary."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def run_media(argv: list[str], timeout: int = 90) -> bytes:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired as exc:
        raise VoiceError("media_timeout") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VoiceError("invalid_audio") from exc


def probe(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise VoiceError("audio_size_limit")
    try:
        data = json.loads(run_media([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-probesize", "5000000", "-analyzeduration", "10000000",
            "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,duration", "-of", "json", str(path)
        ], 15))
        streams = data.get("streams", [])
        if len(streams) != 1 or streams[0].get("codec_type") != "audio":
            raise VoiceError("invalid_audio")
        codec = streams[0].get("codec_name")
        if codec not in {"opus", "vorbis", "aac", "mp3", "pcm_s16le", "pcm_s24le", "pcm_f32le"}:
            raise VoiceError("invalid_audio")
        formats = set(data["format"]["format_name"].split(","))
        if not formats.intersection({"webm", "matroska", "ogg", "mov", "mp4", "m4a", "aac", "mp3", "wav"}):
            raise VoiceError("invalid_audio")
        raw_duration = data["format"].get("duration", streams[0].get("duration"))
        duration = float(raw_duration) if raw_duration is not None else None
        # MediaRecorder WebM may omit duration; decoded PCM is authoritative below.
        if duration is not None and (not math.isfinite(duration) or duration <= 0 or duration > MAX_SECONDS + .05):
            raise VoiceError("audio_duration_limit")
        return {"codec": codec, "container": sorted(formats), "duration": duration}
    except (KeyError, ValueError, TypeError) as exc:
        raise VoiceError("invalid_audio") from exc


def normalize(original: Path, playback: Path) -> dict:
    info = probe(original)
    # Limit decoded output even when container duration lies. Temporary PCM is
    # private, bounded to 601 seconds, and removed on every failure path.
    with tempfile.TemporaryDirectory(prefix="voice-", dir=original.parent) as tmp:
        pcm = Path(tmp) / "speech.pcm"
        common = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1",
                  "-protocol_whitelist", "file,pipe", "-i", str(original),
                  "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000"]
        run_media(common + ["-t", "601", "-f", "s16le", "-y", str(pcm)])
        samples = array.array("h", pcm.read_bytes())
        duration = len(samples) / 16000
        if not 0 < duration <= MAX_SECONDS + .05:
            raise VoiceError("audio_duration_limit")
        width = max(1, math.ceil(len(samples) / 64))
        peaks = [round(max(abs(v) for v in samples[i:i+width]) / 32768 * 100)
                 for i in range(0, len(samples), width)]
        try:
            run_media(["ffmpeg", "-nostdin", "-v", "error", "-threads", "1",
                       "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", str(pcm),
                       "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "32k",
                       "-movflags", "+faststart", "-f", "mp4", "-y", str(playback)])
        except Exception:
            playback.unlink(missing_ok=True)
            raise
        return {"duration_ms": round(duration * 1000), "codec": "aac",
                "mime": "audio/mp4", "size_bytes": playback.stat().st_size,
                "original_codec": info["codec"], "waveform_peaks": peaks}


def parse_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if not value:
        return 0, size - 1, False
    import re
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size <= 0 or len(value) > 80:
        raise VoiceError("invalid_range")
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise VoiceError("invalid_range")
        start, end = max(0, size - suffix), size - 1
    if start > end or start >= size:
        raise VoiceError("invalid_range")
    return start, end, True


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration_ms: int
    segments: list[dict]


class VoiceTranscriptionEngine(Protocol):
    def transcribe(self, path: Path) -> TranscriptionResult: ...


class FasterWhisperEngine:
    SEGMENT_NO_SPEECH_MAX = 0.75
    AUTO_LANGUAGE_MIN_PROB = 0.60

    def __init__(self):
        # The model must be provisioned once. Never download during a job.
        if not (MODEL_PATH / "model.bin").is_file():
            raise VoiceError("local_model_unavailable")
        from faster_whisper import WhisperModel
        self.model = WhisperModel(str(MODEL_PATH), device="cpu", compute_type="int8",
                                  cpu_threads=2, num_workers=1, local_files_only=True)

    @staticmethod
    def _cyrillic_ratio(value: str) -> float:
        letters = [ch for ch in value.lower() if ch.isalpha()]
        if not letters:
            return 0.0
        cyrillic = sum("а" <= ch <= "я" or ch == "ё" for ch in letters)
        return cyrillic / len(letters)

    def _decode(self, pcm: Path, language: str | None) -> dict:
        segments, info = self.model.transcribe(
            str(pcm), language=language, task="transcribe", beam_size=5,
            vad_filter=True, condition_on_previous_text=False, temperature=0.0,
            no_speech_threshold=self.SEGMENT_NO_SPEECH_MAX, log_prob_threshold=-1.0,
        )
        accepted = []
        qualities = []
        for segment in segments:
            value = segment.text.strip()
            if not value:
                continue
            if float(segment.no_speech_prob) >= self.SEGMENT_NO_SPEECH_MAX:
                continue
            if float(segment.avg_logprob) < -1.0 or float(segment.compression_ratio) >= 2.4:
                continue
            accepted.append({"start": segment.start, "end": segment.end, "text": value})
            qualities.append(float(segment.avg_logprob))
        text_value = " ".join(item["text"] for item in accepted).strip()
        return {
            "text": text_value,
            "language": info.language if accepted else None,
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "duration_ms": round(float(info.duration) * 1000),
            "segments": accepted,
            "avg_logprob": sum(qualities) / len(qualities) if qualities else -99.0,
        }

    def transcribe(self, path: Path) -> TranscriptionResult:
        with tempfile.TemporaryDirectory(prefix="voice-stt-") as tmp:
            pcm = Path(tmp) / "speech.wav"
            run_media(["ffmpeg", "-nostdin", "-v", "error", "-threads", "1",
                       "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:a:0",
                       "-t", "600.05", "-ac", "1", "-ar", "16000", "-y", str(pcm)])
            selected = self._decode(pcm, None)
            preferred = os.getenv("MESSENGER_STT_PREFERRED_LANGUAGE", "ru").strip().lower()
            should_retry_preferred = (
                preferred not in {"", "auto"}
                and preferred != selected["language"]
                and (
                    not selected["text"]
                    or selected["language_probability"] < self.AUTO_LANGUAGE_MIN_PROB
                )
            )
            if should_retry_preferred:
                fallback = self._decode(pcm, preferred)
                # Prefer a forced Russian pass only when it actually contains
                # substantial Cyrillic text and is not materially less probable
                # than the low-confidence auto-detected candidate.
                use_fallback = bool(fallback["text"])
                if preferred == "ru" and use_fallback:
                    use_fallback = (
                        self._cyrillic_ratio(fallback["text"]) >= 0.35
                        and (
                            not selected["text"]
                            or fallback["avg_logprob"] >= selected["avg_logprob"] - 0.25
                        )
                    )
                if use_fallback:
                    selected = fallback
            return TranscriptionResult(
                selected["text"],
                selected["language"] if selected["text"] else None,
                selected["duration_ms"],
                selected["segments"],
            )


if __name__ == "__main__":
    import sys
    if sys.platform == "linux":
        import ctypes, signal
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() == 1:
            raise SystemExit(1)
    # Invoked only by the isolated worker with a server-generated file path.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        print(json.dumps(asdict(FasterWhisperEngine().transcribe(Path(sys.argv[1]))), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error_code": exc.code if isinstance(exc, VoiceError) else "local_stt_failed"}))
        raise SystemExit(1)
