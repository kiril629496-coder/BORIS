from __future__ import annotations
import os, sys
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import ensure_schema, trunk_upsert
from app.services.asterisk_gateway import active_channel_count, render_mcn_pjsip_config

A="__qa_mcn_registration_runtime_v1"
ensure_schema()
assert active_channel_count()==0, "active channels present"
db=SessionLocal()
try:
    real=int(db.execute(text("""SELECT count(*) FROM telephony_trunks
      WHERE provider='mcn' AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]')""")).scalar() or 0)
    assert real==0, f"real MCN trunks present: {real}"
    db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A});db.commit()
finally: db.close()
r=trunk_upsert(A,{"provider":"mcn","name":"MCN QA registration runtime","auth_mode":"registration",
    "registrar":"127.0.0.1:15060","username":"qa-user","password":"qa-pass",
    "source_ips":[],"codecs":["alaw","ulaw"],"enabled":True})
assert r.get("status")=="ok",r
tid=int(r["trunk"]["id"])
os.environ["BORIS_MCN_SIP_BIND"]="127.0.0.1:15061"
out=render_mcn_pjsip_config(include_synthetic=True)
assert out.get("trunks_rendered")==1,out
Path("/tmp/boris_mcn_registration_tid").write_text(str(tid))
print("MCN_REGISTRATION_RUNTIME_PREPARED",{"trunk_id":tid,"rendered":out.get("trunks_rendered")})
