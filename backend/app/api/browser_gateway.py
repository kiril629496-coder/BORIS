# browser gateway durable execution helpers are intentionally kept in this authoritative module
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.auth import get_current_user
from app.services.exception_observability import observe_suppressed

# Public transport endpoint for the paired Chrome extension. Pair-code creation
# is exposed from the protected mass-editor router; all endpoints here require
# either a short-lived pair code or the resulting opaque gateway token.
router = APIRouter(prefix="/api/avito/browser-gateway", tags=["browser-gateway-transport"])
control_router = APIRouter(prefix="/api/browser-gateway", tags=["browser-gateway"])
transport_router = APIRouter(prefix="/api/browser-gateway/transport", tags=["browser-gateway-transport-v2"])

SYSTEM_ACCOUNT = "__browser_gateway__"
CROWD_GATEWAY_ACCOUNT = "__crowd_onboarding__"
TOKEN_TTL_DAYS = 90
PAIR_TTL_MINUTES = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _append_execution_journal(account_id: str, action: str, details: dict[str, Any], actor: str = "boris_browser") -> None:
    """Append one idempotent-friendly execution fact to the existing account audit log."""
    db = SessionLocal()
    try:
        row = db.execute(text("select id,value from storage where account_id=:a and key='audit_log' order by id desc limit 1"), {"a": account_id}).fetchone()
        try:
            log = json.loads(row[1]) if row and row[1] else []
        except Exception:
            log = []
        if not isinstance(log, list):
            log = []
        entry = {"ts": _iso(), "actor": actor, "action": action, "details": json.dumps(details, ensure_ascii=False)[:2000]}
        signature = (entry["action"], entry["details"])
        if not log or (str(log[-1].get("action")), str(log[-1].get("details"))) != signature:
            log.append(entry)
        payload = json.dumps(log[-500:], ensure_ascii=False)
        if row:
            db.execute(text("update storage set value=:v where id=:i"), {"v": payload, "i": row[0]})
        else:
            db.execute(text("insert into storage(account_id,key,value) values(:a,'audit_log',:v)"), {"a": account_id, "v": payload})
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _db_get(account_id: str, key: str) -> Any:
    db = SessionLocal()
    try:
        row = db.execute(text("select value from storage where account_id=:a and key=:k"), {"a": account_id, "k": key}).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]
    finally:
        db.close()


def _db_put(account_id: str, key: str, value: Any) -> None:
    # STORAGE_SINGLETON_ATOMIC_PUT_V1: serialize one logical Storage singleton
    # by account/key and collapse any historical duplicate rows before writing.
    db = SessionLocal()
    try:
        import hashlib as _hashlib
        raw = json.dumps(value, ensure_ascii=False)
        digest = _hashlib.blake2b((str(account_id)+"|"+str(key)).encode("utf-8"), digest_size=8).digest()
        lk = int.from_bytes(digest, "big", signed=False)
        if lk >= (1 << 63):
            lk -= (1 << 64)
        db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": lk})
        rows = db.execute(text("select id from storage where account_id=:a and key=:k order by id desc for update"), {"a": account_id, "k": key}).fetchall()
        if rows:
            keep = int(rows[0][0])
            db.execute(text("update storage set value=:v where id=:i"), {"v": raw, "i": keep})
            if len(rows) > 1:
                db.execute(text("delete from storage where account_id=:a and key=:k and id<>:i"), {"a": account_id, "k": key, "i": keep})
        else:
            db.execute(text("insert into storage (account_id,key,value) values (:a,:k,:v)"), {"a": account_id, "k": key, "v": raw})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _db_delete(account_id: str, key: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("delete from storage where account_id=:a and key=:k"), {"a": account_id, "k": key})
        db.commit()
    finally:
        db.close()


def _account_identity(account_id: str | None) -> dict[str, Any]:
    if not account_id:
        return {"account_id": None, "account_name": None, "expected_avito_user_id": None}
    db = SessionLocal()
    try:
        row = db.execute(text("select name,avito_user_id from accounts where account_id=:a limit 1"), {"a": account_id}).fetchone()
    finally:
        db.close()
    return {
        "account_id": account_id,
        "account_name": str(row[0] or "") if row else None,
        "expected_avito_user_id": str(row[1] or "") if row and row[1] else None,
    }


def _session_vault_put(account_id: str | None, gateway: dict[str, Any], state: str | None = None) -> None:
    """Persist the logical browser session separately from the rotating gateway token."""
    if not account_id:
        return
    current = _db_get(SYSTEM_ACCOUNT, f"session:{account_id}") or {}
    # Do not perpetuate legacy cross-account routing hints from pre-isolation builds.
    # V2 sessions are permanently account-scoped; a mismatch is a block, never a rebind.
    for legacy_key in (
        "routed_to_account_id", "rebound_to_account_id", "candidate_account_id",
        "matched_account_id", "profile_routing_target",
    ):
        current.pop(legacy_key, None)
    vault = {
        **current,
        "account_id": account_id,
        "account_name": gateway.get("account_name") or current.get("account_name"),
        "expected_avito_user_id": gateway.get("expected_avito_user_id") or current.get("expected_avito_user_id"),
        "gateway_id": gateway.get("gateway_id") or current.get("gateway_id"),
        "device_name": gateway.get("device_name") or current.get("device_name"),
        "extension_version": gateway.get("extension_version") or current.get("extension_version"),
        "state": state or gateway.get("state") or current.get("state") or "paired",
        "last_seen_at": gateway.get("last_seen_at") or current.get("last_seen_at"),
        "avito_logged_in": gateway.get("avito_logged_in"),
        "profile_instance_id": gateway.get("profile_instance_id") or current.get("profile_instance_id"),
        "identity_verified": gateway.get("identity_verified") if gateway.get("identity_verified") is not None else current.get("identity_verified"),
        "identity_mismatch": bool(gateway.get("identity_mismatch")) if "identity_mismatch" in gateway else bool(current.get("identity_mismatch")),
        "actual_profile_name": gateway.get("actual_profile_name") or current.get("actual_profile_name"),
        "session_scope": "account",
        "updated_at": _iso(),
    }
    _db_put(SYSTEM_ACCOUNT, f"session:{account_id}", vault)


def _supersede_account_gateways(account_id: str | None, new_gateway_id: str) -> None:
    """Exactly one active browser identity per BORIS account; old tokens are revoked."""
    if not account_id:
        return
    db = SessionLocal()
    try:
        rows = db.execute(text("select id,key,value from storage where account_id=:sys and key like 'gateway:gw_%'"), {"sys": SYSTEM_ACCOUNT}).fetchall()
        now = _iso()
        for row_id, key, raw in rows:
            try:
                g = json.loads(raw)
            except Exception:
                continue
            if str(g.get("account_id") or "") != str(account_id) or g.get("gateway_id") == new_gateway_id:
                continue
            old_token = g.get("token")
            g["state"] = "superseded"
            g["superseded_at"] = now
            g["superseded_by"] = new_gateway_id
            db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(g, ensure_ascii=False), "i": row_id})
            if old_token:
                db.execute(text("delete from storage where account_id=:sys and key=:k"), {"sys": SYSTEM_ACCOUNT, "k": f"token:{old_token}"})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _supersede_profile_instance_gateways(profile_instance_id: str | None, new_gateway_id: str, new_account_id: str | None) -> None:
    """One Chrome profile instance may belong to exactly one BORIS account.

    chrome.storage.local is profile-scoped, so profile_instance_id is a durable
    logical identity for that browser profile. Re-pairing the same profile to a
    different account revokes the old token instead of silently sharing cookies.
    """
    profile_instance_id = str(profile_instance_id or "").strip()
    if not profile_instance_id:
        return
    db = SessionLocal()
    affected_accounts: set[str] = set()
    try:
        rows = db.execute(text("select id,value from storage where account_id=:sys and key like 'gateway:gw_%'"), {"sys": SYSTEM_ACCOUNT}).fetchall()
        now = _iso()
        for row_id, raw in rows:
            try:
                g = json.loads(raw)
            except Exception:
                continue
            if str(g.get("profile_instance_id") or "") != profile_instance_id or g.get("gateway_id") == new_gateway_id:
                continue
            old_account_id = str(g.get("account_id") or "")
            old_token = str(g.get("token") or "")
            g["state"] = "profile_superseded"
            g["superseded_at"] = now
            g["superseded_by"] = new_gateway_id
            g["superseded_reason"] = "same_chrome_profile_repaired"
            db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(g, ensure_ascii=False), "i": row_id})
            if old_token:
                db.execute(text("delete from storage where account_id=:sys and key=:k"), {"sys": SYSTEM_ACCOUNT, "k": f"token:{old_token}"})
            if old_account_id and old_account_id != str(new_account_id or ""):
                affected_accounts.add(old_account_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for old_account_id in affected_accounts:
        old_vault = _db_get(SYSTEM_ACCOUNT, f"session:{old_account_id}") or {}
        old_vault.update({
            "state": "profile_superseded",
            "gateway_id": None,
            "identity_verified": False,
            "identity_mismatch": True,
            "updated_at": _iso(),
        })
        _db_put(SYSTEM_ACCOUNT, f"session:{old_account_id}", old_vault)
        _append_execution_journal(old_account_id, "browser_profile_superseded", {
            "profile_instance_id": profile_instance_id,
            "new_account_id": new_account_id,
        })


def _write_execution_receipt(account_id: str, job: dict[str, Any], executor: str, verified: bool | None = None) -> dict[str, Any]:
    receipt = {
        "receipt_id": f"receipt_{job.get('job_id')}",
        "job_id": job.get("job_id"),
        "account_id": account_id,
        "executor": executor,
        "capability": job.get("capability") or ("avito_mass_editor" if str(job.get("mode") or "edit") != "scan" else "avito_scan"),
        "status": job.get("status"),
        "verified": bool(verified) if verified is not None else None,
        "total": job.get("total"),
        "changed": job.get("changed"),
        "verified_count": job.get("verified"),
        "failed": job.get("failed"),
        "gateway_id": job.get("gateway_id"),
        "route": job.get("route"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at") or job.get("updated_at") or _iso(),
        "error": job.get("error"),
    }
    _db_put(account_id, f"execution_receipt:{job.get('job_id')}", receipt)
    return receipt


def create_pair_code(account_id: str | None = None) -> dict[str, Any]:
    code = "-".join([secrets.token_hex(2).upper(), secrets.token_hex(2).upper(), secrets.token_hex(2).upper()])
    expires = _now() + timedelta(minutes=PAIR_TTL_MINUTES)
    identity = _account_identity((account_id or "").strip() or None)
    rec = {"code": code, "created_at": _iso(), "expires_at": _iso(expires), "used": False, **identity}
    _db_put(SYSTEM_ACCOUNT, f"pair:{code}", rec)
    return rec


class PairRequest(BaseModel):
    code: str
    device_name: str = "Chrome"
    extension_version: str = "1.0.0"
    profile_instance_id: str | None = None


@router.post("/pair")
def pair(req: PairRequest):
    code = req.code.strip().upper()
    rec = _db_get(SYSTEM_ACCOUNT, f"pair:{code}")
    if not rec:
        raise HTTPException(status_code=404, detail="Код подключения не найден")
    if rec.get("used"):
        raise HTTPException(status_code=409, detail="Код уже использован")
    try:
        expires = datetime.fromisoformat(rec["expires_at"])
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный код подключения")
    if expires < _now():
        _db_delete(SYSTEM_ACCOUNT, f"pair:{code}")
        raise HTTPException(status_code=410, detail="Код подключения истёк")

    gateway_id = "gw_" + secrets.token_hex(6)
    token = secrets.token_urlsafe(32)
    token_rec = {
        "gateway_id": gateway_id,
        "token": token,
        "device_name": req.device_name[:120],
        "extension_version": req.extension_version[:40],
        "created_at": _iso(),
        "expires_at": _iso(_now() + timedelta(days=TOKEN_TTL_DAYS)),
        "last_seen_at": _iso(),
        "state": "paired",
        "current_job_id": None,
        "account_id": rec.get("account_id"),
        "account_name": rec.get("account_name"),
        "expected_avito_user_id": rec.get("expected_avito_user_id"),
        "session_scope": "account" if rec.get("account_id") else "unbound",
        "session_epoch": secrets.token_hex(6),
        "profile_instance_id": str(req.profile_instance_id or "").strip()[:160] or None,
        "identity_verified": None,
        "identity_mismatch": False,
    }
    _supersede_profile_instance_gateways(token_rec.get("profile_instance_id"), gateway_id, rec.get("account_id"))
    _supersede_account_gateways(rec.get("account_id"), gateway_id)
    _db_put(SYSTEM_ACCOUNT, f"token:{token}", token_rec)
    _db_put(SYSTEM_ACCOUNT, f"gateway:{gateway_id}", token_rec)
    _session_vault_put(rec.get("account_id"), token_rec, "paired")
    rec["used"] = True
    rec["gateway_id"] = gateway_id
    _db_put(SYSTEM_ACCOUNT, f"pair:{code}", rec)
    _append_execution_journal(rec.get("account_id"), "browser_session_paired", {
        "gateway_id": gateway_id, "device_name": token_rec.get("device_name"),
        "extension_version": token_rec.get("extension_version"), "session_epoch": token_rec.get("session_epoch"),
        "profile_instance_id": token_rec.get("profile_instance_id")
    }) if rec.get("account_id") else None
    return {"status": "ok", "gateway_id": gateway_id, "token": token, "account_id": rec.get("account_id"), "account_name": rec.get("account_name"), "session_scope": token_rec.get("session_scope"), "profile_instance_id": token_rec.get("profile_instance_id")}


def _auth(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Gateway token required")
    token = authorization.split(" ", 1)[1].strip()
    rec = _db_get(SYSTEM_ACCOUNT, f"token:{token}")
    if not rec:
        raise HTTPException(status_code=401, detail="Gateway token invalid")
    try:
        if datetime.fromisoformat(rec["expires_at"]) < _now():
            raise HTTPException(status_code=401, detail="Gateway token expired")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Gateway token invalid")
    return rec


class HeartbeatRequest(BaseModel):
    state: str = "idle"
    current_url: str | None = None
    avito_logged_in: bool | None = None


@router.post("/heartbeat")
def heartbeat(req: HeartbeatRequest, authorization: str | None = Header(default=None)):
    rec = _auth(authorization)
    rec["last_seen_at"] = _iso()
    rec["current_url"] = (req.current_url or "")[:500]
    rec["avito_logged_in"] = req.avito_logged_in
    if req.avito_logged_in is False and rec.get("account_id"):
        rec["state"] = "reauth_required"
    elif rec.get("identity_mismatch"):
        # Identity mismatch is sticky until a fresh identity probe proves the
        # originally paired account is open again. A heartbeat must never clear it.
        rec["state"] = "identity_mismatch"
    else:
        rec["state"] = req.state[:60]
    _db_put(SYSTEM_ACCOUNT, f"token:{rec['token']}", rec)
    _db_put(SYSTEM_ACCOUNT, f"gateway:{rec['gateway_id']}", rec)
    _session_vault_put(rec.get("account_id"), rec, rec.get("state"))
    return {"status": "ok", "server_time": _iso()}


def _next_waiting_job(gateway_id: str | None = None, current_job_id: str | None = None, bound_account_id: str | None = None) -> tuple[str, str, dict[str, Any]] | None:
    db = SessionLocal()
    try:
        rows = db.execute(text("select account_id,key,value from storage where key like 'mass_editor_job:%' order by id asc")).fetchall()
        decoded: list[tuple[str, str, dict[str, Any]]] = []
        for account_id, key, raw in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            decoded.append((account_id, key, job))

        # 1) Resume the exact job this browser was already executing.
        if gateway_id and current_job_id:
            for account_id, key, job in decoded:
                if job.get("job_id") == current_job_id and job.get("gateway_id") == gateway_id and job.get("status") in {"claimed_gateway", "running_gateway", "resume_gateway"} and bound_account_id and str(account_id) == str(bound_account_id):
                    return account_id, key, job

        # 2) Recover any other unfinished job already leased to this gateway.
        if gateway_id:
            for account_id, key, job in decoded:
                if job.get("gateway_id") == gateway_id and job.get("status") in {"claimed_gateway", "running_gateway", "resume_gateway"} and bound_account_id and str(account_id) == str(bound_account_id):
                    return account_id, key, job

        return None
    finally:
        db.close()


def _claim_oldest_queued_job(gateway_id: str, bound_account_id: str | None = None) -> tuple[str, str, dict[str, Any]] | None:
    """Atomically claim one queued job. Avito mutation jobs require an account-bound browser session."""
    db = SessionLocal()
    try:
        rows = db.execute(text("select id,account_id,key,value from storage where key like 'mass_editor_job:%' order by id asc for update skip locked")).fetchall()
        for row_id, account_id, key, raw in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            status = job.get("status")
            stale_lease = False
            if status in {"claimed_gateway", "running_gateway", "resume_gateway"}:
                try:
                    stale_lease = (_now() - datetime.fromisoformat(job.get("updated_at") or job.get("claimed_at") or "")).total_seconds() > 90
                except Exception:
                    stale_lease = True
            if status not in {"waiting_gateway", "queued_gateway"} and not stale_lease:
                continue
            # Browser writes are never dispatched to a generic/unbound Chrome
            # session. The gateway must be paired from this exact account.
            if not bound_account_id or str(account_id) != str(bound_account_id):
                continue
            now = _iso()
            job["status"] = "claimed_gateway"
            job["gateway_id"] = gateway_id
            job["claimed_at"] = now
            job["updated_at"] = now
            db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(job, ensure_ascii=False), "i": row_id})
            db.commit()
            return account_id, key, job
        db.commit()
        return None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


MIN_AVITO_MASS_WRITE_GATEWAY_VERSION = "2.1.13"

def _gateway_version_at_least(current: str | None, minimum: str = MIN_AVITO_MASS_WRITE_GATEWAY_VERSION) -> bool:
    def parts(v):
        out=[]
        for chunk in str(v or "0").split("."):
            digits="".join(ch for ch in chunk if ch.isdigit())
            out.append(int(digits or 0))
        return tuple((out+[0,0,0])[:3])
    return parts(current) >= parts(minimum)


def _pending_mass_write_exists(account_id: str | None) -> bool:
    """True when this exact account still has an unfinished Avito mass mutation.

    This is deliberately account-scoped. It is used only to explain why an old
    Browser Gateway build is idle; it never dispatches a write or weakens the
    identity guard.
    """
    if not account_id:
        return False
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "select value from storage where account_id=:a and key like 'mass_editor_job:%' order by id desc limit 50"
        ), {"a": str(account_id)}).fetchall()
        for (raw,) in rows:
            try:
                job = json.loads(raw or "{}")
            except Exception:
                continue
            if str(job.get("mode") or "edit") == "scan":
                continue
            if job.get("status") in {"waiting_gateway", "queued_gateway", "claimed_gateway", "running_gateway", "resume_gateway"}:
                return True
        return False
    finally:
        db.close()


def _auto_release_hag_flow(rec: dict[str, Any]) -> None:
    """Finish the HAG client task automatically once a safe Gateway is online.

    Stage 1 retries exactly one pilot. Stage 2 releases the 578-item remainder
    only after that pilot is durably verified after save/reopen.
    """
    account_id = str(rec.get("account_id") or "")
    if account_id != "evgeniy_peregorodki_12447":
        return
    if rec.get("identity_verified") is not True or rec.get("identity_mismatch"):
        return
    if not _gateway_version_at_least(rec.get("extension_version")):
        return
    pilot_key = "mass_editor_job:massedit_hag_livepilot_8dc574b8e8c1"
    main_key = "mass_editor_job:massedit_aa9a44f2122a"
    pilot = _db_get(account_id, pilot_key) or {}
    main = _db_get(account_id, main_key) or {}
    if not pilot or not main:
        return
    pitems = pilot.get("items") or []
    pilot_ok = bool(pitems) and all(str(x.get("run_status") or "") in {"verified", "verified_external"} for x in pitems)
    if not pilot_ok:
        active = str(pilot.get("status") or "") in {"waiting_gateway", "queued_gateway", "claimed_gateway", "running_gateway", "resume_gateway"}
        if not active:
            for item in pitems:
                item["run_status"] = ""
                item["attempts"] = 0
                item["error"] = None
                for k in ["changed_fields","verified_fields","save_trace","terminal_url","diagnostics","last_failed_at","duration_ms"]:
                    item.pop(k, None)
            pilot.update({"status":"waiting_gateway","processed":0,"changed":0,"verified":0,"skipped":0,"failed":0,"error":None,"gateway_id":None,"updated_at":_iso()})
            pilot["route"] = {"executor":"browser_gateway","reason":"hag_2_1_11_autopilot_single_verified_pilot"}
            pilot["metadata"] = {**(pilot.get("metadata") or {}), "diagnostic_capture": True, "autofinish": True}
            _db_put(account_id, pilot_key, pilot)
        return
    if str(main.get("status") or "") in {"paused_pilot_validation", "paused_for_fix", "paused_gateway_upgrade"}:
        main["status"] = "waiting_gateway"
        main["error"] = None
        main["gateway_id"] = None
        main["updated_at"] = _iso()
        main["route"] = {"executor":"browser_gateway","reason":"hag_pilot_verified_auto_release_remaining"}
        main["metadata"] = {**(main.get("metadata") or {}), "autofinish": True, "pilot_job_id": pilot.get("job_id")}
        _db_put(account_id, main_key, main)


def _mass_write_upgrade_state(rec: dict[str, Any]) -> dict[str, Any] | None:
    current = str(rec.get("extension_version") or "0")
    if _gateway_version_at_least(current):
        return None
    if not _pending_mass_write_exists(rec.get("account_id")):
        return None
    return {
        "reason": "gateway_upgrade_required",
        "current_version": current,
        "required_version": MIN_AVITO_MASS_WRITE_GATEWAY_VERSION,
        "account_id": rec.get("account_id"),
    }

@router.get("/next-job")
def next_job(authorization: str | None = Header(default=None)):
    rec = _auth(authorization)
    # A service-worker/Chrome restart must not strand a leased job forever.
    _recover_browser_jobs(rec.get("account_id"))
    found = None
    crowd_session = str(rec.get("account_id") or "") == CROWD_GATEWAY_ACCOUNT
    identity_ready = crowd_session or (rec.get("identity_verified") is True and not rec.get("identity_mismatch"))
    kind = "browser" if crowd_session or not identity_ready else "avito"
    if not identity_ready:
        # Hard isolation rule: a paired Chrome profile is NOT trusted until a
        # read-only identity probe proves that the active Avito profile belongs
        # to the BORIS account selected at pairing. No client READ/WRITE/PUBLISH
        # job may run while identity is unknown or mismatched.
        _maybe_queue_identity_watchdog(rec, interval_seconds=30)
        found = _claim_identity_watchdog_job(rec["gateway_id"], rec.get("account_id"))
        if not found:
            reason = "identity_mismatch" if rec.get("identity_mismatch") else "identity_verification_pending"
            return {"status": "blocked", "reason": reason, "retry_after_seconds": 30}
    else:
        _auto_release_hag_flow(rec)
        # Mass writes require the gateway build that knows how to commit Avito's
        # nested Characteristics drawer and then the main Save changes step.
        # Older builds may open the editor successfully but silently leave the
        # field unsaved, so they are READ-only for mass mutation jobs.
        write_capable = _gateway_version_at_least(rec.get("extension_version"))
        if write_capable:
            found = _next_waiting_job(rec.get("gateway_id"), rec.get("current_job_id"), rec.get("account_id"))
            if not found:
                found = _claim_oldest_queued_job(rec["gateway_id"], rec.get("account_id"))
        if not found:
            found = _next_browser_job(rec.get("gateway_id"), rec.get("current_job_id"), rec.get("account_id"))
            kind = "browser"
        if not found:
            found = _claim_oldest_browser_job(rec["gateway_id"], rec.get("account_id"))
            kind = "browser"
        if not found and crowd_session:
            return {"status": "idle", "crowd_onboarding": True}
        if not found:
            # While idle, periodically prove that the originally paired Avito
            # profile is still open. A mismatch blocks the session; it never
            # transfers this browser to another BORIS account.
            if _maybe_queue_identity_watchdog(rec):
                found = _claim_identity_watchdog_job(rec["gateway_id"], rec.get("account_id"))
                kind = "browser"
            if not found:
                upgrade = _mass_write_upgrade_state(rec)
                if upgrade:
                    return {"status": "idle", "mass_write": upgrade}
                return {"status": "idle"}
    account_id, key, job = found
    if job.get("gateway_id") != rec["gateway_id"]:
        return {"status": "idle"}
    job["status"] = "claimed_gateway"
    job["gateway_id"] = rec["gateway_id"]
    job["claimed_at"] = job.get("claimed_at") or _iso()
    job["updated_at"] = _iso()
    _db_put(account_id, key, job)
    rec["current_job_id"] = job.get("job_id")
    rec["current_capability"] = job.get("capability") or ("avito_mass_editor" if kind == "avito" else None)
    rec["state"] = "working"
    rec["last_seen_at"] = _iso()
    _db_put(SYSTEM_ACCOUNT, f"token:{rec['token']}", rec)
    _db_put(SYSTEM_ACCOUNT, f"gateway:{rec['gateway_id']}", rec)
    if kind == "browser":
        return {"status":"job","job":{
            "kind":"browser","job_id":job.get("job_id"),"account_id":account_id,
            "domain":job.get("domain"),"capability":job.get("capability"),
            "risk_level":job.get("risk_level","READ"),"mode":job.get("mode","prepare"),"confirmed":bool(job.get("confirmed")),
            "steps":job.get("steps",[]),"privacy":job.get("privacy",{}),"metadata":job.get("metadata",{}),
            "current_step":job.get("current_step"),"job_status":job.get("status"),
        }}
    # Never send already-proven terminal items back to the extension.  In
    # particular, autoload-verified items are part of the durable parent job
    # for reporting, but must not be reopened/mutated by Browser Gateway.
    terminal_statuses = {"verified", "verified_external", "skipped", "brand_field_unavailable"}
    dispatch_items = [
        item for item in (job.get("items") or [])
        if str(item.get("run_status") or "") not in terminal_statuses
        and not (str(item.get("run_status") or "") == "failed" and int(item.get("attempts") or 0) >= 3)
    ]
    return {"status":"job","job":{
        "kind":"avito","job_id":job.get("job_id"),"account_id":account_id,
        "mode":job.get("mode","edit"),"items":dispatch_items,"rules":job.get("rules",{}),
        "route":job.get("route",{}),"metadata":job.get("metadata",{}),
    }}


class ProgressRequest(BaseModel):
    job_id: str
    account_id: str
    item_id: int | str | None = None
    event: str
    payload: dict[str, Any] = {}


@router.post("/progress")
def progress(req: ProgressRequest, authorization: str | None = Header(default=None)):
    rec = _auth(authorization)
    # A gateway may report mutations only for the account/browser identity it
    # was paired with. This closes the callback side of cross-account routing.
    if not rec.get("account_id") or str(rec.get("account_id")) != str(req.account_id):
        raise HTTPException(status_code=403, detail="Gateway session is not bound to this account")
    key = f"mass_editor_job:{req.job_id}"
    job = _db_get(req.account_id, key)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if str(job.get("status") or "").startswith("paused_"):
        raise HTTPException(status_code=409, detail="Job paused by BORIS")
    if job.get("gateway_id") not in (None, rec["gateway_id"]):
        raise HTTPException(status_code=409, detail="Job claimed by another gateway")

    event = req.event
    payload = req.payload or {}
    items = job.get("items", [])
    target = None
    if req.item_id is not None:
        for item in items:
            if str(item.get("id")) == str(req.item_id):
                target = item
                break

    if event == "job_started":
        job["status"] = "running_gateway"
    elif event == "item_scanned" and target is not None:
        target["run_status"] = "scanned"
        observed = payload.get("fields", []) or []
        target["observed_fields"] = observed
        job["processed"] = max(int(job.get("processed") or 0), int(payload.get("index") or 0))
        # Merge live editor schema into per-account memory. Never share fields across clients.
        try:
            mem_key = "mass_editor_characteristics_memory"
            memory = _db_get(req.account_id, mem_key) or {"account_id": req.account_id, "fields": {}, "active_items_scanned": 0, "source": "browser_gateway"}
            raw_fields = memory.get("fields") or {}
            iterable = raw_fields.values() if isinstance(raw_fields, dict) else raw_fields
            by_label = {str(f.get("label") or "").strip().lower(): f for f in iterable if isinstance(f, dict) and f.get("label")}
            now_iso = _iso()
            for f in observed:
                label = str(f.get("label") or "").strip()
                if not label:
                    continue
                k = label.lower()
                cur = by_label.get(k) or {"label": label, "options": [], "seen_count": 0}
                opts = set(str(x).strip() for x in (cur.get("options") or []) if str(x).strip())
                opts.update(str(x).strip() for x in (f.get("options") or []) if str(x).strip())
                if f.get("value"):
                    opts.add(str(f.get("value")).strip())
                cur.update({"label": label, "type": f.get("type") or cur.get("type") or "unknown", "options": sorted(opts)[:250], "seen_count": int(cur.get("seen_count") or 0) + 1, "last_seen": now_iso, "source": "browser_gateway"})
                by_label[k] = cur
            memory["fields"] = {str(f.get("label") or ""): f for f in sorted(by_label.values(), key=lambda x: str(x.get("label") or "").lower()) if f.get("label")}

            # Build a per-category/subcategory profile. Mixed Avito accounts must
            # never share one flat set of parameters across unrelated niches.
            discr_labels = ["ProductType", "GoodsType", "GoodsSubType", "ServiceType", "ServiceSubtype", "ServiceSubspecies", "Category", "Subcategory"]
            observed_by_label = {str(f.get("label") or "").strip(): f for f in observed if isinstance(f, dict)}
            parts = [str(target.get("category") or "").strip()]
            for label in discr_labels:
                f = observed_by_label.get(label)
                value = str((f or {}).get("value") or "").strip()
                if value and value not in parts:
                    parts.append(value)
            parts = [p for p in parts if p]
            segment_key = " / ".join(parts[:4]) or "Другие объявления"
            segments = memory.setdefault("segments", {})
            seg = segments.setdefault(segment_key, {"key": segment_key, "title": segment_key, "item_ids": [], "fields": {}, "seen_count": 0, "source": "browser_gateway"})
            iid = str(target.get("id") or req.item_id or "")
            if iid and iid not in seg["item_ids"]:
                seg["item_ids"].append(iid)
            seg["seen_count"] = len(seg["item_ids"])
            seg["updated_at"] = now_iso
            seg_fields = seg.setdefault("fields", {})
            for f in observed:
                label = str(f.get("label") or "").strip()
                if not label:
                    continue
                cur = seg_fields.get(label) or {"label": label, "options": [], "seen_count": 0}
                opts = set(str(x).strip() for x in (cur.get("options") or []) if str(x).strip())
                opts.update(str(x).strip() for x in (f.get("options") or []) if str(x).strip())
                if f.get("value"):
                    opts.add(str(f.get("value")).strip())
                cur.update({"label": label, "type": f.get("type") or cur.get("type") or "unknown", "options": sorted(opts)[:250], "seen_count": int(cur.get("seen_count") or 0) + 1, "last_seen": now_iso, "source": "browser_gateway"})
                seg_fields[label] = cur
            memory.setdefault("item_segments", {})[iid] = segment_key
            memory["active_items_scanned"] = len(memory.get("item_segments") or {})
            memory["updated_at"] = now_iso
            memory["source"] = "browser_gateway"
            _db_put(req.account_id, mem_key, memory)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=734)
    elif event == "item_changed" and target is not None:
        target["run_status"] = "changed"
        target["changed_fields"] = payload.get("changed_fields", [])
        target["promo_clicks"] = int(payload.get("promo_clicks") or 0)
        target["save_trace"] = (payload.get("save_trace") or [])[-8:]
        target["terminal_url"] = str(payload.get("terminal_url") or "")[:500]
        target["error"] = None
    elif event == "item_verified" and target is not None:
        target["run_status"] = "verified"
        target["verified_fields"] = payload.get("verified_fields", [])
        if payload.get("duration_ms") is not None:
            target["duration_ms"] = int(payload.get("duration_ms") or 0)
        target["error"] = None
    elif event == "item_skipped" and target is not None:
        target["run_status"] = "skipped"
        target["error"] = payload.get("reason")
        if payload.get("diagnostics"):
            target["diagnostics"] = (payload.get("diagnostics") or [])[-20:]
    elif event == "diagnostic_frame":
        frames=job.setdefault("diagnostic_frames",[])
        frames.append({"stage":str(payload.get("stage") or "")[:80],"path":str(payload.get("path") or "")[:500],"seq":int(payload.get("seq") or 0),"at":_iso()})
        job["diagnostic_frames"]=sorted(frames,key=lambda x:int(x.get("seq") or 0))[-120:]
    elif event == "diagnostic_frame_failed":
        errs=job.setdefault("diagnostic_frame_errors",[])
        errs.append({"stage":str(payload.get("stage") or "")[:80],"error":str(payload.get("error") or "")[:500],"seq":int(payload.get("seq") or 0),"at":_iso()})
        job["diagnostic_frame_errors"]=errs[-40:]
    elif event == "item_failed" and target is not None:
        target["run_status"] = "failed"
        target["attempts"] = int(target.get("attempts") or 0) + 1
        target["error"] = str(payload.get("error") or "Unknown error")[:500]
        if payload.get("save_trace"):
            target["save_trace"] = (payload.get("save_trace") or [])[-8:]
        if payload.get("terminal_url"):
            target["terminal_url"] = str(payload.get("terminal_url") or "")[:500]
        if payload.get("promo_clicks") is not None:
            target["promo_clicks"] = int(payload.get("promo_clicks") or 0)
        if payload.get("duration_ms") is not None:
            target["duration_ms"] = int(payload.get("duration_ms") or 0)
        target["last_failed_at"] = _iso()
    elif event == "job_completed":
        scan_mode = str(job.get("mode") or "edit") == "scan"
        retryable = any(
            (str(item.get("run_status") or "") in {"", "changed"})
            or (not scan_mode and str(item.get("run_status") or "") == "scanned")
            or (str(item.get("run_status") or "") == "failed" and int(item.get("attempts") or 0) < 3)
            for item in items
            if str(item.get("run_status") or "") not in {"verified_external", "brand_field_unavailable"}
        )
        if retryable:
            job["status"] = "resume_gateway"
            rec["state"] = "working"
        else:
            terminal_failed = sum(1 for item in items if str(item.get("run_status") or "") == "failed")
            job["status"] = "completed" if terminal_failed == 0 else "completed_with_errors"
            rec["current_job_id"] = None
            rec["state"] = "idle"
    elif event == "job_blocked":
        job["status"] = "blocked_gateway"
        job["error"] = str(payload.get("error") or "Gateway blocked")[:500]
        rec["current_job_id"] = None
        rec["state"] = "blocked"

    # Counters are derived from durable per-item state instead of blindly
    # incremented. This makes retries/resumes idempotent after Chrome suspends
    # the extension or the user changes tabs/windows.
    job["changed"] = sum(1 for item in items if str(item.get("run_status") or "") in {"changed", "verified", "verified_external"})
    job["verified"] = sum(1 for item in items if str(item.get("run_status") or "") in {"verified", "verified_external"})
    job["skipped"] = sum(1 for item in items if str(item.get("run_status") or "") in {"skipped", "brand_field_unavailable"})
    job["failed"] = sum(1 for item in items if str(item.get("run_status") or "") == "failed" and int(item.get("attempts") or 0) >= 3)
    scan_mode_now = str(job.get("mode") or "edit") == "scan"
    job["processed"] = sum(
        1 for item in items
        if (scan_mode_now and str(item.get("run_status") or "") == "scanned")
        or str(item.get("run_status") or "") in {"verified", "verified_external", "skipped", "brand_field_unavailable"}
        or (str(item.get("run_status") or "") == "failed" and int(item.get("attempts") or 0) >= 3)
    )
    job["updated_at"] = _iso()
    _db_put(req.account_id, key, job)
    if event == "job_completed" and job.get("status") in {"completed", "completed_with_errors"}:
        receipt = _write_execution_receipt(
            req.account_id, job, "browser_gateway",
            verified=(int(job.get("verified") or 0) == int(job.get("changed") or 0) and int(job.get("failed") or 0) == 0),
        )
        _append_execution_journal(req.account_id, "avito_mass_edit_completed" if str(job.get("mode") or "edit") != "scan" else "avito_scan_completed", {
            "job_id": req.job_id, "executor": "browser_gateway", "gateway_id": rec.get("gateway_id"),
            "total": job.get("total", 0), "changed": job.get("changed", 0), "verified": job.get("verified", 0),
            "skipped": job.get("skipped", 0), "failed": job.get("failed", 0), "status": job.get("status"),
            "receipt_id": receipt.get("receipt_id"),
        })
    elif event == "job_blocked":
        _append_execution_journal(req.account_id, "avito_browser_blocked", {"job_id": req.job_id, "gateway_id": rec.get("gateway_id"), "error": job.get("error")})
    rec["last_seen_at"] = _iso()
    _db_put(SYSTEM_ACCOUNT, f"token:{rec['token']}", rec)
    _db_put(SYSTEM_ACCOUNT, f"gateway:{rec['gateway_id']}", rec)
    return {"status": "ok", "job_status": job.get("status")}



BROWSER_CAPABILITIES = {
    "open_url", "get_current_url", "get_page_title", "read_page", "find_element",
    "click", "input_text", "select", "scroll", "wait_for", "upload_file",
    "download_file", "screenshot", "screenshot_element", "get_tabs", "activate_tab", "close_tab",
    "read_avito_search", "crowd_prefill_registration", "crowd_human_checkpoint",
    "crowd_submit_registration", "crowd_prefill_post", "crowd_submit_post", "crowd_verify_post",
    # Gateway 3.x declarative recipe runtime. These primitives are intentionally
    # generic so new sites/forms are described server-side without shipping a
    # new Chrome extension build.
    "inspect_form", "fill_semantic", "check_semantic", "submit_semantic",
    "assert_value", "wait_page_state", "extract_links", "human_checkpoint",
    "record_start", "record_marker", "record_stop", "demo_pause", "highlight_element",
}
RISK_LEVELS = {"READ", "PREPARE", "WRITE", "PUBLISH", "SENSITIVE"}
ALLOWED_DOMAINS = {
    "avito.ru": "Avito",
    "mail.yandex.ru": "Яндекс Почта",
    "id.yandex.ru": "Яндекс ID",
    "accounts.google.com": "Google Account",
    "mail.google.com": "Gmail",
    "studio.youtube.com": "YouTube Studio",
    "youtube.com": "YouTube",
    "vk.com": "VK",
    "web.telegram.org": "Telegram Web",
    "e.mail.ru": "Mail.ru",
    "account.mail.ru": "Mail.ru Account",
}


def _crowd_domain_allowed(domain: str) -> bool:
    """Allow only domains already present in the audited Crowd marketplace registry."""
    d = str(domain or "").strip().lower().split(":", 1)[0].removeprefix("www.")
    if not d:
        return False
    try:
        from urllib.parse import urlparse
        from app.services import service_marketplace as _marketplace
        for platform in _marketplace.all_platform_objects():
            host = (urlparse(str(getattr(platform, "url", "") or "")).hostname or "").lower().removeprefix("www.")
            if host and (d == host or d.endswith("." + host) or host.endswith("." + d)):
                return True
    except Exception:
        return False
    return False


def _domain_allowed(domain: str) -> bool:
    d = str(domain or "").strip().lower().split(":", 1)[0]
    return any(d == host or d.endswith("." + host) for host in ALLOWED_DOMAINS) or _crowd_domain_allowed(d)


def _ensure_account_access(user, account_id: str) -> None:
    from app.services.platform_roles import is_platform_owner
    if is_platform_owner(user):
        return
    if str(getattr(user, "account_id", "") or "") == str(account_id):
        return
    db=SessionLocal()
    try:
        owned=db.execute(text("select 1 from accounts where account_id=:a and owner_user_id=:u limit 1"),{"a":account_id,"u":getattr(user,"id",None)}).fetchone()
        if owned: return
        granted=db.execute(text("select 1 from user_account_access where account_id=:a and user_id=:u and can_view=true limit 1"),{"a":account_id,"u":getattr(user,"id",None)}).fetchone()
        if granted: return
    finally:
        db.close()
    raise HTTPException(status_code=403,detail="Нет доступа к этому аккаунту")


def _account_gateway_state(account_id: str) -> dict[str, Any]:
    """Authoritative runtime state for one account-scoped Browser Gateway session."""
    db = SessionLocal()
    try:
        rows = db.execute(text("select value from storage where account_id=:sys and key like 'gateway:gw_%' order by id desc limit 200"), {"sys": SYSTEM_ACCOUNT}).fetchall()
    finally:
        db.close()
    now = _now()
    best = None
    for (raw,) in rows:
        try:
            gw = json.loads(raw)
        except Exception:
            continue
        if str(gw.get("account_id") or "") != str(account_id):
            continue
        if gw.get("state") == "superseded" or gw.get("superseded_at"):
            continue
        try:
            last = datetime.fromisoformat(gw.get("last_seen_at") or "")
            age = max(0.0, (now - last).total_seconds())
        except Exception:
            age = 10**9
        current = dict(gw)
        # Chrome MV3 service workers can be suspended between 30s alarm ticks.
        # Treat short heartbeat gaps as recoverable instead of flipping the UI
        # offline after one missed wake-up.  The extension has its own keepalive
        # and job recovery; 120s avoids false-offline while still surfacing real loss.
        current["online"] = age <= 120
        current["age_seconds"] = int(age) if age < 10**8 else None
        if best is None or (current.get("online") and not best.get("online")):
            best = current
    if not best:
        vault = _db_get(SYSTEM_ACCOUNT, f"session:{account_id}") or {}
        legacy_state = str(vault.get("state") or "not_paired")
        if legacy_state in {"auto_routed_away", "profile_superseded", "superseded"}:
            legacy_state = "not_paired"
        return {
            "account_id": account_id, "connected": False, "online": False,
            "state": legacy_state, "requires_login": True,
            "identity_mismatch": bool(vault.get("identity_mismatch")),
            "identity_verified": bool(vault.get("identity_verified")) if vault.get("identity_verified") is not None else None,
            "recoverable": False, "gateway": None,
        }
    requires_login = best.get("avito_logged_in") is False
    identity_mismatch = bool(best.get("identity_mismatch")) or str(best.get("state") or "") == "identity_mismatch"
    identity_verified = best.get("identity_verified")
    if identity_mismatch:
        logical_state = "identity_mismatch"
    elif requires_login:
        logical_state = "reauth_required"
    else:
        logical_state = "online" if best.get("online") else "offline"
    return {
        "account_id": account_id,
        "connected": bool(best.get("online")),
        "online": bool(best.get("online")),
        "state": logical_state,
        "requires_login": requires_login,
        "identity_mismatch": identity_mismatch,
        "identity_verified": bool(identity_verified) if identity_verified is not None else None,
        "recoverable": bool(not requires_login and not identity_mismatch and best.get("gateway_id")),
        "gateway": {k: best.get(k) for k in ["gateway_id","device_name","extension_version","last_seen_at","state","current_job_id","current_capability","avito_logged_in","services","account_id","account_name","expected_avito_user_id","session_scope","session_epoch","profile_instance_id","identity_verified","identity_mismatch","actual_profile_name","online","age_seconds"]},
    }


def _append_browser_audit(account_id: str, job: dict[str, Any], event: str, payload: dict[str, Any] | None = None) -> None:
    """Durable account-scoped audit trail for browser execution and verification."""
    entry = {
        "event_id": "bevt_" + secrets.token_hex(6), "at": _iso(), "event": str(event),
        "job_id": job.get("job_id"), "account_id": account_id, "capability": job.get("capability"),
        "risk_level": job.get("risk_level"), "executor": "browser_gateway", "payload": payload or {},
    }
    _db_put(account_id, f"browser_audit:{entry['at']}:{entry['event_id']}", entry)


def _recover_browser_jobs(account_id: str | None = None) -> dict[str, int]:
    """Release stale claims after gateway loss; completed work is never replayed."""
    db = SessionLocal(); released = failed = 0
    try:
        q = "select id,account_id,value from storage where key like 'browser_job:%'"
        params = {}
        if account_id:
            q += " and account_id=:a"; params["a"] = account_id
        rows = db.execute(text(q), params).fetchall()
        for row_id, acc, raw in rows:
            try: job = json.loads(raw)
            except Exception: continue
            status = str(job.get("status") or "")
            if status not in {"claimed_gateway","running_gateway","resume_gateway","waiting_human"}: continue
            gwid = job.get("gateway_id"); stale = True
            if gwid:
                gw = _db_get(SYSTEM_ACCOUNT, f"gateway:{gwid}") or {}
                try: stale = (_now() - datetime.fromisoformat(gw.get("last_seen_at") or "")).total_seconds() > 75
                except Exception: stale = True
            if not stale: continue
            attempts = int(job.get("recovery_attempts") or 0) + 1
            job["recovery_attempts"] = attempts; job["updated_at"] = _iso(); job["gateway_id"] = None
            if status == "waiting_human" and str(job.get("capability") or "").startswith("crowd_"):
                # A CAPTCHA checkpoint is before submission.  If Chrome died here,
                # restart this browser job from step 0 on the next live Gateway so
                # the same registration is repopulated instead of requiring a new
                # pairing code or remaining stranded on the old gateway_id.
                job["current_step"] = None
                job["results"] = []
                job.pop("human_checkpoint", None)
                job["metadata"] = {**(job.get("metadata") or {}), "auto_recovered_waiting_human": True}
            crowd_recoverable = str(job.get("capability") or "").startswith("crowd_")
            if attempts <= 3 or crowd_recoverable:
                job["status"] = "waiting_gateway"; job["recovered_at"] = _iso(); released += 1
            else:
                job["status"] = "completed_with_errors"; job["error"] = "Browser Session потеряна после 3 восстановлений"; job["completed_at"] = _iso(); failed += 1
            db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job,ensure_ascii=False),"i":row_id})
            _append_browser_audit(acc, job, "job_recovered" if attempts <= 3 else "job_recovery_exhausted", {"attempt":attempts})
            if attempts > 3:
                _write_execution_receipt(acc, job, "browser_gateway", verified=False)
                _append_execution_journal(acc, "browser_job_recovery_exhausted", {"job_id": job.get("job_id"), "attempts": attempts, "error": job.get("error")})
        db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()
    return {"released": released, "failed": failed}


def _avito_api_state(account_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = db.execute(text("select avito_client_id,avito_client_secret,avito_user_id from accounts where account_id=:a limit 1"), {"a": account_id}).fetchone()
    finally:
        db.close()
    connected = bool(row and row[0] and row[1])
    return {"connected": connected, "avito_user_id": str(row[2] or "") if row else ""}


def maintain_browser_sessions() -> dict[str, int]:
    """Server-side watchdog run by the existing background_jobs engine; no second worker system."""
    recovered = _recover_browser_jobs(None)
    db = SessionLocal()
    online = offline = reauth = superseded = 0
    try:
        rows = db.execute(text("select value from storage where account_id=:sys and key like 'gateway:gw_%'"), {"sys": SYSTEM_ACCOUNT}).fetchall()
    finally:
        db.close()
    seen_accounts: set[str] = set()
    for (raw,) in rows:
        try: gw = json.loads(raw)
        except Exception: continue
        account_id = str(gw.get("account_id") or "")
        if not account_id or account_id in seen_accounts: continue
        if gw.get("state") == "superseded" or gw.get("superseded_at"):
            superseded += 1
            continue
        seen_accounts.add(account_id)
        state = _account_gateway_state(account_id)
        if state.get("requires_login"):
            reauth += 1; logical = "reauth_required"
        elif state.get("online"):
            online += 1; logical = "online"
        else:
            offline += 1; logical = "offline"
        current = state.get("gateway") or gw
        current["last_seen_at"] = (state.get("gateway") or {}).get("last_seen_at") or gw.get("last_seen_at")
        _session_vault_put(account_id, current, logical)
    return {"online": online, "offline": offline, "reauth": reauth, "superseded": superseded, **recovered}


def _execution_route(account_id: str, capability: str, risk_level: str = "READ") -> dict[str, Any]:
    """Single API-first decision point. Conservative: only capabilities proven safe via official API route there."""
    capability = str(capability or "").strip().lower()
    risk = str(risk_level or "READ").strip().upper()
    api_capabilities = {
        "account_self", "list_items", "item_read", "autoload_status", "autoload_upload",
        "balance_read", "messenger_read",
    }
    browser_required = capability in {
        "mass_edit_characteristics", "scan_characteristics", "edit_live_form", "visual_qa",
    }
    api_state = _avito_api_state(account_id)
    if capability in api_capabilities and not browser_required and api_state.get("connected"):
        return {"executor": "avito_api", "reason": "official_api_supported", "browser_required": False, "api": api_state}
    session = _account_gateway_state(account_id)
    if session.get("identity_mismatch"):
        return {"executor": "blocked", "reason": "browser_identity_mismatch", "browser_required": True, "session": session, "api": api_state}
    if session.get("online") and session.get("identity_verified") is not True:
        return {"executor": "blocked", "reason": "browser_identity_verification_pending", "browser_required": True, "session": session, "api": api_state}
    if capability in api_capabilities and not browser_required and not api_state.get("connected"):
        if session.get("online"):
            return {"executor": "browser_gateway", "reason": "api_not_connected_browser_fallback", "browser_required": True, "api": api_state, "session": session}
        return {"executor": "blocked", "reason": "api_not_connected_and_browser_offline", "browser_required": False, "api": api_state, "session": session}
    if browser_required and not session.get("online"):
        return {"executor": "blocked", "reason": "account_browser_session_required", "browser_required": True, "session": session}
    if risk != "READ" and not session.get("online"):
        return {"executor": "blocked", "reason": "account_browser_session_required", "browser_required": True, "session": session}
    return {"executor": "browser_gateway", "reason": "api_capability_not_available", "browser_required": True, "session": session}


def _next_browser_job(gateway_id: str | None, current_job_id: str | None, bound_account_id: str | None = None):
    if not gateway_id:
        return None
    db = SessionLocal()
    try:
        rows = db.execute(text("select account_id,key,value from storage where key like 'browser_job:%' order by id asc")).fetchall()
        decoded = []
        for account_id, key, raw in rows:
            try: job = json.loads(raw)
            except Exception: continue
            decoded.append((account_id, key, job))
        if current_job_id:
            for account_id, key, job in decoded:
                if job.get("job_id") == current_job_id and job.get("gateway_id") == gateway_id and job.get("status") in {"claimed_gateway","running_gateway","resume_gateway"} and bound_account_id and str(account_id) == str(bound_account_id):
                    return account_id, key, job
        for account_id, key, job in decoded:
            if job.get("gateway_id") == gateway_id and job.get("status") in {"claimed_gateway","running_gateway","resume_gateway"} and bound_account_id and str(account_id) == str(bound_account_id):
                return account_id, key, job
        return None
    finally:
        db.close()



def _claim_oldest_browser_job(gateway_id: str, bound_account_id: str | None = None):
    db = SessionLocal()
    try:
        rows = db.execute(text("select id,account_id,key,value from storage where key like 'browser_job:%' order by id asc for update skip locked")).fetchall()
        for row_id, account_id, key, raw in rows:
            try: job = json.loads(raw)
            except Exception: continue
            if job.get("status") not in {"waiting_gateway","queued_gateway"}:
                continue
            # Every client browser job, including READ, is account-scoped. Reading a
            # page from the wrong logged-in profile can leak another client's data.
            if not bound_account_id or str(account_id) != str(bound_account_id):
                continue
            preferred = job.get("gateway_id")
            if preferred and preferred != gateway_id:
                continue
            now = _iso()
            job.update({"status":"claimed_gateway","gateway_id":gateway_id,"claimed_at":job.get("claimed_at") or now,"updated_at":now})
            db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job,ensure_ascii=False),"i":row_id})
            db.commit()
            return account_id, key, job
        db.commit(); return None
    except Exception:
        db.rollback(); raise
    finally:
        db.close()


def _claim_identity_watchdog_job(gateway_id: str, bound_account_id: str | None = None):
    """Claim only the account's READ-only identity watchdog job.

    This is deliberately separate from the generic browser queue so an identity
    mismatch can never accidentally drain a WRITE/PUBLISH/ordinary READ task.
    """
    if not gateway_id or not bound_account_id:
        return None
    db = SessionLocal()
    try:
        rows = db.execute(text("select id,account_id,key,value from storage where account_id=:a and key like 'browser_job:%' order by id asc for update skip locked"), {"a": bound_account_id}).fetchall()
        for row_id, account_id, key, raw in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            if str((job.get("metadata") or {}).get("purpose") or "") != "auto_identity_watchdog":
                continue
            if job.get("status") not in {"waiting_gateway", "queued_gateway", "claimed_gateway", "running_gateway", "resume_gateway"}:
                continue
            preferred = job.get("gateway_id")
            if preferred and preferred != gateway_id:
                continue
            now = _iso()
            job.update({
                "status": "claimed_gateway", "gateway_id": gateway_id,
                "claimed_at": job.get("claimed_at") or now, "updated_at": now,
            })
            db.execute(text("update storage set value=:v where id=:i"), {"v": json.dumps(job, ensure_ascii=False), "i": row_id})
            db.commit()
            return account_id, key, job
        db.commit()
        return None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class BrowserStep(BaseModel):
    action: str
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    value: str | None = None
    timeout_ms: int = 15000
    redact_selectors: list[str] = []
    # Gateway 3.x stable recipe runtime. Site-specific behavior belongs here,
    # not in extension source. Only declarative values are accepted; no JS/code.
    options: dict[str, Any] = {}


class BrowserJobCreate(BaseModel):
    account_id: str
    domain: str
    capability: str
    risk_level: Literal["READ","PREPARE","WRITE","PUBLISH","SENSITIVE"] = "READ"
    mode: str = "prepare"
    steps: list[BrowserStep]
    gateway_id: str | None = None
    confirmed: bool = False
    metadata: dict[str, Any] = {}


@control_router.post("/jobs")
def create_browser_job(req: BrowserJobCreate, user=Depends(get_current_user)):
    _ensure_account_access(user, req.account_id)
    if not _domain_allowed(req.domain):
        raise HTTPException(status_code=400, detail="Домен не разрешён Browser Gateway")
    if req.capability not in BROWSER_CAPABILITIES and req.capability not in {"browser_recipe","instruction_record","visual_qa","youtube_prepare","vk_prepare","avito_search_analysis"}:
        raise HTTPException(status_code=400, detail="Неизвестная capability")
    if req.risk_level in {"WRITE","PUBLISH","SENSITIVE"} and not req.confirmed:
        raise HTTPException(status_code=409, detail="Для этого действия требуется явное подтверждение владельца")
    # All browser jobs are account-scoped, including READ: never leave a task
    # waiting for an arbitrary logged-in profile.
    session = _account_gateway_state(req.account_id)
    if not session.get("online"):
        raise HTTPException(status_code=409, detail="Для браузерного действия нужна отдельная Browser Session этого аккаунта")
    if session.get("identity_mismatch"):
        raise HTTPException(status_code=409, detail="В Browser Session открыт другой профиль Avito. BORIS заблокировал действия до возврата правильного аккаунта.")
    if session.get("identity_verified") is not True:
        raise HTTPException(status_code=409, detail="Browser Session ещё проверяет, что открыт правильный профиль Avito. Дождитесь автоматической проверки перед действиями.")
    if session.get("requires_login"):
        raise HTTPException(status_code=409, detail="В Browser Session этого аккаунта требуется повторный вход в Avito")
    clean_steps = []
    for step in req.steps[:120]:
        if step.action not in BROWSER_CAPABILITIES:
            raise HTTPException(status_code=400, detail=f"Недопустимое действие: {step.action}")
        if step.url:
            from urllib.parse import urlparse
            host = urlparse(step.url).hostname or ""
            if not _domain_allowed(host):
                raise HTTPException(status_code=400, detail=f"URL вне разрешённых доменов: {host}")
        if step.selector and "password" in step.selector.lower():
            raise HTTPException(status_code=400, detail="Селекторы password-полей запрещены")
        clean_steps.append(step.model_dump())
    job_id = "browser_" + secrets.token_hex(7)
    job = {
        "job_id":job_id,"status":"waiting_gateway","created_at":_iso(),"updated_at":_iso(),
        "account_id":req.account_id,"domain":req.domain.lower(),"capability":req.capability,
        "risk_level":req.risk_level,"mode":req.mode,"steps":clean_steps,"gateway_id":req.gateway_id,"confirmed":bool(req.confirmed),
        "initiated_by_user_id":getattr(user,"id",None),"metadata":req.metadata,"results":[],"error":None,
        "privacy":{"guard":True,"never_read_passwords":True},
    }
    _db_put(req.account_id, f"browser_job:{job_id}", job)
    return {"status":"queued","job_id":job_id,"risk_level":req.risk_level,"steps":len(clean_steps)}


@control_router.get("/jobs/{job_id}")
def get_browser_job(job_id: str, account_id: str, user=Depends(get_current_user)):
    _ensure_account_access(user, account_id)
    job = _db_get(account_id, f"browser_job:{job_id}")
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    return {"status":"ok","job":job}


@control_router.get("/sessions/{account_id}")
def account_session(account_id: str, user=Depends(get_current_user)):
    _ensure_account_access(user, account_id)
    session = _account_gateway_state(account_id)
    vault = _db_get(SYSTEM_ACCOUNT, f"session:{account_id}") or {}
    return {"status": "ok", "session": session, "vault": {k: vault.get(k) for k in ["account_id","account_name","expected_avito_user_id","gateway_id","device_name","extension_version","state","last_seen_at","avito_logged_in","session_scope","profile_instance_id","identity_verified","identity_mismatch","actual_profile_name","updated_at"]}}


@control_router.get("/receipts/{account_id}")
def execution_receipts(account_id: str, limit: int = 50, user=Depends(get_current_user)):
    _ensure_account_access(user, account_id)
    db = SessionLocal()
    try:
        rows = db.execute(text("select value from storage where account_id=:a and key like 'execution_receipt:%' order by id desc limit :n"), {"a": account_id, "n": max(1, min(int(limit), 200))}).fetchall()
    finally:
        db.close()
    receipts = []
    for (raw,) in rows:
        try: receipts.append(json.loads(raw))
        except Exception: continue
    return {"status": "ok", "account_id": account_id, "receipts": receipts}


@control_router.get("/execution-health/{account_id}")
def execution_health(account_id: str, user=Depends(get_current_user)):
    _ensure_account_access(user, account_id)
    session = _account_gateway_state(account_id)
    api_state = _avito_api_state(account_id)
    db = SessionLocal()
    try:
        rows = db.execute(text("select key,value from storage where account_id=:a and (key like 'mass_editor_job:%' or key like 'browser_job:%') order by id desc limit 200"), {"a": account_id}).fetchall()
    finally:
        db.close()
    jobs=[]
    for key, raw in rows:
        try: jobs.append(json.loads(raw))
        except Exception: continue
    active=[j for j in jobs if j.get("status") in {"waiting_gateway","queued_gateway","claimed_gateway","running_gateway","resume_gateway","waiting_human","queued","claimed_isolated","starting","running","resume_isolated","waiting_avito_code"}]
    failed=[j for j in jobs if j.get("status") in {"completed_with_errors","blocked_gateway","blocked_auth_or_ui","blocked_transport","blocked_network_route","blocked_captcha","blocked_identity","blocked_credentials"}]
    completed=[j for j in jobs if j.get("status") == "completed"]
    browser_ready = bool(session.get("online") and not session.get("requires_login") and not session.get("identity_mismatch") and session.get("identity_verified") is True)
    isolated_session = _db_get(account_id, "isolated_browser_session") or {}
    isolated_route = _db_get(account_id, "isolated_browser_route") or {}
    isolated_ready = bool(isolated_session.get("ready") is True and isolated_session.get("state") == "ready" and isolated_route.get("state") == "ready")
    browser_executor = "browser_gateway" if browser_ready else ("isolated_browser" if isolated_ready else None)
    browser_execution_ready = bool(browser_ready or isolated_ready)
    api_ready = bool(api_state.get("connected"))
    return {
        "status":"ok","account_id":account_id,"session":session,"api":api_state,
        "isolated":{"session":isolated_session,"route":isolated_route,"ready":isolated_ready},
        "queue":{"active":len(active),"failed_recent":len(failed[:50]),"completed_recent":len(completed[:50])},
        "api_ready": api_ready,
        "browser_ready": browser_ready,
        "isolated_ready": isolated_ready,
        "browser_execution_ready": browser_execution_ready,
        "browser_executor": browser_executor,
        "ready_for_background_work": bool(api_ready and browser_execution_ready),
        "mode": "full" if api_ready and browser_execution_ready else ("api_only" if api_ready else ("browser_only" if browser_execution_ready else "offline")),
    }


class RouteRequest(BaseModel):
    account_id: str
    capability: str
    risk_level: Literal["READ","PREPARE","WRITE","PUBLISH","SENSITIVE"] = "READ"


@control_router.post("/route")
def route_execution(req: RouteRequest, user=Depends(get_current_user)):
    _ensure_account_access(user, req.account_id)
    return {"status": "ok", "account_id": req.account_id, "capability": req.capability, **_execution_route(req.account_id, req.capability, req.risk_level)}


@control_router.get("/gateways")
def list_gateways(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        rows = db.execute(text("select value from storage where account_id=:a and key like 'gateway:gw_%' order by id desc"), {"a":SYSTEM_ACCOUNT}).fetchall()
    finally:
        db.close()
    out=[]; now=_now()
    for (raw,) in rows:
        try: g=json.loads(raw)
        except Exception: continue
        try: last=datetime.fromisoformat(g.get("last_seen_at") or "")
        except Exception: last=datetime.fromtimestamp(0,tz=timezone.utc)
        out.append({
            "gateway_id":g.get("gateway_id"),"device_name":g.get("device_name"),"extension_version":g.get("extension_version"),
            "online":(now-last).total_seconds()<75,"last_seen":g.get("last_seen_at"),"state":g.get("state"),
            "current_job_id":g.get("current_job_id"),"current_capability":g.get("current_capability"),
            "account_id":g.get("account_id"),"session_scope":g.get("session_scope","unbound"),
            "services":g.get("services") or {},
            "protocol_version":g.get("protocol_version"),"features":g.get("features") or [],
        })
    return {"status":"ok","gateways":out,"allowed_services":sorted(set(ALLOWED_DOMAINS.values()))}


class BrowserHeartbeatRequest(BaseModel):
    state: str = "idle"
    current_url: str | None = None
    services: dict[str, bool] = {}
    current_capability: str | None = None
    extension_version: str | None = None
    # Stable Gateway 3.x protocol negotiation. The server should route jobs by
    # supported primitives/features instead of forcing extension upgrades for
    # every site-specific recipe change.
    protocol_version: int | None = None
    features: list[str] = []


@router.post("/heartbeat-v2")
def heartbeat_v2(req: BrowserHeartbeatRequest, authorization: str | None = Header(default=None)):
    rec=_auth(authorization)
    # Self-heal stale job pointers left by a browser/service-worker interruption.
    current_job_id = str(rec.get("current_job_id") or "")
    if current_job_id and rec.get("account_id"):
        bj = _db_get(str(rec.get("account_id")), f"browser_job:{current_job_id}")
        mj = _db_get(str(rec.get("account_id")), f"mass_editor_job:{current_job_id}") if not bj else None
        current_job = bj or mj or {}
        if not current_job or str(current_job.get("status") or "") in {"completed","completed_with_errors","blocked_gateway","blocked_auth_or_ui","blocked_credentials","blocked_transport","paused_for_fix","superseded_by_new_gateway"}:
            rec["current_job_id"] = None
            rec["current_capability"] = None
            if req.state == "working":
                req.state = "idle"
    rec["last_seen_at"]=_iso(); rec["current_url"]=(req.current_url or "")[:500]
    if req.extension_version:
        rec["extension_version"] = str(req.extension_version)[:40]
    if req.protocol_version is not None:
        rec["protocol_version"] = max(0, min(int(req.protocol_version), 1000))
    if req.features:
        rec["features"] = sorted({str(x)[:120] for x in req.features if str(x).strip()})[:200]
    # Never let an ordinary heartbeat erase an identity mismatch. Only the
    # identity watchdog may clear this sticky safety block after verification.
    rec["state"] = "identity_mismatch" if rec.get("identity_mismatch") else req.state[:60]
    rec["services"]={str(k)[:80]:bool(v) for k,v in (req.services or {}).items()}
    rec["current_capability"]=(req.current_capability or "")[:120] or None
    _db_put(SYSTEM_ACCOUNT,f"token:{rec['token']}",rec); _db_put(SYSTEM_ACCOUNT,f"gateway:{rec['gateway_id']}",rec)
    _session_vault_put(rec.get("account_id"), rec, rec.get("state"))
    try: rotate_required=(datetime.fromisoformat(rec["expires_at"])-_now()) < timedelta(days=7)
    except Exception: rotate_required=True
    return {"status":"ok","server_time":_iso(),"token_expires_at":rec.get("expires_at"),"rotate_required":rotate_required}


@router.post("/rotate-token")
def rotate_token(authorization: str | None = Header(default=None)):
    rec=_auth(authorization); old=rec["token"]; new=secrets.token_urlsafe(32)
    rec["token"]=new; rec["expires_at"]=_iso(_now()+timedelta(days=TOKEN_TTL_DAYS)); rec["rotated_at"]=_iso()
    _db_put(SYSTEM_ACCOUNT,f"token:{new}",rec); _db_put(SYSTEM_ACCOUNT,f"gateway:{rec['gateway_id']}",rec); _db_delete(SYSTEM_ACCOUNT,f"token:{old}")
    return {"status":"ok","token":new,"expires_at":rec["expires_at"]}


class BrowserProgressRequest(BaseModel):
    job_id: str
    account_id: str
    event: str
    step_index: int | None = None
    payload: dict[str, Any] = {}


@router.post("/browser-progress")
def browser_progress(req: BrowserProgressRequest, authorization: str | None = Header(default=None)):
    rec=_auth(authorization)
    if not rec.get("account_id") or str(rec.get("account_id")) != str(req.account_id):
        raise HTTPException(status_code=403,detail="Gateway session is not bound to this account")
    key=f"browser_job:{req.job_id}"; job=_db_get(req.account_id,key)
    if not job: raise HTTPException(status_code=404,detail="Browser job not found")
    if job.get("gateway_id") not in (None,rec["gateway_id"]): raise HTTPException(status_code=409,detail="Job claimed by another gateway")
    job["gateway_id"]=rec["gateway_id"]
    event=req.event; payload=req.payload or {}
    if event=="job_started": job["status"]="running_gateway"; job["started_at"]=job.get("started_at") or _iso()
    elif event=="step_result":
        results=job.setdefault("results",[]); idx=int(req.step_index or 0)
        results[:] = [x for x in results if int(x.get("step_index",-1)) != idx]
        results.append({"step_index":idx,"at":_iso(),"result":payload})
        job["current_step"] = idx
    elif event=="human_checkpoint_ready":
        idx=int(req.step_index or 0)
        job["current_step"] = idx
        job["status"] = "waiting_human"
        job["human_checkpoint"] = {"at": _iso(), **payload}
        rec["state"] = "waiting_human"
        rec["current_job_id"] = job.get("job_id")
        rec["current_capability"] = job.get("capability") or "crowd_onboarding"
    elif event=="job_completed": job["status"]="completed"; job["completed_at"]=_iso(); rec["current_job_id"]=None; rec["current_capability"]=None; rec["state"]="idle"
    elif event=="job_failed": job["status"]="completed_with_errors"; job["completed_at"]=_iso(); job["error"]=str(payload.get("error") or "Browser job failed")[:1000]; rec["current_job_id"]=None; rec["current_capability"]=None; rec["state"]="idle"
    job["updated_at"]=_iso(); _db_put(req.account_id,key,job)
    if event == "job_completed":
        try:
            watchdog_outcome = _process_identity_watchdog(job, rec)
            if watchdog_outcome is not None:
                job["identity_watchdog"] = watchdog_outcome
                _db_put(req.account_id, key, job)
                # Identity validation may set/clear the sticky mismatch flag in the
                # authoritative token record. Refresh it before writing completion
                # state so this callback cannot accidentally erase the safety block.
                refreshed = _db_get(SYSTEM_ACCOUNT, f"token:{rec['token']}") or rec
                rec.clear(); rec.update(refreshed)
                rec["current_job_id"] = None
                rec["current_capability"] = None
                outcome_status = str(watchdog_outcome.get("status") or "")
                if outcome_status == "mismatch" or rec.get("identity_mismatch"):
                    rec["state"] = "identity_mismatch"
                elif outcome_status == "verified":
                    rec["state"] = "idle"
        except Exception as _watch_exc:
            job["identity_watchdog"] = {"status":"error","message":str(_watch_exc)[:180]}
            _db_put(req.account_id, key, job)
        # Position Monitor reuses the same already-authenticated READ-only page
        # result. It is strictly lower authority than Browser Gateway identity:
        # no provider writes and no promotion decision are performed here.
        try:
            from app.services.avito_position_monitor import process_completed_job as _position_process_completed
            _position_outcome = _position_process_completed(job)
            if _position_outcome is not None:
                job["position_monitor"] = {
                    "status": _position_outcome.get("status", "ok") if isinstance(_position_outcome, dict) else "ok",
                    "measured_at": (_position_outcome or {}).get("measured_at") if isinstance(_position_outcome, dict) else None,
                    "matched": (_position_outcome or {}).get("page_cards_matched") if isinstance(_position_outcome, dict) else None,
                }
                # POSITION_HARVEST_PAYLOAD_V1: preserve the bounded parser result
                # so the hourly monitor can continue pagination exactly once without
                # re-parsing the completed browser job and duplicating snapshots.
                if isinstance(_position_outcome, dict):
                    job["position_monitor_result"] = _position_outcome
                    job["position_monitor_harvested_at"] = None
                _db_put(req.account_id, key, job)
        except Exception as _position_exc:
            job["position_monitor"] = {"status":"error","message":str(_position_exc)[:180]}
            _db_put(req.account_id, key, job)
    if event in {"job_completed", "job_failed"}:
        receipt = _write_execution_receipt(req.account_id, job, "browser_gateway", verified=(event == "job_completed"))
        _append_execution_journal(req.account_id, "browser_job_completed" if event == "job_completed" else "browser_job_failed", {
            "job_id": req.job_id, "executor": "browser_gateway", "gateway_id": rec.get("gateway_id"),
            "capability": job.get("capability"), "domain": job.get("domain"), "risk_level": job.get("risk_level"),
            "status": job.get("status"), "error": job.get("error"), "results_count": len(job.get("results") or []),
            "receipt_id": receipt.get("receipt_id"),
        })
    rec["last_seen_at"]=_iso(); _db_put(SYSTEM_ACCOUNT,f"token:{rec['token']}",rec); _db_put(SYSTEM_ACCOUNT,f"gateway:{rec['gateway_id']}",rec)
    return {"status":"ok","job_status":job.get("status")}


class ScreenshotArtifact(BaseModel):
    job_id: str
    account_id: str
    image_base64: str
    mime: str = "image/png"
    step_index: int = 0


@router.post("/artifact")
def save_artifact(req: ScreenshotArtifact, authorization: str | None = Header(default=None)):
    rec=_auth(authorization)
    if not rec.get("account_id") or str(rec.get("account_id")) != str(req.account_id):
        raise HTTPException(status_code=403,detail="Gateway session is not bound to this account")
    job=_db_get(req.account_id,f"browser_job:{req.job_id}")
    if not job:
        job=_db_get(req.account_id,f"mass_editor_job:{req.job_id}")
    if not job: raise HTTPException(status_code=404,detail="Browser or mass editor job not found")
    if job.get("gateway_id") not in (None,rec["gateway_id"]): raise HTTPException(status_code=409,detail="Job claimed by another gateway")
    try: raw=base64.b64decode(req.image_base64,validate=True)
    except Exception: raise HTTPException(status_code=400,detail="Invalid image")
    if len(raw)>8*1024*1024: raise HTTPException(status_code=413,detail="Screenshot too large")
    ext="jpg" if "jpeg" in req.mime.lower() else "png"
    root=Path("/root/BORIS/frontend/public/help/gateway")/req.job_id
    root.mkdir(parents=True,exist_ok=True)
    path=root/f"step-{max(0,int(req.step_index)):03d}.{ext}"; path.write_bytes(raw)
    return {"status":"ok","path":f"/help/gateway/{req.job_id}/{path.name}"}


@router.post("/video-artifact")
async def save_video_artifact(
    request: Request,
    job_id: str,
    account_id: str,
    step_index: int = 0,
    authorization: str | None = Header(default=None),
):
    rec = _auth(authorization)
    if not rec.get("account_id") or str(rec.get("account_id")) != str(account_id):
        raise HTTPException(status_code=403, detail="Gateway session is not bound to this account")
    job = _db_get(account_id, f"browser_job:{job_id}") or _db_get(account_id, f"mass_editor_job:{job_id}")
    if not job:
        raise HTTPException(status_code=404, detail="Browser or mass editor job not found")
    if job.get("gateway_id") not in (None, rec["gateway_id"]):
        raise HTTPException(status_code=409, detail="Job claimed by another gateway")
    mime = str(request.headers.get("content-type") or "video/webm").split(";", 1)[0].strip().lower()
    if mime not in {"video/webm", "video/mp4"}:
        raise HTTPException(status_code=415, detail="Unsupported video type")
    max_bytes = 120 * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Video too large")
        except ValueError:
            pass
    root = Path("/root/BORIS/frontend/public/help/gateway") / job_id
    root.mkdir(parents=True, exist_ok=True)
    ext = "mp4" if mime == "video/mp4" else "webm"
    path = root / f"video-{max(0, int(step_index)):03d}-{int(_now().timestamp())}.{ext}"
    total = 0
    try:
        with path.open("wb") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="Video too large")
                fh.write(chunk)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1586)
        raise
    if total <= 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Empty video")
    public_path = f"/help/gateway/{job_id}/{path.name}"
    _append_execution_journal(account_id, "browser_video_saved", {
        "job_id": job_id,
        "gateway_id": rec.get("gateway_id"),
        "path": public_path,
        "bytes": total,
        "mime": mime,
        "step_index": int(step_index),
    })
    return {"status": "ok", "path": public_path, "bytes": total, "mime": mime}


@router.get("/status")
def status(authorization: str | None = Header(default=None)):
    rec = _auth(authorization)
    return {"status": "ok", "gateway": {k: rec.get(k) for k in ["gateway_id", "device_name", "extension_version", "protocol_version", "features", "last_seen_at", "state", "current_job_id", "current_capability", "avito_logged_in", "services", "account_id", "account_name", "expected_avito_user_id", "session_scope", "session_epoch", "profile_instance_id", "identity_verified", "identity_mismatch", "actual_profile_name", "identity_checked_at"]}}


# Gateway 2.0 generic transport aliases. Legacy /api/avito/browser-gateway/*
# stays mounted so already-installed 1.x extensions keep reconnecting.
@transport_router.post("/pair")
def pair_v2(req: PairRequest):
    return pair(req)

@transport_router.post("/heartbeat")
def heartbeat_transport_v2(req: BrowserHeartbeatRequest, authorization: str | None = Header(default=None)):
    return heartbeat_v2(req, authorization)

@transport_router.get("/next-job")
def next_job_transport_v2(authorization: str | None = Header(default=None)):
    return next_job(authorization)

@transport_router.post("/browser-progress")
def browser_progress_transport_v2(req: BrowserProgressRequest, authorization: str | None = Header(default=None)):
    return browser_progress(req, authorization)

@transport_router.post("/artifact")
def artifact_transport_v2(req: ScreenshotArtifact, authorization: str | None = Header(default=None)):
    return save_artifact(req, authorization)


@transport_router.post("/video-artifact")
async def video_artifact_transport_v2(
    request: Request,
    job_id: str,
    account_id: str,
    step_index: int = 0,
    authorization: str | None = Header(default=None),
):
    return await save_video_artifact(request, job_id, account_id, step_index, authorization)


@transport_router.get("/status")
def status_transport_v2(authorization: str | None = Header(default=None)):
    return status(authorization)

@transport_router.post("/rotate-token")
def rotate_token_transport_v2(authorization: str | None = Header(default=None)):
    return rotate_token(authorization)


# BORIS_AVITO_GATEWAY_ACCOUNT_ISOLATION_V2
def _rebind_gateway_by_profile_name(requested_account_id: str, actual_name: str, gateway_id: str | None = None) -> dict:
    """Compatibility name; V2 NEVER rebinds a gateway to another account.

    The gateway is permanently scoped to the BORIS account chosen at pairing.
    A different active Avito profile becomes a sticky identity_mismatch block.
    Only a later successful probe of the originally paired account clears it.
    """
    from app.models.account import Account
    from app.api.avito import get_avito_token, _extract_token
    import httpx

    def norm(v: str) -> str:
        return " ".join(str(v or "").lower().replace("ё", "е").split())

    requested_account_id = str(requested_account_id or "").strip()
    actual_name = " ".join(str(actual_name or "").split()).strip()[:120]
    if not requested_account_id or not actual_name:
        return {"status": "inconclusive", "reason": "missing_identity", "actual_name": actual_name}

    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == requested_account_id).first()
    finally:
        db.close()
    if not acc:
        return {"status": "inconclusive", "reason": "account_not_found", "actual_name": actual_name}

    expected_name = ""
    expected_avito_user_id = str(getattr(acc, "avito_user_id", "") or "")
    try:
        # BROWSER_GATEWAY_IDENTITY_SHARED_THROTTLE_V1: identity verification is
        # read-only but still consumes Avito quota. Honor tenant-wide Retry-After
        # before token/provider I/O and publish a local 429 for sibling stages.
        from app.services.avito_account_throttle import account_throttle_remaining as _gw_throttle_remaining, record_account_throttle as _gw_record_throttle
        if _gw_throttle_remaining(requested_account_id) > 0:
            return {"status": "inconclusive", "reason": "avito_account_throttled", "actual_name": actual_name}
        token_obj = get_avito_token(requested_account_id)
        token = _extract_token(token_obj)
        if token and _gw_throttle_remaining(requested_account_id) <= 0:
            r = httpx.get("https://api.avito.ru/core/v1/accounts/self", headers={"Authorization": f"Bearer {token}"}, timeout=8)
            if r.status_code == 429:
                raw=str(r.headers.get("Retry-After") or "").strip()
                retry=30
                if raw:
                    try:
                        retry=max(0,int(float(raw)))
                    except Exception:
                        try:
                            from email.utils import parsedate_to_datetime as _parse_gw_ra
                            from datetime import datetime as _gw_dt, timezone as _gw_tz
                            _when=_parse_gw_ra(raw)
                            if _when.tzinfo is None:
                                _when=_when.replace(tzinfo=_gw_tz.utc)
                            retry=max(0,int((_when.astimezone(_gw_tz.utc)-_gw_dt.now(_gw_tz.utc)).total_seconds()))
                        except Exception:
                            retry=30
                _gw_record_throttle(requested_account_id,retry,source="browser_gateway_identity_429")
                return {"status": "inconclusive", "reason": "avito_account_throttled", "actual_name": actual_name}
            if r.status_code == 200:
                me = r.json() or {}
                expected_name = str(me.get("name") or "").strip()
                expected_avito_user_id = str(me.get("id") or expected_avito_user_id or "")
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1575)
    if not expected_name:
        return {"status": "inconclusive", "reason": "authoritative_identity_unavailable", "actual_name": actual_name}

    if not gateway_id:
        state = _account_gateway_state(requested_account_id)
        gateway_id = str(((state.get("gateway") or {}).get("gateway_id") or ""))
    if not gateway_id:
        return {"status": "inconclusive", "reason": "gateway_not_found", "actual_name": actual_name, "expected_name": expected_name}

    key = f"gateway:{gateway_id}"
    gw = _db_get(SYSTEM_ACCOUNT, key) or {}
    if not gw or str(gw.get("account_id") or "") != requested_account_id:
        return {"status": "inconclusive", "reason": "gateway_scope_changed", "actual_name": actual_name, "expected_name": expected_name}

    verified = norm(actual_name) == norm(expected_name)
    gw["actual_profile_name"] = actual_name
    gw["expected_avito_user_id"] = expected_avito_user_id or gw.get("expected_avito_user_id")
    gw["identity_verified"] = bool(verified)
    gw["identity_mismatch"] = not verified
    gw["identity_checked_at"] = _iso()
    gw["state"] = "idle" if verified else "identity_mismatch"
    token = str(gw.get("token") or "")
    _db_put(SYSTEM_ACCOUNT, key, gw)
    if token:
        token_rec = _db_get(SYSTEM_ACCOUNT, f"token:{token}") or dict(gw)
        token_rec.update({
            "actual_profile_name": actual_name,
            "expected_avito_user_id": gw.get("expected_avito_user_id"),
            "identity_verified": bool(verified),
            "identity_mismatch": not verified,
            "identity_checked_at": gw["identity_checked_at"],
            "state": gw["state"],
        })
        _db_put(SYSTEM_ACCOUNT, f"token:{token}", token_rec)
    _session_vault_put(requested_account_id, gw, gw["state"])
    _append_execution_journal(requested_account_id, "browser_identity_verified" if verified else "browser_identity_mismatch", {
        "gateway_id": gateway_id,
        "expected_name": expected_name,
        "actual_name": actual_name,
        "profile_instance_id": gw.get("profile_instance_id"),
    })
    return {
        "status": "verified" if verified else "mismatch",
        "account_id": requested_account_id,
        "gateway_id": gateway_id,
        "expected_name": expected_name,
        "actual_name": actual_name,
        "expected_avito_user_id": expected_avito_user_id,
        "identity_verified": bool(verified),
        "identity_mismatch": not verified,
    }


# BORIS_AVITO_IDENTITY_WATCHDOG_V2
def _extract_avito_profile_name(text_value: str) -> str:
    """Extract the active Avito business/profile name from profile/pro/items text."""
    import re as _re
    text_value = str(text_value or "")
    for pattern in [
        # Current Avito Pro may flatten the header as
        # "Сообщенияtiming2 HAG Стеклострой4.944 отзываКошелёк".
        # The `timing` anchor is Pro-specific and avoids the top navigation
        # "Сообщения2КорзинаКошелёк" false positive. Rating/reviews are stripped
        # from the captured candidate below.
        r"Сообщенияtiming\d*\s+(.{3,160}?)Кошелёк",
        r"Сообщения(?:timing)?\d*\s+(.{3,120}?)(?:\d+(?:,\d+)?\s*·\s*\d+\s*отзыв|0нет отзывов|Реквизиты проверены|Кошелёк)",
        r"Главное\s+Мои объявления\s+Выйти из профиля.*?Сообщения(?:timing)?\d*\s+(.{3,120}?)(?:\d+(?:,\d+)?\s*·\s*\d+\s*отзыв|0нет отзывов|Кошелёк)",
    ]:
        m = _re.search(pattern, text_value, _re.I | _re.S)
        if m:
            candidate = " ".join(m.group(1).split()).strip(" -·")
            # Avito sometimes concatenates the profile name, rating and review
            # count without spaces, e.g. "Антон4.537 отзывов". Rating text is not
            # part of the account identity and must not create a false mismatch.
            candidate = _re.sub(r"\d(?:[.,]\d{1,2})\s*[· ]*\s*\d+\s*отзыв\w*.*$", "", candidate, flags=_re.I).strip(" -·")
            candidate = _re.sub(r"\s+\d+\s*отзыв\w*.*$", "", candidate, flags=_re.I).strip(" -·")
            return candidate[:120]
    return ""


def _maybe_queue_identity_watchdog(rec: dict[str, Any], interval_seconds: int = 180) -> bool:
    """Queue a cheap READ-only identity probe for the currently bound browser.

    It runs only while the gateway is otherwise idle. The extension needs no new
    capability: 2.1.4 already supports open_url + read_page.
    """
    account_id = str(rec.get("account_id") or "")
    gateway_id = str(rec.get("gateway_id") or "")
    if not account_id or not gateway_id:
        return False
    marker = _db_get(SYSTEM_ACCOUNT, f"identity_watchdog:{gateway_id}") or {}
    try:
        last = datetime.fromisoformat(str(marker.get("queued_at") or marker.get("completed_at") or ""))
        if (_now() - last).total_seconds() < interval_seconds:
            return False
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1672)
    job_id = "browser_" + secrets.token_hex(7)
    job = {
        "job_id": job_id, "kind": "browser", "status": "waiting_gateway",
        "created_at": _iso(), "updated_at": _iso(), "account_id": account_id,
        "domain": "avito.ru", "capability": "read_page", "risk_level": "READ", "mode": "read",
        "steps": [
            {"action":"open_url","url":"https://www.avito.ru/profile/pro/items","selector":None,"text":None,"value":None,"timeout_ms":25000,"redact_selectors":[]},
            {"action":"read_page","url":None,"selector":None,"text":None,"value":None,"timeout_ms":15000,"redact_selectors":[]},
        ],
        "gateway_id": gateway_id, "confirmed": False,
        "metadata": {"purpose":"auto_identity_watchdog"}, "results": [], "error": None,
        "privacy": {"guard": True, "never_read_passwords": True},
    }
    _db_put(account_id, f"browser_job:{job_id}", job)
    _db_put(SYSTEM_ACCOUNT, f"identity_watchdog:{gateway_id}", {"job_id":job_id,"account_id":account_id,"queued_at":_iso()})
    return True


def _process_identity_watchdog(job: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any] | None:
    if str((job.get("metadata") or {}).get("purpose") or "") != "auto_identity_watchdog":
        return None
    page = {}
    for row in job.get("results") or []:
        result = row.get("result") or {}
        if isinstance(result, dict) and result.get("text"):
            page = result
    actual_name = _extract_avito_profile_name(str(page.get("text") or ""))
    outcome: dict[str, Any] = {"status":"no_profile_name","actual_name":actual_name}
    if actual_name:
        outcome = _rebind_gateway_by_profile_name(str(job.get("account_id") or ""), actual_name, str(rec.get("gateway_id") or ""))
    _db_put(SYSTEM_ACCOUNT, f"identity_watchdog:{rec.get('gateway_id')}", {
        "job_id": job.get("job_id"), "account_id": job.get("account_id"), "completed_at": _iso(),
        "actual_name": actual_name, "outcome": outcome,
    })
    return outcome
