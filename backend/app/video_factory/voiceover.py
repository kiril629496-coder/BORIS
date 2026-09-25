# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .costs import append as cost_append

MODEL = os.environ.get("BORIS_VIDEO_TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.environ.get("BORIS_VIDEO_TTS_VOICE", "onyx")
PRICE_PER_M_CHARS = 15.0
OPERATION = "BORIS Video Factory TTS"


def narration_text(data):
    plan = data.get("plan") or {}
    scenes = plan.get("scenes") or []
    parts = []
    for scene in scenes:
        text = (scene.get("narration") or scene.get("caption") or "").strip()
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    if plan.get("mode") == "boris_tutorial":
        return ". ".join(parts)[:4096]
    budget = max(12, int(float(plan.get("duration") or 30) * 1.65))
    chosen = []
    used = 0
    cta = (plan.get("cta") or "").strip()
    for text in parts:
        words = text.split()
        reserve = len(cta.split()) if cta and text != cta else 0
        if used + len(words) + reserve <= budget or not chosen:
            chosen.append(text)
            used += len(words)
    if cta and cta not in chosen:
        cta_words = cta.split()
        available = max(0, budget - used)
        if available >= min(4, len(cta_words)):
            chosen.append(" ".join(cta_words[:available]))
    return ". ".join(chosen)[:4096]


def estimate_cost(text):
    return round(len(text or "") / 1_000_000 * PRICE_PER_M_CHARS, 8)


def _audio_duration(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        return 0.0
    try:
        return float((json.loads(proc.stdout).get("format") or {}).get("duration") or 0)
    except Exception:
        return 0.0


def _fit_audio(path, target):
    actual = _audio_duration(path)
    if actual <= 0 or target <= 0:
        return actual, 1.0
    if actual <= target * .96:
        return actual, 1.0
    ratio = actual / (target * .93)
    if ratio > 1.35:
        raise RuntimeError(f"voiceover_too_long:{actual:.2f}>{target:.2f}")
    tmp = Path(path).with_name("boris_voice_fit.mp3")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-filter:a", f"atempo={ratio:.5f}", "-vn",
         "-codec:a", "libmp3lame", "-q:a", "3", str(tmp)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode:
        raise RuntimeError("voiceover_fit_failed")
    tmp.replace(path)
    return _audio_duration(path), ratio


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tts-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _atomic_json(path: Path, body: dict) -> None:
    _atomic_bytes(path, json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def generate_boris_voice(data, projects, outputs, max_cost_usd=0.10):
    """Generate one video voiceover through the canonical paid-audio guard.

    Provider I/O, budget reservation, idempotency and ambiguous-outcome quarantine
    live only in ``openai_audio_guard.speech``. This function owns only the
    project-local artifact and post-processing. If local ffmpeg/persistence fails
    after a paid provider success, the canonical audio cache makes the next retry
    free instead of permanently blocking the project.
    """
    text = narration_text(data)
    cost_usd = estimate_cost(text)
    if not text:
        raise RuntimeError("voiceover_empty_text")
    if cost_usd > float(max_cost_usd):
        raise RuntimeError(f"voiceover_budget_exceeded:{cost_usd:.6f}>{max_cost_usd:.6f}")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("openai_not_configured")
    account_id = str(data.get("account_id") or "").strip()
    if not account_id:
        raise RuntimeError("voiceover_account_required")

    instructions = (
        "Говори по-русски уверенно, естественно и тепло, как профессиональный ведущий "
        "деловой презентации. Не спеши. Делай короткие смысловые паузы. "
        "Не используй рекламную театральность."
    )
    outdir = Path(outputs) / str(data["id"])
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "boris_voice.mp3"
    meta_path = outdir / "boris_voice.paid-meta.json"
    intent_basis = json.dumps({
        "policy": "video_tts_shared_audio_guard_v2",
        "account_id": account_id,
        "project_id": str(data.get("id") or ""),
        "model": MODEL,
        "voice": VOICE,
        "text": text,
        "instructions": instructions,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    intent_hash = hashlib.sha256(intent_basis.encode("utf-8")).hexdigest()

    meta = _read_json(meta_path)
    if out.is_file() and out.stat().st_size > 0 and meta and meta.get("intent_hash") == intent_hash:
        duration = _audio_duration(out)
        data["voiceover_meta"] = {
            "text": text, "words": len(text.split()), "duration": round(duration, 3),
            "speed_factor": float(meta.get("speed_factor") or 1.0), "model": MODEL,
            "voice": VOICE, "cache_hit": True,
        }
        return str(out), 0.0

    from app.services.openai_audio_guard import speech
    from app.usage import USD_TO_RUB
    guarded = speech(
        account_id=account_id,
        text_value=text,
        voice=VOICE,
        model=MODEL,
        operation=OPERATION,
        cost_rub=float(cost_usd * float(USD_TO_RUB or 90)),
        timeout=180,
        instructions=instructions,
        module="video_tts",
        provenance={"project_id": str(data.get("id") or ""), "source": "video_factory"},
    )
    audio = bytes((guarded or {}).get("audio") or b"")
    if not audio:
        raise RuntimeError("voiceover_guard_returned_empty_audio")
    request_id = str((guarded or {}).get("request_id") or "").strip() or ("audio-cache:" + intent_hash)
    provider_cache_hit = bool((guarded or {}).get("cache_hit"))

    _atomic_bytes(out, audio)
    fitted_duration, speed = (
        _fit_audio(out, float((data.get("plan") or {}).get("duration") or 30))
        if (data.get("plan") or {}).get("mode") != "boris_tutorial"
        else (_audio_duration(out), 1.0)
    )
    _atomic_json(meta_path, {
        "schema": 2, "intent_hash": intent_hash, "request_id": request_id,
        "cost_rub": float((guarded or {}).get("cost_rub") or 0),
        "speed_factor": round(speed, 4), "duration": round(fitted_duration, 3),
        "canonical_audio_guard": True,
    })
    data["voiceover_meta"] = {
        "text": text, "words": len(text.split()), "duration": round(fitted_duration, 3),
        "speed_factor": round(speed, 4), "model": MODEL, "voice": VOICE,
        "cache_hit": provider_cache_hit,
    }
    charged_usd = 0.0 if provider_cache_hit else cost_usd
    if charged_usd > 0:
        cost_append(projects, data["id"], "openai", MODEL, "tts", len(text), "characters", charged_usd,
                    {"voice": VOICE, "request_id": request_id, "duration": round(fitted_duration, 3),
                     "speed_factor": round(speed, 4), "canonical_audio_guard": True})
    return str(out), charged_usd
