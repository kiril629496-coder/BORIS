"""Bounded FFmpeg worker for BORIS Messenger video assets."""
from __future__ import annotations
import json, time, sys
from uuid import uuid4
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import messenger_video as video
from app.services import messenger_telemetry as telemetry
from app.services.messenger_video_media import normalize, VideoError

LOCK_ID=8264817319
JOB_TIMEOUT=660
LEASE_SECONDS=900
RETRY_DELAYS=(30,120,600)
MAX_ATTEMPTS=len(RETRY_DELAYS)+1

def _emit(db,row):
    if row.get("message_id"):
        video.core().emit_messenger_event(db,row["account_id"],"internal.message.updated","message",row["message_id"],
                                          {"conversation_id":row["conversation_id"],"message_type":row["kind"]})

def _requeue_abandoned(db):
    rows=db.execute(text(
        "SELECT id,payload_json FROM background_jobs WHERE kind='messenger_video_processing' AND status='running' FOR UPDATE"
    )).mappings().all()
    for job in rows:
        asset_id=str((job["payload_json"] or {}).get("asset_id",""))
        if asset_id:
            db.execute(text(
                "UPDATE internal_video_assets SET processing_status='queued',processing_worker_id=NULL,"
                "processing_lease_until=NULL WHERE id=:id AND processing_status='processing'"
            ),{"id":asset_id})
    if rows:
        db.execute(text(
            "UPDATE background_jobs SET status='queued',scheduled_at=now(),updated_at=now(),started_at=NULL "
            "WHERE kind='messenger_video_processing' AND status='running'"
        ))

def claim_one():
    db=SessionLocal()
    try:
        _requeue_abandoned(db)
        job=db.execute(text(
            "SELECT * FROM background_jobs WHERE kind='messenger_video_processing' AND status='queued' "
            "AND (scheduled_at IS NULL OR scheduled_at<=now()) ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
        )).mappings().first()
        if not job:
            db.commit(); return None
        asset_id=str((job["payload_json"] or {}).get("asset_id",""))
        row=db.execute(text(
            "SELECT v.* FROM internal_video_assets v LEFT JOIN internal_messages m ON m.id=v.message_id "
            "WHERE v.id=:id AND v.deleted_at IS NULL AND (v.message_id IS NULL OR m.deleted_at IS NULL) FOR UPDATE OF v"
        ),{"id":asset_id}).mappings().first()
        if not row:
            db.execute(text("UPDATE background_jobs SET status='cancelled',finished_at=now(),updated_at=now() WHERE id=:id"),{"id":job["id"]})
            db.commit(); return {"terminal":True}
        if row["processing_status"]=="ready":
            db.execute(text("UPDATE background_jobs SET status='completed',finished_at=now(),updated_at=now(),error_text=NULL WHERE id=:id"),{"id":job["id"]})
            db.commit(); return {"terminal":True}
        attempt=int(job["attempts"] or 0)+1
        if attempt>MAX_ATTEMPTS:
            db.execute(text("UPDATE internal_video_assets SET processing_status='failed',processing_error_code='retry_exhausted',processed_at=now() WHERE id=:id"),{"id":asset_id})
            db.execute(text("UPDATE background_jobs SET status='failed',error_text='retry_exhausted',finished_at=now(),updated_at=now() WHERE id=:id"),{"id":job["id"]})
            telemetry.record(
                db,
                "transcode_failed",
                user_id=row["uploader_user_id"],
                account_id=row["account_id"],
                entity_type="message" if row.get("message_id") else "conversation",
                entity_id=row["message_id"] or row["conversation_id"],
                details={"error_code": "retry_exhausted", "attempt": attempt, "media_kind": row["kind"]},
            )
            _emit(db,row); db.commit(); return {"terminal":True}
        worker_id=uuid4().hex
        db.execute(text("UPDATE background_jobs SET status='running',attempts=:n,started_at=now(),updated_at=now(),error_text=NULL WHERE id=:id"),{"n":attempt,"id":job["id"]})
        db.execute(text(
            "UPDATE internal_video_assets SET processing_status='processing',processing_error_code=NULL,"
            "processing_worker_id=:w,processing_lease_until=now()+(:lease*interval '1 second'),processing_attempts=:n WHERE id=:id"
        ),{"w":worker_id,"lease":LEASE_SECONDS,"n":attempt,"id":asset_id})
        _emit(db,row); db.commit()
        return {"terminal":False,"job_id":int(job["id"]),"asset_id":asset_id,"attempt":attempt,"worker_id":worker_id,
                "account_id":row["account_id"],"conversation_id":int(row["conversation_id"]),"message_id":row["message_id"],
                "kind":row["kind"],"original_storage_ref":row["original_storage_ref"],
                "playback_storage_ref":row["playback_storage_ref"],"thumbnail_storage_ref":row["thumbnail_storage_ref"]}
    finally: db.close()

def process_local(claim):
    try:
        source=video.core()._attachment_storage_path(claim["original_storage_ref"],claim["conversation_id"])
        playback=video.core()._attachment_storage_path(claim["playback_storage_ref"],claim["conversation_id"])
        thumb=video.core()._attachment_storage_path(claim["thumbnail_storage_ref"],claim["conversation_id"])
        started=time.monotonic()
        result=normalize(source,playback,thumb,kind=claim["kind"])
        return result,None,time.monotonic()-started
    except VideoError as exc:
        return None,exc.code,0.0
    except Exception:
        return None,"video_processing_failed",0.0

def finalize(claim,result,error,elapsed):
    db=SessionLocal()
    try:
        job=db.execute(text("SELECT id,status,attempts FROM background_jobs WHERE id=:id FOR UPDATE"),{"id":claim["job_id"]}).mappings().first()
        live=db.execute(text("SELECT * FROM internal_video_assets WHERE id=:id AND deleted_at IS NULL FOR UPDATE"),{"id":claim["asset_id"]}).mappings().first()
        if not job: db.rollback(); return True
        if not live:
            db.execute(text("UPDATE background_jobs SET status='cancelled',finished_at=now(),updated_at=now() WHERE id=:id"),{"id":claim["job_id"]}); db.commit(); return True
        if live["processing_worker_id"]!=claim["worker_id"] or int(live["processing_attempts"] or 0)!=claim["attempt"] or job["status"]!="running":
            db.rollback(); return True
        if error:
            retry=claim["attempt"]<=len(RETRY_DELAYS)
            state="queued" if retry else "failed"
            db.execute(text(
                "UPDATE internal_video_assets SET processing_status=:s,processing_error_code=:e,processing_worker_id=NULL,"
                "processing_lease_until=NULL,processed_at=CASE WHEN :s='failed' THEN now() ELSE NULL END WHERE id=:id"
            ),{"s":state,"e":error,"id":claim["asset_id"]})
            db.execute(text(
                "UPDATE background_jobs SET status=:s,error_text=:e,scheduled_at=now()+(:d*interval '1 second'),"
                "finished_at=CASE WHEN :s='failed' THEN now() ELSE NULL END,updated_at=now() WHERE id=:id"
            ),{"s":state,"e":error,"d":RETRY_DELAYS[claim["attempt"]-1] if retry else 0,"id":claim["job_id"]})
            if not retry:
                telemetry.record(
                    db,
                    "transcode_failed",
                    user_id=live["uploader_user_id"],
                    account_id=live["account_id"],
                    entity_type="message" if live.get("message_id") else "conversation",
                    entity_id=live["message_id"] or live["conversation_id"],
                    details={
                        "error_code": error,
                        "attempt": claim["attempt"],
                        "elapsed_ms": int(max(0.0, elapsed) * 1000),
                        "media_kind": live["kind"],
                    },
                )
            _emit(db,live); db.commit(); return True
        db.execute(text(
            "UPDATE internal_video_assets SET processing_status='ready',processing_error_code=NULL,processing_worker_id=NULL,"
            "processing_lease_until=NULL,processed_at=now(),duration_ms=:d,width=:w,height=:h,playback_size_bytes=:s,"
            "playback_mime=:mime WHERE id=:id"
        ),{"d":result["duration_ms"],"w":result["width"],"h":result["height"],"s":result["playback_size_bytes"],
           "mime":result["playback_mime"],"id":claim["asset_id"]})
        # Attachment can be committed before processing completes; synchronize exact size now.
        db.execute(text(
            "UPDATE internal_message_attachments SET size_bytes=:s,content_type='video/mp4' "
            "WHERE id=(SELECT attachment_id FROM internal_video_assets WHERE id=:id)"
        ),{"s":result["playback_size_bytes"],"id":claim["asset_id"]})
        db.execute(text(
            "UPDATE background_jobs SET status='completed',finished_at=now(),updated_at=now(),error_text=NULL,"
            "result_json=CAST(:r AS JSONB),actual_kopeks=0 WHERE id=:id"
        ),{"r":json.dumps({"elapsed_seconds":elapsed,"external_media_cost_rub":0}),"id":claim["job_id"]})
        telemetry.record(
            db,
            "transcode_success",
            user_id=live["uploader_user_id"],
            account_id=live["account_id"],
            entity_type="message" if live.get("message_id") else "conversation",
            entity_id=live["message_id"] or live["conversation_id"],
            details={
                "media_kind": live["kind"],
                "attempt": claim["attempt"],
                "elapsed_ms": int(max(0.0, elapsed) * 1000),
                "duration_ms": int(result["duration_ms"] or 0),
                "size_bytes": int(result["playback_size_bytes"] or 0),
                "width": int(result["width"] or 0),
                "height": int(result["height"] or 0),
            },
        )
        _emit(db,live); db.commit(); return True
    finally: db.close()

def run_once():
    from app.db.session import engine
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:id)"),{"id":LOCK_ID}).scalar(): return False
        try:
            claim=claim_one()
            if claim is None: return False
            if claim.get("terminal"): return True
            result,error,elapsed=process_local(claim)
            return finalize(claim,result,error,elapsed)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:id)"),{"id":LOCK_ID})

def cleanup_orphans():
    db=SessionLocal()
    try:
        rows=db.execute(text(
            "SELECT * FROM internal_video_assets WHERE message_id IS NULL AND created_at<now()-interval '24 hours' "
            "FOR UPDATE SKIP LOCKED LIMIT 100"
        )).mappings().all()
        for row in rows:
            for ref in (row["original_storage_ref"],row["playback_storage_ref"],row["thumbnail_storage_ref"]):
                video.core()._attachment_storage_path(ref,row["conversation_id"]).unlink(missing_ok=True)
            db.execute(text("DELETE FROM internal_video_assets WHERE id=:id AND message_id IS NULL"),{"id":str(row["id"])})
        db.execute(text("DELETE FROM internal_call_events WHERE created_at<now()-interval '1 day'"))
        db.commit()
    finally: db.close()

if __name__=="__main__":
    if "--once" in sys.argv: run_once()
    else:
        last_cleanup=0.0
        while True:
            try:
                if time.monotonic()-last_cleanup>3600:
                    cleanup_orphans(); last_cleanup=time.monotonic()
                if not run_once(): time.sleep(2)
            except Exception as exc:
                print(json.dumps({"video_worker":"degraded","error":type(exc).__name__}),flush=True)
                time.sleep(5)
