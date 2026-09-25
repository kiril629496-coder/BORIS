import json, os, sys
from pathlib import Path
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from app.services.phone_media_gateway import provision_webrtc_endpoint, delete_webrtc_endpoint

eid="bp_qa_node_register"
try:
    delete_webrtc_endpoint(eid)
except Exception:
    pass
r=provision_webrtc_endpoint(eid,120)
assert r.get("status")=="ok",r
p=Path("/tmp/boris_media_register_qa.json")
p.write_text(json.dumps(r["media"],ensure_ascii=False))
os.chmod(p,0o600)
print("MEDIA_QA_FILE_READY")
