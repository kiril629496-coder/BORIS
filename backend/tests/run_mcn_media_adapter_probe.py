import sys
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)
from app.services.telephony_adapters.mcn import MCNAdapter
from app.services.telephony_core import _validate_media_session_payload
from app.services.phone_media_gateway import delete_webrtc_endpoint,gateway_health

print("GATEWAY_READY",gateway_health().get("runtime_ready"))
adapter=MCNAdapter()
res=adapter.media_session(
    device_id="dev_qa_media",
    call_id="call_qa_media",
    credentials={},
    public_config={"account_id":"__qa_media_account"},
)
print("MCN_MEDIA_ADAPTER",res.ok,res.status,(res.payload or {}).get("transport"))
assert res.ok
ok,reason=_validate_media_session_payload(res.payload)
print("MEDIA_CONTRACT",ok,reason)
assert ok
eid=(res.payload or {}).get("endpoint_id")
assert eid
print("MEDIA_CLEANUP",delete_webrtc_endpoint(eid).get("status"))
