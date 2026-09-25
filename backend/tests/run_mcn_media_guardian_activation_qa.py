from __future__ import annotations
import json, os, sys, time
from pathlib import Path

sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.telephony_core import media_session_guardian
from app.services.phone_media_gateway import webrtc_endpoint_health
from mcn_media_qa_cleanup import cleanup_mcn_media_qa

s=json.loads(Path("/tmp/boris_mcn_media_guardian_state.json").read_text())
try:
    eh=webrtc_endpoint_health(s["endpoint_id"])
    print("ENDPOINT_HEALTH_BEFORE_GUARD",eh)
    assert eh.get("ready") is True,eh

    g=media_session_guardian(100,include_synthetic=True)
    print("GUARDIAN_ACTIVATION",g)
    assert g.get("activation_started",0)>=1,g
    time.sleep(1.0)

    db=SessionLocal()
    try:
        row=db.execute(text("""SELECT bridge_id,media_channel_id,activated_at,last_bridge_error,status
                               FROM telephony_media_sessions WHERE id=:i"""),{"i":s["lease_id"]}).mappings().first()
        print("LEASE_AFTER_ACTIVATION",dict(row or {}))
        assert row and row.get("activated_at") is not None,row
        assert row.get("status")=="active",row
    finally:
        db.close()
    print("MCN_MEDIA_GUARDIAN_QA_DONE")
finally:
    print("MCN_MEDIA_QA_FINALLY",cleanup_mcn_media_qa(s))
