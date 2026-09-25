#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/BORIS/backend")

import requests
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv("/root/BORIS/backend/.env")

from app.db.session import SessionLocal
from app.services.telephony_core import create_call, ensure_schema, set_recording_notice_status

ACCOUNT = "__qa_asterisk_recording_v1"
CHANNEL_ID = "qa-ari-recording-v1"


def cleanup() -> None:
    db = SessionLocal()
    paths: list[str] = []
    try:
        paths = [str(x[0]) for x in db.execute(text("""
        SELECT local_path FROM telephony_recordings
        WHERE account_id=:a AND local_path IS NOT NULL
        """), {"a": ACCOUNT}).all()]
        db.execute(text("DELETE FROM telephony_asterisk_events WHERE account_id=:a OR channel_id=:ch"),
                   {"a": ACCOUNT, "ch": CHANNEL_ID})
        db.execute(text("DELETE FROM telephony_asterisk_recordings WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_call_carrier_links WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_events WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_recordings WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"), {"a": ACCOUNT})
        db.commit()
    finally:
        db.close()
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass


def main() -> None:
    ensure_schema()
    cleanup()
    base = str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
    auth = (str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""), str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
    assert all(auth), "ARI credentials missing"

    try:
        requests.delete(base + f"/channels/{CHANNEL_ID}", auth=auth, timeout=2)
        call = create_call(
            ACCOUNT, "outbound", "+74950000000", "+74951112233",
            provider="mcn", provider_call_id=CHANNEL_ID,
            source="qa", source_ref="recording-probe",
        )
        call_id = str(call["id"])
        notice = set_recording_notice_status(ACCOUNT, call_id, "not_required")
        assert notice["status"] == "ok"

        r = requests.post(
            base + "/channels",
            params={
                "endpoint": "Local/999@boris-qa-loop",
                "app": "boris-phone",
                "appArgs": f"outbound,{ACCOUNT}",
                "channelId": CHANNEL_ID,
                "timeout": 10,
            },
            json={"variables": {"BORIS_QA_RECORDING": "1"}},
            auth=auth, timeout=5,
        )
        assert r.status_code in {200, 201}, (r.status_code, r.text[:300])

        deadline = time.time() + 6
        registry = None
        while time.time() < deadline:
            db = SessionLocal()
            try:
                registry = db.execute(text("""
                SELECT * FROM telephony_asterisk_recordings
                WHERE account_id=:a AND call_id=:c ORDER BY id DESC LIMIT 1
                """), {"a": ACCOUNT, "c": call_id}).mappings().first()
                if registry and registry.get("status") in {"recording", "attached"}:
                    break
            finally:
                db.close()
            time.sleep(0.1)
        assert registry is not None, "Asterisk recording was not started"
        assert registry["status"] in {"recording", "attached"}, dict(registry)

        time.sleep(1.5)
        requests.delete(base + f"/channels/{CHANNEL_ID}", auth=auth, timeout=5)

        deadline = time.time() + 8
        attached = None
        call_row = None
        while time.time() < deadline:
            db = SessionLocal()
            try:
                attached = db.execute(text("""
                SELECT ar.status ar_status,ar.duration_sec,ar.attached_recording_id,
                       r.id recording_id,r.local_path,r.checksum,r.status recording_status,
                       r.transcript_status,r.analysis_status
                FROM telephony_asterisk_recordings ar
                LEFT JOIN telephony_recordings r ON r.id=ar.attached_recording_id
                WHERE ar.account_id=:a AND ar.call_id=:c
                ORDER BY ar.id DESC LIMIT 1
                """), {"a": ACCOUNT, "c": call_id}).mappings().first()
                call_row = db.execute(text("""
                SELECT state,recording_status FROM telephony_calls
                WHERE account_id=:a AND id=:c
                """), {"a": ACCOUNT, "c": call_id}).mappings().first()
                if (attached and attached.get("ar_status") == "attached" and attached.get("local_path")
                        and call_row and call_row.get("state") in {"ended", "failed", "missed"}):
                    break
            finally:
                db.close()
            time.sleep(0.15)

        assert attached is not None, "recording registry disappeared"
        assert attached["ar_status"] == "attached", dict(attached)
        assert attached["recording_id"], dict(attached)
        assert attached["checksum"], dict(attached)
        assert attached["recording_status"] == "available", dict(attached)
        assert call_row and call_row["recording_status"] == "available", dict(call_row or {})
        path = Path(str(attached["local_path"]))
        assert path.is_file(), str(path)
        assert path.stat().st_size > 44, path.stat().st_size
        assert str(path.resolve()).startswith("/root/BORIS/backend/telephony_recordings/")
        print("ASTERISK_RECORDING_PIPELINE_OK", {
            "bytes": path.stat().st_size,
            "call_state": call_row["state"],
            "recording_status": attached["recording_status"],
            "transcript_status": attached["transcript_status"],
            "analysis_status": attached["analysis_status"],
        })
    finally:
        try:
            requests.delete(base + f"/channels/{CHANNEL_ID}", auth=auth, timeout=2)
        except Exception:
            pass
        cleanup()


if __name__ == "__main__":
    main()
