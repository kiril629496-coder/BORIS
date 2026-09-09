from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import mcn_core

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
ARI_BASE = str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
ARI_APP = str(os.getenv("BORIS_ASTERISK_ARI_APP") or "boris-phone").strip()
GENERATED_DIR = Path("/var/lib/asterisk-boris/generated")
GENERATED_PJSIP = GENERATED_DIR / "pjsip_mcn.conf"
ASTERISK_RECORDING_SPOOL = Path("/var/lib/asterisk-boris/spool/asterisk/recording")
BORIS_RECORDING_ROOT = Path("/root/BORIS/backend/telephony_recordings")
ASTERISK_CHANNEL_ID_RE = re.compile(r"[A-Za-z0-9_.:;-]{8,200}")
ASTERISK_BIN = "/opt/boris/asterisk-22/sbin/asterisk"
ASTERISK_CONF = "/etc/asterisk-boris/asterisk.conf"


def _valid_asterisk_channel_id(value: str) -> bool:
    """Allow real ARI channel ids, including Local-channel suffixes ;1/;2, but no path syntax."""
    return bool(ASTERISK_CHANNEL_ID_RE.fullmatch(str(value or "").strip()))


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        db = SessionLocal()
        try:
            db.execute(text("SELECT pg_advisory_xact_lock(721946324)"))
            db.execute(text("""
            CREATE TABLE IF NOT EXISTS telephony_asterisk_runtime_health (
                singleton_key text PRIMARY KEY DEFAULT 'main',
                state text NOT NULL DEFAULT 'unknown',
                websocket_connected boolean NOT NULL DEFAULT false,
                connected_at timestamptz,
                last_event_at timestamptz,
                last_health_at timestamptz,
                last_error text,
                reconnect_count bigint NOT NULL DEFAULT 0,
                event_count bigint NOT NULL DEFAULT 0,
                pid integer,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """))
            db.execute(text("""
            CREATE TABLE IF NOT EXISTS telephony_asterisk_events (
                id bigserial PRIMARY KEY,
                event_fingerprint text NOT NULL UNIQUE,
                event_type text NOT NULL,
                channel_id text,
                account_id text,
                call_id text,
                status text NOT NULL DEFAULT 'received',
                error_code text,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                received_at timestamptz NOT NULL DEFAULT now(),
                processed_at timestamptz
            )
            """))
            db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_telephony_asterisk_events_channel
            ON telephony_asterisk_events(channel_id,received_at DESC)
            """))
            db.execute(text("""
            CREATE TABLE IF NOT EXISTS telephony_asterisk_config_state (
                singleton_key text PRIMARY KEY DEFAULT 'mcn',
                rendered_sha256 text,
                applied_sha256 text,
                rendered_at timestamptz,
                applied_at timestamptz,
                status text NOT NULL DEFAULT 'not_rendered',
                last_error text,
                metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """))
            db.execute(text("""
            CREATE TABLE IF NOT EXISTS telephony_asterisk_recordings (
                id bigserial PRIMARY KEY,
                recording_name text NOT NULL UNIQUE,
                account_id text NOT NULL,
                call_id text NOT NULL,
                channel_id text NOT NULL,
                format text NOT NULL DEFAULT 'wav',
                status text NOT NULL DEFAULT 'requested',
                duration_sec integer,
                source_path text,
                attached_recording_id bigint,
                last_error text,
                created_at timestamptz NOT NULL DEFAULT now(),
                started_at timestamptz,
                finished_at timestamptz,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """))
            db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_telephony_asterisk_recordings_call
            ON telephony_asterisk_recordings(account_id,call_id,created_at DESC)
            """))
            db.commit()
            _SCHEMA_READY = True
        finally:
            db.close()


def _ari_auth() -> tuple[str, str]:
    user = str(os.getenv("BORIS_ASTERISK_ARI_USER") or "").strip()
    password = str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or "").strip()
    if not user or not password:
        raise RuntimeError("asterisk_ari_credentials_missing")
    return user, password


def _request(method: str, path: str, *, params: dict[str, Any] | None = None,
             body: dict[str, Any] | None = None, timeout: float = 5.0) -> tuple[int, Any]:
    user, password = _ari_auth()
    url = ARI_BASE + "/" + str(path or "").lstrip("/")
    response = requests.request(
        method.upper(), url, params=params, json=body, auth=(user, password),
        timeout=timeout, headers={"Accept": "application/json", "User-Agent": "BORIS-Asterisk-Gateway/1.0"},
    )
    try:
        payload: Any = response.json() if response.content else {}
    except Exception:
        payload = {}
    return int(response.status_code), payload


def asterisk_health() -> dict[str, Any]:
    ensure_schema()
    try:
        code, payload = _request("GET", "/asterisk/info", timeout=3)
        version = str(((payload or {}).get("system") or {}).get("version") or "")
        ok = 200 <= code < 300 and version.startswith("22.")
        result = {
            "status": "ok" if ok else "asterisk_unhealthy",
            "ready": bool(ok), "http_status": code,
            "version": version or None,
        }
    except Exception as exc:
        result = {"status": "asterisk_unreachable", "ready": False, "error_code": type(exc).__name__[:120]}
    db = SessionLocal()
    try:
        db.execute(text("""
        INSERT INTO telephony_asterisk_runtime_health(singleton_key,state,last_health_at,last_error,metadata_json)
        VALUES('main',:state,now(),:err,CAST(:m AS jsonb))
        ON CONFLICT(singleton_key) DO UPDATE SET state=excluded.state,last_health_at=excluded.last_health_at,
          last_error=excluded.last_error,metadata_json=telephony_asterisk_runtime_health.metadata_json || excluded.metadata_json,
          updated_at=now()
        """), {
            "state": "healthy" if result.get("ready") else "degraded",
            "err": None if result.get("ready") else result.get("status"),
            "m": json.dumps({"ari_http_status": result.get("http_status"), "version": result.get("version")}),
        })
        db.commit()
    finally:
        db.close()
    return result


def application_health() -> dict[str, Any]:
    try:
        code, payload = _request("GET", f"/applications/{quote(ARI_APP, safe='')}", timeout=3)
        ok = 200 <= code < 300 and isinstance(payload, dict) and payload.get("name") == ARI_APP
        return {
            "status": "ok" if ok else "ari_app_not_connected",
            "ready": ok, "http_status": code,
            "name": payload.get("name") if isinstance(payload, dict) else None,
            "channel_ids": list((payload or {}).get("channel_ids") or [])[:100] if isinstance(payload, dict) else [],
        }
    except Exception as exc:
        return {"status": "ari_app_unreachable", "ready": False, "error_code": type(exc).__name__[:120]}


def runtime_mark(*, state: str, websocket_connected: bool, error: str | None = None,
                 event: bool = False, metadata: dict[str, Any] | None = None) -> None:
    ensure_schema()
    safe_state = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(state or "unknown"))[:100] or "unknown"
    safe_error = re.sub(r"(?i)(password|token|secret|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", str(error or ""))[:500] or None
    db = SessionLocal()
    try:
        db.execute(text("""
        INSERT INTO telephony_asterisk_runtime_health(
          singleton_key,state,websocket_connected,connected_at,last_event_at,last_health_at,last_error,
          reconnect_count,event_count,pid,metadata_json
        ) VALUES(
          'main',:state,:ws,CASE WHEN :ws THEN now() ELSE NULL END,CASE WHEN :ev THEN now() ELSE NULL END,
          now(),:err,CASE WHEN :ws THEN 0 ELSE 1 END,CASE WHEN :ev THEN 1 ELSE 0 END,:pid,CAST(:m AS jsonb)
        )
        ON CONFLICT(singleton_key) DO UPDATE SET
          state=excluded.state,
          websocket_connected=excluded.websocket_connected,
          connected_at=CASE WHEN excluded.websocket_connected AND NOT telephony_asterisk_runtime_health.websocket_connected THEN now()
                            ELSE telephony_asterisk_runtime_health.connected_at END,
          last_event_at=CASE WHEN :ev THEN now() ELSE telephony_asterisk_runtime_health.last_event_at END,
          last_health_at=now(),
          last_error=:err,
          reconnect_count=telephony_asterisk_runtime_health.reconnect_count + CASE WHEN :ws THEN 0 ELSE 1 END,
          event_count=telephony_asterisk_runtime_health.event_count + CASE WHEN :ev THEN 1 ELSE 0 END,
          pid=:pid,
          metadata_json=telephony_asterisk_runtime_health.metadata_json || CAST(:m AS jsonb),
          updated_at=now()
        """), {"state": safe_state, "ws": bool(websocket_connected), "ev": bool(event), "err": safe_error,
                "pid": os.getpid(), "m": json.dumps(metadata or {}, ensure_ascii=False)})
        db.commit()
    finally:
        db.close()


def runtime_status() -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM telephony_asterisk_runtime_health WHERE singleton_key='main'")).mappings().first()
    finally:
        db.close()
    h = asterisk_health()
    app = application_health()
    return {"status": "ok", "asterisk": h, "ari_application": app, "runtime": dict(row) if row else None,
            "ready": bool(h.get("ready") and app.get("ready") and row and row.get("websocket_connected"))}



def _event_channel_id(event: dict[str, Any]) -> str:
    channel = event.get("channel") if isinstance(event.get("channel"), dict) else {}
    cid = str((channel or {}).get("id") or event.get("channel_id") or "").strip()
    if cid:
        return cid[:200]
    rec = event.get("recording") if isinstance(event.get("recording"), dict) else {}
    target = str((rec or {}).get("target_uri") or "").strip()
    if target.startswith("channel:"):
        return target.split(":", 1)[1][:200]
    return ""


def _recording_name(account_id: str, call_id: str) -> str:
    digest = hashlib.sha256(f"{account_id}|{call_id}".encode()).hexdigest()[:40]
    return f"boris_{digest}"


def ensure_call_recording(account_id: str, call_id: str, channel_id: str) -> dict[str, Any]:
    """Start one deterministic Asterisk recording for a BORIS-managed SIP call.

    Recording is gated by the existing BORIS notice/consent state. Unknown means
    "wait", not "record anyway". Repeated calls are idempotent.
    """
    ensure_schema()
    db = SessionLocal()
    try:
        call = db.execute(text("""
        SELECT id,provider,state,recording_notice_status,recording_status
        FROM telephony_calls WHERE account_id=:a AND id=:c
        """), {"a": account_id, "c": call_id}).mappings().first()
        if not call:
            return {"status": "call_not_found"}
        provider = str(call.get("provider") or "").strip().lower()
        if provider not in {"mcn", "telphin"}:
            return {"status": "not_applicable"}
        notice = str(call.get("recording_notice_status") or "unknown")
        if notice in {"declined", "disabled"}:
            db.execute(text("""
            UPDATE telephony_calls SET recording_status='disabled',updated_at=now()
            WHERE account_id=:a AND id=:c
            """), {"a": account_id, "c": call_id})
            db.commit()
            return {"status": "recording_disabled", "notice": notice}
        if notice not in {"announced", "consented", "not_required"}:
            db.execute(text("""
            UPDATE telephony_calls
            SET recording_status=CASE WHEN recording_status IN ('not_requested','waiting_notice') THEN 'waiting_notice' ELSE recording_status END,
                updated_at=now()
            WHERE account_id=:a AND id=:c
            """), {"a": account_id, "c": call_id})
            db.commit()
            return {"status": "waiting_recording_notice", "notice": notice}

        name = _recording_name(account_id, call_id)
        existing = db.execute(text("""
        SELECT * FROM telephony_asterisk_recordings WHERE recording_name=:n
        """), {"n": name}).mappings().first()
        if existing and str(existing.get("status") or "") in {"requested", "recording", "finished", "attached"}:
            return {"status": "existing", "recording_name": name, "state": existing.get("status")}

        db.execute(text("""
        INSERT INTO telephony_asterisk_recordings(recording_name,account_id,call_id,channel_id,status)
        VALUES(:n,:a,:c,:ch,'requested')
        ON CONFLICT(recording_name) DO UPDATE SET channel_id=excluded.channel_id,
          status=CASE WHEN telephony_asterisk_recordings.status IN ('failed','missing_file') THEN 'requested'
                      ELSE telephony_asterisk_recordings.status END,
          last_error=NULL,updated_at=now()
        """), {"n": name, "a": account_id, "c": call_id, "ch": channel_id})
        db.commit()
    finally:
        db.close()

    try:
        code, payload = _request(
            "POST", f"/channels/{quote(channel_id, safe='')}/record",
            params={
                "name": name,
                "format": "wav",
                "ifExists": "fail",
                "beep": "false",
                "maxSilenceSeconds": 0,
            },
            timeout=5,
        )
    except requests.Timeout:
        return {"status": "transport_ambiguous", "recording_name": name}
    except Exception as exc:
        db = SessionLocal()
        try:
            db.execute(text("""
            UPDATE telephony_asterisk_recordings SET status='failed',last_error=:e,updated_at=now()
            WHERE recording_name=:n
            """), {"e": type(exc).__name__[:120], "n": name})
            db.commit()
        finally:
            db.close()
        return {"status": "recording_start_error", "error_code": type(exc).__name__[:120]}

    live = False
    if code in {200, 201}:
        live = True
    elif code == 409:
        try:
            live_code, _ = _request("GET", f"/recordings/live/{quote(name, safe='')}", timeout=3)
            live = live_code == 200
        except Exception:
            live = False
    if not live:
        db = SessionLocal()
        try:
            db.execute(text("""
            UPDATE telephony_asterisk_recordings SET status='failed',last_error=:e,updated_at=now()
            WHERE recording_name=:n
            """), {"e": f"ari_http_{code}", "n": name})
            db.execute(text("""
            UPDATE telephony_calls SET recording_status='failed',updated_at=now()
            WHERE account_id=:a AND id=:c
            """), {"a": account_id, "c": call_id})
            db.commit()
        finally:
            db.close()
        return {"status": "recording_start_rejected", "http_status": code}

    db = SessionLocal()
    try:
        db.execute(text("""
        UPDATE telephony_asterisk_recordings
        SET status='recording',started_at=COALESCE(started_at,now()),last_error=NULL,updated_at=now()
        WHERE recording_name=:n
        """), {"n": name})
        db.execute(text("""
        UPDATE telephony_calls SET recording_status='recording',updated_at=now()
        WHERE account_id=:a AND id=:c
        """), {"a": account_id, "c": call_id})
        db.commit()
    finally:
        db.close()
    return {"status": "ok", "recording_name": name}


def _recording_event(event: dict[str, Any], event_row_id: int | None) -> dict[str, Any]:
    rec = event.get("recording") if isinstance(event.get("recording"), dict) else {}
    name = str((rec or {}).get("name") or "").strip()
    fmt = str((rec or {}).get("format") or "wav").strip().lower()
    etype = str(event.get("type") or "")
    if not re.fullmatch(r"boris_[a-f0-9]{40}", name):
        _finish_event(event_row_id, status="ignored", error_code="recording_name_unmanaged")
        return {"status": "ignored", "reason": "recording_name_unmanaged"}
    db = SessionLocal()
    try:
        row = db.execute(text("""
        SELECT r.*,c.provider AS call_provider
        FROM telephony_asterisk_recordings r
        JOIN telephony_calls c ON c.account_id=r.account_id AND c.id=r.call_id
        WHERE r.recording_name=:n
        """), {"n": name}).mappings().first()
        if not row:
            _finish_event(event_row_id, status="ignored", error_code="recording_registry_missing")
            return {"status": "ignored", "reason": "recording_registry_missing"}
        account_id = str(row["account_id"])
        call_id = str(row["call_id"])
        if etype == "RecordingStarted":
            db.execute(text("""
            UPDATE telephony_asterisk_recordings
            SET status='recording',started_at=COALESCE(started_at,now()),updated_at=now()
            WHERE recording_name=:n
            """), {"n": name})
            db.commit()
            _finish_event(event_row_id, status="processed", account_id=account_id, call_id=call_id)
            return {"status": "ok", "recording": "started", "call_id": call_id}
    finally:
        db.close()

    if etype != "RecordingFinished":
        _finish_event(event_row_id, status="ignored", account_id=account_id, call_id=call_id)
        return {"status": "ignored"}

    if not re.fullmatch(r"[A-Za-z0-9]{1,12}", fmt):
        _finish_event(event_row_id, status="error", account_id=account_id, call_id=call_id,
                      error_code="recording_format_invalid")
        return {"status": "error", "error_code": "recording_format_invalid"}

    src = (ASTERISK_RECORDING_SPOOL / f"{name}.{fmt}").resolve()
    spool = ASTERISK_RECORDING_SPOOL.resolve()
    if spool not in src.parents or not src.is_file():
        db = SessionLocal()
        try:
            db.execute(text("""
            UPDATE telephony_asterisk_recordings SET status='missing_file',last_error='recording_file_missing',
              finished_at=now(),updated_at=now() WHERE recording_name=:n
            """), {"n": name})
            db.commit()
        finally:
            db.close()
        _finish_event(event_row_id, status="error", account_id=account_id, call_id=call_id,
                      error_code="recording_file_missing")
        return {"status": "error", "error_code": "recording_file_missing"}

    duration = int((rec or {}).get("duration") or 0)
    account_bucket = hashlib.sha256(account_id.encode()).hexdigest()[:16]
    call_bucket = hashlib.sha256(call_id.encode()).hexdigest()[:24]
    target_dir = BORIS_RECORDING_ROOT / account_bucket
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{call_bucket}.{fmt}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)

    from app.services.telephony_core import attach_recording_file
    attached = attach_recording_file(
        account_id, call_id, str(target), duration_sec=duration or None,
        provider=str(row.get("call_provider") or "").strip().lower() or None, provider_recording_id=name,
    )
    if attached.get("status") != "ok":
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        db = SessionLocal()
        try:
            db.execute(text("""
            UPDATE telephony_asterisk_recordings SET status='failed',last_error=:e,
              finished_at=now(),updated_at=now() WHERE recording_name=:n
            """), {"e": str(attached.get("status") or "attach_failed")[:120], "n": name})
            db.commit()
        finally:
            db.close()
        _finish_event(event_row_id, status="error", account_id=account_id, call_id=call_id,
                      error_code=str(attached.get("status") or "attach_failed")[:120])
        return {"status": "error", "error_code": attached.get("status")}

    recording_id = int((attached.get("recording") or {}).get("id") or 0) or None
    db = SessionLocal()
    try:
        db.execute(text("""
        UPDATE telephony_asterisk_recordings
        SET status='attached',duration_sec=:d,source_path=:p,attached_recording_id=:rid,
          finished_at=now(),last_error=NULL,updated_at=now()
        WHERE recording_name=:n
        """), {"d": duration or None, "p": str(target), "rid": recording_id, "n": name})
        db.commit()
    finally:
        db.close()

    try:
        _request("DELETE", f"/recordings/stored/{quote(name, safe='')}", timeout=3)
    except Exception:
        pass
    try:
        src.unlink(missing_ok=True)
    except Exception:
        pass

    _finish_event(event_row_id, status="processed", account_id=account_id, call_id=call_id)
    return {"status": "ok", "recording": "attached", "call_id": call_id,
            "recording_id": recording_id, "duration_sec": duration or None}


def _claim_event(event: dict[str, Any]) -> tuple[str, int | None]:
    ensure_schema()
    channel = event.get("channel") if isinstance(event.get("channel"), dict) else {}
    channel_id = _event_channel_id(event) or None
    event_type = str(event.get("type") or "unknown")[:120]
    timestamp = str(event.get("timestamp") or "")
    state = str((channel or {}).get("state") or "")
    cause = str(event.get("cause") or "")
    fingerprint = hashlib.sha256(f"{event_type}|{timestamp}|{channel_id}|{state}|{cause}".encode()).hexdigest()
    db = SessionLocal()
    try:
        row = db.execute(text("""
        INSERT INTO telephony_asterisk_events(event_fingerprint,event_type,channel_id,metadata_json)
        VALUES(:f,:t,:c,CAST(:m AS jsonb))
        ON CONFLICT(event_fingerprint) DO NOTHING RETURNING id
        """), {"f": fingerprint, "t": event_type, "c": channel_id,
               "m": json.dumps({
                   "state": state,
                   "cause": cause,
                   "recording_name": str(((event.get("recording") or {}).get("name") if isinstance(event.get("recording"), dict) else "") or "")[:120] or None,
                   "recording_format": str(((event.get("recording") or {}).get("format") if isinstance(event.get("recording"), dict) else "") or "")[:32] or None,
               }, ensure_ascii=False)}).scalar()
        db.commit()
        return ("claimed", int(row)) if row else ("duplicate", None)
    finally:
        db.close()


def _finish_event(event_id: int | None, *, status: str, account_id: str | None = None,
                  call_id: str | None = None, error_code: str | None = None) -> None:
    if not event_id:
        return
    db = SessionLocal()
    try:
        db.execute(text("""
        UPDATE telephony_asterisk_events SET status=:s,account_id=:a,call_id=:c,error_code=:e,processed_at=now()
        WHERE id=:id
        """), {"s": str(status)[:80], "a": account_id, "c": call_id,
               "e": str(error_code or "")[:120] or None, "id": int(event_id)})
        db.commit()
    finally:
        db.close()


def _existing_call_for_channel(channel_id: str, provider: str | None = None) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        row = db.execute(text("""
        SELECT * FROM telephony_calls
        WHERE provider_call_id=:pc AND (:p IS NULL OR provider=:p)
        ORDER BY started_at DESC LIMIT 1
        """), {"pc": channel_id, "p": str(provider or "").strip().lower() or None}).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()


def mcn_deferred_asterisk_event_guardian(limit: int = 100, include_synthetic: bool = False) -> dict[str, Any]:
    """Recover MCN ARI events that lost the canonical-call commit race.

    This never replays a carrier action. It only consumes already persisted ARI
    evidence after the matching canonical MCN call exists, then applies the same
    local call-state transition that handle_ari_event would have applied.
    """
    ensure_schema()
    limit=max(1,min(int(limit),500))
    db=SessionLocal()
    try:
        rows=db.execute(text("""
          SELECT e.id,e.event_fingerprint,e.event_type,e.channel_id,e.error_code,e.metadata_json,
                 c.account_id,c.id AS call_id,c.provider,c.direction,c.from_number,c.to_number,
                 c.answered_at
          FROM telephony_asterisk_events e
          JOIN LATERAL (
            SELECT tc.* FROM telephony_calls tc
            WHERE tc.provider='mcn' AND tc.provider_call_id=e.channel_id
            ORDER BY tc.started_at DESC LIMIT 1
          ) c ON true
          WHERE e.status='deferred'
            AND e.error_code IN ('canonical_call_not_found','outbound_call_commit_race')
            AND (:include_synthetic OR NOT (
              lower(c.account_id) ~ '^__.*qa' OR lower(c.account_id) ~ '^qa[-_]'
            ))
          ORDER BY e.received_at
          LIMIT :l
        """),{'l':limit,'include_synthetic':bool(include_synthetic)}).mappings().all()
    finally:
        db.close()

    processed=ignored=failed=0
    errors=[]
    for raw in rows:
        row=dict(raw)
        event_id=int(row['id'])
        account_id=str(row['account_id'])
        call_id=str(row['call_id'])
        channel_id=str(row.get('channel_id') or '')
        error_code=str(row.get('error_code') or '')
        if error_code=='outbound_call_commit_race':
            _finish_event(event_id,status='processed',account_id=account_id,call_id=call_id)
            processed+=1
            continue

        etype=str(row.get('event_type') or '')
        meta=dict(row.get('metadata_json') or {})
        mapped=None
        if etype=='ChannelStateChange':
            state=str(meta.get('state') or '').lower()
            if state in {'ring','ringing'}:
                mapped='call.ringing'
            elif state=='up':
                mapped='call.answered'
        elif etype=='ChannelHold':
            mapped='call.hold'
        elif etype=='ChannelUnhold':
            mapped='call.resumed'
        elif etype=='ChannelDestroyed':
            mapped='call.ended' if row.get('answered_at') else (
                'call.missed' if str(row.get('direction') or '')=='inbound' else 'call.failed'
            )

        if not mapped:
            _finish_event(event_id,status='ignored',account_id=account_id,call_id=call_id,
                          error_code='deferred_event_not_stateful')
            ignored+=1
            continue
        try:
            from app.services.telephony_core import apply_event
            out=apply_event(
                account_id,mapped,call_id=call_id,provider='mcn',
                provider_call_id=channel_id,
                provider_event_id='asterisk-deferred:'+str(row.get('event_fingerprint') or event_id),
                payload={'direction':row.get('direction'),'from':row.get('from_number'),
                         'to':row.get('to_number'),'asterisk_channel_id':channel_id,
                         'deferred_recovery':True},
            )
            if str(out.get('status') or '') in {'ok','duplicate'}:
                _finish_event(event_id,status='processed',account_id=account_id,call_id=call_id)
                processed+=1
            else:
                failed+=1
                errors.append({'event_id':event_id,'status':str(out.get('status') or 'unknown')[:120]})
        except Exception as exc:
            failed+=1
            errors.append({'event_id':event_id,'error_code':type(exc).__name__[:120]})
    return {'status':'ok' if failed==0 else 'degraded','picked':len(rows),
            'processed':processed,'ignored':ignored,'failed':failed,'errors':errors[:10]}


def _event_id(event: dict[str, Any], channel_id: str) -> str:
    raw = "|".join([
        "asterisk", str(event.get("type") or ""), str(event.get("timestamp") or ""),
        channel_id, str(((event.get("channel") or {}).get("state") if isinstance(event.get("channel"), dict) else "") or ""),
        str(event.get("cause") or ""),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def hangup_channel(channel_id: str, reason: str = "normal") -> bool:
    try:
        code, _ = _request("DELETE", f"/channels/{quote(channel_id, safe='')}", params={"reason": reason}, timeout=4)
        return code in {204, 404}
    except Exception:
        return False


def process_ari_event(event: dict[str, Any]) -> dict[str, Any]:
    claim, row_id = _claim_event(event)
    if claim == "duplicate":
        return {"status": "duplicate"}
    runtime_mark(state="connected", websocket_connected=True, event=True)
    etype = str(event.get("type") or "")
    channel = event.get("channel") if isinstance(event.get("channel"), dict) else {}
    if etype in {"RecordingStarted", "RecordingFinished"}:
        return _recording_event(event, row_id)
    channel_id = _event_channel_id(event)
    if not channel_id:
        _finish_event(row_id, status="ignored", error_code="channel_id_missing")
        return {"status": "ignored", "reason": "channel_id_missing"}

    try:
        if etype == "StasisStart":
            args = event.get("args") if isinstance(event.get("args"), list) else []
            mode = str(args[0] if args else "").strip().lower()
            if mode == "inbound":
                did_value = str(args[1] if len(args) > 1 else ((channel.get("dialplan") or {}).get("exten") if isinstance(channel.get("dialplan"), dict) else "") or "")
                did = mcn_core.did_resolve(did_value, active_only=True)
                if not did or not did.get("account_id"):
                    hangup_channel(channel_id)
                    _finish_event(row_id, status="rejected", error_code="unknown_mcn_did")
                    return {"status": "rejected", "reason": "unknown_mcn_did"}
                account_id = str(did["account_id"])
                caller = str(((channel.get("caller") or {}).get("number") if isinstance(channel.get("caller"), dict) else "") or "")
                from app.services.telephony_core import apply_event
                result = apply_event(
                    account_id, "call.ringing", provider="mcn", provider_call_id=channel_id,
                    provider_event_id=_event_id(event, channel_id),
                    payload={"direction": "inbound", "from": caller, "to": did["number_e164"],
                             "source": "mcn", "asterisk_channel_id": channel_id},
                )
                call = result.get("call") or {}
                call_id = str(call.get("id") or "")
                if call_id:
                    mcn_core.carrier_link_upsert(account_id, call_id, {
                        "did_id": did["id"], "asterisk_uniqueid": channel_id,
                        "metadata": {"ari_event": "StasisStart"},
                    })
                _finish_event(row_id, status="processed", account_id=account_id, call_id=call_id or None)
                return {"status": "ok", "account_id": account_id, "call_id": call_id or None}
            if mode == "telphin-inbound":
                endpoint_id = str(args[1] if len(args) > 1 else "").strip()
                did_value = str(args[2] if len(args) > 2 else ((channel.get("dialplan") or {}).get("exten") if isinstance(channel.get("dialplan"), dict) else "") or "").strip()
                from app.services.telphin_sip_trunk import account_for_endpoint
                account_id = account_for_endpoint(endpoint_id)
                if not account_id:
                    hangup_channel(channel_id)
                    _finish_event(row_id, status="rejected", error_code="unknown_telphin_endpoint")
                    return {"status": "rejected", "reason": "unknown_telphin_endpoint"}
                caller = str(((channel.get("caller") or {}).get("number") if isinstance(channel.get("caller"), dict) else "") or "")
                from app.services.telephony_core import apply_event
                result = apply_event(
                    account_id, "call.ringing", provider="telphin", provider_call_id=channel_id,
                    provider_event_id=_event_id(event, channel_id),
                    payload={"direction": "inbound", "from": caller, "to": did_value,
                             "source": "telphin_sip", "asterisk_channel_id": channel_id,
                             "telphin_endpoint_id": endpoint_id},
                )
                call = result.get("call") or {}
                call_id = str(call.get("id") or "")
                _finish_event(row_id, status="processed", account_id=account_id, call_id=call_id or None)
                return {"status": "ok", "provider": "telphin", "account_id": account_id, "call_id": call_id or None}
            if mode in {"outbound", "telphin-outbound"}:
                # request_outbound_call commits the canonical row immediately after ARI accepts
                # the originate. Do not create a second call if StasisStart wins that race.
                provider = "telphin" if mode == "telphin-outbound" else "mcn"
                existing = _existing_call_for_channel(channel_id, provider)
                _finish_event(row_id, status="processed" if existing else "deferred",
                              account_id=str(existing.get("account_id")) if existing else None,
                              call_id=str(existing.get("id")) if existing else None,
                              error_code=None if existing else "outbound_call_commit_race")
                return {"status": "ok" if existing else "deferred", "provider": provider}
            if mode == "media-leg":
                account_id = str(args[1] if len(args) > 1 else "").strip()
                call_id = str(args[2] if len(args) > 2 else "").strip()
                try: lease_id = int(args[3] if len(args) > 3 else 0)
                except Exception: lease_id = 0
                bridge_id = str(args[4] if len(args) > 4 else "").strip()
                db = SessionLocal()
                try:
                    lease = db.execute(text("""SELECT id,status,account_id,call_id,bridge_id
                      FROM telephony_media_sessions WHERE id=:i AND account_id=:a AND call_id=:c"""),
                      {"i": lease_id, "a": account_id, "c": call_id}).mappings().first() if lease_id > 0 else None
                finally:
                    db.close()
                if not lease or str(lease.get("status") or "") != "active" or str(lease.get("bridge_id") or bridge_id) != bridge_id:
                    hangup_channel(channel_id)
                    _finish_event(row_id, status="rejected", account_id=account_id or None, call_id=call_id or None,
                                  error_code="media_lease_invalid")
                    return {"status": "rejected", "reason": "media_lease_invalid"}
                attached = attach_media_leg(bridge_id, channel_id)
                db = SessionLocal()
                try:
                    if attached.get("status") == "ok":
                        db.execute(text("""UPDATE telephony_media_sessions SET media_channel_id=:m,
                          activated_at=now(),last_bridge_error=NULL,updated_at=now() WHERE id=:i"""),
                          {"i": lease_id, "m": channel_id})
                    else:
                        db.execute(text("""UPDATE telephony_media_sessions SET last_bridge_error=:e,
                          updated_at=now() WHERE id=:i"""),
                          {"i": lease_id, "e": str(attached.get("status") or "media_leg_attach_failed")[:120]})
                    db.commit()
                finally:
                    db.close()
                _finish_event(row_id, status="processed" if attached.get("status") == "ok" else "error",
                              account_id=account_id, call_id=call_id,
                              error_code=None if attached.get("status") == "ok" else str(attached.get("status") or "media_leg_attach_failed"))
                return {"status": "ok" if attached.get("status") == "ok" else "error",
                        "media_lease_id": lease_id, "call_id": call_id}
            _finish_event(row_id, status="ignored", error_code="stasis_mode_unknown")
            return {"status": "ignored", "reason": "stasis_mode_unknown"}

        if etype == "ChannelDestroyed" and re.fullmatch(r"bml-[0-9]+", channel_id):
            try: lease_id = int(channel_id.split("-", 1)[1])
            except Exception: lease_id = 0
            db = SessionLocal()
            try:
                lease = db.execute(text("""SELECT account_id,call_id,status FROM telephony_media_sessions
                  WHERE id=:i"""), {"i": lease_id}).mappings().first() if lease_id > 0 else None
                if lease and str(lease.get("status") or "") == "active":
                    db.execute(text("""UPDATE telephony_media_sessions SET status='cleanup_pending',
                      next_cleanup_at=now(),last_bridge_error='media_leg_destroyed',updated_at=now()
                      WHERE id=:i"""), {"i": lease_id})
                    db.commit()
            finally:
                db.close()
            _finish_event(row_id, status="processed" if lease else "ignored",
                          account_id=str(lease.get("account_id")) if lease else None,
                          call_id=str(lease.get("call_id")) if lease else None,
                          error_code=None if lease else "media_lease_not_found")
            return {"status": "ok" if lease else "ignored", "media_lease_id": lease_id}
        existing = _existing_call_for_channel(channel_id)
        if not existing:
            _finish_event(row_id, status="deferred", error_code="canonical_call_not_found")
            return {"status": "deferred", "reason": "canonical_call_not_found"}
        account_id = str(existing["account_id"])
        call_id = str(existing["id"])
        provider = str(existing.get("provider") or "").strip().lower() or "mcn"
        from app.services.telephony_core import apply_event
        mapped: str | None = None
        if etype == "ChannelStateChange":
            state = str(channel.get("state") or "").lower()
            if state in {"ring", "ringing"}:
                mapped = "call.ringing"
            elif state == "up":
                mapped = "call.answered"
        elif etype == "ChannelHold":
            mapped = "call.hold"
        elif etype == "ChannelUnhold":
            mapped = "call.resumed"
        elif etype == "ChannelDestroyed":
            mapped = "call.ended" if existing.get("answered_at") else ("call.missed" if existing.get("direction") == "inbound" else "call.failed")
        if mapped:
            result = apply_event(
                account_id, mapped, call_id=call_id, provider=provider, provider_call_id=channel_id,
                provider_event_id=_event_id(event, channel_id),
                payload={"direction": existing.get("direction"), "from": existing.get("from_number"),
                         "to": existing.get("to_number"), "asterisk_channel_id": channel_id,
                         "asterisk_event_type": etype},
            )
            recording = None
            if mapped == "call.answered":
                recording = ensure_call_recording(account_id, call_id, channel_id)
            _finish_event(row_id, status="processed", account_id=account_id, call_id=call_id)
            return {"status": "ok", "mapped": mapped, "call_id": call_id,
                    "result_status": result.get("status"), "recording": recording}
        _finish_event(row_id, status="ignored", account_id=account_id, call_id=call_id)
        return {"status": "ignored", "reason": "event_not_mapped"}
    except Exception as exc:
        _finish_event(row_id, status="error", error_code=type(exc).__name__[:120])
        return {"status": "error", "error_code": type(exc).__name__[:120]}


def _safe_label(value: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or ""))[:limit]


def _registrar_host(value: str | None) -> str:
    return mcn_core.normalize_sip_target(value)



def _mcn_trunk_prerequisites(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return whether an MCN trunk has enough material to be rendered safely.

    Registration auth needs registrar plus login/password. IP auth needs
    registrar plus explicit source IP allow-list. Source IPs are optional for
    registration mode because Asterisk maps inbound requests through the
    outbound-registration line token; if MCN also provides IPs, identify rules
    are rendered as an additional restriction and diagnostic aid.
    """
    if not cfg:
        return {"ready": False, "reason": "credentials_unavailable", "auth_mode": None}
    auth_mode = str(cfg.get("auth_mode") or "registration").strip().lower()
    registrar = _registrar_host(cfg.get("registrar"))
    source_ips = [str(x).strip() for x in (cfg.get("source_ips") or []) if str(x).strip()]
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    if not registrar:
        return {"ready": False, "reason": "mcn_registrar_missing", "auth_mode": auth_mode}
    if auth_mode == "registration":
        if not username or not password:
            return {"ready": False, "reason": "mcn_registration_credentials_missing", "auth_mode": auth_mode}
        if re.search(r"[\s;@]", username):
            return {"ready": False, "reason": "mcn_registration_username_unsafe", "auth_mode": auth_mode}
        return {"ready": True, "reason": None, "auth_mode": auth_mode,
                "registrar": registrar, "source_ips": source_ips}
    if auth_mode == "ip":
        if not source_ips:
            return {"ready": False, "reason": "mcn_source_ips_missing", "auth_mode": auth_mode}
        return {"ready": True, "reason": None, "auth_mode": auth_mode,
                "registrar": registrar, "source_ips": source_ips}
    return {"ready": False, "reason": "mcn_auth_mode_invalid", "auth_mode": auth_mode}



def _mcn_transport_bind() -> str:
    """Internal bind override used by production config/tests; invalid values fail closed."""
    raw = str(os.getenv("BORIS_MCN_SIP_BIND") or "0.0.0.0:5064").strip()
    m = re.fullmatch(r"((?:\d{1,3}\.){3}\d{1,3}):(\d{1,5})", raw)
    if not m:
        raise ValueError("BORIS_MCN_SIP_BIND must be IPv4:port")
    octets = [int(x) for x in m.group(1).split(".")]
    port = int(m.group(2))
    if any(x < 0 or x > 255 for x in octets) or not (1 <= port <= 65535):
        raise ValueError("BORIS_MCN_SIP_BIND invalid")
    return raw


def _asterisk_conf_escape(value: str) -> str:
    """Escape semicolons because Asterisk .conf treats them as comments."""
    return str(value or "").replace(";", r"\;")


MCN_REGISTRATION_EXPIRATION_SECONDS = 120


def _mcn_codec_allow(codecs: Any) -> str:
    values=[str(x) for x in (codecs or ["alaw"]) if re.fullmatch(r"[A-Za-z0-9_-]+",str(x))]
    return ",".join(values) or "alaw"


def _mcn_pjsip_auth_lines(auth_mode: str, auth_section: str,
                          username: str, password: str) -> list[str]:
    """Render SIP auth directives only for registration-mode MCN trunks."""
    if str(auth_mode or "").strip().lower() != "registration" or not username or not password:
        return []
    password_conf=_asterisk_conf_escape(password)
    return [
        f"outbound_auth={auth_section}",
        f"from_user={username}",
        "",
        f"[{auth_section}]",
        "type=auth",
        "auth_type=userpass",
        f"username={username}",
        f"password={password_conf}",
    ]


def render_mcn_pjsip_config(include_synthetic: bool = False) -> dict[str, Any]:
    """Render MCN endpoints only when their auth mode has complete prerequisites.

    IP-auth trunks require an explicit MCN source-IP allow-list. Registration-auth
    trunks may render with registrar plus login/password; Asterisk binds inbound
    requests to that endpoint using the outbound-registration line token.
    """
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text("""
        SELECT t.id,t.account_id FROM telephony_trunks t
        WHERE t.provider='mcn' AND t.enabled=true AND t.status IN ('configured_unverified','connected')
          AND (:include_synthetic OR NOT (lower(t.account_id) ~ '^__.*qa' OR lower(t.account_id) ~ '^qa[-_]'))
          AND EXISTS (
              SELECT 1 FROM telephony_entitlements e
              WHERE e.account_id=t.account_id AND e.enabled=true AND e.paid_until>now()
          )
        ORDER BY t.priority,t.id
        """), {"include_synthetic": bool(include_synthetic)}).mappings().all()
    finally:
        db.close()
    sections: list[str] = []
    rendered = 0
    skipped: list[dict[str, Any]] = []
    any_external = False
    for row in rows:
        cfg = mcn_core.trunk_credentials(str(row["account_id"]), int(row["id"]))
        if not cfg:
            skipped.append({"trunk_id": row["id"], "reason": "credentials_unavailable"})
            continue
        prereq = _mcn_trunk_prerequisites(cfg)
        if not prereq.get("ready"):
            skipped.append({"trunk_id": row["id"], "reason": prereq.get("reason") or "mcn_trunk_incomplete"})
            continue
        source_ips = list(prereq.get("source_ips") or [])
        registrar = str(prereq.get("registrar") or "")
        any_external = True
        tid = int(cfg["id"])
        endpoint = f"mcn-trunk-{tid}"
        auth = f"mcn-auth-{tid}"
        aor = f"mcn-aor-{tid}"
        codecs = _mcn_codec_allow(cfg.get("codecs"))
        username = str(cfg.get("username") or "").strip()
        password = str(cfg.get("password") or "").strip()
        auth_mode = str(cfg.get("auth_mode") or "registration")
        proxy = _registrar_host(cfg.get("outbound_proxy")) or registrar
        block = [
            f"[{aor}]",
            "type=aor",
            f"contact=sip:{proxy}",
            "qualify_frequency=30",
            "",
            f"[{endpoint}]",
            "type=endpoint",
            "transport=transport-mcn-udp",
            "context=boris-mcn-inbound",
            "disallow=all",
            f"allow={codecs}",
            f"aors={aor}",
            "direct_media=no",
            "rtp_symmetric=yes",
            "force_rport=yes",
            "rewrite_contact=yes",
            "dtmf_mode=rfc4733",
            f"from_domain={registrar}",
        ]
        # Registration credentials must never bleed into an IP-auth trunk.
        # Secrets may remain encrypted in DB to support an intentional switch
        # back to registration mode, but IP mode renders zero auth directives.
        block.extend(_mcn_pjsip_auth_lines(auth_mode, auth, username, password))
        for i, ip in enumerate(source_ips):
            if not re.fullmatch(r"[0-9A-Fa-f:.]+(?:/\d{1,3})?", ip):
                continue
            block.extend(["", f"[mcn-identify-{tid}-{i}]", "type=identify", f"endpoint={endpoint}", f"match={ip}"])
        if auth_mode == "registration" and username and password:
            block.extend([
                "", f"[mcn-reg-{tid}]", "type=registration", "transport=transport-mcn-udp",
                f"outbound_auth={auth}", f"server_uri=sip:{registrar}",
                f"client_uri=sip:{username}@{registrar}", "retry_interval=30",
                "forbidden_retry_interval=300", "fatal_retry_interval=300", f"expiration={MCN_REGISTRATION_EXPIRATION_SECONDS}",
                f"endpoint={endpoint}", "line=yes",
            ])
        sections.append("\n".join(block))
        rendered += 1
    header = [
        "; GENERATED BY BORIS — DO NOT EDIT",
        "; Secrets live here only because Asterisk PJSIP needs them; file mode must stay 0640.",
    ]
    if any_external:
        header.extend([
            "[transport-mcn-udp]", "type=transport", "protocol=udp", f"bind={_mcn_transport_bind()}",
            "allow_reload=no", "",
        ])
    content = "\n".join(header + sections) + "\n"
    sha = hashlib.sha256(content.encode()).hexdigest()
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    old = GENERATED_PJSIP.read_text("utf-8") if GENERATED_PJSIP.exists() else ""
    changed = old != content
    if changed:
        fd, tmp = tempfile.mkstemp(prefix=".pjsip_mcn.", dir=str(GENERATED_DIR))
        try:
            os.write(fd, content.encode())
            os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
        os.replace(tmp, GENERATED_PJSIP)
    db = SessionLocal()
    try:
        db.execute(text("""
        INSERT INTO telephony_asterisk_config_state(singleton_key,rendered_sha256,rendered_at,status,metadata_json)
        VALUES('mcn',:sha,now(),:st,CAST(:m AS jsonb))
        ON CONFLICT(singleton_key) DO UPDATE SET rendered_sha256=:sha,rendered_at=now(),status=:st,
          last_error=NULL,metadata_json=CAST(:m AS jsonb),updated_at=now()
        """), {"sha": sha, "st": "rendered_pending_apply" if changed else "rendered",
                "m": json.dumps({"trunks_rendered": rendered, "trunks_skipped": skipped, "external_transport": any_external})})
        db.commit()
    finally:
        db.close()
    return {"status": "ok", "changed": changed, "sha256": sha, "trunks_rendered": rendered,
            "trunks_skipped": skipped, "external_transport": any_external}


def active_channel_count() -> int:
    try:
        code, payload = _request("GET", "/channels", timeout=3)
        return len(payload) if 200 <= code < 300 and isinstance(payload, list) else -1
    except Exception:
        return -1



def _live_trunk_gate(account_id: str, trunk: dict[str, Any] | None) -> dict[str, Any]:
    """Fail closed on stale DB health and trigger one safe re-registration when possible."""
    if not trunk:
        return {"ready": False, "status": "mcn_trunk_missing", "self_heal": None}
    prereq = _mcn_trunk_prerequisites(trunk)
    if not prereq.get("ready"):
        return {"ready": False, "status": prereq.get("reason") or "mcn_trunk_incomplete",
                "auth_mode": prereq.get("auth_mode"), "registration_state": None,
                "state": None, "self_heal": None}
    live = _mcn_trunk_live_health(trunk)
    heal = None
    if (str(trunk.get("auth_mode") or "registration").lower() == "registration"
            and not live.get("ready")
            and live.get("registration_state") in {"unregistered", "stopped"}):
        heal = _registration_retry(int(trunk["id"]))
    mcn_core.trunk_mark_health(
        account_id, int(trunk["id"]), bool(live.get("ready")),
        str(live.get("status") or "unknown"),
        None if live.get("ready") else str(live.get("status") or "mcn_transport_not_ready"),
    )
    return {
        "ready": bool(live.get("ready")),
        "status": live.get("status"),
        "auth_mode": live.get("auth_mode"),
        "registration_state": live.get("registration_state"),
        "state": live.get("state"),
        "self_heal": heal.get("status") if isinstance(heal, dict) else None,
    }


def originate_mcn(account_id: str, to_number: str, from_number: str | None,
                  metadata: dict[str, Any]) -> dict[str, Any]:
    # Defense in depth: internal callers must not bypass the paid Phone gate by
    # calling the Asterisk/MCN transport directly.
    from app.services.telephony_core import phone_entitlement_status
    if not bool(phone_entitlement_status(account_id).get('active')):
        return {'status':'phone_entitlement_required','owner_action_required':False}
    health = asterisk_health()
    if not health.get("ready"):
        return {"status": "asterisk_unavailable"}
    trunk = mcn_core.trunk_credentials(account_id)
    if not trunk:
        return {"status": "mcn_not_ready", "blockers": ["mcn_trunk_missing"]}
    live_gate = _live_trunk_gate(account_id, trunk)
    if not live_gate.get("ready"):
        return {"status": "mcn_trunk_not_connected", "live_status": live_gate.get("status"),
                "registration_state": live_gate.get("registration_state"),
                "self_heal": live_gate.get("self_heal"), "owner_action_required": live_gate.get("registration_state") == "rejected"}
    # Fail closed before paid carrier origination when BORIS has not yet proven
    # the MCN SIP/RTP network allow-list. The minute guardian and trunk-save
    # guardian perform the self-heal; the call path never guesses or bypasses it.
    from app.services.mcn_network_guard import mcn_firewall_health
    network = mcn_firewall_health()
    if not network.get("ready"):
        return {"status": "mcn_network_not_ready",
                "network_status": network.get("status"),
                "pending_actions": int(network.get("pending_actions") or 0),
                "owner_action_required": False}
    readiness = mcn_core.readiness(account_id)
    if not readiness.get("ready"):
        return {"status": "mcn_not_ready", "blockers": readiness.get("blockers") or []}
    to_e164 = mcn_core.normalize_e164(to_number)
    if not to_e164:
        return {"status": "invalid_number"}
    dids = mcn_core.did_list(account_id)
    cli = mcn_core.normalize_e164(from_number)
    allowed = {mcn_core.normalize_e164(x) for x in (trunk.get("allowed_cli") or [])}
    allowed.update({str(x.get("number_e164")) for x in dids if x.get("outbound_cli_enabled") and x.get("status") == "active"})
    allowed.discard("")
    if not cli:
        cli = next(iter(sorted(allowed)), "")
    if not cli or cli not in allowed:
        return {"status": "caller_id_not_allowed"}
    idem = str(metadata.get("idempotency_key") or "").strip()
    if not idem:
        return {"status": "idempotency_required"}
    channel_id = "mcn-" + hashlib.sha256(f"{account_id}|{idem}".encode()).hexdigest()[:32]
    endpoint = f"PJSIP/{re.sub(r'\D+','',to_e164)}@mcn-trunk-{int(trunk['id'])}"
    params = {
        "endpoint": endpoint,
        "app": ARI_APP,
        "appArgs": f"outbound,{account_id}",
        "callerId": cli,
        "channelId": channel_id,
        "timeout": 30,
    }
    body = {"variables": {
        "BORIS_ACCOUNT_ID": account_id,
        "BORIS_DIRECTION": "outbound",
        "BORIS_IDEMPOTENCY_KEY": idem,
        "BORIS_OUTBOUND_CLI": cli,
    }}
    try:
        code, payload = _request("POST", "/channels", params=params, body=body, timeout=8)
    except requests.Timeout:
        return {"status": "transport_ambiguous"}
    except Exception as exc:
        return {"status": "transport_error", "error_code": type(exc).__name__[:120]}
    if code in {200, 201} and isinstance(payload, dict) and payload.get("id"):
        return {"status": "accepted", "provider_call_id": str(payload["id"]),
                "payload": {"asterisk_channel_id": str(payload["id"]), "trunk_id": int(trunk["id"]), "cli": cli}}
    if code == 409:
        # Deterministic channel id means the intent may already exist. Never originate a second one.
        return {"status": "transport_ambiguous"}
    return {"status": "provider_rejected", "http_status": code}



def command_mcn(account_id: str, provider_call_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    channel_id = str(provider_call_id or "").strip()
    if not _valid_asterisk_channel_id(channel_id):
        return {"status": "invalid_provider_call_id"}
    cmd = str(command or "").strip().lower()
    try:
        if cmd == "answer":
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/answer", timeout=5)
        elif cmd == "hangup":
            code, _ = _request("DELETE", f"/channels/{quote(channel_id, safe='')}", timeout=5)
        elif cmd == "hold":
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/hold", timeout=5)
        elif cmd == "resume":
            code, _ = _request("DELETE", f"/channels/{quote(channel_id, safe='')}/hold", timeout=5)
        elif cmd == "dtmf":
            digits = str((payload or {}).get("digits") or "").strip()
            if not re.fullmatch(r"[0-9*#]{1,32}", digits):
                return {"status": "invalid_dtmf"}
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/dtmf",
                               params={"dtmf": digits}, timeout=5)
        elif cmd == "transfer":
            target = mcn_core.normalize_e164(payload.get("to_number"))
            if not target:
                return {"status": "invalid_transfer_target"}
            trunk = mcn_core.trunk_credentials(account_id)
            if not trunk:
                return {"status": "mcn_trunk_not_connected"}
            live_gate = _live_trunk_gate(account_id, trunk)
            if not live_gate.get("ready"):
                return {"status": "mcn_trunk_not_connected", "live_status": live_gate.get("status"),
                        "registration_state": live_gate.get("registration_state"),
                        "self_heal": live_gate.get("self_heal"),
                        "owner_action_required": live_gate.get("registration_state") == "rejected"}
            endpoint = f"PJSIP/{re.sub(r'\D+','',target)}@mcn-trunk-{int(trunk['id'])}"
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/redirect",
                               params={"endpoint": endpoint}, timeout=5)
        else:
            return {"status": "command_unsupported"}
    except requests.Timeout:
        return {"status": "transport_ambiguous"}
    except Exception as exc:
        return {"status": "transport_error", "error_code": type(exc).__name__[:120]}
    return {"status": "ok" if code in {200, 204} else "provider_rejected", "http_status": code}


def media_interconnect_health() -> dict[str, Any]:
    """Health of the single-Asterisk BORIS media path.

    WebPhone no longer depends on the retired second Asterisk/loopback peer.
    A healthy path requires the canonical Asterisk, its ARI/Stasis application,
    and the short-lived WebPhone endpoint gateway to be live on that same instance.
    """
    main = asterisk_health()
    app = application_health()
    try:
        from app.services.phone_media_gateway import gateway_health
        browser_gateway = gateway_health()
    except Exception:
        browser_gateway = {"status": "unavailable", "runtime_ready": False, "wss_configured": False}
    ready = bool(
        main.get("ready") and
        app.get("ready") and
        browser_gateway.get("runtime_ready") and
        browser_gateway.get("wss_configured")
    )
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "main_asterisk": bool(main.get("ready")),
        "ari_application": bool(app.get("ready")),
        "browser_gateway": bool(browser_gateway.get("runtime_ready")),
        "wss_configured": bool(browser_gateway.get("wss_configured")),
        "single_asterisk": True,
    }


def originate_telphin(account_id: str, to_number: str, from_number: str | None,
                      metadata: dict[str, Any]) -> dict[str, Any]:
    """Originate Telphin through the canonical BORIS Asterisk so media/control stay on one call."""
    health = asterisk_health()
    if not health.get("ready"):
        return {"status": "asterisk_unavailable"}
    from app.services.telphin_sip_trunk import trunk_status
    trunk = trunk_status(account_id)
    if not trunk.get("ready"):
        return {"status": "telphin_sip_not_registered"}
    endpoint_id = str(trunk.get("endpoint_id") or "").strip()
    if not re.fullmatch(r"telphin-trunk-[a-f0-9]{16}", endpoint_id):
        return {"status": "telphin_sip_endpoint_invalid"}
    to_e164 = mcn_core.normalize_e164(to_number)
    if not to_e164:
        return {"status": "invalid_number"}
    cli = mcn_core.normalize_e164(from_number)
    idem = str(metadata.get("idempotency_key") or "").strip()
    if not idem:
        return {"status": "idempotency_required"}
    channel_id = "telphin-" + hashlib.sha256(f"{account_id}|{idem}".encode()).hexdigest()[:32]
    params = {
        "endpoint": f"PJSIP/{re.sub(r'\D+','',to_e164)}@{endpoint_id}",
        "app": ARI_APP,
        "appArgs": f"telphin-outbound,{account_id}",
        "channelId": channel_id,
        "timeout": 30,
    }
    if cli:
        params["callerId"] = cli
    body = {"variables": {
        "BORIS_ACCOUNT_ID": account_id,
        "BORIS_PROVIDER": "telphin",
        "BORIS_DIRECTION": "outbound",
        "BORIS_IDEMPOTENCY_KEY": idem,
    }}
    try:
        code, payload = _request("POST", "/channels", params=params, body=body, timeout=8)
    except requests.Timeout:
        return {"status": "transport_ambiguous"}
    except Exception as exc:
        return {"status": "transport_error", "error_code": type(exc).__name__[:120]}
    if code in {200, 201} and isinstance(payload, dict) and payload.get("id"):
        return {"status": "accepted", "provider_call_id": str(payload["id"]),
                "payload": {"asterisk_channel_id": str(payload["id"]),
                            "telphin_endpoint_id": endpoint_id, "cli": cli or None}}
    if code == 409:
        return {"status": "transport_ambiguous"}
    return {"status": "provider_rejected", "http_status": code}


def command_telphin(account_id: str, provider_call_id: str, command: str,
                    payload: dict[str, Any]) -> dict[str, Any]:
    """Control the same Asterisk channel that carries Telphin audio."""
    channel_id = str(provider_call_id or "").strip()
    if not _valid_asterisk_channel_id(channel_id):
        return {"status": "invalid_provider_call_id"}
    cmd = str(command or "").strip().lower()
    try:
        if cmd == "answer":
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/answer", timeout=5)
        elif cmd == "hangup":
            code, _ = _request("DELETE", f"/channels/{quote(channel_id, safe='')}", timeout=5)
        elif cmd == "hold":
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/hold", timeout=5)
        elif cmd == "resume":
            code, _ = _request("DELETE", f"/channels/{quote(channel_id, safe='')}/hold", timeout=5)
        elif cmd == "transfer":
            target = mcn_core.normalize_e164(payload.get("to_number"))
            if not target:
                return {"status": "invalid_transfer_target"}
            from app.services.telphin_sip_trunk import trunk_status
            trunk = trunk_status(account_id)
            endpoint_id = str(trunk.get("endpoint_id") or "")
            if not trunk.get("ready") or not re.fullmatch(r"telphin-trunk-[a-f0-9]{16}", endpoint_id):
                return {"status": "telphin_sip_not_registered"}
            code, _ = _request("POST", f"/channels/{quote(channel_id, safe='')}/redirect",
                               params={"endpoint": f"PJSIP/{re.sub(r'\D+','',target)}@{endpoint_id}"}, timeout=5)
        else:
            return {"status": "command_unsupported"}
    except requests.Timeout:
        return {"status": "transport_ambiguous"}
    except Exception as exc:
        return {"status": "transport_error", "error_code": type(exc).__name__[:120]}
    return {"status": "ok" if code in {200, 204} else "provider_rejected", "http_status": code}


def _media_bridge_ids(lease_id: int) -> tuple[str, str]:
    lid = int(lease_id)
    if lid <= 0:
        raise ValueError("invalid_media_lease")
    return f"bm-{lid}", f"bml-{lid}"


def bind_browser_media(account_id: str, call_id: str, provider_call_id: str,
                       browser_endpoint_id: str, lease_id: int) -> dict[str, Any]:
    """Bridge one provider channel to one registered browser endpoint via loopback SIP."""
    channel_id = str(provider_call_id or "").strip()
    endpoint_id = str(browser_endpoint_id or "").strip()
    if not _valid_asterisk_channel_id(channel_id):
        return {"status": "provider_channel_invalid"}
    if not re.fullmatch(r"bp_[A-Za-z0-9_]{1,61}", endpoint_id):
        return {"status": "browser_endpoint_invalid"}
    bridge_id, media_channel_id = _media_bridge_ids(lease_id)
    if not asterisk_health().get("ready"):
        return {"status": "asterisk_unavailable"}
    try:
        ch_code, _ = _request("GET", f"/channels/{quote(channel_id, safe='')}", timeout=3)
        if ch_code != 200:
            return {"status": "provider_channel_missing"}
        b_code, _ = _request("GET", f"/bridges/{quote(bridge_id, safe='')}", timeout=3)
        if b_code == 404:
            b_code, _ = _request("POST", "/bridges",
                                  params={"type": "mixing", "bridgeId": bridge_id,
                                          "name": f"BORIS media lease {int(lease_id)}"}, timeout=4)
        if b_code not in {200, 201}:
            return {"status": "bridge_create_failed", "http_status": b_code}
        add_code, _ = _request("POST", f"/bridges/{quote(bridge_id, safe='')}/addChannel",
                               params={"channel": channel_id}, timeout=4)
        if add_code not in {204, 409, 422}:
            return {"status": "provider_bridge_add_failed", "http_status": add_code}
        params = {
            "endpoint": f"PJSIP/{endpoint_id}",
            "app": ARI_APP,
            "appArgs": f"media-leg,{account_id},{call_id},{int(lease_id)},{bridge_id}",
            "channelId": media_channel_id,
            "timeout": 20,
        }
        code, payload = _request("POST", "/channels", params=params,
                                 body={"variables": {"BORIS_MEDIA_LEASE_ID": str(int(lease_id)),
                                                     "BORIS_CALL_ID": call_id,
                                                     "BORIS_ACCOUNT_ID": account_id}}, timeout=8)
        if code in {200, 201}:
            return {"status": "activating", "bridge_id": bridge_id,
                    "media_channel_id": media_channel_id,
                    "asterisk_payload_id": str((payload or {}).get("id") or media_channel_id)}
        if code == 409:
            return {"status": "activating", "bridge_id": bridge_id,
                    "media_channel_id": media_channel_id, "replayed": True}
        try:
            _request("DELETE", f"/bridges/{quote(bridge_id, safe='')}", timeout=3)
        except Exception:
            pass
        return {"status": "media_leg_originate_failed", "http_status": code}
    except requests.Timeout:
        return {"status": "transport_ambiguous"}
    except Exception as exc:
        return {"status": "transport_error", "error_code": type(exc).__name__[:120]}


def attach_media_leg(bridge_id: str, media_channel_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"bm-[0-9]+", str(bridge_id or "")):
        return {"status": "bridge_id_invalid"}
    if not re.fullmatch(r"bml-[0-9]+", str(media_channel_id or "")):
        return {"status": "media_channel_invalid"}
    try:
        code, _ = _request("POST", f"/bridges/{quote(bridge_id, safe='')}/addChannel",
                           params={"channel": media_channel_id}, timeout=4)
    except Exception as exc:
        return {"status": "transport_error", "error_code": type(exc).__name__[:120]}
    return {"status": "ok" if code in {204, 409, 422} else "bridge_add_failed",
            "http_status": code}


def cleanup_media_bridge(lease_id: int) -> dict[str, Any]:
    """Remove only the temporary browser leg/bridge; never hang up the provider channel."""
    bridge_id, media_channel_id = _media_bridge_ids(lease_id)
    errors = []
    try:
        code, _ = _request("DELETE", f"/channels/{quote(media_channel_id, safe='')}", timeout=3)
        if code not in {204, 404}:
            errors.append("media_channel")
    except Exception:
        errors.append("media_channel")
    try:
        code, _ = _request("DELETE", f"/bridges/{quote(bridge_id, safe='')}", timeout=3)
        if code not in {204, 404}:
            errors.append("bridge")
    except Exception:
        errors.append("bridge")
    return {"status": "ok" if not errors else "cleanup_pending",
            "bridge_id": bridge_id, "media_channel_id": media_channel_id, "errors": errors}


def _set_config_applied(sha256: str, status: str, error: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        db.execute(text("""
        INSERT INTO telephony_asterisk_config_state(singleton_key,applied_sha256,applied_at,status,last_error,metadata_json)
        VALUES('mcn',:sha,CASE WHEN :ok THEN now() ELSE NULL END,:st,:err,CAST(:m AS jsonb))
        ON CONFLICT(singleton_key) DO UPDATE SET
          applied_sha256=CASE WHEN :ok THEN :sha ELSE telephony_asterisk_config_state.applied_sha256 END,
          applied_at=CASE WHEN :ok THEN now() ELSE telephony_asterisk_config_state.applied_at END,
          status=:st,last_error=:err,
          metadata_json=telephony_asterisk_config_state.metadata_json || CAST(:m AS jsonb),
          updated_at=now()
        """), {"sha": sha256, "ok": status == "applied", "st": status,
                "err": str(error or "")[:500] or None,
                "m": json.dumps(metadata or {}, ensure_ascii=False)})
        db.commit()
    finally:
        db.close()


def _endpoint_health(trunk_id: int) -> dict[str, Any]:
    try:
        code, payload = _request("GET", f"/endpoints/PJSIP/mcn-trunk-{int(trunk_id)}", timeout=3)
    except Exception as exc:
        return {"ready": False, "status": "ari_error", "error_code": type(exc).__name__[:120]}
    if code == 404:
        return {"ready": False, "status": "endpoint_missing", "http_status": code}
    state = str((payload or {}).get("state") or "").strip().lower() if isinstance(payload, dict) else ""
    ready = 200 <= code < 300 and state == "online"
    return {"ready": ready, "status": "ok" if ready else (state or "endpoint_unavailable"),
            "http_status": code, "state": state or None}




def _parse_registration_state(output: str) -> str:
    """Parse Asterisk PJSIP registration status without returning CLI output or secrets."""
    body = str(output or "")
    low = body.lower()
    if "no objects found" in low or "unable to find object" in low:
        return "missing"
    m = re.search(r"(?im)^\s*status\s*[:=]\s*([^\r\n]+)", body)
    value = m.group(1).strip().lower() if m else low
    if re.search(r"\brejected\b", value):
        return "rejected"
    if re.search(r"\bunregistered\b", value):
        return "unregistered"
    if re.search(r"\bregistered\b", value):
        return "registered"
    if re.search(r"\b(auth(?:entication)?\.?\s*sent|request\s*sent|trying|registering|in progress)\b", value):
        return "pending"
    if re.search(r"\bstopped\b", value):
        return "stopped"
    return "unknown"


def _registration_health(trunk_id: int) -> dict[str, Any]:
    """Strict proof for login/password MCN: REGISTER must be Registered."""
    reg_id = f"mcn-reg-{int(trunk_id)}"
    try:
        cp = subprocess.run(
            [ASTERISK_BIN, "-C", ASTERISK_CONF, "-rx", f"pjsip show registration {reg_id}"],
            timeout=5, check=False, capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return {"ready": False, "status": "registration_cli_timeout", "registration_state": "unknown"}
    except Exception as exc:
        return {"ready": False, "status": "registration_cli_error",
                "registration_state": "unknown", "error_code": type(exc).__name__[:120]}
    output = (cp.stdout or "") + "\n" + (cp.stderr or "")
    if cp.returncode != 0 and "unable to connect to remote asterisk" in output.lower():
        return {"ready": False, "status": "asterisk_cli_unavailable", "registration_state": "unknown"}
    state = _parse_registration_state(output)
    status_map = {
        "registered": "registration_registered",
        "rejected": "registration_rejected",
        "unregistered": "registration_unregistered",
        "pending": "registration_pending",
        "stopped": "registration_stopped",
        "missing": "registration_missing",
        "unknown": "registration_unknown",
    }
    return {"ready": state == "registered", "status": status_map[state],
            "registration_state": state}


def _registration_retry(trunk_id: int) -> dict[str, Any]:
    """One safe self-heal attempt for a loaded but unregistered outbound registration."""
    reg_id = f"mcn-reg-{int(trunk_id)}"
    try:
        cp = subprocess.run(
            [ASTERISK_BIN, "-C", ASTERISK_CONF, "-rx", f"pjsip send register {reg_id}"],
            timeout=5, check=False, capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return {"status": "registration_retry_timeout"}
    except Exception as exc:
        return {"status": "registration_retry_error", "error_code": type(exc).__name__[:120]}
    output = ((cp.stdout or "") + "\n" + (cp.stderr or "")).lower()
    failed = cp.returncode != 0 or "unable to connect to remote asterisk" in output or "no such command" in output
    if failed:
        return {"status": "registration_retry_failed"}
    return {"status": "registration_retry_sent"}


def _mcn_trunk_live_health(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Use REGISTER state for credential auth, endpoint state for IP-auth."""
    if not cfg:
        return {"ready": False, "status": "trunk_credentials_unavailable", "auth_mode": None}
    auth_mode = str(cfg.get("auth_mode") or "registration").strip().lower()
    try:
        trunk_id = int(cfg.get("id") or 0)
    except (TypeError, ValueError):
        trunk_id = 0
    if trunk_id <= 0:
        return {"ready": False, "status": "trunk_id_invalid", "auth_mode": auth_mode}
    if auth_mode == "registration":
        return {**_registration_health(trunk_id), "auth_mode": auth_mode, "trunk_id": trunk_id}
    if auth_mode == "ip":
        return {**_endpoint_health(trunk_id), "auth_mode": auth_mode, "trunk_id": trunk_id}
    return {"ready": False, "status": "mcn_auth_mode_invalid",
            "auth_mode": auth_mode, "trunk_id": trunk_id}


def mcn_account_transport_health(account_id: str, sync_db: bool = False) -> dict[str, Any]:
    """Live Asterisk proof for at least one MCN trunk of this account.

    Read-only callers keep sync_db=False. Explicit provider health-checks use
    sync_db=True so a recovered REGISTER immediately restores the DB readiness
    state and an unregistered line gets one safe re-registration attempt.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text("""
        SELECT id,status,auth_mode FROM telephony_trunks
        WHERE account_id=:a AND provider='mcn' AND enabled=true
        ORDER BY priority,id
        """), {"a": account_id}).mappings().all()
    finally:
        db.close()
    results = []
    online = 0
    for row in rows:
        cfg = mcn_core.trunk_credentials(account_id, int(row["id"]))
        live = _live_trunk_gate(account_id, cfg) if sync_db else _mcn_trunk_live_health(cfg)
        owner_action = bool(live.get("registration_state") == "rejected")
        results.append({
            "trunk_id": int(row["id"]),
            "db_status": row.get("status"),
            "auth_mode": row.get("auth_mode"),
            "live_status": live.get("status"),
            "state": live.get("state"),
            "registration_state": live.get("registration_state"),
            "owner_action_required": owner_action,
            "owner_action_code": "check_mcn_sip_credentials" if owner_action else None,
        })
        if live.get("ready"):
            online += 1
    owner_action_required = any(bool(x.get("owner_action_required")) for x in results)
    return {"status": "ok" if online else "mcn_transport_not_ready",
            "ready": online > 0, "online_trunks": online, "trunks": results,
            "owner_action_required": owner_action_required,
            "owner_action_code": "check_mcn_sip_credentials" if owner_action_required else None}


def _verify_mcn_trunk(account_id: str, trunk_id: int, db_status: str | None = None) -> dict[str, Any]:
    """Verify one configured MCN trunk and commit only evidence-backed health."""
    cfg = mcn_core.trunk_credentials(account_id, int(trunk_id))
    prereq = _mcn_trunk_prerequisites(cfg)
    if not prereq.get("ready"):
        reason = str(prereq.get("reason") or "mcn_trunk_incomplete")
        if str(db_status or "") == "connected":
            mcn_core.trunk_mark_health(account_id, int(trunk_id), False, reason, reason)
        return {"trunk_id": int(trunk_id), "ready": False, "status": reason,
                "auth_mode": prereq.get("auth_mode"), "self_heal": None}

    live = _mcn_trunk_live_health(cfg)
    self_heal = None
    if (str(cfg.get("auth_mode") or "registration").lower() == "registration"
            and not live.get("ready")
            and live.get("registration_state") in {"unregistered", "stopped"}):
        self_heal = _registration_retry(int(trunk_id))

    mcn_core.trunk_mark_health(
        account_id, int(trunk_id), bool(live.get("ready")),
        str(live.get("status") or "unknown"),
        None if live.get("ready") else str(live.get("status") or "mcn_transport_not_ready"),
    )
    return {
        "trunk_id": int(trunk_id),
        "ready": bool(live.get("ready")),
        "status": live.get("status"),
        "auth_mode": live.get("auth_mode"),
        "state": live.get("state"),
        "registration_state": live.get("registration_state"),
        "self_heal": self_heal.get("status") if isinstance(self_heal, dict) else None,
    }


def mcn_pjsip_guardian(include_synthetic: bool = False) -> dict[str, Any]:
    """Render/apply MCN PJSIP safely and heal ARI bridge without owner involvement.

    Transport changes require an Asterisk restart. They are applied only when ARI
    proves there are zero active channels; otherwise the change is deferred.
    """
    ensure_schema()
    rendered = render_mcn_pjsip_config(include_synthetic=include_synthetic)
    try:
        from app.services.mcn_network_guard import sync_mcn_firewall
        firewall = sync_mcn_firewall()
    except Exception as exc:
        firewall = {"status": "firewall_guard_error", "ready": False,
                    "error_code": type(exc).__name__[:120], "owner_action_required": False}
    sha = str(rendered.get("sha256") or "")
    changed = bool(rendered.get("changed"))
    applied = False
    deferred = False
    action = "none"
    error_code = None

    ah = asterisk_health()
    if not ah.get("ready"):
        try:
            cp = subprocess.run(["systemctl", "restart", "boris-asterisk.service"],
                                timeout=20, check=False, capture_output=True, text=True)
            action = "restart_asterisk"
            if cp.returncode != 0:
                error_code = "asterisk_restart_failed"
        except Exception as exc:
            error_code = type(exc).__name__[:120]
    elif changed:
        channels = active_channel_count()
        if channels < 0:
            deferred = True
            action = "defer_active_channel_unknown"
            _set_config_applied(sha, "apply_deferred", "active_channel_count_unknown",
                                {"active_channels": channels})
        elif channels > 0:
            deferred = True
            action = "defer_active_calls"
            _set_config_applied(sha, "apply_deferred", "active_calls_present",
                                {"active_channels": channels})
        else:
            try:
                if rendered.get("external_transport"):
                    cp = subprocess.run(["systemctl", "restart", "boris-asterisk.service"],
                                        timeout=20, check=False, capture_output=True, text=True)
                    action = "restart_asterisk_for_transport"
                else:
                    # Asterisk 22 CLI has no `pjsip reload` command and may
                    # still return process code 0 for an unknown CLI command.
                    # `module reload res_pjsip.so` is not reloadable in this
                    # build either. A global core reload is the supported path
                    # for non-transport PJSIP config and is only attempted at
                    # the verified zero-active-channel boundary above.
                    cp = subprocess.run([
                        "sudo", "-u", "asterisk-boris",
                        "/opt/boris/asterisk-22/sbin/asterisk",
                        "-C", "/etc/asterisk-boris/asterisk.conf",
                        "-rx", "core reload",
                    ], timeout=10, check=False, capture_output=True, text=True)
                    action = "core_reload"
                cli_output=((cp.stdout or "")+"\n"+(cp.stderr or "")).lower()
                cli_failed=any(x in cli_output for x in (
                    "no such command","reported a reload failure","unable to connect to remote asterisk",
                ))
                if cp.returncode == 0 and not cli_failed:
                    applied = True
                    _set_config_applied(sha, "applied", None, {"action": action})
                else:
                    error_code = "asterisk_config_apply_failed"
                    _set_config_applied(sha, "apply_failed", error_code, {"action": action})
            except Exception as exc:
                error_code = type(exc).__name__[:120]
                _set_config_applied(sha, "apply_failed", error_code, {"action": action})

    # The websocket bridge is separately self-reconnecting, but a stale service
    # should not require the owner to notice it.
    app = application_health()
    bridge_healed = False
    if ah.get("ready") and not app.get("ready"):
        try:
            cp = subprocess.run(["systemctl", "restart", "boris-asterisk-bridge.service"],
                                timeout=15, check=False, capture_output=True, text=True)
            bridge_healed = cp.returncode == 0
        except Exception:
            bridge_healed = False

    # Verify every rendered trunk against the live PJSIP endpoint. A rendered
    # config is never enough to call the carrier "connected".
    db = SessionLocal()
    try:
        rows = db.execute(text("""
        SELECT id,account_id,status FROM telephony_trunks
        WHERE provider='mcn' AND enabled=true
          AND status IN ('configured_unverified','connected')
          AND (:include_synthetic OR NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]'))
        ORDER BY id
        """), {"include_synthetic": bool(include_synthetic)}).mappings().all()
    finally:
        db.close()
    verified = 0
    degraded = 0
    trunk_results = []
    verified_accounts: set[str] = set()
    for row in rows:
        result = _verify_mcn_trunk(str(row["account_id"]), int(row["id"]), str(row.get("status") or ""))
        trunk_results.append(result)
        if result.get("ready"):
            verified += 1
            verified_accounts.add(str(row["account_id"]))
        else:
            degraded += 1

    provider_verified = 0
    provider_degraded = 0
    provider_results: list[dict[str, Any]] = []
    if verified_accounts:
        # Keep generic BORIS provider state synchronized with the live MCN trunk.
        # This removes the old 5-minute window where SIP was already online but
        # the owner UI still showed "not verified" until another guardian ran.
        from app.services.telephony_core import provider_credentials, verify_provider_connection
        for account_id in sorted(verified_accounts):
            try:
                provider, _ = provider_credentials(account_id)
                if str(provider or "").strip().lower() != "mcn":
                    continue
                pv = verify_provider_connection(account_id, None)
                ok = bool(pv.get("connected"))
                provider_verified += 1 if ok else 0
                provider_degraded += 0 if ok else 1
                provider_results.append({"account_id": account_id, "connected": ok,
                                         "status": pv.get("status")})
            except Exception as exc:
                provider_degraded += 1
                provider_results.append({"account_id": account_id, "connected": False,
                                         "status": "provider_verify_error",
                                         "error_type": type(exc).__name__[:120]})

    owner_action_required = any(str(x.get("registration_state") or "") == "rejected" for x in trunk_results)
    firewall_degraded = not bool(firewall.get("ready", False)) and bool(firewall.get("desired_networks"))
    return {
        "status": "degraded" if (error_code or degraded > 0 or provider_degraded > 0 or firewall_degraded) else "ok",
        "rendered": rendered.get("trunks_rendered"),
        "skipped": rendered.get("trunks_skipped") or [],
        "changed": changed,
        "applied": applied,
        "deferred": deferred,
        "action": action,
        "error_code": error_code,
        "bridge_healed": bridge_healed,
        "trunks_verified": verified,
        "trunks_degraded": degraded,
        "trunks": trunk_results,
        "provider_verified": provider_verified,
        "provider_degraded": provider_degraded,
        "provider_results": provider_results,
        "firewall": firewall,
        "owner_action_required": owner_action_required,
        "owner_action_code": "check_mcn_sip_credentials" if owner_action_required else None,
    }


__all__ = [
    "ensure_schema", "asterisk_health", "application_health", "runtime_mark", "runtime_status",
    "process_ari_event", "render_mcn_pjsip_config", "mcn_pjsip_guardian", "active_channel_count", "originate_mcn",
    "command_mcn", "hangup_channel",
]
