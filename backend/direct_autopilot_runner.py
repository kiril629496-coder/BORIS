#!/usr/bin/env python3
"""Periodic account-scoped Yandex Direct sync + safe decision cycle."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from app.db.session import SessionLocal
from app.director.models import DirectConnection
from sqlalchemy import text
from app.director.core import DirectError, sync_entities, sync_report, sync_search_queries, sync_marketing_controls, sync_dimension_report, audit_and_plan, plan_advanced_actions

def main():
    db=SessionLocal(); ok=0; skipped=0; failed=0
    try:
        ids=[r[0] for r in db.execute(text("""
            SELECT d.account_id FROM direct_connections d
             WHERE d.status='CONNECTED'
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=d.account_id AND r.blocked=true
                      AND r.module IN ('actions','background','direct','marketing')
               )
             ORDER BY d.account_id
        """)).fetchall()]
    finally: db.close()
    for account_id in ids:
        db=SessionLocal(); locked=False
        try:
            locked=bool(db.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"),{"k":f"boris-director:{account_id}"}).scalar())
            if not locked:
                skipped+=1; print(f"AI_DIRECTOR account={account_id} status=SKIP lock=busy", flush=True); continue
            sync_entities(db,account_id)
            sync_marketing_controls(db,account_id)
            try: sync_dimension_report(db,account_id,30)
            except DirectError as e:
                if e.code!="REPORT_PENDING": raise
            try: sync_search_queries(db,account_id,14)
            except DirectError as e:
                if e.code!="REPORT_PENDING": raise
            try: sync_report(db,account_id,30)
            except DirectError as e:
                if e.code!="REPORT_PENDING": raise
            audit_and_plan(db,account_id)
            plan_advanced_actions(db,account_id)
            ok+=1
        except DirectError as e:
            failed+=1; print(f"AI_DIRECTOR account={account_id} status=FAIL code={e.code}", flush=True)
            db.rollback()
        except Exception as e:
            failed+=1; print(f"AI_DIRECTOR account={account_id} status=FAIL type={type(e).__name__}", flush=True); db.rollback()
        finally:
            if locked:
                try: db.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"),{"k":f"boris-director:{account_id}"})
                except Exception: pass
            db.close()
    print(f"AI_DIRECTOR_RUN finished={dt.datetime.utcnow().isoformat()} connected={len(ids)} ok={ok} failed={failed} skipped={skipped}", flush=True)
    return 1 if failed else 0
if __name__=='__main__': raise SystemExit(main())
