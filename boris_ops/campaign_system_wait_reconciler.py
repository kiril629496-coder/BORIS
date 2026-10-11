#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path("/root/BORIS")
CURRENT=(ROOT/"releases/backend/current/backend").resolve()
if str(CURRENT) not in sys.path:
    sys.path.insert(0,str(CURRENT))

from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob
from sqlalchemy import text

ALLOWED_STAGES={"media_source_wait","media_retry","billing_pending"}
ALLOWED_KINDS={"campaign_prepare","campaign_enhance","campaign_banners"}
LOCK_KEY="boris_campaign_system_wait_reconciler_v1"

def reconcile(*, dry_run: bool=False, limit: int=100):
    db=SessionLocal()
    try:
        locked=bool(db.execute(text("select pg_try_advisory_lock(hashtext(:k))"),{"k":LOCK_KEY}).scalar())
        if not locked:
            return {"status":"busy","changed":0,"jobs":[]}
        now=datetime.now(timezone.utc)
        rows=(db.query(BackgroundJob)
              .filter(
                  BackgroundJob.kind.in_(tuple(ALLOWED_KINDS)),
                  BackgroundJob.status=="awaiting_confirmation",
                  BackgroundJob.scheduled_at.isnot(None),
                  BackgroundJob.scheduled_at<=now,
              )
              .order_by(BackgroundJob.id)
              .limit(max(1,min(int(limit or 100),500)))
              .all())
        changed=[]
        skipped=[]
        for job in rows:
            result=dict(job.result_json or {})
            stage=str(result.get("stage") or "")
            retryable=bool(result.get("retryable"))
            if (not retryable) or stage not in ALLOWED_STAGES:
                skipped.append({"id":job.id,"stage":stage,"retryable":retryable})
                continue
            changed.append({"id":job.id,"kind":job.kind,"account_id":job.account_id,"stage":stage})
            if dry_run:
                continue
            job.status="queued"
            job.result_json=None
            job.error_text=None
            job.started_at=None
            job.finished_at=None
            job.scheduled_at=None
            payload=dict(job.payload_json or {})
            payload["stage"]="queued"
            payload["_system_wait_resumed_at"]=now.isoformat()
            payload["_system_wait_resumed_stage"]=stage
            job.payload_json=payload
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return {
            "status":"dry_run" if dry_run else "ok",
            "changed":len(changed),
            "jobs":changed,
            "skipped":skipped,
            "current_backend":CURRENT.parent.name,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        try:
            db.execute(text("select pg_advisory_unlock(hashtext(:k))"),{"k":LOCK_KEY})
            db.commit()
        except Exception:
            db.rollback()
        db.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--quiet",action="store_true")
    ap.add_argument("--limit",type=int,default=100)
    args=ap.parse_args()
    result=reconcile(dry_run=args.dry_run,limit=args.limit)
    if (not args.quiet) or int(result.get("changed") or 0)>0 or str(result.get("status") or "") not in {"ok","dry_run"}:
        print(json.dumps(result,ensure_ascii=False,default=str))
