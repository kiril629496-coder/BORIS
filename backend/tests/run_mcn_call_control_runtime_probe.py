from __future__ import annotations
import os, sys, time
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
import requests
from app.services.asterisk_gateway import command_mcn

A="__qa_mcn_controls_v1"
CH="qa-mcn-controls-v1"
base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
try:
    requests.delete(base+f"/channels/{CH}",auth=auth,timeout=2)
except Exception:
    pass
r=requests.post(base+"/channels",params={
    "endpoint":"Local/999@boris-qa-loop",
    "app":"boris-phone",
    "appArgs":f"outbound,{A}",
    "channelId":CH,
    "timeout":10,
},auth=auth,timeout=5)
assert r.status_code in {200,201},(r.status_code,r.text[:300])
time.sleep(.4)

checks={}
for cmd,payload in [
    ("hold",{}),
    ("resume",{}),
    ("dtmf",{"digits":"12#"}),
]:
    out=command_mcn(A,CH,cmd,payload)
    checks[cmd]=out
    assert out.get("status")=="ok",(cmd,out)

bad=command_mcn(A,CH,"dtmf",{"digits":"12A"})
assert bad.get("status")=="invalid_dtmf",bad
checks["invalid_dtmf"]=bad

out=command_mcn(A,CH,"hangup",{})
checks["hangup"]=out
assert out.get("status")=="ok",out
time.sleep(.2)
missing=requests.get(base+f"/channels/{CH}",auth=auth,timeout=3)
assert missing.status_code==404,missing.status_code
print("MCN_CALL_CONTROL_RUNTIME_OK",checks)
