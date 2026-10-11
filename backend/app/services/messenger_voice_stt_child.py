"""Persistent local STT child for BORIS Messenger Voice.

Loads faster-whisper exactly once per child lifetime. Communication is newline
JSON over stdin/stdout; no network access, DB access, user secrets or provider API.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from app.services.messenger_voice_media import (
    FasterWhisperEngine,
    MODEL_VERSION,
    VoiceError,
)


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    try:
        engine = FasterWhisperEngine()
    except Exception:
        print(json.dumps({"kind": "boot_error", "error": "local_stt_unavailable"}), flush=True)
        return 2

    print(json.dumps({"kind": "ready", "model": MODEL_VERSION}), flush=True)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            task_id = str(request.get("task_id") or "")
            path = Path(str(request.get("path") or ""))
            if not task_id or not path.is_absolute():
                raise ValueError("invalid_request")
            result = asdict(engine.transcribe(path))
            print(json.dumps({
                "kind": "result",
                "task_id": task_id,
                "result": result,
                "error": None,
            }, ensure_ascii=False), flush=True)
        except VoiceError as exc:
            print(json.dumps({
                "kind": "result",
                "task_id": locals().get("task_id", ""),
                "result": None,
                "error": exc.code,
            }), flush=True)
        except Exception:
            print(json.dumps({
                "kind": "result",
                "task_id": locals().get("task_id", ""),
                "result": None,
                "error": "local_stt_failed",
            }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
