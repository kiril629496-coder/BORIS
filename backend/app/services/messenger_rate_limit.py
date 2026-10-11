# -*- coding: utf-8 -*-
"""Small PostgreSQL-backed abuse limiter for authenticated Messenger actions."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text

from app.db.session import SessionLocal


def _lock_id(kind: str, rate_key: str) -> int:
    raw = hashlib.sha256(f"{kind}:{rate_key}".encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=False)
    return value if value < 2**63 else value - 2**64


def enforce(kind: str, rate_key: str, *, limit: int, window_seconds: int, message: str = "Слишком много действий. Повторите позже.") -> None:
    key = str(rate_key or "")[:240]
    if not key:
        raise HTTPException(status_code=400, detail="rate_key required")
    now = datetime.now(timezone.utc)
    since = now - timedelta(seconds=max(1, int(window_seconds)))
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_id(kind, key)})
        count = int(db.execute(text("""
            SELECT count(*) FROM auth_rate_events
            WHERE kind=:kind AND rate_key=:key AND created_at>=:since
        """), {"kind": kind, "key": key, "since": since}).scalar() or 0)
        if count >= int(limit):
            db.rollback()
            raise HTTPException(status_code=429, detail=message)
        db.execute(text("""
            INSERT INTO auth_rate_events(kind,rate_key,ip,created_at)
            VALUES(:kind,:key,NULL,:now)
        """), {"kind": kind, "key": key, "now": now})
        db.commit()
    finally:
        db.close()
