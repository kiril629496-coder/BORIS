# -*- coding: utf-8 -*-
"""Ручки Command Center. Тонкий транспорт: вся логика в command_flow.

Роутер регистрируется БЕЗ общего гейта _protected — как manager_router и
crm: check_account_access требует одиночный account_id, а здесь есть
запросы без аккаунта. Права проверяются внутри, через command_policy.
"""
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Any, Dict, Optional
import os
import math
import subprocess
import tempfile
import uuid
import requests

from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as limits
from app.services import command_policy as policy
from app.services import command_registry as registry
from app.services import command_store as store
from app.services.command_contract import CommandInput
from app.services import command_ops  # noqa: F401 — регистрирует исполнителей

try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/command", tags=["command"])


class CommandBody(BaseModel):
    command_id: Optional[str] = None
    source: str = "text"
    text: Optional[str] = None
    transcript: Optional[str] = None
    account_id: Optional[str] = None
    page_context: Optional[Dict[str, Any]] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    locale: str = "ru"
    device: Optional[str] = None
    audio_duration: Optional[float] = None
    audio_format: Optional[str] = None
    transcription_model: Optional[str] = None


class CommandRef(BaseModel):
    command_id: str


@router.post("/parse")
def parse(body: CommandBody, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        # OWNER_CONTROL_PLANE_CAPTURE_BEFORE_EXECUTION_V2. A command that cannot
        # be durably recorded is not allowed to create an invisible side effect.
        # Generate the canonical command UUID up front so Command Center and
        # Control Plane share the same immutable correlation reference.
        if not body.command_id:
            body.command_id = str(uuid.uuid4())
        captured = None
        if str(getattr(_cur, "role", "") or "").lower() == "owner":
            raw = (body.text or body.transcript or "").strip()
            if raw:
                try:
                    from app.services.control_plane import capture_owner_instruction
                    captured = capture_owner_instruction(
                        db, text=raw, user_id=getattr(_cur, "id", None), source="command_center",
                        source_ref=body.command_id, account_id=body.account_id,
                    )
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    try:
                        from app.services.reliability import record_event
                        record_event(db, "control_plane", "owner_instruction.capture_failed", "Команда владельца остановлена: не удалось записать её в Control Plane", severity="critical", account_id=body.account_id, details={"error_type": type(exc).__name__, "command_id": body.command_id})
                        db.commit()
                    except Exception:
                        db.rollback()
                    return {"status":"failed","command_id":body.command_id,"error_code":"control_plane_capture_failed"}
        inp = CommandInput(**body.dict())
        result = flow.start(db, _cur, inp)
        if isinstance(result, dict) and captured is not None:
            result["control_plane"] = captured
        return result
    finally:
        db.close()


@router.post("/confirm")
def confirm(body: CommandRef, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return flow.confirm(db, _cur, body.command_id)
    finally:
        db.close()


@router.post("/cancel")
def cancel(body: CommandRef, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return flow.cancel(db, _cur, body.command_id)
    finally:
        db.close()


@router.get("/status/{command_id}")
def status(command_id: str, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = store.load(db, command_id)
        if not row or row.get("user_id") != policy._uid(_cur):
            return {"status": "error", "error_code": "forbidden_account"}
        replay = flow._existing_out(row) or {}
        return {"status": "ok", "command_id": str(row.get("command_id")),
                "state": row.get("status"), "stage": None,
                "intent": row.get("intent"), "operation": row.get("operation"),
                "risk_level": row.get("risk_level"),
                "units": row.get("units"),
                "human_summary": row.get("human_summary"),
                "clarify_question": row.get("clarify_question"),
                "answer": replay.get("answer"), "data": replay.get("data"),
                "error_code": row.get("error_code")}
    finally:
        db.close()


@router.get("/history")
def history(limit: int = 20, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return {"status": "ok",
                "commands": store.history(db, policy._uid(_cur), min(limit, 100))}
    finally:
        db.close()


@router.get("/capabilities")
def capabilities(account_id: str = "", _cur=Depends(get_current_user)):
    """Что доступно этому пользователю на этом аккаунте: флаги, остаток
    пула и список операций. Нужна, чтобы интерфейс не предлагал того,
    чего у клиента нет."""
    db = SessionLocal()
    try:
        if not account_id:
            return {"status": "error", "error_code": "forbidden_account"}
        owner = limits.resolve_billing_owner(db, account_id)
        if not owner:
            return {"status": "error", "error_code": "no_billing_owner"}
        kind, oid = owner
        if not policy.account_visible(db, _cur, account_id):
            return {"status": "error", "error_code": "forbidden_account"}
        feats = limits.features_of(db, kind, oid)
        pool = limits.pool_state(db, kind, oid)
        ops = []
        for op in registry.REGISTRY.values():
            if not op.intents:
                continue
            if policy.has_permission(db, _cur, account_id, op.permission):
                ops.append({"operation": op.id, "title": op.title,
                            "risk": op.risk, "units": op.units})
        return {"status": "ok", "features": feats,
                "pool": {"purchased": pool["purchased"], "used": pool["used"],
                         "left": pool["left"], "active": pool["active"],
                         "paid_until": pool["paid_until"].isoformat()
                         if pool["paid_until"] else None},
                "operations": ops}
    finally:
        db.close()


VOICE_PRESETS = {
    "business_male": {"voice": "onyx", "label": "Деловой мужчина"},
    "friendly_male": {"voice": "echo", "label": "Дружелюбный мужчина"},
    "business_female": {"voice": "nova", "label": "Деловая женщина"},
    "friendly_female": {"voice": "shimmer", "label": "Дружелюбная женщина"},
}


def _voice_access(db, cur, account_id: str, feature: str):
    if not account_id or not policy.account_visible(db, cur, account_id):
        raise HTTPException(status_code=403, detail="forbidden_account")
    owner = limits.resolve_billing_owner(db, account_id)
    if not owner:
        raise HTTPException(status_code=403, detail="no_billing_owner")
    kind, oid = owner
    if not limits.has_feature(db, kind, oid, feature):
        raise HTTPException(status_code=403, detail="voice_feature_disabled")
    # Feature flags describe what the tariff includes; the command pool is the
    # paid-period clock. Never spend on STT/TTS when that paid period expired.
    pool = limits.pool_state(db, kind, oid, limits.RESOURCE_COMMANDS)
    if not pool.get("active"):
        raise HTTPException(status_code=402, detail="command_subscription_inactive")


def _probe_audio_duration(raw: bytes, filename: str) -> float | None:
    """Measure uploaded audio locally so billing never trusts browser-reported duration."""
    suffix = os.path.splitext(str(filename or ""))[1].lower()
    if suffix not in {".webm", ".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".oga", ".flac", ".mpeg", ".mpga"}:
        suffix = ".audio"
    path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="boris-voice-", suffix=suffix, delete=False) as fh:
            path = fh.name
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        proc = subprocess.run(
            ["/usr/bin/ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=8, check=False,
        )
        if proc.returncode != 0:
            return None
        value = float((proc.stdout or "").strip())
        if not math.isfinite(value) or value <= 0:
            return None
        return value
    except Exception:
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


@router.post("/voice/transcribe")
async def voice_transcribe(account_id: str = Form(...), duration_seconds: float = Form(0), audio: UploadFile = File(...), _cur=Depends(get_current_user)):
    """Голос клиента -> текст. Никаких клонов голоса; аудио не сохраняется."""
    db = SessionLocal()
    try:
        _voice_access(db, _cur, account_id, registry.FEATURE_VOICE)
    finally:
        db.close()
    raw = await audio.read()
    if not raw or len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="invalid_audio")
    filename = audio.filename or "voice.webm"
    measured_seconds = _probe_audio_duration(raw, filename)
    if measured_seconds is None:
        raise HTTPException(status_code=400, detail="audio_duration_unknown")
    if measured_seconds > 300.0:
        raise HTTPException(status_code=413, detail="voice_command_too_long")
    seconds = float(measured_seconds)
    # Privacy boundary: raw client audio never leaves our Russian server.
    # Voice commands use the same local faster-whisper contour as call analysis.
    from app.api.calltracking import _transcribe, _asr_hint, _asr_fix, _LOCAL_STT_MODEL_NAME
    try:
        text_value = _asr_fix(
            account_id,
            _transcribe(raw, filename, _asr_hint(account_id), account_id)
        ).strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="local_transcription_failed") from exc
    return {"status": "ok", "text": text_value, "model": _LOCAL_STT_MODEL_NAME,
            "duration_seconds": round(seconds, 2),
            "cost_usd": 0.0,
            "cache_hit": False,
            "privacy": "local_only"}


class SpeechBody(BaseModel):
    account_id: str
    text: str
    voice: str = "business_male"


@router.post("/voice/speech")
def voice_speech(body: SpeechBody, _cur=Depends(get_current_user)):
    """Текст BORIS -> синтетический AI-голос. Только четыре системных пресета."""
    db = SessionLocal()
    try:
        _voice_access(db, _cur, body.account_id, registry.FEATURE_TTS)
    finally:
        db.close()
    preset = VOICE_PRESETS.get(body.voice)
    if not preset:
        raise HTTPException(status_code=400, detail="unknown_voice")
    txt = (body.text or "").strip()[:4096]
    if not txt:
        raise HTTPException(status_code=400, detail="empty_text")
    # External TTS cannot receive real personal data because audio output cannot
    # safely restore placeholders. Replace PII with neutral spoken labels.
    from app.services.ai_privacy import safe_external_ai_payload
    provider_txt, privacy_vault = safe_external_ai_payload(txt)
    spoken_labels = {
        "PERSON": "клиент",
        "PHONE": "номер телефона",
        "EMAIL": "электронная почта",
        "HANDLE": "контакт",
        "ADDRESS": "адрес",
        "IP": "сетевой адрес",
        "SNILS": "идентификатор",
        "PASSPORT": "паспортные данные",
        "ID": "идентификатор",
        "NUMBER": "номер",
    }
    for token in privacy_vault.mapping:
        kind = token.replace("[[BORIS_", "").split("_", 1)[0]
        provider_txt = provider_txt.replace(token, spoken_labels.get(kind, "данные клиента"))
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="openai_not_configured")
    cost_usd = len(provider_txt) / 1_000_000 * 15.0
    from app.usage import USD_TO_RUB
    from app.services.openai_audio_guard import speech as _guarded_speech
    try:
        result = _guarded_speech(
            account_id=body.account_id, text_value=provider_txt, voice=preset["voice"],
            model="tts-1", operation="голосовой ответ BORIS",
            cost_rub=cost_usd * float(USD_TO_RUB or 90), timeout=180,
        )
    except RuntimeError as exc:
        code = str(exc)
        if "OUTCOME_UNCERTAIN" in code:
            raise HTTPException(status_code=409, detail="audio_paid_outcome_uncertain")
        if "BUDGET" in code:
            raise HTTPException(status_code=429, detail="ai_budget_guard")
        raise HTTPException(status_code=502, detail="speech_failed")
    headers = {
        "X-Boris-Voice": body.voice,
        "X-Boris-Chars": str(len(txt)),
        "X-Boris-Cost-Usd": f"{0.0 if result.get('cache_hit') else cost_usd:.8f}",
        "X-Boris-Cache-Hit": "1" if result.get("cache_hit") else "0",
    }
    return Response(content=bytes(result.get("audio") or b""), media_type="audio/mpeg", headers=headers)


@router.get("/voice/presets")
def voice_presets(_cur=Depends(get_current_user)):
    return {"status": "ok", "voices": [{"id": k, "label": v["label"]} for k, v in VOICE_PRESETS.items()]}
