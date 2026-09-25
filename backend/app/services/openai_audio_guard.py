# -*- coding: utf-8 -*-
"""Exactly-once safety contour for paid OpenAI audio operations.

Used by Command Center transcription and speech. One semantic intent is
serialized across workers, successful artifacts are cached durably, and an
ambiguous provider outcome is quarantined instead of blindly repurchased.
"""
from __future__ import annotations

import hashlib
import json
import os
from app.services.ai_privacy import safe_external_ai_payload
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import text, create_engine
from sqlalchemy.pool import NullPool

from app.db.session import DATABASE_URL
from app.services import ai_budget
from app.services.ai_guard_audit import log_guard_event
from app.usage import log_usage
from app.services.exception_observability import observe_suppressed


def _root() -> Path:
    return Path(os.environ.get("BORIS_AUDIO_PAID_CACHE_DIR", "/root/BORIS/backend/.ai_paid_cache/audio"))


def _paths(intent_hash: str, ext: str) -> tuple[Path, Path, Path]:
    root = _root()
    return root / (intent_hash + ext), root / (intent_hash + ".json"), root / (intent_hash + ".uncertain.json")


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".audio-paid-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(bytes(payload)); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass


def _write_json(path: Path, body: dict) -> None:
    _atomic(path, json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _intent(account_id: str, operation: str, basis: dict) -> tuple[str, str]:
    raw = json.dumps({"policy":"openai_audio_exactly_once_v1", "account_id":str(account_id),
                      "operation":str(operation), "basis":basis},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest, "audio:" + digest


def _lock(account_id: str, intent_key: str):
    raw = int.from_bytes(hashlib.blake2b((str(account_id)+"|"+intent_key).encode("utf-8"), digest_size=8).digest(), "big")
    key = raw if raw < (1 << 63) else raw - (1 << 64)
    engine=create_engine(DATABASE_URL,poolclass=NullPool,pool_pre_ping=True,
                         connect_args={"application_name":"boris-openai-audio-lock"})
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("select pg_advisory_lock(:k)"), {"k":key})
    return conn, key, engine


def _lock_parts(value):
    """Accept the current (conn,key,engine) lock and legacy 2-part zero-paid mocks."""
    parts=tuple(value)
    if len(parts)==2: return parts[0],parts[1],None
    if len(parts)==3: return parts
    raise RuntimeError("AUDIO_LOCK_CONTRACT_INVALID")


def _unlock(conn, key, engine=None):
    try: conn.execute(text("select pg_advisory_unlock(:k)"), {"k":key})
    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=91)
    try: conn.close()
    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=93)
    if engine is not None:
        try: engine.dispose()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=96)


def _quarantine(account_id: str, operation: str, intent_hash: str, uncertain_path: Path,
                error_code: str, provider_request_id: str | None = None) -> None:
    _write_json(uncertain_path, {"schema":1, "intent_hash":intent_hash,
                                "status":"provider_outcome_uncertain", "error_code":error_code,
                                "provider_request_id":provider_request_id or None,
                                "created_at_unix":int(time.time())})
    log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                    event_code="AUDIO_OUTCOME_UNCERTAIN", status="quarantined",
                    intent_key="audio:"+intent_hash[:40], details={"reason":error_code})


def _check_uncertain(account_id: str, operation: str, intent_hash: str, uncertain_path: Path) -> None:
    obj = _read_json(uncertain_path)
    if obj and obj.get("intent_hash") == intent_hash:
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AUDIO_OUTCOME_UNCERTAIN", status="quarantined",
                        intent_key="audio:"+intent_hash[:40],
                        details={"reason":str(obj.get("error_code") or "unknown")})
        raise RuntimeError("AUDIO_PAID_OUTCOME_UNCERTAIN")


def _reserve(account_id: str, operation: str, module: str, est_kopeks: int, intent_hash: str) -> str:
    run_id = "audio:%s:%s" % (account_id, uuid.uuid4().hex)
    token = ai_budget.reserve(account_id=account_id, module=module, operation=operation,
                              run_id=run_id, est_kopeks=max(1,int(est_kopeks)), calls_planned=1)
    status = str((token or {}).get("status") or "") if isinstance(token,dict) else ""
    if status not in {"reserved","observe","exists"}:
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AI_BUDGET_RESERVE_FAILED", status="blocked",
                        intent_key="audio:"+intent_hash[:40], details={"status":status})
        raise RuntimeError("AUDIO_BUDGET_RESERVE_FAILED")
    allowance = ai_budget.allow_next(run_id)
    if not bool((allowance or {}).get("allowed") if isinstance(allowance,dict) else allowance):
        ai_budget.release(run_id)
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AI_BUDGET_NOT_ALLOWED", status="blocked",
                        intent_key="audio:"+intent_hash[:40],
                        details={"reason":str((allowance or {}).get("reason") or "") if isinstance(allowance,dict) else ""})
        raise RuntimeError("AUDIO_BUDGET_NOT_ALLOWED")
    return run_id


def transcribe(*, account_id: str, audio: bytes, filename: str, content_type: str,
               language: str="ru", model: str="gpt-transcribe", operation: str="голосовая команда BORIS",
               cost_rub: float | None = None, timeout: int=180, prompt: str="",
               response_format: str="json", module: str="voice_transcription", provenance: dict | None = None) -> dict:
    """Exactly-once transcription for Command Center and calltracking.

    `cost_rub` may be supplied when the duration is already known. For Whisper
    verbose_json callers it can be None: the provider duration is then logged
    through canonical `audio_seconds`, so api_usage remains the source of truth.
    """
    account_id = str(account_id or "").strip()
    if not account_id: raise RuntimeError("AUDIO_ACCOUNT_REQUIRED")
    if str(os.environ.get("BORIS_ALLOW_EXTERNAL_RAW_AUDIO") or "").strip() != "1":
        raise RuntimeError("AUDIO_EXTERNAL_PII_BLOCKED_USE_LOCAL_STT")
    audio = bytes(audio or b"")
    try: max_audio_bytes=max(1,int(os.environ.get("BORIS_AUDIO_MAX_UPLOAD_BYTES","25000000") or 25000000))
    except Exception: max_audio_bytes=25000000
    if not audio:
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AUDIO_INPUT_EMPTY", status="blocked", details={"reason":"empty_audio"})
        raise RuntimeError("AUDIO_INPUT_EMPTY")
    if len(audio)>max_audio_bytes:
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AUDIO_INPUT_TOO_LARGE", status="blocked",
                        details={"reason":"audio_bytes_limit"})
        raise RuntimeError("AUDIO_INPUT_TOO_LARGE")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("AUDIO_OPENAI_KEY_MISSING")
    audio_hash = hashlib.sha256(bytes(audio)).hexdigest()
    clean_prompt = str(prompt or "").strip()
    clean_format = str(response_format or "json").strip() or "json"
    digest, intent_key = _intent(account_id, operation, {
        "kind":"transcription", "model":model, "audio_sha256":audio_hash,
        "language":language, "prompt":clean_prompt, "response_format":clean_format,
    })
    _, meta_path, uncertain_path = _paths(digest, ".txt")
    cached = _read_json(meta_path)
    if cached and cached.get("intent_hash") == digest and isinstance(cached.get("text"),str):
        return {"text":cached["text"], "request_id":cached.get("request_id"), "cache_hit":True,
                "cost_rub":0.0, "duration":float(cached.get("duration") or 0)}
    _check_uncertain(account_id,operation,digest,uncertain_path)
    lock_db, lock_key, lock_engine = _lock_parts(_lock(account_id,intent_key))
    try:
        cached = _read_json(meta_path)
        if cached and cached.get("intent_hash") == digest and isinstance(cached.get("text"),str):
            return {"text":cached["text"], "request_id":cached.get("request_id"), "cache_hit":True,
                    "cost_rub":0.0, "duration":float(cached.get("duration") or 0)}
        _check_uncertain(account_id,operation,digest,uncertain_path)
        if cost_rub is None:
            try: est = max(1, int(os.environ.get("BORIS_AUDIO_TRANSCRIPTION_EST_KOPEKS", "100") or 100))
            except Exception: est = 100
        else:
            est=max(1,int(round(float(cost_rub or 0)*100*1.20)))
        run_id=_reserve(account_id,operation,module,est,digest)
        try:
            _write_json(uncertain_path,{"schema":1,"intent_hash":digest,"status":"provider_in_progress","error_code":"provider_outcome_unknown"})
            import requests
            from app.services.reliability import dependency_call
            data_payload={"model":model,"language":language,"response_format":clean_format}
            if clean_prompt:
                data_payload["prompt"] = clean_prompt
            try:
                response=dependency_call("openai.transcription",requests.post,"https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization":"Bearer "+api_key,"Idempotency-Key":intent_key},
                    files={"file":(filename,audio,content_type or "audio/webm")},
                    data=data_payload,timeout=int(timeout),account_id=account_id,tenant_limit_per_minute=30)
            except Exception as exc:
                _quarantine(account_id,operation,digest,uncertain_path,"audio_transport_outcome_unknown")
                raise RuntimeError("AUDIO_PAID_OUTCOME_UNCERTAIN") from exc
            status=int(getattr(response,"status_code",0) or 0)
            if status != 200:
                uncertain_path.unlink(missing_ok=True)
                raise RuntimeError("AUDIO_PROVIDER_HTTP_ERROR:%s"%status)
            rid=str(getattr(response,"headers",{}).get("x-request-id") or "").strip() or ("ambiguous-audio:"+digest)
            try: body=response.json()
            except Exception:
                _quarantine(account_id,operation,digest,uncertain_path,"provider_200_unparseable",rid)
                raise RuntimeError("AUDIO_PAID_OUTCOME_UNCERTAIN")
            text_value=str((body or {}).get("text") or "").strip()
            if not text_value:
                _quarantine(account_id,operation,digest,uncertain_path,"provider_200_empty_transcript",rid)
                raise RuntimeError("AUDIO_PAID_OUTCOME_UNCERTAIN")
            duration=float((body or {}).get("duration") or 0)
            if cost_rub is None:
                logged_cost = log_usage(account_id,"openai",model,operation,
                                        audio_seconds=duration,request_id=rid,usage_details={"boris_intent_key":intent_key,"boris_provenance":{str(k)[:64]:v for k,v in (provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}})
            else:
                logged_cost = log_usage(account_id,"openai",model,operation,
                                        cost_rub=float(cost_rub or 0),request_id=rid,usage_details={"boris_intent_key":intent_key,"boris_provenance":{str(k)[:64]:v for k,v in (provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}})
            ai_budget.commit(run_id,rid)
            _write_json(meta_path,{"schema":1,"intent_hash":digest,"request_id":rid,
                                   "text":text_value,"duration":duration})
            uncertain_path.unlink(missing_ok=True)
            return {"text":text_value,"request_id":rid,"cache_hit":False,
                    "cost_rub":float(logged_cost or cost_rub or 0),"duration":duration}
        finally:
            ai_budget.release(run_id)
    finally:
        _unlock(lock_db,lock_key,lock_engine)


def speech(*, account_id: str, text_value: str, voice: str, model: str="tts-1",
           operation: str="голосовой ответ BORIS", cost_rub: float, timeout: int=180,
           instructions: str="", module: str="command_tts", provenance: dict | None=None) -> dict:
    account_id=str(account_id or "").strip()
    if not account_id: raise RuntimeError("AUDIO_ACCOUNT_REQUIRED")
    text_value=str(text_value or "")
    protected_text, privacy_vault = safe_external_ai_payload(text_value)
    if privacy_vault.mapping:
        labels = {
            "PERSON": "клиент", "PHONE": "номер телефона", "EMAIL": "электронная почта",
            "HANDLE": "контакт", "ADDRESS": "адрес", "IP": "сетевой адрес",
            "SNILS": "идентификатор", "PASSPORT": "паспортные данные",
            "ID": "идентификатор", "NUMBER": "номер",
        }
        for token in privacy_vault.mapping:
            kind = token.replace("[[BORIS_", "").split("_", 1)[0]
            protected_text = protected_text.replace(token, labels.get(kind, "данные клиента"))
        text_value = protected_text
    try: max_tts_chars=max(1,int(os.environ.get("BORIS_AUDIO_TTS_MAX_CHARS","4096") or 4096))
    except Exception: max_tts_chars=4096
    if not text_value.strip():
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AUDIO_TTS_TEXT_EMPTY", status="blocked", details={"reason":"empty_text"})
        raise RuntimeError("AUDIO_TTS_TEXT_EMPTY")
    if len(text_value)>max_tts_chars:
        log_guard_event(account_id=account_id, source="openai_audio", operation=operation,
                        event_code="AUDIO_TTS_TEXT_TOO_LARGE", status="blocked",
                        details={"reason":"tts_chars_limit"})
        raise RuntimeError("AUDIO_TTS_TEXT_TOO_LARGE")
    api_key=os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("AUDIO_OPENAI_KEY_MISSING")
    instructions=str(instructions or "").strip()
    speech_basis={"kind":"speech","model":model,"voice":voice,"text":text_value}
    # Keep the original command-TTS cache identity stable when no style
    # instructions are supplied; Video Factory adds instructions explicitly.
    if instructions:
        speech_basis["instructions"]=instructions
    digest,intent_key=_intent(account_id,operation,speech_basis)
    audio_path,meta_path,uncertain_path=_paths(digest,".mp3")
    meta=_read_json(meta_path)
    if audio_path.is_file() and audio_path.stat().st_size>0 and meta and meta.get("intent_hash")==digest:
        return {"audio":audio_path.read_bytes(),"request_id":meta.get("request_id"),"cache_hit":True,"cost_rub":0.0}
    _check_uncertain(account_id,operation,digest,uncertain_path)
    lock_db,lock_key,lock_engine=_lock_parts(_lock(account_id,intent_key))
    try:
        meta=_read_json(meta_path)
        if audio_path.is_file() and audio_path.stat().st_size>0 and meta and meta.get("intent_hash")==digest:
            return {"audio":audio_path.read_bytes(),"request_id":meta.get("request_id"),"cache_hit":True,"cost_rub":0.0}
        _check_uncertain(account_id,operation,digest,uncertain_path)
        run_id=_reserve(account_id,operation,str(module or "command_tts"),max(1,int(round(float(cost_rub or 0)*100*1.20))),digest)
        try:
            _write_json(uncertain_path,{"schema":1,"intent_hash":digest,"status":"provider_in_progress","error_code":"provider_outcome_unknown"})
            import requests
            from app.services.reliability import dependency_call
            try:
                payload={"model":model,"voice":voice,"input":text_value,"response_format":"mp3"}
                if instructions:
                    payload["instructions"]=instructions
                response=dependency_call("openai.tts",requests.post,"https://api.openai.com/v1/audio/speech",
                    headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json","Idempotency-Key":intent_key},
                    json=payload,
                    timeout=int(timeout),account_id=account_id,tenant_limit_per_minute=30)
            except Exception as exc:
                _quarantine(account_id,operation,digest,uncertain_path,"audio_transport_outcome_unknown")
                raise RuntimeError("AUDIO_PAID_OUTCOME_UNCERTAIN") from exc
            status=int(getattr(response,"status_code",0) or 0)
            if status != 200:
                uncertain_path.unlink(missing_ok=True)
                raise RuntimeError("AUDIO_PROVIDER_HTTP_ERROR:%s"%status)
            audio_bytes=bytes(getattr(response,"content",b"") or b"")
            rid=str(getattr(response,"headers",{}).get("x-request-id") or "").strip() or ("ambiguous-audio:"+digest)
            if not audio_bytes:
                _quarantine(account_id,operation,digest,uncertain_path,"provider_200_empty_audio",rid)
                raise RuntimeError("AUDIO_PAID_OUTCOME_UNCERTAIN")
            log_usage(account_id,"openai",model,operation,cost_rub=float(cost_rub or 0),request_id=rid,
                      usage_details={"boris_intent_key":intent_key,"boris_provenance":{str(k)[:64]:v for k,v in (provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}})
            ai_budget.commit(run_id,rid)
            try:
                _atomic(audio_path,audio_bytes)
                _write_json(meta_path,{"schema":1,"intent_hash":digest,"request_id":rid})
                uncertain_path.unlink(missing_ok=True)
            except Exception as exc:
                _quarantine(account_id,operation,digest,uncertain_path,"audio_local_persistence_failed",rid)
                raise
            return {"audio":audio_bytes,"request_id":rid,"cache_hit":False,"cost_rub":float(cost_rub or 0)}
        finally:
            ai_budget.release(run_id)
    finally:
        _unlock(lock_db,lock_key,lock_engine)
