from __future__ import annotations
import json, os, sys, time
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
import requests
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.telephony_core import media_session_guardian, release_media_session
from app.services.phone_media_gateway import webrtc_endpoint_health

s=json.loads(Path("/tmp/boris_mcn_answer_media_state.json").read_text())
eh=webrtc_endpoint_health(s["endpoint_id"])
assert eh.get("ready") is True,eh
g=media_session_guardian(100,include_synthetic=True)
print("ANSWER_MEDIA_GUARDIAN",g)
assert g.get("activation_started",0)>=1,g
time.sleep(.8)

rel=release_media_session(s["account_id"],s["device_id"],s["call_id"],s["lease_id"],"qa_complete")
print("ANSWER_MEDIA_RELEASE",rel)
base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
for ch in [s["provider_channel_id"],s["outer_channel_id"]]:
    try:
        requests.delete(base+f"/channels/{ch}",auth=auth,timeout=3)
    except Exception:
        pass
time.sleep(.4)

db=SessionLocal()
try:
    final=db.execute(text("SELECT state,device_id FROM telephony_calls WHERE account_id=:a AND id=:c"),
                     {"a":s["account_id"],"c":s["call_id"]}).mappings().first()
    print("ANSWER_MEDIA_FINAL_CALL",dict(final or {}))
    for sql in [
        "DELETE FROM telephony_push_outbox WHERE account_id=:a",
        "DELETE FROM telephony_media_sessions WHERE account_id=:a",
        "DELETE FROM telephony_commands WHERE account_id=:a",
        "DELETE FROM telephony_call_targets WHERE account_id=:a",
        "DELETE FROM telephony_call_carrier_links WHERE account_id=:a",
        "DELETE FROM telephony_asterisk_events WHERE account_id=:a",
        "DELETE FROM telephony_events WHERE account_id=:a",
        "DELETE FROM telephony_recordings WHERE account_id=:a",
        "DELETE FROM telephony_calls WHERE account_id=:a",
        "DELETE FROM telephony_devices WHERE account_id=:a",
        "DELETE FROM telephony_dids WHERE account_id=:a",
        "DELETE FROM telephony_provider_configs WHERE account_id=:a",
        "DELETE FROM telephony_audit WHERE account_id=:a",
    ]:
        db.execute(text(sql),{"a":s["account_id"]})
    db.commit()
finally:
    db.close()
print("MCN_ANSWER_MEDIA_E2E_DONE")
