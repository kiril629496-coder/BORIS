from __future__ import annotations
import subprocess, sys, time
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import ensure_schema,trunk_upsert
from app.services.asterisk_gateway import (
    active_channel_count,mcn_pjsip_guardian,_registration_health,
    _parse_registration_state,GENERATED_PJSIP
)

A="__qa_mcn_regstate_v1"
PORT=5077
MODE="/tmp/boris_mcn_fake_registrar_mode"

def cleanup_db():
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A})
        db.commit()
    finally: db.close()

def wait_state(tid, expected, timeout=12):
    deadline=time.time()+timeout
    last={}
    while time.time()<deadline:
        last=_registration_health(tid)
        if last.get("registration_state") in expected:
            return last
        time.sleep(.2)
    raise AssertionError((expected,last))

ensure_schema()
assert _parse_registration_state("Status: Registered")=="registered"
assert _parse_registration_state("Status: Rejected")=="rejected"
assert _parse_registration_state("No objects found.")=="missing"
assert active_channel_count()==0,"active calls"
db=SessionLocal()
try:
    real=int(db.execute(text("""SELECT count(*) FROM telephony_trunks WHERE provider='mcn'
        AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]')""")).scalar() or 0)
finally: db.close()
assert real==0,"real MCN trunks exist; refusing synthetic registration QA"
cleanup_db()
tid=None
try:
    open(MODE,"w").write("ok\n")
    made=trunk_upsert(A,{"provider":"mcn","name":"MCN QA registration state","auth_mode":"registration",
        "registrar":f"127.0.0.1:{PORT}","username":"qa-mcn-user","password":"qa-mcn-password",
        "source_ips":[],"codecs":["alaw","ulaw"],"enabled":True})
    assert made.get("status")=="ok",made
    tid=int(made["trunk"]["id"])
    g1=mcn_pjsip_guardian(include_synthetic=True)
    assert not g1.get("error_code"),g1
    reg=wait_state(tid,{"registered"},15)
    g2=mcn_pjsip_guardian(include_synthetic=True)
    db=SessionLocal()
    try:
        row=db.execute(text("SELECT status,last_health_status,last_error FROM telephony_trunks WHERE id=:i"),{"i":tid}).mappings().first()
    finally: db.close()
    assert row and row["status"]=="connected",row
    assert g2.get("trunks_verified",0)>=1,g2

    open(MODE,"w").write("reject\n")
    subprocess.run(["/opt/boris/asterisk-22/sbin/asterisk","-C","/etc/asterisk-boris/asterisk.conf",
                    "-rx",f"pjsip send unregister mcn-reg-{tid}"],check=False,capture_output=True,text=True,timeout=5)
    time.sleep(.4)
    subprocess.run(["/opt/boris/asterisk-22/sbin/asterisk","-C","/etc/asterisk-boris/asterisk.conf",
                    "-rx",f"pjsip send register mcn-reg-{tid}"],check=False,capture_output=True,text=True,timeout=5)
    rej=wait_state(tid,{"rejected"},10)
    assert rej.get("ready") is False,rej
    g3=mcn_pjsip_guardian(include_synthetic=True)
    db=SessionLocal()
    try:
        row2=db.execute(text("SELECT status,last_health_status,last_error FROM telephony_trunks WHERE id=:i"),{"i":tid}).mappings().first()
    finally: db.close()
    assert row2 and row2["status"]=="configured_unverified",row2
    assert row2["last_health_status"]=="registration_rejected",row2
    assert g3.get("trunks_degraded",0)>=1,g3
    print("MCN_REGISTRATION_STATE_RUNTIME_OK",{
        "registered":reg.get("registration_state"),
        "rejected":rej.get("registration_state"),
        "connected_status":row["status"],
        "rejected_status":row2["status"],
    })
finally:
    cleanup_db()
    clean=mcn_pjsip_guardian(include_synthetic=False)
    body=GENERATED_PJSIP.read_text("utf-8") if GENERATED_PJSIP.exists() else ""
    assert "qa-mcn-user" not in body and f"127.0.0.1:{PORT}" not in body
    print("MCN_REGISTRATION_QA_CLEANUP",{"status":clean.get("status"),"rendered":clean.get("rendered"),
        "action":clean.get("action"),"error_code":clean.get("error_code")})
