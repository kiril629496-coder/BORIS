"""Deterministic provider-free QA simulator. Never registered in production provider catalog."""
from __future__ import annotations
from uuid import uuid4
from app.services.telephony_core import apply_event

def simulate_incoming(account_id:str, from_number:str='+70000000000', to_number:str='+79990000000'):
    pid='sim-'+uuid4().hex
    eid='sim-'+uuid4().hex
    return apply_event(account_id,'call.ringing',None,'sip',pid,eid,{'direction':'inbound','from_number':from_number,'to_number':to_number,'source':'qa_simulator'})

def simulate_answer(account_id:str, call_id:str):
    return apply_event(account_id,'call.answered',call_id,'sip',None,'sim-'+uuid4().hex,{})

def simulate_end(account_id:str, call_id:str):
    return apply_event(account_id,'call.ended',call_id,'sip',None,'sim-'+uuid4().hex,{})
