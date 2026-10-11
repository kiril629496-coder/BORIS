"""Bounded one-shot cleanup for stale BORIS Messenger calls."""
from __future__ import annotations
from sqlalchemy import text
from app.db.session import SessionLocal, engine
from app.services import messenger_calls as calls

LOCK_ID=8264817337

def run_once() -> dict:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:id)"),{"id":LOCK_ID}).scalar():
            return {"status":"busy","expired":0}
        try:
            db=SessionLocal()
            try:
                expired=calls.expire_all(db)
                # Signal payloads are already scrubbed immediately at terminal;
                # this is a hard upper bound for abandoned historical rows.
                deleted=int(db.execute(text(
                    "WITH gone AS (DELETE FROM internal_call_events "
                    "WHERE created_at<now()-interval '1 day' RETURNING 1) "
                    "SELECT count(*) FROM gone"
                )).scalar() or 0)
                db.commit()
                return {"status":"ok","expired":int(expired),"old_events_deleted":deleted}
            finally:
                db.close()
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:id)"),{"id":LOCK_ID})

if __name__=="__main__":
    import json
    print(json.dumps(run_once(),ensure_ascii=False))
