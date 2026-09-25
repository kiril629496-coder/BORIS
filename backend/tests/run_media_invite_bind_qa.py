import os, sys, time
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
import requests
from app.services.asterisk_gateway import bind_browser_media, cleanup_media_bridge
from app.services.phone_media_gateway import delete_webrtc_endpoint

base=str(os.getenv("BORIS_ASTERISK_ARI_URL") or "http://127.0.0.1:8088/ari").rstrip("/")
auth=(str(os.getenv("BORIS_ASTERISK_ARI_USER") or ""),str(os.getenv("BORIS_ASTERISK_ARI_PASSWORD") or ""))
channel_id="qa-media-carrier-v1"
lease_id=990001
eid="bp_qa_public_invite"
try:
    requests.delete(base+f"/channels/{channel_id}",auth=auth,timeout=2)
except Exception:
    pass
r=requests.post(base+"/channels",params={
    "endpoint":"Local/999@boris-qa-loop",
    "app":"boris-phone",
    "appArgs":"outbound,__qa_media_sig",
    "channelId":channel_id,
    "timeout":10,
},auth=auth,timeout=5)
assert r.status_code in {200,201},(r.status_code,r.text[:300])
time.sleep(0.5)
out=bind_browser_media("__qa_media_sig","qa-call-media",channel_id,eid,lease_id)
print("MEDIA_BIND_RESULT",out)
assert out.get("status") in {"activating","ok"},out
time.sleep(2.0)
print("MEDIA_BRIDGE_CLEANUP",cleanup_media_bridge(lease_id))
try:
    requests.delete(base+f"/channels/{channel_id}",auth=auth,timeout=3)
except Exception:
    pass
