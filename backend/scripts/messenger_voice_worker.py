"""Dedicated bounded consumer of Messenger voice transcription jobs.

The PostgreSQL advisory lock lives on an AUTOCOMMIT connection. Database
transactions are deliberately short: claim -> COMMIT/CLOSE, local STT runs
without a DB session, then finalize -> COMMIT/CLOSE.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from uuid import uuid4

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import messenger_voice as voice
from app.services.messenger_voice_media import MODEL_VERSION

LOCK_ID = 8264817304
JOB_TIMEOUT = 900
LEASE_SECONDS = JOB_TIMEOUT + 300
RETRY_DELAYS = (30, 120, 600)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1


class PersistentSTTRunner:
    """One local child, one model load, bounded per-job wait."""

    def __init__(self):
        self.proc = None

    def _env(self):
        return {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "OMP_NUM_THREADS": "2",
        }

    def stop(self):
        proc = self.proc
        self.proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    def _read_json(self, timeout):
        proc = self.proc
        if not proc or not proc.stdout:
            raise RuntimeError("stt_child_missing")
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError("stt_child_timeout")
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("stt_child_closed")
        return json.loads(line)

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self.stop()
        child = Path(__file__).with_name("messenger_voice_stt_child.py")
        self.proc = subprocess.Popen(
            [sys.executable, str(child)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=self._env(),
        )
        try:
            hello = self._read_json(120)
        except Exception:
            self.stop()
            raise
        if hello.get("kind") != "ready" or hello.get("model") != MODEL_VERSION:
            self.stop()
            raise RuntimeError("stt_child_boot_failed")

    def transcribe(self, path, timeout):
        self.start()
        proc = self.proc
        if not proc or not proc.stdin:
            raise RuntimeError("stt_child_missing")
        task_id = uuid4().hex
        try:
            proc.stdin.write(json.dumps({"task_id": task_id, "path": str(path)}) + "\n")
            proc.stdin.flush()
            response = self._read_json(timeout)
        except TimeoutError:
            self.stop()
            raise
        except Exception:
            self.stop()
            raise
        if response.get("kind") != "result" or response.get("task_id") != task_id:
            self.stop()
            raise RuntimeError("stt_child_protocol_error")
        if response.get("error"):
            return None, str(response["error"])
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("stt_child_invalid_result")
        return result, None


_STT_RUNNER = PersistentSTTRunner()
atexit.register(_STT_RUNNER.stop)


def event(db, row):
    voice.core().emit_messenger_event(
        db,
        row["account_id"],
        "internal.message.updated",
        "message",
        row["message_id"],
        {"conversation_id": row["conversation_id"], "message_type": "voice"},
    )


def _requeue_abandoned(db):
    """Requeue jobs left running by a dead worker.

    Caller already owns the global advisory lock, so no live Messenger STT
    worker can legitimately own a running row at this moment.
    """
    rows = db.execute(text(
        "SELECT id,payload_json FROM background_jobs "
        "WHERE kind='messenger_voice_transcription' AND status='running' "
        "FOR UPDATE"
    )).mappings().all()
    for job in rows:
        asset_id = str((job["payload_json"] or {}).get("asset_id", ""))
        if asset_id:
            db.execute(text(
                "UPDATE internal_voice_assets "
                "SET transcription_status='queued',transcription_worker_id=NULL,"
                "transcription_lease_until=NULL "
                "WHERE id=:id AND transcription_status='processing'"
            ), {"id": asset_id})
    if rows:
        db.execute(text(
            "UPDATE background_jobs SET status='queued',scheduled_at=now(),"
            "updated_at=now(),started_at=NULL "
            "WHERE kind='messenger_voice_transcription' AND status='running'"
        ))


def claim_one():
    db = SessionLocal()
    try:
        _requeue_abandoned(db)
        job = db.execute(text(
            "SELECT * FROM background_jobs "
            "WHERE kind='messenger_voice_transcription' AND status='queued' "
            "AND (scheduled_at IS NULL OR scheduled_at<=now()) "
            "ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
        )).mappings().first()
        if not job:
            db.commit()
            return None

        asset_id = str((job["payload_json"] or {}).get("asset_id", ""))
        row = db.execute(text(
            "SELECT v.* FROM internal_voice_assets v "
            "JOIN internal_messages m ON m.id=v.message_id "
            "WHERE v.id=:id AND v.deleted_at IS NULL AND m.deleted_at IS NULL "
            "AND v.conversation_id=m.conversation_id FOR UPDATE OF v"
        ), {"id": asset_id}).mappings().first()

        if not row:
            db.execute(text(
                "UPDATE background_jobs SET status='cancelled',finished_at=now(),"
                "updated_at=now() WHERE id=:id"
            ), {"id": job["id"]})
            db.commit()
            return {"terminal": True}

        if row["transcription_status"] in {"ready", "no_speech"}:
            db.execute(text(
                "UPDATE background_jobs SET status='completed',finished_at=now(),"
                "updated_at=now(),error_text=NULL WHERE id=:id"
            ), {"id": job["id"]})
            db.commit()
            return {"terminal": True}

        attempt = int(job["attempts"] or 0) + 1
        if attempt > MAX_ATTEMPTS:
            db.execute(text(
                "UPDATE internal_voice_assets SET transcription_status='failed',"
                "transcription_error_code='retry_exhausted',"
                "transcription_worker_id=NULL,transcription_lease_until=NULL,"
                "transcription_completed_at=now() WHERE id=:id"
            ), {"id": asset_id})
            db.execute(text(
                "UPDATE background_jobs SET status='failed',error_text='retry_exhausted',"
                "finished_at=now(),updated_at=now() WHERE id=:id"
            ), {"id": job["id"]})
            event(db, row)
            db.commit()
            return {"terminal": True}

        worker_id = uuid4().hex
        db.execute(text(
            "UPDATE background_jobs SET status='running',attempts=:n,started_at=now(),"
            "updated_at=now(),error_text=NULL WHERE id=:id"
        ), {"n": attempt, "id": job["id"]})
        db.execute(text(
            "UPDATE internal_voice_assets SET transcription_status='processing',"
            "transcription_started_at=COALESCE(transcription_started_at,now()),"
            "transcription_completed_at=NULL,transcription_error_code=NULL,"
            "transcription_worker_id=:w,"
            "transcription_lease_until=now()+(:lease * interval '1 second'),"
            "transcription_attempts=:n WHERE id=:id"
        ), {"w": worker_id, "lease": LEASE_SECONDS, "n": attempt, "id": asset_id})
        event(db, row)
        db.commit()

        # Return only immutable data required for local processing. The DB
        # session closes before FFmpeg/Whisper starts.
        return {
            "terminal": False,
            "job_id": int(job["id"]),
            "asset_id": asset_id,
            "attempt": attempt,
            "worker_id": worker_id,
            "account_id": row["account_id"],
            "conversation_id": int(row["conversation_id"]),
            "message_id": int(row["message_id"]),
            "duration_ms": int(row["duration_ms"]),
            "playback_storage_ref": row["playback_storage_ref"],
            "transcription_model": row["transcription_model"],
        }
    finally:
        db.close()


def process_local(claim):
    if not voice.enabled("MESSENGER_LOCAL_TRANSCRIPTION_ENABLED"):
        return None, "local_stt_unavailable", 0.0
    if claim["transcription_model"] != MODEL_VERSION:
        return None, "model_version_mismatch", 0.0

    path = voice.core()._attachment_storage_path(
        claim["playback_storage_ref"], claim["conversation_id"]
    )
    started = time.monotonic()
    try:
        result, child_error = _STT_RUNNER.transcribe(path, JOB_TIMEOUT)
        if child_error:
            return None, child_error, time.monotonic() - started
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("invalid_result")
        result["text"] = result["text"].strip()
        if len(result["text"]) > 100000 or not isinstance(result.get("segments", []), list):
            raise ValueError("invalid_result")
        return result, None, time.monotonic() - started
    except TimeoutError:
        return None, "local_stt_timeout", time.monotonic() - started
    except Exception:
        # A broken child is discarded so the next retry starts a clean model
        # process; no provider fallback is ever attempted.
        _STT_RUNNER.stop()
        code = (
            "local_stt_unavailable"
            if not voice.enabled("MESSENGER_LOCAL_TRANSCRIPTION_ENABLED")
            else "local_stt_failed"
        )
        return None, code, time.monotonic() - started


def finalize(claim, result, error, elapsed_seconds):
    db = SessionLocal()
    try:
        job = db.execute(text(
            "SELECT id,status,attempts FROM background_jobs WHERE id=:id FOR UPDATE"
        ), {"id": claim["job_id"]}).mappings().first()
        live = db.execute(text(
            "SELECT v.* FROM internal_voice_assets v "
            "JOIN internal_messages m ON m.id=v.message_id "
            "WHERE v.id=:id AND v.deleted_at IS NULL AND m.deleted_at IS NULL "
            "AND v.conversation_id=m.conversation_id FOR UPDATE OF v,m"
        ), {"id": claim["asset_id"]}).mappings().first()

        if not job:
            db.rollback()
            return True

        if not live:
            db.execute(text(
                "UPDATE background_jobs SET status='cancelled',finished_at=now(),"
                "updated_at=now() WHERE id=:id"
            ), {"id": claim["job_id"]})
            db.commit()
            return True

        # Never let an old/crashed worker overwrite a later claim.
        if (
            live["transcription_worker_id"] != claim["worker_id"]
            or int(live["transcription_attempts"] or 0) != claim["attempt"]
            or job["status"] != "running"
            or int(job["attempts"] or 0) != claim["attempt"]
        ):
            db.rollback()
            return True

        if error:
            retry = claim["attempt"] <= len(RETRY_DELAYS)
            next_status = "queued" if retry else "failed"
            db.execute(text(
                "UPDATE internal_voice_assets SET transcription_status=:s,"
                "transcription_error_code=:e,transcription_worker_id=NULL,"
                "transcription_lease_until=NULL,"
                "transcription_completed_at=CASE WHEN :s='failed' THEN now() ELSE NULL END "
                "WHERE id=:id"
            ), {
                "s": next_status,
                "e": error,
                "id": claim["asset_id"],
            })
            db.execute(text(
                "UPDATE background_jobs SET status=:s,error_text=:e,"
                "scheduled_at=now()+(:delay * interval '1 second'),"
                "finished_at=CASE WHEN :s='failed' THEN now() ELSE NULL END,"
                "updated_at=now() WHERE id=:id"
            ), {
                "s": next_status,
                "e": error,
                "delay": RETRY_DELAYS[claim["attempt"] - 1] if retry else 0,
                "id": claim["job_id"],
            })
            event(db, live)
            db.commit()
            return True

        text_value = (result or {}).get("text", "").strip()
        outcome = "ready" if text_value else "no_speech"
        db.execute(text(
            "UPDATE internal_voice_assets SET transcription_status=:s,"
            "transcription_text=:t,transcription_language=:l,"
            "transcription_segments=CAST(:segments AS JSONB),"
            "transcription_completed_at=now(),transcription_error_code=NULL,"
            "transcription_worker_id=NULL,transcription_lease_until=NULL "
            "WHERE id=:id"
        ), {
            "s": outcome,
            "t": text_value if text_value else None,
            "l": (result or {}).get("language") if text_value else None,
            "segments": json.dumps((result or {}).get("segments", []) if text_value else []),
            "id": claim["asset_id"],
        })
        db.execute(text(
            "UPDATE background_jobs SET status='completed',finished_at=now(),"
            "updated_at=now(),error_text=NULL,result_json=CAST(:r AS JSONB),"
            "actual_kopeks=0 WHERE id=:id"
        ), {
            "r": json.dumps({
                "outcome": outcome,
                "elapsed_seconds": elapsed_seconds,
                "audio_minutes": claim["duration_ms"] / 60000,
                "external_stt_cost_rub": 0,
            }),
            "id": claim["job_id"],
        })
        event(db, live)
        db.commit()
        return True
    finally:
        db.close()


def process_one():
    claim = claim_one()
    if claim is None:
        return False
    if claim.get("terminal"):
        return True
    result, error, elapsed = process_local(claim)
    return finalize(claim, result, error, elapsed)


def run_once():
    # Advisory lock is held on an AUTOCOMMIT connection, never in the
    # SQLAlchemy Session used by claim/finalize.
    from app.db.session import engine

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID}).scalar():
            return False
        try:
            return process_one()
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})


def cleanup_orphans():
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT * FROM internal_voice_assets "
            "WHERE message_id IS NULL AND created_at<now()-interval '24 hours' "
            "FOR UPDATE SKIP LOCKED LIMIT 100"
        )).mappings().all()
        for row in rows:
            for ref in (row["original_storage_ref"], row["playback_storage_ref"]):
                voice.core()._attachment_storage_path(
                    ref, row["conversation_id"]
                ).unlink(missing_ok=True)
            db.execute(text(
                "DELETE FROM internal_voice_assets "
                "WHERE id=:id AND message_id IS NULL"
            ), {"id": str(row["id"])})
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        last_cleanup = 0.0
        while True:
            try:
                if time.monotonic() - last_cleanup > 3600:
                    cleanup_orphans()
                    last_cleanup = time.monotonic()
                if not run_once():
                    time.sleep(2)
            except Exception as exc:
                # Stable class only: no paths, transcript, credentials or SQL.
                print(json.dumps({
                    "voice_worker": "degraded",
                    "error": type(exc).__name__,
                }), flush=True)
                time.sleep(5)
