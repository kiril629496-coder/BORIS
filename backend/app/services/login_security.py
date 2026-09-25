# -*- coding: utf-8 -*-
"""Primary client admin email OTP for new devices.

Platform owner remains highest privilege. A user who owns one or more accounts
(accounts.owner_user_id=user.id) is the primary client admin for those tenants.
Only a new/untrusted browser device requires the additional email code.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from app.db.session import SessionLocal

OTP_TTL_MINUTES = 10
TRUST_DAYS = 180
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


def _secret() -> str:
    return os.getenv("JWT_SECRET") or os.getenv("FERNET_KEY") or "BORIS_LOGIN_OTP_FALLBACK"


def _digest(value: str) -> str:
    return hmac.new(_secret().encode(), value.encode(), hashlib.sha256).hexdigest()


def _device_hash(device_key: str) -> str:
    key = str(device_key or "").strip()
    return _digest("device:" + key) if key else ""


def ensure_schema() -> None:
    db = SessionLocal()
    try:
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.commit()
    finally:
        db.close()


def is_primary_client_admin(user_id: int) -> bool:
    # Primary-admin truth lives in accounts.owner_user_id; no auth DDL is needed.
    db = SessionLocal()
    try:
        return bool(db.execute(text(
            "SELECT 1 FROM accounts WHERE owner_user_id=:u LIMIT 1"
        ), {"u": int(user_id)}).first())
    finally:
        db.close()


def is_trusted(user_id: int, device_key: str) -> bool:
    ensure_schema()
    dh = _device_hash(device_key)
    if not dh:
        return False
    db = SessionLocal()
    try:
        row = db.execute(text("""
          SELECT id FROM auth_trusted_devices
          WHERE user_id=:u AND device_hash=:d AND trusted_until>now()
          LIMIT 1
        """), {"u": int(user_id), "d": dh}).first()
        if row:
            db.execute(text("UPDATE auth_trusted_devices SET last_seen_at=now() WHERE id=:i"), {"i": row[0]})
            db.commit()
        return bool(row)
    finally:
        db.close()


def issue(user_id: int, email: str, device_key: str, user_agent: str = "") -> dict:
    """Create one challenge and enqueue the six-digit code email."""
    ensure_schema()
    dh = _device_hash(device_key)
    if not dh:
        return {"status": "device_key_required"}

    db = SessionLocal()
    try:
        recent = db.execute(text("""
          SELECT challenge_id,created_at FROM auth_login_email_challenges
          WHERE user_id=:u AND used_at IS NULL AND expires_at>now()
          ORDER BY created_at DESC LIMIT 1
        """), {"u": int(user_id)}).mappings().first()
        if recent:
            age = (datetime.now(timezone.utc) - recent["created_at"]).total_seconds()
            if age < RESEND_COOLDOWN_SECONDS:
                return {
                    "status": "cooldown",
                    "challenge_id": str(recent["challenge_id"]),
                    "retry_after_seconds": max(1, RESEND_COOLDOWN_SECONDS - int(age)),
                }

        challenge_id = uuid.uuid4().hex
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)
        code_hash = _digest("code:" + challenge_id + ":" + code)
        db.execute(text("""
          INSERT INTO auth_login_email_challenges(
            challenge_id,user_id,email,code_hash,device_hash,expires_at,max_attempts
          ) VALUES(:c,:u,:e,:h,:d,:x,:m)
        """), {
            "c": challenge_id, "u": int(user_id), "e": str(email),
            "h": code_hash, "d": dh, "x": expires, "m": MAX_ATTEMPTS,
        })
        db.commit()
    finally:
        db.close()

    from app.services.email_queue import enqueue_email
    subject = "Подтвердите вход в BORIS AI"
    body = (
        f"Код входа в BORIS: {code}\n\n"
        f"Он действует {OTP_TTL_MINUTES} минут.\n"
        "Если вы не входили в BORIS на новом устройстве, никому не сообщайте этот код."
    )
    result = enqueue_email(
        email, subject, body,
        source="auth_login_otp",
        idempotency_key="auth-login-otp:" + challenge_id,
        expires_at=expires,
        send_now=True,
        max_attempts=5,
    )
    if str(result.get("status") or "") in {"error","dead"}:
        fdb = SessionLocal()
        try:
            fdb.execute(text("UPDATE auth_login_email_challenges SET used_at=now() WHERE challenge_id=:c"), {"c": challenge_id})
            fdb.commit()
        finally:
            fdb.close()
        return {"status": "email_failed", "challenge_id": challenge_id}
    return {
        "status": "otp_required",
        "challenge_id": challenge_id,
        "expires_in_seconds": OTP_TTL_MINUTES * 60,
    }


def verify(challenge_id: str, code: str, device_key: str, user_agent: str = "") -> dict:
    ensure_schema()
    cid = str(challenge_id or "").strip()
    code = str(code or "").strip()
    dh = _device_hash(device_key)
    if not cid or not code or not dh:
        return {"status": "invalid"}

    db = SessionLocal()
    try:
        row = db.execute(text("""
          SELECT * FROM auth_login_email_challenges
          WHERE challenge_id=:c
          FOR UPDATE
        """), {"c": cid}).mappings().first()
        if not row:
            db.rollback()
            return {"status": "invalid"}
        if row["used_at"] is not None:
            db.rollback()
            return {"status": "used"}
        if row["expires_at"] <= datetime.now(timezone.utc):
            db.rollback()
            return {"status": "expired"}
        if int(row["attempts"] or 0) >= int(row["max_attempts"] or MAX_ATTEMPTS):
            db.rollback()
            return {"status": "locked"}
        if not hmac.compare_digest(str(row["device_hash"]), dh):
            db.execute(text("UPDATE auth_login_email_challenges SET attempts=attempts+1 WHERE challenge_id=:c"), {"c": cid})
            db.commit()
            return {"status": "invalid_device"}

        expected = _digest("code:" + cid + ":" + code)
        if not hmac.compare_digest(expected, str(row["code_hash"])):
            attempts = int(row["attempts"] or 0) + 1
            db.execute(text("UPDATE auth_login_email_challenges SET attempts=:n WHERE challenge_id=:c"), {"n": attempts, "c": cid})
            db.commit()
            return {"status": "locked" if attempts >= int(row["max_attempts"] or MAX_ATTEMPTS) else "invalid_code",
                    "attempts_left": max(0, int(row["max_attempts"] or MAX_ATTEMPTS) - attempts)}

        trusted_until = datetime.now(timezone.utc) + timedelta(days=TRUST_DAYS)
        db.execute(text("UPDATE auth_login_email_challenges SET used_at=now() WHERE challenge_id=:c"), {"c": cid})
        db.execute(text("""
          INSERT INTO auth_trusted_devices(user_id,device_hash,user_agent,trusted_until,last_seen_at)
          VALUES(:u,:d,:ua,:x,now())
          ON CONFLICT(user_id,device_hash) DO UPDATE SET
            user_agent=excluded.user_agent,trusted_until=excluded.trusted_until,last_seen_at=now()
        """), {
            "u": int(row["user_id"]), "d": dh, "ua": str(user_agent or "")[:500],
            "x": trusted_until,
        })
        db.commit()
        return {"status": "ok", "user_id": int(row["user_id"]), "trusted_until": trusted_until.isoformat()}
    finally:
        db.close()


def mask_email(email: str) -> str:
    raw = str(email or "")
    if "@" not in raw:
        return "***"
    local, domain = raw.split("@", 1)
    if len(local) <= 2:
        left = local[:1] + "*"
    else:
        left = local[:2] + "*" * max(1, len(local)-2)
    return left + "@" + domain
