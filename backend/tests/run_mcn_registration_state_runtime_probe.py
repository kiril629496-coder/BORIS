from __future__ import annotations
import socket
import subprocess
import threading
import time
import sys

sys.path.insert(0, "/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env", override=True)

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import ensure_schema, trunk_upsert
from app.services.asterisk_gateway import (
    active_channel_count,
    mcn_pjsip_guardian,
    _registration_health,
    _parse_registration_state,
    GENERATED_PJSIP,
)

ACCOUNT="__qa_mcn_regstate_v1"
PORT=5077

def db_cleanup():
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"), {"a":ACCOUNT})
        db.commit()
    finally:
        db.close()

class FakeRegistrar:
    def __init__(self):
        self.mode="ok"
        self.stop_evt=threading.Event()
        self.sock=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", PORT))
        self.sock.settimeout(0.2)
        self.register_count=0
        self.options_count=0
        self.thread=threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_evt.set()
        self.thread.join(timeout=2)
        self.sock.close()

    @staticmethod
    def headers(msg:str)->dict[str,str]:
        out={}
        for line in msg.replace("\r\n","\n").split("\n")[1:]:
            if ":" not in line:
                continue
            k,v=line.split(":",1)
            out[k.strip().lower()]=v.strip()
        return out

    def run(self):
        while not self.stop_evt.is_set():
            try:
                data,addr=self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            msg=data.decode("utf-8","replace")
            first=msg.splitlines()[0] if msg.splitlines() else ""
            h=self.headers(msg)
            if first.startswith("REGISTER "):
                self.register_count+=1
                code="200 OK" if self.mode=="ok" else "403 Forbidden"
                to=h.get("to","<sip:qa@localhost>")
                if "tag=" not in to:
                    to += ";tag=borisqa"
                lines=[
                    f"SIP/2.0 {code}",
                    f"Via: {h.get('via','SIP/2.0/UDP 127.0.0.1')}",
                    f"From: {h.get('from','<sip:qa@localhost>')}",
                    f"To: {to}",
                    f"Call-ID: {h.get('call-id','borisqa')}",
                    f"CSeq: {h.get('cseq','1 REGISTER')}",
                ]
                if h.get("contact"):
                    lines.append(f"Contact: {h['contact']}")
                lines += ["Server: BORIS-QA-Registrar/1.0", "Content-Length: 0", "", ""]
                self.sock.sendto("\r\n".join(lines).encode(), addr)
            elif first.startswith("OPTIONS "):
                self.options_count+=1
                to=h.get("to","<sip:qa@localhost>")
                if "tag=" not in to:
                    to += ";tag=borisqa"
                lines=[
                    "SIP/2.0 200 OK",
                    f"Via: {h.get('via','SIP/2.0/UDP 127.0.0.1')}",
                    f"From: {h.get('from','<sip:qa@localhost>')}",
                    f"To: {to}",
                    f"Call-ID: {h.get('call-id','borisqa')}",
                    f"CSeq: {h.get('cseq','1 OPTIONS')}",
                    "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REGISTER",
                    "Content-Length: 0", "", ""
                ]
                self.sock.sendto("\r\n".join(lines).encode(), addr)

def wait_state(tid:int, expected:set[str], timeout:float=12.0):
    """Wait for live registration and self-repair a synthetic-config race.

    Production telephony runtime intentionally renders with include_synthetic=False.
    If it runs during this QA window, it may remove the QA registration from the
    shared generated file. Detect that exact condition and restore the synthetic
    config automatically. Never mask a real registration error/rejection.
    """
    deadline=time.time()+timeout
    last={}
    repairs=0
    next_repair_at=0.0
    token=f"[mcn-reg-{int(tid)}]"
    while time.time()<deadline:
        last=_registration_health(tid)
        if str(last.get("registration_state")) in expected:
            if repairs:
                last={**last,"qa_config_repairs":repairs}
            return last
        now=time.time()
        if str(last.get("registration_state"))=="missing" and now>=next_repair_at and repairs<3:
            body=GENERATED_PJSIP.read_text("utf-8") if GENERATED_PJSIP.exists() else ""
            if token not in body:
                heal=mcn_pjsip_guardian(include_synthetic=True)
                assert not heal.get("error_code"),("qa_config_self_heal_failed",heal)
                repairs+=1
                next_repair_at=now+1.0
        time.sleep(0.2)
    raise AssertionError(("registration state timeout", expected, last, {"qa_config_repairs":repairs}))

ensure_schema()
assert _parse_registration_state("Status: Registered")=="registered"
assert _parse_registration_state("Status: Rejected")=="rejected"
assert _parse_registration_state("No objects found.")=="missing"
assert active_channel_count()==0, "refuse QA while calls are active"

db=SessionLocal()
try:
    real=int(db.execute(text("""
      SELECT count(*) FROM telephony_trunks
      WHERE provider='mcn'
        AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]')
    """)).scalar() or 0)
finally:
    db.close()
assert real==0, "refuse synthetic registration QA while real MCN trunks exist"

# Production telephony runtime runs mcn_pjsip_guardian() every minute and
# intentionally excludes synthetic trunks. Without isolation it can erase the
# generated QA registration between render and verification. Synthetic runtime
# QA is therefore allowed only at the already-proven zero-call/zero-real-MCN
# boundary, and the background runner is always restored in finally.
runtime_was_active = subprocess.run(
    ["systemctl","is-active","--quiet","boris-telephony-runtime.service"],
    check=False,
).returncode == 0
if runtime_was_active:
    stopped = subprocess.run(
        ["systemctl","stop","boris-telephony-runtime.service"],
        check=False, capture_output=True, text=True, timeout=20,
    )
    assert stopped.returncode == 0, ("telephony runtime stop failed", stopped.stderr[-300:])
    assert subprocess.run(
        ["systemctl","is-active","--quiet","boris-telephony-runtime.service"],
        check=False,
    ).returncode != 0, "telephony runtime still active during isolated MCN QA"

db_cleanup()
server=FakeRegistrar()
server.start()
tid=None
try:
    made=trunk_upsert(ACCOUNT,{
        "provider":"mcn",
        "name":"MCN QA registration state",
        "auth_mode":"registration",
        "registrar":f"127.0.0.1:{PORT}",
        "username":"qa-mcn-user",
        "password":"qa-mcn-password",
        "source_ips":[],
        "codecs":["alaw","ulaw"],
        "enabled":True,
    })
    assert made.get("status")=="ok",made
    tid=int(made["trunk"]["id"])

    first=mcn_pjsip_guardian(include_synthetic=True)
    assert first.get("error_code") in {None,""},first
    registered=wait_state(tid,{"registered"})
    assert registered.get("ready") is True,registered

    second=mcn_pjsip_guardian(include_synthetic=True)
    row=None
    db=SessionLocal()
    try:
        row=db.execute(text("SELECT status,last_health_status,last_error FROM telephony_trunks WHERE id=:i"),{"i":tid}).mappings().first()
    finally:
        db.close()
    assert row and row["status"]=="connected",row
    assert second.get("trunks_verified",0)>=1,second

    # Prove the strict checker does not confuse endpoint reachability with REGISTER success.
    server.mode="reject"
    import subprocess
    subprocess.run([
        "/opt/boris/asterisk-22/sbin/asterisk","-C","/etc/asterisk-boris/asterisk.conf",
        "-rx",f"pjsip send unregister mcn-reg-{tid}"
    ],check=False,capture_output=True,text=True,timeout=5)
    time.sleep(.3)
    subprocess.run([
        "/opt/boris/asterisk-22/sbin/asterisk","-C","/etc/asterisk-boris/asterisk.conf",
        "-rx",f"pjsip send register mcn-reg-{tid}"
    ],check=False,capture_output=True,text=True,timeout=5)
    rejected=wait_state(tid,{"rejected"},timeout=8.0)
    assert rejected.get("ready") is False,rejected

    third=mcn_pjsip_guardian(include_synthetic=True)
    db=SessionLocal()
    try:
        row2=db.execute(text("SELECT status,last_health_status,last_error FROM telephony_trunks WHERE id=:i"),{"i":tid}).mappings().first()
    finally:
        db.close()
    assert row2 and row2["status"]=="configured_unverified",row2
    assert str(row2["last_health_status"])=="registration_rejected",row2
    assert third.get("trunks_degraded",0)>=1,third

    print("MCN_REGISTRATION_STATE_RUNTIME_OK",{
        "trunk_id":tid,
        "registered":registered.get("registration_state"),
        "rejected":rejected.get("registration_state"),
        "register_requests":server.register_count,
        "options_requests":server.options_count,
    })
finally:
    db_cleanup()
    try:
        cleanup=mcn_pjsip_guardian(include_synthetic=False)
        print("MCN_REGISTRATION_QA_CLEANUP",{
            "status":cleanup.get("status"),
            "rendered":cleanup.get("rendered"),
            "action":cleanup.get("action"),
            "error_code":cleanup.get("error_code"),
        })
    finally:
        server.close()
    body=GENERATED_PJSIP.read_text("utf-8") if GENERATED_PJSIP.exists() else ""
    assert "qa-mcn-user" not in body and f"127.0.0.1:{PORT}" not in body
    if runtime_was_active:
        started = subprocess.run(
            ["systemctl","start","boris-telephony-runtime.service"],
            check=False, capture_output=True, text=True, timeout=20,
        )
        assert started.returncode == 0, ("telephony runtime restore failed", started.stderr[-300:])
        deadline=time.time()+15
        while time.time()<deadline:
            if subprocess.run(
                ["systemctl","is-active","--quiet","boris-telephony-runtime.service"],
                check=False,
            ).returncode == 0:
                break
            time.sleep(.2)
        else:
            raise AssertionError("telephony runtime not active after MCN QA restore")
        print("MCN_REGISTRATION_RUNTIME_SERVICE_RESTORED", "active")
