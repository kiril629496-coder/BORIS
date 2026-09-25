from __future__ import annotations
import sys
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import asterisk_gateway as ag
from app.services.mcn_core import ensure_schema,trunk_upsert,did_upsert,trunk_mark_health,readiness

A="__qa_mcn_originate_gate_v1"

def cleanup():
    db=SessionLocal()
    try:
        for table in ["telephony_dids","telephony_trunks"]:
            db.execute(text(f"DELETE FROM {table} WHERE account_id=:a"),{"a":A})
        db.commit()
    finally: db.close()

ensure_schema(); cleanup()
try:
    made=trunk_upsert(A,{
        "provider":"mcn","name":"MCN originate live gate","auth_mode":"registration",
        "registrar":"sip.example.test","username":"qa-user","password":"qa-pass",
        "source_ips":[],"allowed_cli":["+74950009992"],"enabled":True,
    })
    assert made.get("status")=="ok",made
    tid=int(made["trunk"]["id"])
    did=did_upsert(A,{"provider":"mcn","number":"+74950009992","trunk_id":tid,"purpose":"main",
                     "status":"active","inbound_enabled":True,"outbound_cli_enabled":True})
    assert did.get("status")=="ok",did
    trunk_mark_health(A,tid,False,"registration_unregistered","registration_unregistered")
    assert readiness(A).get("ready") is False

    orig_health=ag.asterisk_health
    orig_live=ag._mcn_trunk_live_health
    orig_request=ag._request
    orig_retry=ag._registration_retry
    try:
        ag.asterisk_health=lambda:{"ready":True}
        ag._mcn_trunk_live_health=lambda _cfg:{
            "ready":True,"status":"registration_registered",
            "registration_state":"registered","auth_mode":"registration"
        }
        calls=[]
        ag._request=lambda method,path,**kw:(calls.append((method,path,kw)) or (201,{"id":"mcn-qa-accepted"}))
        out=ag.originate_mcn(A,"+74950001111","+74950009992",
                             {"idempotency_key":"qa-originate-recover-1"})
        assert out.get("status")=="accepted",out
        assert calls and calls[0][0]=="POST" and calls[0][1]=="/channels",calls
        assert readiness(A).get("ready") is True,readiness(A)

        ag._mcn_trunk_live_health=lambda _cfg:{
            "ready":False,"status":"registration_rejected",
            "registration_state":"rejected","auth_mode":"registration"
        }
        calls.clear()
        rejected=ag.originate_mcn(A,"+74950001111","+74950009992",
                                  {"idempotency_key":"qa-originate-reject-1"})
        assert rejected.get("status")=="mcn_trunk_not_connected",rejected
        assert rejected.get("registration_state")=="rejected",rejected
        assert rejected.get("owner_action_required") is True,rejected
        assert not calls,calls

        retry_calls=[]
        ag._mcn_trunk_live_health=lambda _cfg:{
            "ready":False,"status":"registration_unregistered",
            "registration_state":"unregistered","auth_mode":"registration"
        }
        ag._registration_retry=lambda _tid:(retry_calls.append(_tid) or {"status":"registration_retry_sent"})
        retry=ag.originate_mcn(A,"+74950001111","+74950009992",
                               {"idempotency_key":"qa-originate-retry-1"})
        assert retry.get("status")=="mcn_trunk_not_connected",retry
        assert retry.get("self_heal")=="registration_retry_sent",retry
        assert retry_calls==[tid],retry_calls
        print("MCN_ORIGINATE_LIVE_GATE_OK",{
            "recovered_from_stale_db":True,
            "rejected_fail_closed":True,
            "unregistered_self_heal":True,
        })
    finally:
        ag.asterisk_health=orig_health
        ag._mcn_trunk_live_health=orig_live
        ag._request=orig_request
        ag._registration_retry=orig_retry
finally:
    cleanup()
