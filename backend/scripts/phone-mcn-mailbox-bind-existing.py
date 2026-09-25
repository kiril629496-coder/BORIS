#!/usr/bin/env python3
"""Safely pin one already-connected physical mailbox as the MCN source.

This does not copy, decrypt or print mailbox credentials.  It proves the
selected mailbox through read-only Sent-folder evidence to mcn.ru, then stores
only mailbox id + normalized email SHA-256 + Mail.ru IMAP transport metadata.
Guardian validates all of those on every use and fails closed on drift.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal

ACCOUNT_ID = "__mcn_mailbox_watch__"
BINDING_KEY = "mcn_provider_mailbox_binding_v1"
DISCOVERY_PATH = ROOT / "scripts" / "phone-company-card-mailbox-discovery.py"


def _load_discovery():
    spec = importlib.util.spec_from_file_location("mcn_card_discovery_for_binding", DISCOVERY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _identity_sha(value: str) -> str:
    return hashlib.sha256(str(value or "").strip().lower().encode("utf-8")).hexdigest()


def inspect(mailbox_id: int) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT id,account_id,email_address,status,imap_host,imap_port,imap_ssl
            FROM client_mailboxes WHERE id=:id
        """), {"id": int(mailbox_id)}).mappings().first()
    finally:
        db.close()
    if not row:
        return {"status": "blocked", "reason": "mailbox_not_found", "mailbox_id": int(mailbox_id)}
    row = dict(row)
    email_value = str(row.get("email_address") or "").strip().lower()
    domain = email_value.rsplit("@", 1)[-1] if "@" in email_value else ""
    if str(row.get("status") or "").lower() != "active":
        return {"status": "blocked", "reason": "mailbox_not_active", "mailbox_id": int(mailbox_id)}
    if domain != "mail.ru" or str(row.get("imap_host") or "").strip().lower() != "imap.mail.ru":
        return {"status": "blocked", "reason": "mailbox_not_mailru", "mailbox_id": int(mailbox_id)}
    if int(row.get("imap_port") or 0) != 993 or not bool(row.get("imap_ssl")):
        return {"status": "blocked", "reason": "mailbox_transport_not_strict_tls", "mailbox_id": int(mailbox_id)}

    discovery = _load_discovery().discover(int(mailbox_id), "mcn.ru", 1500)
    if discovery.get("status") != "ok":
        return {
            "status": "blocked",
            "reason": str(discovery.get("reason") or "mcn_sent_proof_unavailable")[:160],
            "mailbox_id": int(mailbox_id),
        }
    items = discovery.get("items") if isinstance(discovery.get("items"), list) else []
    current_cards = [x for x in items if isinstance(x, dict) and x.get("current_card_match") is True]
    if not items or not current_cards:
        return {
            "status": "blocked",
            "reason": "mcn_sent_proof_insufficient",
            "mailbox_id": int(mailbox_id),
            "matched_messages": len(items),
            "current_card_matches": len(current_cards),
        }
    return {
        "status": "proved",
        "mailbox_id": int(mailbox_id),
        "binding": {
            "version": 1,
            "mailbox_id": int(mailbox_id),
            "email_sha256": _identity_sha(email_value),
            "imap_host": "imap.mail.ru",
            "imap_port": 993,
            "imap_ssl": True,
            "proof": {
                "provider_domain": "mcn.ru",
                "matched_messages": len(items),
                "current_card_matches": len(current_cards),
            },
        },
        "truth": "read-only Sent proof; no mailbox credential or address is returned",
    }


def apply_binding(proof: dict) -> dict:
    binding = dict(proof.get("binding") or {})
    if proof.get("status") != "proved" or not binding:
        return {"status": "blocked", "reason": "proof_required"}
    binding["bound_at"] = datetime.now(timezone.utc).isoformat()
    raw = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": ACCOUNT_ID + ":" + BINDING_KEY})
        row = db.execute(text("""
            SELECT id,value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1 FOR UPDATE
        """), {"a": ACCOUNT_ID, "k": BINDING_KEY}).first()
        if row and row[1]:
            try:
                existing = json.loads(str(row[1]))
            except Exception:
                existing = {}
            same = (
                int(existing.get("mailbox_id") or 0) == int(binding["mailbox_id"])
                and str(existing.get("email_sha256") or "") == str(binding["email_sha256"])
                and str(existing.get("imap_host") or "") == "imap.mail.ru"
                and int(existing.get("imap_port") or 0) == 993
                and bool(existing.get("imap_ssl")) is True
            )
            if same:
                db.commit()
                return {"status": "already_bound", "mailbox_id": int(binding["mailbox_id"])}
            db.rollback()
            return {"status": "blocked", "reason": "existing_binding_mismatch"}
        db.execute(text("INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"), {
            "a": ACCOUNT_ID, "k": BINDING_KEY, "v": raw,
        })
        db.commit()
        return {
            "status": "bound",
            "mailbox_id": int(binding["mailbox_id"]),
            "proof": binding.get("proof") or {},
            "truth": "only mailbox id, identity hash and transport metadata were persisted",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mailbox-id", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    proof = inspect(args.mailbox_id)
    if proof.get("status") != "proved":
        print(json.dumps(proof, ensure_ascii=False, default=str))
        return 2
    if not args.apply:
        safe = {k: v for k, v in proof.items() if k != "binding"}
        safe["status"] = "would_bind"
        print(json.dumps(safe, ensure_ascii=False, default=str))
        return 0
    out = apply_binding(proof)
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0 if out.get("status") in {"bound", "already_bound"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
