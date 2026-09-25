from __future__ import annotations
import json, os, sys, time
from pathlib import Path

sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)

import requests
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.telephony_core import (
    ensure_schema, save_provider_config, register_device, create_call,
    request_media_session, media_session_guardian,
)
from mcn_media_qa_cleanup import cleanup_mcn_media_qa

A="__qa_mcn_media_guardian_v1"
D="dev_qa_mcn_media_guardian"
CH="qa-mcn-media-carrier-v1"
STATE_FILE=Path("/tmp/boris_mcn_media_guardian_state.json")
INVITE_FILE=Path("/tmp/boris_media_invite_qa.json")


def ari():
    base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
    auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
    return base,auth


ensure_schema()
base,auth=ari()
cleanup_state={"account_id":A,"channel_id":CH}
# Always start from a clean synthetic namespace.
try:
    cleanup_mcn_media_qa(cleanup_state)
except Exception:
    pass
try:
    requests.delete(base+f"/channels/{CH}",auth=auth,timeout=2)
except Exception:
    pass

try:
    pc=save_provider_config(
        A,"mcn",
        credentials={"transport":"mcn_trunk","account_id":A},
        public_config={"operator":"mcn","account_id":A},
    )
    assert pc.get("status")=="ok",pc

    dev=register_device(A,None,"QA WebPhone","web","0.0.0",{"calls":True,"media":True},device_id=D)
    assert dev.get("status")=="ok",dev

    call=create_call(A,"inbound","+74950000001","+74950000002",provider="mcn",provider_call_id=CH,device_id=D,source="qa")
    cid=str(call["id"])
    cleanup_state.update({"device_id":D,"call_id":cid})

    db=SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_calls SET state='active',answered_at=now(),device_id=:d,updated_at=now()
                          WHERE account_id=:a AND id=:c"""),{"a":A,"c":cid,"d":D})
        db.commit()
    finally:
        db.close()

    r=requests.post(base+"/channels",params={
        "endpoint":"Local/999@boris-qa-loop",
        "app":"boris-phone",
        "appArgs":f"outbound,{A}",
        "channelId":CH,
        "timeout":10,
    },auth=auth,timeout=5)
    assert r.status_code in {200,201},(r.status_code,r.text[:300])
    time.sleep(0.5)

    media=request_media_session(A,D,cid)
    assert media.get("status")=="ok",media
    cleanup_state.update({
        "lease_id":int(media["media_lease_id"]),
        "endpoint_id":media["media"]["endpoint_id"],
    })

    INVITE_FILE.write_text(json.dumps(media["media"],ensure_ascii=False))
    os.chmod(INVITE_FILE,0o600)

    state=media_session_guardian(100,include_synthetic=True)
    assert state.get("activation_waiting_registration",0)>=1,state

    STATE_FILE.write_text(json.dumps(cleanup_state,ensure_ascii=False))
    os.chmod(STATE_FILE,0o600)

    print("MCN_MEDIA_GUARDIAN_PREPARED",{
        "lease_id":media["media_lease_id"],
        "waiting_registration":state.get("activation_waiting_registration"),
        "endpoint_id":media["media"]["endpoint_id"],
    })
except Exception:
    # Failed setup must never leave an Asterisk endpoint, synthetic call or DB rows.
    try:
        print("MCN_MEDIA_QA_SETUP_CLEANUP",cleanup_mcn_media_qa(cleanup_state))
    finally:
        raise
