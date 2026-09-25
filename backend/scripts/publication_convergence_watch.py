#!/usr/bin/env python3
import json, time, traceback
from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.campaign_item import CampaignItem
from app.models.background_job import BackgroundJob
from app.services.campaign_identity import ensure_feed_identity
from app.api.avito import _autoload_upload_snapshot
from app.services.reliability import queue_health, storage_health

TARGETS = [
    (226, "antonybatono_78732", "Антон"),
    (225, "mbroker_27859", "Мария"),
    (144, "planetazayavki_65985", "PLANETA"),
]
OUT = "/root/BORIS/backend/logs/publication_convergence_watch.log"


def line(msg):
    stamp = datetime.now(timezone.utc).isoformat()
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {msg}\n")
    print(f"[{stamp}] {msg}", flush=True)


def check_one(campaign_id, account_id, label):
    db = SessionLocal()
    try:
        rows = (db.query(CampaignItem)
                .filter(CampaignItem.campaign_id == campaign_id,
                        CampaignItem.account_id == account_id,
                        CampaignItem.status != "superseded")
                .order_by(CampaignItem.id).all())
        expected = [str(ensure_feed_identity(r)) for r in rows]
        jobs = (db.query(BackgroundJob)
                .filter(BackgroundJob.kind == "campaign_deliver",
                        BackgroundJob.account_id == account_id)
                .order_by(BackgroundJob.id.desc()).limit(2).all())
        job_view = []
        for j in jobs:
            p = j.payload_json or {}
            job_view.append({"id": j.id, "status": j.status, "stage": p.get("stage"),
                             "watch": p.get("watch_count"),
                             "scheduled_at": j.scheduled_at.isoformat() if j.scheduled_at else None})
        db.commit()
    finally:
        db.close()
    snap = _autoload_upload_snapshot(account_id, None, expected_ad_ids=expected)
    scope = snap.get("scope") or {}
    result = {
        "label": label, "campaign_id": campaign_id, "account_id": account_id,
        "campaign_items": len(expected), "upload_id": snap.get("upload_id"),
        "upload_status": snap.get("avito_status"), "scope": scope,
        "jobs": job_view,
    }
    line(json.dumps(result, ensure_ascii=False, default=str))
    return result


def main():
    line("WATCH_START iterations=7 cadence=480s production")
    for i in range(7):
        line(f"ITERATION {i+1}/7")
        for args in TARGETS:
            try:
                check_one(*args)
            except Exception as e:
                line(f"ERROR target={args[2]} type={type(e).__name__} error={e}")
                line(traceback.format_exc().replace("\n", " | "))
        db = SessionLocal()
        try:
            line("HEALTH " + json.dumps({"queue": queue_health(db), "storage": storage_health("/")}, ensure_ascii=False, default=str))
        finally:
            db.close()
        if i < 6:
            time.sleep(480)
    line("WATCH_DONE")

if __name__ == "__main__":
    main()
