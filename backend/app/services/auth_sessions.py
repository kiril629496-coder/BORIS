# -*- coding: utf-8 -*-
"""Revocable user sessions for BORIS authentication."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


SESSION_TTL_HOURS = max(1, min(int(os.getenv("BORIS_AUTH_SESSION_TTL_HOURS", "168")), 24 * 30))


def _secret() -> str:
    secret = os.getenv("JWT_SECRET") or os.getenv("FERNET_KEY")
    if not secret:
        raise RuntimeError("MESSENGER_SESSION_SECRET_MISSING")
    return secret


def _digest(prefix: str, value: str) -> str:
    return hmac.new(_secret().encode("utf-8"), (prefix + str(value or "")).encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session(db, user_id: int, security_epoch: int, *, user_agent: str = "", ip: str = "", device_label: str = "") -> tuple[str, datetime]:
    session_id = secrets.token_urlsafe(32)[:64]
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(text("""
        INSERT INTO auth_user_sessions(
            session_id,user_id,security_epoch,user_agent,ip_hash,device_label,expires_at
        ) VALUES(:sid,:u,:e,:ua,:ip,:label,:x)
    """), {
        "sid": session_id,
        "u": int(user_id),
        "e": int(security_epoch or 0),
        "ua": str(user_agent or "")[:500] or None,
        "ip": _digest("ip:", ip) if ip else None,
        "label": str(device_label or "")[:160] or None,
        "x": expires_at,
    })
    return session_id, expires_at


def validate_session(db, *, user_id: int, session_id: str, token_epoch: int, current_epoch: int) -> bool:
    if int(token_epoch or 0) != int(current_epoch or 0):
        return False
    row = db.execute(text("""
        SELECT session_id,last_seen_at FROM auth_user_sessions
        WHERE session_id=:sid AND user_id=:u AND security_epoch=:e
          AND revoked_at IS NULL AND expires_at>now()
        LIMIT 1
    """), {"sid": str(session_id), "u": int(user_id), "e": int(current_epoch or 0)}).mappings().first()
    if not row:
        return False
    last_seen = row.get("last_seen_at")
    if not last_seen or last_seen < datetime.now(timezone.utc) - timedelta(minutes=5):
        db.execute(text("UPDATE auth_user_sessions SET last_seen_at=now() WHERE session_id=:sid"), {"sid": str(session_id)})
        db.commit()
    return True


def list_sessions(db, user_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT session_id,user_agent,device_label,created_at,last_seen_at,expires_at,revoked_at
        FROM auth_user_sessions
        WHERE user_id=:u
        ORDER BY COALESCE(last_seen_at,created_at) DESC
        LIMIT 100
    """), {"u": int(user_id)}).mappings().all()
    return [
        {
            "session_id": row["session_id"],
            "user_agent": row["user_agent"],
            "device_label": row["device_label"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "last_seen_at": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            "revoked": row["revoked_at"] is not None,
        }
        for row in rows
    ]


def revoke_session(db, user_id: int, session_id: str, reason: str = "user_revoked") -> bool:
    result = db.execute(text("""
        UPDATE auth_user_sessions
        SET revoked_at=COALESCE(revoked_at,now()), revoked_reason=:reason
        WHERE session_id=:sid AND user_id=:u
    """), {"sid": str(session_id), "u": int(user_id), "reason": str(reason)[:160]})
    return bool(result.rowcount)


def revoke_all_and_advance_epoch(db, user_id: int, reason: str = "revoke_all") -> int:
    row = db.execute(text("""
        UPDATE users
        SET security_epoch=COALESCE(security_epoch,0)+1
        WHERE id=:u
        RETURNING security_epoch
    """), {"u": int(user_id)}).first()
    if not row:
        raise ValueError("user_not_found")
    new_epoch = int(row[0])
    db.execute(text("""
        UPDATE auth_user_sessions
        SET revoked_at=COALESCE(revoked_at,now()), revoked_reason=:reason
        WHERE user_id=:u AND revoked_at IS NULL
    """), {"u": int(user_id), "reason": str(reason)[:160]})
    return new_epoch
