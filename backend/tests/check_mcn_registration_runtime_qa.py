from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
os.environ["BORIS_MCN_SIP_BIND"]="127.0.0.1:15061"
from app.services.asterisk_gateway import _registration_health, mcn_pjsip_guardian, mcn_account_transport_health
from app.services.mcn_core import trunk_list

A="__qa_mcn_registration_runtime_v1"
expected=sys.argv[1]
tid=int(Path("/tmp/boris_mcn_registration_tid").read_text().strip())
last=None
deadline=time.time()+10
while time.time()<deadline:
    last=_registration_health(tid)
    if last.get("registration_state")==expected:
        break
    time.sleep(.2)
assert last and last.get("registration_state")==expected,(expected,last)
print("MCN_REGISTRATION_STATE_RUNTIME",expected,last)

g=mcn_pjsip_guardian(include_synthetic=True)
print("MCN_REGISTRATION_GUARDIAN_RUNTIME",{"status":g.get("status"),"verified":g.get("trunks_verified"),"degraded":g.get("trunks_degraded"),"trunks":g.get("trunks")})
rows=trunk_list(A)
assert rows,rows
transport=mcn_account_transport_health(A)
print("MCN_REGISTRATION_TRANSPORT_RUNTIME",transport)
if expected=="registered":
    assert g.get("status")=="ok",g
    assert g.get("trunks_verified")==1,g
    assert rows[0]["status"]=="connected",rows[0]
    assert transport.get("ready") is True,transport
else:
    assert g.get("status")=="degraded",g
    assert g.get("trunks_degraded",0)>=1,g
    assert rows[0]["status"]=="configured_unverified",rows[0]
    assert transport.get("ready") is False,transport
print("MCN_REGISTRATION_EXPECTATION_OK",expected)
