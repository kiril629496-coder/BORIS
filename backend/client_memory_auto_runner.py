#!/usr/bin/env python3
"""Incremental account-scoped BORIS memory learning.

Hourly mode learns only accounts whose messenger history changed.
--full additionally refreshes listing-derived facts for every account.
All writes remain scoped by account_id.
"""
import argparse
import json
from sqlalchemy import text

from app.db.session import SessionLocal
from client_memory_runner import extract_dialogs, extract_listings, mark_conflicts

MARKER_KEY = "client_memory_auto_cursor_v1"


def _marker(db, account_id: str) -> int:
    row = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"
    ), {"a": account_id, "k": MARKER_KEY}).first()
    if not row or not row[0]:
        return 0
    try:
        raw = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        if isinstance(raw, dict):
            return int(raw.get("last_dialog_ts") or 0)
        return int(raw or 0)
    except Exception:
        try:
            return int(str(row[0]).strip())
        except Exception:
            return 0


def _save_marker(db, account_id: str, ts: int) -> None:
    value = json.dumps({"last_dialog_ts": int(ts or 0)}, ensure_ascii=False)
    row = db.execute(text(
        "SELECT id FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"
    ), {"a": account_id, "k": MARKER_KEY}).first()
    if row:
        db.execute(text("UPDATE storage SET value=:v WHERE id=:i"), {"v": value, "i": row[0]})
    else:
        db.execute(text(
            "INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"
        ), {"a": account_id, "k": MARKER_KEY, "v": value})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    learned = skipped = errors = 0
    try:
        rows = db.execute(text("""
            SELECT a.account_id, COALESCE(MAX(m.avito_created_at), 0) AS latest_ts
            FROM accounts a
            LEFT JOIN messenger_messages m ON m.account_id = a.account_id
            GROUP BY a.account_id
            ORDER BY a.account_id
        """)).all()

        for account_id, latest_ts in rows:
            account_id = str(account_id or "").strip()
            if not account_id:
                continue
            latest_ts = int(latest_ts or 0)
            previous = _marker(db, account_id)
            changed = latest_ts > previous
            if not changed and not args.full:
                skipped += 1
                continue
            try:
                listing_items = listing_facts = 0
                if args.full:
                    listing_items, listing_facts = extract_listings(db, account_id)
                dialogs, pairs, dialog_facts = extract_dialogs(db, account_id)
                conflicts = mark_conflicts(db, account_id)
                _save_marker(db, account_id, latest_ts)
                db.commit()
                learned += 1
                print(
                    "MEMORY_OK",
                    account_id,
                    "dialogs=", dialogs,
                    "pairs=", pairs,
                    "dialog_facts=", dialog_facts,
                    "listing_items=", listing_items,
                    "listing_facts=", listing_facts,
                    "conflicts=", conflicts,
                    "cursor=", latest_ts,
                )
            except Exception as exc:
                db.rollback()
                errors += 1
                print("MEMORY_ERROR", account_id, type(exc).__name__, str(exc)[:180])

        print("MEMORY_AUTO_SUMMARY", "learned=", learned, "skipped=", skipped, "errors=", errors, "full=", args.full)
        return 1 if errors else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
