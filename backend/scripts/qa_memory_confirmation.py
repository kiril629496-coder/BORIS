#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.api.client_memory import _confirm, _row, confirmed_context


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    db = SessionLocal()
    try:
        account_id = db.execute(text("""
            SELECT account_id FROM accounts
            WHERE account_id IS NOT NULL AND account_id <> ''
            ORDER BY id LIMIT 1
        """)).scalar()
        check(bool(account_id), "no account for memory QA")
        marker = "BORIS_MEMORY_QA_CONFIRMATION_MARKER"
        fact_hash = hashlib.md5((str(account_id) + marker).encode()).hexdigest()
        tx = db.begin_nested()
        try:
            fid = db.execute(text("""
                INSERT INTO client_facts
                  (account_id,category,name,value,source_type,source_ref,confidence,status,fact_hash,snippet,scope)
                VALUES
                  (:a,'company','qa_memory_confirmation',:v,'client','qa_memory_confirmation',60,'draft',:h,'rollback qa','account')
                RETURNING id
            """), {"a": account_id, "v": marker, "h": fact_hash}).scalar_one()

            before = confirmed_context(db, account_id, include_rules=False)
            check(marker not in before, "draft fact leaked into confirmed context")

            row_before = db.execute(text("SELECT * FROM client_facts WHERE id=:i"), {"i": fid}).first()
            check(bool(_row(row_before).get("requires_confirmation")), "draft fact not marked requires_confirmation")

            _confirm(db, account_id, int(fid), "qa")
            row_after = db.execute(text("SELECT * FROM client_facts WHERE id=:i"), {"i": fid}).first()
            projected = _row(row_after)
            check(projected.get("status") == "confirmed", "confirm did not persist confirmed status")
            check(projected.get("confidence") == 100, "confirm did not promote confidence to 100")
            check(projected.get("requires_confirmation") is False, "confirmed fact still requires confirmation")

            after = confirmed_context(db, account_id, include_rules=False)
            check(marker in after, "confirmed fact absent from content/dialog context")
        finally:
            tx.rollback()

        leaked = int(db.execute(text("SELECT count(*) FROM client_facts WHERE fact_hash=:h"), {"h": fact_hash}).scalar() or 0)
        check(leaked == 0, "rollback QA left a fact in production")
        print("MEMORY_CONFIRMATION_E2E=PASS", {"account_scoped": True, "draft_hidden": True, "confirmed_visible": True, "rollback_clean": True})
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
