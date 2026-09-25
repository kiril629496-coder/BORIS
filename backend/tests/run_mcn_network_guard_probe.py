from __future__ import annotations
import sys
sys.path.insert(0,"/root/BORIS/backend")
from dotenv import load_dotenv
load_dotenv("/root/BORIS/backend/.env",override=True)

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mcn_core import ensure_schema,trunk_upsert
from app.services.mcn_network_guard import (
    firewall_plan, desired_mcn_networks, sync_mcn_firewall, _sip_port, _rtp_range
)

A="__qa_mcn_network_guard_v1"

def cleanup():
    db=SessionLocal()
    try:
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id=:a"),{"a":A})
        db.commit()
    finally:
        db.close()

ensure_schema()
cleanup()
try:
    assert _sip_port()==5064,_sip_port()
    assert _rtp_range()==(10000,10999),_rtp_range()

    add=firewall_plan(["203.0.113.0/24"],[],5064,10000,10999)
    assert len(add)==2,add
    assert {x["kind"] for x in add}=={"sip","rtp"},add
    assert all(x["action"]=="allow" for x in add),add
    assert all(isinstance(x["argv"],list) for x in add),add

    delete=firewall_plan([],["203.0.113.0/24"],5064,10000,10999)
    assert len(delete)==2,delete
    assert all(x["action"]=="delete" for x in delete),delete

    made=trunk_upsert(A,{
        "provider":"mcn","name":"MCN network guard QA","auth_mode":"ip",
        "registrar":"sip.example.test","source_ips":["203.0.113.0/24"],
        "enabled":True,
    })
    assert made.get("status")=="ok",made
    assert "203.0.113.0/24" not in desired_mcn_networks(False),desired_mcn_networks(False)
    assert "203.0.113.0/24" in desired_mcn_networks(True),desired_mcn_networks(True)

    dry=sync_mcn_firewall(dry_run=True)
    assert dry.get("status")=="dry_run",dry
    assert "203.0.113.0/24" not in (dry.get("desired_networks") or []),dry
    print("MCN_NETWORK_GUARD_OK",{
        "sip_port":_sip_port(),
        "rtp_range":_rtp_range(),
        "synthetic_excluded":True,
        "add_rules":len(add),
        "delete_rules":len(delete),
    })
finally:
    cleanup()
