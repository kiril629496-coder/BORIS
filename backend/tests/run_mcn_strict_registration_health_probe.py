from __future__ import annotations
import sys
sys.path.insert(0,'/root/BORIS/backend')
from dotenv import load_dotenv
load_dotenv('/root/BORIS/backend/.env',override=True)

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import asterisk_gateway as ag
from app.services.mcn_core import ensure_schema, trunk_upsert, did_upsert, readiness, status as mcn_status
from app.services.telephony_adapters.mcn import MCNAdapter

A='__qa_mcn_strict_reg_health_v1'

def cleanup():
    db=SessionLocal()
    try:
        db.execute(text('DELETE FROM telephony_dids WHERE account_id=:a'),{'a':A})
        db.execute(text('DELETE FROM telephony_trunks WHERE account_id=:a'),{'a':A})
        db.commit()
    finally:
        db.close()

def row(tid:int):
    db=SessionLocal()
    try:
        return db.execute(text('SELECT status,last_health_status,last_error FROM telephony_trunks WHERE id=:i'),{'i':tid}).mappings().first()
    finally:
        db.close()

# Installed Asterisk module emits a Status field for outbound registrations.
assert ag._parse_registration_state('Status: Registered\n')=='registered'
assert ag._parse_registration_state('Status: Rejected\n')=='rejected'
assert ag._parse_registration_state('Status: Unregistered\n')=='unregistered'
assert ag._parse_registration_state('Status: Stopped\n')=='stopped'
assert ag._parse_registration_state('No objects found.\n')=='missing'

class CP:
    def __init__(self,out:str,rc:int=0):
        self.stdout=out
        self.stderr=''
        self.returncode=rc

orig_run=ag.subprocess.run
try:
    ag.subprocess.run=lambda *a,**k: CP('Status: Registered\n')
    x=ag._registration_health(991)
    assert x['ready'] is True and x['registration_state']=='registered',x
    ag.subprocess.run=lambda *a,**k: CP('Status: Rejected\n')
    x=ag._registration_health(991)
    assert x['ready'] is False and x['status']=='registration_rejected',x
    ag.subprocess.run=lambda *a,**k: CP('No objects found.\n')
    x=ag._registration_health(991)
    assert x['ready'] is False and x['registration_state']=='missing',x
finally:
    ag.subprocess.run=orig_run

ensure_schema()
cleanup()
try:
    made=trunk_upsert(A,{'provider':'mcn','name':'MCN strict registration probe','auth_mode':'registration','registrar':'sip.example.test','username':'qa-user','password':'qa-pass','source_ips':[],'codecs':['alaw','ulaw'],'enabled':True})
    assert made.get('status')=='ok',made
    tid=int(made['trunk']['id'])
    did=did_upsert(A,{'provider':'mcn','number':'+74950009991','trunk_id':tid,'purpose':'main','status':'active','inbound_enabled':True,'outbound_cli_enabled':True})
    assert did.get('status')=='ok',did
    original=ag._registration_health
    try:
        ag._registration_health=lambda _tid:{'ready':True,'status':'registration_registered','registration_state':'registered'}
        ok=ag._verify_mcn_trunk(A,tid,'configured_unverified')
        r1=row(tid)
        assert ok['ready'] is True,ok
        assert r1 and r1['status']=='connected' and r1['last_health_status']=='registration_registered',r1
        ready1=readiness(A)
        assert ready1.get('ready') is True and ready1.get('connected_trunks')==1,ready1
        status1=mcn_status(A)
        assert status1.get('connected_trunks')==1,status1
        ag._registration_health=lambda _tid:{'ready':False,'status':'registration_rejected','registration_state':'rejected'}
        bad=ag._verify_mcn_trunk(A,tid,'connected')
        r2=row(tid)
        assert bad['ready'] is False,bad
        assert r2 and r2['status']=='configured_unverified' and r2['last_health_status']=='registration_rejected',r2
        ready2=readiness(A)
        assert ready2.get('ready') is False and 'mcn_trunk_not_verified' in (ready2.get('blockers') or []),ready2
        status2=mcn_status(A)
        assert status2.get('connected_trunks')==0,status2
        assert bad.get('self_heal') is None,bad

        retry_calls=[]
        original_retry=ag._registration_retry
        ag._registration_health=lambda _tid:{'ready':False,'status':'registration_unregistered','registration_state':'unregistered'}
        ag._registration_retry=lambda _tid:(retry_calls.append(_tid) or {'status':'registration_retry_sent'})
        retry=ag._verify_mcn_trunk(A,tid,'configured_unverified')
        assert retry.get('ready') is False,retry
        assert retry.get('self_heal')=='registration_retry_sent',retry
        assert retry_calls==[tid],retry_calls
        ag._registration_retry=original_retry

        # Pre-call gate must fail closed if DB still says connected but live REGISTER is rejected.
        ag._registration_health=lambda _tid:{'ready':True,'status':'registration_registered','registration_state':'registered'}
        ag._verify_mcn_trunk(A,tid,'configured_unverified')
        original_live=ag._mcn_trunk_live_health
        ag._mcn_trunk_live_health=lambda _cfg:{'ready':False,'status':'registration_rejected','registration_state':'rejected','auth_mode':'registration'}
        gate=ag._live_trunk_gate(A,ag.mcn_core.trunk_credentials(A,tid))
        r3=row(tid)
        assert gate.get('ready') is False and gate.get('registration_state')=='rejected',gate
        assert r3 and r3['status']=='configured_unverified' and r3['last_health_status']=='registration_rejected',r3

        # Explicit provider health must heal stale DB immediately from live REGISTER.
        original_ah=ag.asterisk_health
        original_app=ag.application_health
        ag.asterisk_health=lambda:{'ready':True,'version':'Asterisk QA'}
        ag.application_health=lambda:{'ready':True}
        ag._mcn_trunk_live_health=lambda _cfg:{'ready':True,'status':'registration_registered','registration_state':'registered','auth_mode':'registration'}
        provider_ok=MCNAdapter().health({'account_id':A},{'account_id':A})
        r4=row(tid)
        assert provider_ok.ok is True and provider_ok.status=='ok',provider_ok
        assert r4 and r4['status']=='connected' and r4['last_health_status']=='registration_registered',r4

        ag._mcn_trunk_live_health=lambda _cfg:{'ready':False,'status':'registration_rejected','registration_state':'rejected','auth_mode':'registration'}
        provider_bad=MCNAdapter().health({'account_id':A},{'account_id':A})
        r5=row(tid)
        assert provider_bad.ok is False and provider_bad.status=='mcn_transport_not_ready',provider_bad
        assert r5 and r5['status']=='configured_unverified' and r5['last_health_status']=='registration_rejected',r5
        assert provider_bad.payload.get('mcn_transport',{}).get('owner_action_required') is True,provider_bad.payload
        ag.asterisk_health=original_ah
        ag.application_health=original_app
        ag._mcn_trunk_live_health=original_live
    finally:
        ag._registration_health=original
        if 'original_retry' in locals(): ag._registration_retry=original_retry
        if 'original_live' in locals(): ag._mcn_trunk_live_health=original_live
        if 'original_ah' in locals(): ag.asterisk_health=original_ah
        if 'original_app' in locals(): ag.application_health=original_app
    print('MCN_STRICT_REGISTRATION_HEALTH_OK',{'registered_db_status':r1['status'],'rejected_db_status':r2['status'],'rejected_health':r2['last_health_status'],'unregistered_self_heal':'registration_retry_sent','pre_call_fail_closed':True})
finally:
    cleanup()