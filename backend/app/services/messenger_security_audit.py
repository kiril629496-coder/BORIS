# -*- coding: utf-8 -*-
"""Append-only security/audit evidence for BORIS Messenger."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text


def append_security_audit(
    db,
    *,
    action: str,
    user_id: int | None = None,
    account_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    result: str = "ok",
    details: dict[str, Any] | None = None,
) -> int:
    payload = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
    row = db.execute(text("""
        INSERT INTO messenger_security_audit(
            user_id,account_id,action,entity_type,entity_id,session_id,request_id,result,details
        ) VALUES(
            :u,:a,:action,:etype,:eid,:sid,:rid,:result,CAST(:details AS jsonb)
        ) RETURNING id
    """), {
        "u": int(user_id) if user_id is not None else None,
        "a": str(account_id)[:255] if account_id else None,
        "action": str(action)[:96],
        "etype": str(entity_type)[:64] if entity_type else None,
        "eid": str(entity_id)[:255] if entity_id is not None else None,
        "sid": str(session_id)[:64] if session_id else None,
        "rid": str(request_id)[:128] if request_id else None,
        "result": str(result)[:32],
        "details": payload,
    }).first()
    return int(row[0])
