#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/root/BORIS/backend")

import requests
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.telephony_core import create_call, ensure_schema

ACCOUNT = "__qa_asterisk_bridge_probe_v1"
CHANNEL_ID = "qa-ari-loop-" + str(int(time.time()))


def cleanup() -> None:
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_asterisk_events WHERE account_id=:a OR channel_id LIKE 'qa-ari-loop-%'"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_call_carrier_links WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_events WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_recordings WHERE account_id=:a"), {"a": ACCOUNT})
        db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"), {"a": ACCOUNT})
        db.commit()
    finally:
        db.close()


def main() -> None:
    ensure_schema()
    cleanup()
    try:
        call = create_call(
            ACCOUNT, "outbound", "+74950000000", "+74951112233",
            provider="mcn", provider_call_id=CHANNEL_ID,
            source="qa", source_ref="asterisk-bridge-probe",
        )
        assert call["id"]

        user = str(os.getenv("BORIS_ASTERISK_ARI_USER") or "").strip()
        password = str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or "").strip()
        base = str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
        assert user and password

        r = requests.post(
            base + "/channels",
            params={
                "endpoint": "Local/999@boris-qa-loop",
                "app": "boris-phone",
                "appArgs": f"outbound,{ACCOUNT}",
                "channelId": CHANNEL_ID,
                "timeout": 2,
            },
            json={"variables": {"BORIS_QA_PROBE": "1"}},
            auth=(user, password),
            timeout=5,
        )
        assert r.status_code in {200, 201}, (r.status_code, r.text[:200])

        deadline = time.time() + 5
        observed = False
        while time.time() < deadline:
            db = SessionLocal()
            try:
                n = db.execute(text("""
                SELECT count(*) FROM telephony_asterisk_events
                WHERE channel_id=:c AND event_type='StasisStart'
                """), {"c": CHANNEL_ID}).scalar() or 0
                if n:
                    observed = True
                    break
            finally:
                db.close()
            time.sleep(0.15)
        assert observed, "ARI bridge did not persist StasisStart"

        requests.delete(base + f"/channels/{CHANNEL_ID}", auth=(user, password), timeout=5)

        deadline = time.time() + 5
        final_state = None
        while time.time() < deadline:
            db = SessionLocal()
            try:
                final_state = db.execute(text("SELECT state FROM telephony_calls WHERE account_id=:a AND id=:c"),
                                         {"a": ACCOUNT, "c": call["id"]}).scalar()
                if final_state in {"failed", "ended", "cancelled"}:
                    break
            finally:
                db.close()
            time.sleep(0.15)

        db = SessionLocal()
        try:
            events = [dict(x) for x in db.execute(text("""
            SELECT event_type,status,error_code,call_id
            FROM telephony_asterisk_events
            WHERE channel_id=:c ORDER BY id
            """), {"c": CHANNEL_ID}).mappings().all()]
        finally:
            db.close()

        assert events, "No ARI events persisted"
        assert any(x["event_type"] == "StasisStart" and x["status"] in {"processed", "deferred"} for x in events)
        assert final_state in {"failed", "ended", "cancelled"}, final_state
        print("ASTERISK_BRIDGE_PROBE_OK", {"events": len(events), "final_state": final_state})
    finally:
        cleanup()


if __name__ == "__main__":
    main()
