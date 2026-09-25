from __future__ import annotations
import json, os, sys, time
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
import requests
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import did_upsert
from app.services.telephony_core import (
    ensure_schema, register_device, save_provider_config,
    queue_call_command, dispatch_pending_commands, request_media_session,
    media_session_guardian,
)

A="__qa_mcn_answer_media_v1"
D="dev_qa_mcn_answer_media"
N="+74950008882"
DIG="74950008882"
OUTER="qa-mcn-answer-outer-v1"

def ari():
    base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
    auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
    return base,auth

def cleanup_db():
    db=SessionLocal()
    try:
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
            db.execute(text(sql),{"a":A})
        db.commit()
    finally:
        db.close()

ensure_schema()
base,auth=ari()
try:
    requests.delete(base+f"/channels/{OUTER}",auth=auth,timeout=2)
except Exception:
    pass
cleanup_db()

cfg=save_provider_config(
    A,"mcn",
    credentials={"transport":"mcn_trunk","account_id":A},
    public_config={"operator":"mcn","account_id":A},
)
assert cfg.get("status")=="ok",cfg
did=did_upsert(A,{"number":N,"purpose":"main","status":"active"})
assert did.get("status")=="ok",did
dev=register_device(A,None,"QA manager browser","web","0.0.0",{"calls":True,"media":True},device_id=D)
assert dev.get("status")=="ok",dev

r=requests.post(base+"/channels",params={
    "endpoint":f"Local/{DIG}@boris-mcn-inbound",
    "app":"boris-phone",
    "appArgs":f"outbound,{A}",
    "channelId":OUTER,
    "timeout":10,
},auth=auth,timeout=5)
assert r.status_code in {200,201},(r.status_code,r.text[:300])

call=None
targets=[]
deadline=time.time()+5
while time.time()<deadline:
    db=SessionLocal()
    try:
        call=db.execute(text("""SELECT id,state,provider_call_id,device_id FROM telephony_calls
                                WHERE account_id=:a ORDER BY started_at DESC LIMIT 1"""),{"a":A}).mappings().first()
        if call:
            targets=[dict(x) for x in db.execute(text("""SELECT device_id,status FROM telephony_call_targets
                                                        WHERE account_id=:a AND call_id=:c ORDER BY id"""),
                                                 {"a":A,"c":call["id"]}).mappings().all()]
    finally:
        db.close()
    if call and any(x["device_id"]==D and x["status"]=="ringing" for x in targets):
        break
    time.sleep(.1)
assert call and call["state"]=="ringing",(call,targets)
cid=str(call["id"])
provider_channel=str(call["provider_call_id"])
cmd=queue_call_command(A,cid,"answer",{},None,D,"qa-answer-media-0001")
assert cmd.get("status")=="queued",cmd
disp=dispatch_pending_commands(100,include_synthetic=True)
print("ANSWER_DISPATCH",disp)

deadline=time.time()+4
active=None
while time.time()<deadline:
    db=SessionLocal()
    try:
        active=db.execute(text("""SELECT state,device_id,user_id,provider_call_id FROM telephony_calls
                                  WHERE account_id=:a AND id=:c"""),{"a":A,"c":cid}).mappings().first()
    finally:
        db.close()
    if active and active["state"]=="active" and active["device_id"]==D:
        break
    time.sleep(.1)
assert active and active["state"]=="active" and active["device_id"]==D,active
print("MCN_ANSWER_CLAIM_OK",dict(active))

media=request_media_session(A,D,cid)
assert media.get("status")=="ok",media
Path("/tmp/boris_media_invite_qa.json").write_text(json.dumps(media["media"],ensure_ascii=False))
os.chmod("/tmp/boris_media_invite_qa.json",0o600)
pre=media_session_guardian(100,include_synthetic=True)
assert pre.get("activation_waiting_registration",0)>=1,pre
state={
    "account_id":A,"device_id":D,"call_id":cid,
    "provider_channel_id":provider_channel,"outer_channel_id":OUTER,
    "lease_id":int(media["media_lease_id"]),"endpoint_id":media["media"]["endpoint_id"],
}
Path("/tmp/boris_mcn_answer_media_state.json").write_text(json.dumps(state,ensure_ascii=False))
os.chmod("/tmp/boris_mcn_answer_media_state.json",0o600)
print("MCN_ANSWER_MEDIA_PREPARED",{"call_state":active["state"],"lease_id":state["lease_id"],"waiting_registration":pre.get("activation_waiting_registration")})
