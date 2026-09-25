from __future__ import annotations
import sys
sys.path.insert(0,"/root/BORIS/backend")
from app.services.telephony_adapters.mcn import MCNAdapter
import app.services.asterisk_gateway as ag

adapter=MCNAdapter()
orig_originate=ag.originate_mcn
orig_command=ag.command_mcn
try:
    ag.originate_mcn=lambda *a,**k:{
        "status":"mcn_trunk_not_connected",
        "live_status":"registration_rejected",
        "registration_state":"rejected",
        "self_heal":None,
        "owner_action_required":True,
    }
    r=adapter.make_call(
        to_number="+74950000000",from_number="+74950000001",
        credentials={"account_id":"acc"},public_config={"account_id":"acc"},
        metadata={"account_id":"acc","idempotency_key":"qa-1"},
    )
    assert r.ok is False and r.status=="mcn_trunk_not_connected",r
    assert r.payload.get("registration_state")=="rejected",r.payload
    assert r.payload.get("owner_action_required") is True,r.payload

    ag.command_mcn=lambda *a,**k:{
        "status":"mcn_trunk_not_connected",
        "live_status":"registration_unregistered",
        "registration_state":"unregistered",
        "self_heal":"registration_retry_sent",
        "owner_action_required":False,
    }
    c=adapter.command(
        provider_call_id="qa-provider-call-123",
        command="transfer",
        payload={"account_id":"acc","to_number":"+74950000002"},
        credentials={"account_id":"acc"},
        public_config={"account_id":"acc"},
    )
    assert c.ok is False and c.status=="mcn_trunk_not_connected",c
    assert c.payload.get("self_heal")=="registration_retry_sent",c.payload
    assert c.payload.get("owner_action_required") is False,c.payload
    print("MCN_ADAPTER_FAILURE_PROPAGATION_OK")
finally:
    ag.originate_mcn=orig_originate
    ag.command_mcn=orig_command
