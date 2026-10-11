# -*- coding: utf-8 -*-
"""BORIS Messenger user-scoped legal authority.

The current legal text lives in immutable release files under app/legal_docs.
Acceptance is valid only for the exact (type, version, sha256) tuple.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

APP_ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT = APP_ROOT / "legal_docs"

DOCUMENTS = {
    "terms": {
        "version": "1.0",
        "title": "Пользовательское соглашение BORIS Messenger",
        "filename": "messenger_terms_v1.0.md",
        "public_url": "/messenger/legal?doc=terms",
    },
    "privacy": {
        "version": "1.0",
        "title": "Политика конфиденциальности BORIS Messenger",
        "filename": "messenger_privacy_v1.0.md",
        "public_url": "/privacy",
    },
}


def _doc_text(spec: dict[str, str]) -> str:
    return (DOC_ROOT / spec["filename"]).read_text(encoding="utf-8").strip()


def document_descriptor(document_type: str, *, include_text: bool = False) -> dict[str, Any]:
    spec = DOCUMENTS[document_type]
    body = _doc_text(spec)
    result: dict[str, Any] = {
        "document_type": document_type,
        "version": spec["version"],
        "title": spec["title"],
        "hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "public_url": spec["public_url"],
    }
    if include_text:
        result["text"] = body
    return result


def current_documents(*, include_text: bool = False) -> list[dict[str, Any]]:
    return [document_descriptor(key, include_text=include_text) for key in ("terms", "privacy")]


def acceptance_status(db, user_id: int) -> dict[str, Any]:
    required = current_documents(include_text=False)
    rows = db.execute(text("""
        SELECT document_type, document_version, document_hash, accepted_at
        FROM messenger_legal_acceptances
        WHERE user_id=:u
        ORDER BY accepted_at DESC
    """), {"u": int(user_id)}).mappings().all()
    accepted = {
        (str(row["document_type"]), str(row["document_version"]), str(row["document_hash"])): row
        for row in rows
    }
    items = []
    all_ok = True
    for doc in required:
        key = (doc["document_type"], doc["version"], doc["hash"])
        row = accepted.get(key)
        ok = row is not None
        all_ok = all_ok and ok
        items.append({
            **doc,
            "accepted": ok,
            "accepted_at": row["accepted_at"].isoformat() if row and row["accepted_at"] else None,
        })
    return {"accepted": bool(all_ok), "documents": items}


def require_acceptance(db, user_id: int) -> None:
    state = acceptance_status(db, int(user_id))
    if state["accepted"]:
        return
    raise HTTPException(
        status_code=428,
        detail={
            "code": "messenger_legal_acceptance_required",
            "message": "Примите актуальные условия BORIS Messenger, чтобы продолжить.",
            "documents": [
                {
                    "document_type": item["document_type"],
                    "version": item["version"],
                    "hash": item["hash"],
                }
                for item in state["documents"]
                if not item["accepted"]
            ],
        },
    )


def _evidence_hash(prefix: str, value: str) -> str:
    secret = os.getenv("JWT_SECRET") or os.getenv("FERNET_KEY")
    if not secret:
        raise RuntimeError("MESSENGER_LEGAL_EVIDENCE_SECRET_MISSING")
    return hmac.new(secret.encode("utf-8"), (prefix + value).encode("utf-8"), hashlib.sha256).hexdigest()


def accept_current(
    db,
    user_id: int,
    *,
    ip: str = "",
    user_agent: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    docs = current_documents(include_text=True)
    ip_hash = _evidence_hash("ip:", str(ip or "")) if ip else None
    for doc in docs:
        db.execute(text("""
            INSERT INTO messenger_legal_acceptances(
                user_id,document_type,document_version,document_hash,
                document_title,document_snapshot,ip_hash,user_agent,session_id
            ) VALUES(
                :u,:t,:v,:h,:title,:snapshot,:ip,:ua,:sid
            )
            ON CONFLICT(user_id,document_type,document_version,document_hash)
            DO NOTHING
        """), {
            "u": int(user_id),
            "t": doc["document_type"],
            "v": doc["version"],
            "h": doc["hash"],
            "title": doc["title"],
            "snapshot": doc["text"],
            "ip": ip_hash,
            "ua": str(user_agent or "")[:500] or None,
            "sid": str(session_id or "")[:64] or None,
        })
    return acceptance_status(db, int(user_id))
