from __future__ import annotations
import os, sys, time
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
import requests
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.telephony_core import ensure_schema, register_device
from app.services.mcn_core import did_upsert

A="__qa_mcn_inbound_ari_v1"
D="dev_qa_mcn_inbound_001"
N="+74950008881"
DIG="74950008881"
CH="qa-mcn-inbound-outer-v1"

def cleanup():
    db=SessionLocal()
    try:
        for sql in [
            "DELETE FROM telephony_push_outbox WHERE account_id=:a",
            "DELETE FROM telephony_call_targets WHERE account_id=:a",
            "DELETE FROM telephony_call_carrier_links WHERE account_id=:a",
            "DELETE FROM telephony_asterisk_events WHERE account_id=:a",
            "DELETE FROM telephony_events WHERE account_id=:a",
            "DELETE FROM telephony_recordings WHERE account_id=:a",
            "DELETE FROM telephony_calls WHERE account_id=:a",
            "DELETE FROM telephony_devices WHERE account_id=:a",
            "DELETE FROM telephony_dids WHERE account_id=:a",
            "DELETE FROM telephony_audit WHERE account_id=:a",
        ]:
            db.execute(text(sql),{"a":A})
        db.commit()
    finally:
        db.close()

ensure_schema()
cleanup()
try:
    did=did_upsert(A,{"number":N,"purpose":"main","status":"active"})
    assert did.get("status")=="ok",did
    dev=register_device(A,None,"QA inbound browser","web","0.0.0",{"calls":True,"media":True},device_id=D)
    assert dev.get("status")=="ok",dev

    base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
    auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
    try:
        requests.delete(base+f"/channels/{CH}",auth=auth,timeout=2)
    except Exception:
        pass
    cp=requests.post(base+"/channels",params={
        "endpoint":f"Local/{DIG}@boris-mcn-inbound",
        "app":"boris-phone",
        "appArgs":f"outbound,{A}",
        "channelId":CH,
        "timeout":10,
    },auth=auth,timeout=5)
    assert cp.status_code in {200,201},(cp.status_code,cp.text[:300])

    call=None
    targets=[]
    deadline=time.time()+5
    while time.time()<deadline:
        db=SessionLocal()
        try:
            call=db.execute(text("""SELECT id,state,provider,provider_call_id,direction,from_number,to_number
                                    FROM telephony_calls WHERE account_id=:a
                                    ORDER BY started_at DESC LIMIT 1"""),{"a":A}).mappings().first()
            if call:
                targets=[dict(x) for x in db.execute(text("""SELECT device_id,status FROM telephony_call_targets
                                                            WHERE account_id=:a AND call_id=:c ORDER BY id"""),
                                                     {"a":A,"c":call["id"]}).mappings().all()]
        finally:
            db.close()
        if call and targets:
            break
        time.sleep(.1)

    assert call is not None,"inbound call not created"
    assert call["provider"]=="mcn",dict(call)
    assert call["direction"]=="inbound",dict(call)
    assert call["to_number"]==N,dict(call)
    if not targets:
        db=SessionLocal()
        try:
            devrow=db.execute(text("SELECT id,presence,last_seen_at,platform,capabilities_json FROM telephony_devices WHERE account_id=:a AND id=:d"),{"a":A,"d":D}).mappings().first()
            audits=[dict(x) for x in db.execute(text("SELECT action,result,metadata_json FROM telephony_audit WHERE account_id=:a ORDER BY id DESC LIMIT 12"),{"a":A}).mappings().all()]
            print("MCN_INBOUND_DIAG",{"call":dict(call),"device":dict(devrow or {}),"audits":audits})
        finally:
            db.close()
    assert any(x["device_id"]==D and x["status"]=="ringing" for x in targets),targets
    print("MCN_INBOUND_ARI_ROUTING_OK",{"state":call["state"],"targets":targets,"provider_call_id":bool(call["provider_call_id"])})
    try:
        requests.delete(base+f"/channels/{CH}",auth=auth,timeout=3)
    except Exception:
        pass

    deadline=time.time()+6
    final_state=None
    while time.time()<deadline:
        db=SessionLocal()
        try:
            final_state=db.execute(text("SELECT state FROM telephony_calls WHERE account_id=:a AND id=:c"),
                                   {"a":A,"c":call["id"]}).scalar()
        finally:
            db.close()
        if final_state in {"missed","ended","failed","cancelled"}:
            break
        time.sleep(.15)
    print("MCN_INBOUND_ARI_FINAL",final_state)
finally:
    try:
        base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
        auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
        requests.delete(base+f"/channels/{CH}",auth=auth,timeout=2)
    except Exception:
        pass
    cleanup()
