# -*- coding: utf-8 -*-
"""DB-backed employee isolation contracts for BORIS Phone API controls."""
import unittest, uuid
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import telephony_core as T
from app.api import telephony as API

class TelephonyPublicErrorRedaction(unittest.TestCase):
    def test_recording_link_failure_never_exposes_provider_error_text(self):
        secret='Authorization: Bearer SUPER-SECRET-RECORDING-TOKEN'
        out=API._safe_recording_link_failure(SimpleNamespace(status='provider rejected / 401',error=secret))
        self.assertFalse(out['recording'])
        self.assertEqual(out['status'],'provider_rejected_401')
        self.assertNotIn(secret,str(out))
        self.assertNotIn('SUPER-SECRET',str(out))

class TelephonyWebhookParseRedaction(unittest.TestCase):
    def test_uis_adapter_valueerror_does_not_escape_raw_text(self):
        import asyncio, json
        secret='WEBHOOK-PARSE-SECRET-TOKEN'
        class Req:
            headers={'x-boris-provider-secret':'qa-shared-secret'}
            async def body(self): return json.dumps({'event':'qa'}).encode()
        class Adapter:
            def normalize_webhook(self,*args,**kwargs):
                raise ValueError('Authorization: Bearer '+secret)
        async def run():
            with patch.object(API,'provider_webhook_secret',return_value=('uis','qa-shared-secret')), \
                 patch.object(API,'claim_signed_native_webhook',return_value='claimed'), \
                 patch('app.services.telephony_adapters.get_adapter',return_value=Adapter()):
                with self.assertRaises(HTTPException) as ctx:
                    await API.uis_native_webhook('__qa_tel_webhook_redact',Req())
                self.assertEqual(ctx.exception.status_code,400)
                self.assertEqual(ctx.exception.detail,'invalid provider webhook payload')
                self.assertNotIn(secret,str(ctx.exception.detail))
        asyncio.run(run())

class TelephonyProviderConfigPatchSemantics(unittest.TestCase):
    def _call(self, body):
        user=SimpleNamespace(id=77,role='owner')
        with patch.object(API,'_assert_telephony_account_access',return_value=None), \
             patch.object(API,'_assert_account_owner',return_value=None), \
             patch.object(API,'_require_active_phone_entitlement',return_value={'status':'active','active':True}), \
             patch.object(API,'_actor_user_id',return_value=77), \
             patch.object(API,'save_provider_config',return_value={'status':'ok'}) as save:
            out=API.set_provider_config(body,user)
        self.assertEqual(out['status'],'ok')
        return save

    def test_unpaid_phone_blocks_provider_save_before_storage(self):
        user=SimpleNamespace(id=77,role='owner')
        with patch.object(API,'_assert_telephony_account_access',return_value=None), \
             patch.object(API,'_assert_account_owner',return_value=None), \
             patch.object(API,'phone_entitlement_status',return_value={'status':'not_entitled','active':False}), \
             patch.object(API,'save_provider_config') as save:
            with self.assertRaises(HTTPException) as ctx:
                API.set_provider_config({'account_id':'real-unpaid','provider':'mcn'},user)
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(ctx.exception.detail['code'],'phone_entitlement_required')
        save.assert_not_called()

    def test_unpaid_phone_blocks_provider_verify_before_external_probe(self):
        user=SimpleNamespace(id=77,role='owner')
        with patch.object(API,'_assert_telephony_account_access',return_value=None), \
             patch.object(API,'_assert_account_owner',return_value=None), \
             patch.object(API,'phone_entitlement_status',return_value={'status':'expired','active':False}), \
             patch.object(API,'verify_provider_connection') as verify:
            with self.assertRaises(HTTPException) as ctx:
                API.verify_provider_config({'account_id':'real-expired'},user)
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(ctx.exception.detail['code'],'phone_entitlement_required')
        verify.assert_not_called()

    def test_missing_public_config_means_preserve_existing(self):
        save=self._call({'account_id':'acc','provider':'telphin','credentials':{'sip_password':'new'}})
        args=save.call_args.args
        self.assertIsNone(args[3])

    def test_explicit_empty_public_config_means_clear_public_fields(self):
        save=self._call({'account_id':'acc','provider':'telphin','public_config':{}})
        args=save.call_args.args
        self.assertEqual(args[3],{})


class TelephonyApiAccessIsolation(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_tel_access_'+uuid.uuid4().hex[:12]
        self.u1=910001; self.u2=910002
        self.d1='dev_qa_'+uuid.uuid4().hex
        self.d2='dev_qa_'+uuid.uuid4().hex
        self.c1='call_qa_'+uuid.uuid4().hex
        self.c2='call_qa_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            for d,u in ((self.d1,self.u1),(self.d2,self.u2)):
                db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,:u,'QA','web','online',now())"),{'d':d,'a':self.account,'u':u})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,direction,state,from_number,to_number,user_id,device_id) VALUES(:c,:a,'qa','inbound','active','+70000000001','+70000000002',:u,:d)"),{'c':self.c1,'a':self.account,'u':self.u2,'d':self.d2})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,direction,state,from_number,to_number) VALUES(:c,:a,'qa','inbound','ringing','+70000000001','+70000000002')"),{'c':self.c2,'a':self.account})
            db.execute(text("INSERT INTO telephony_call_targets(account_id,call_id,device_id,status,expires_at) VALUES(:a,:c,:d,'ringing',now()+interval '1 minute')"),{'a':self.account,'c':self.c2,'d':self.d1})
            db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_events','telephony_audit','telephony_call_targets','telephony_commands','telephony_push_outbox','telephony_recordings','telephony_calls','telephony_devices'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_sse_periodic_access_revalidation_closes_revoked_stream(self):
        user=SimpleNamespace(id=self.u1,role='manager')
        with patch.object(API,'_assert_telephony_account_access',return_value=None):
            self.assertTrue(API._stream_access_still_allowed(self.account,user))
        with patch.object(API,'_assert_telephony_account_access',side_effect=HTTPException(403,'revoked')):
            self.assertFalse(API._stream_access_still_allowed(self.account,user))

    def test_sse_revalidates_access_before_yielding_new_event_batch(self):
        import asyncio
        user=SimpleNamespace(id=self.u1,role='manager')
        class Req:
            headers={}
            async def is_disconnected(self): return False
        async def run():
            with patch.object(API,'_assert_telephony_account_access',return_value=None), \
                 patch.object(API,'event_feed',return_value=[{'id':1,'call_id':'secret-call','event_type':'call.ringing'}]), \
                 patch.object(API,'_stream_access_still_allowed',return_value=False):
                response=await API.events_stream(Req(),self.account,0,user)
                it=response.body_iterator.__aiter__()
                first=await it.__anext__()
                self.assertIn('retry: 2000',str(first))
                with self.assertRaises(StopAsyncIteration):
                    await it.__anext__()
        asyncio.run(run())

    def test_manager_cannot_control_other_manager_call_without_device(self):
        with self.assertRaises(HTTPException) as ctx:
            API._assert_call_control_access(self.account,self.c1,SimpleNamespace(id=self.u1,role='manager'),None)
        self.assertEqual(ctx.exception.status_code,403)
    def test_manager_cannot_use_own_unrelated_device_for_other_call(self):
        with self.assertRaises(HTTPException) as ctx:
            API._assert_call_control_access(self.account,self.c1,SimpleNamespace(id=self.u1,role='manager'),self.d1)
        self.assertEqual(ctx.exception.status_code,403)
    def test_bound_manager_can_control_own_call(self):
        API._assert_call_control_access(self.account,self.c1,SimpleNamespace(id=self.u2,role='manager'),None)
    def test_targeted_manager_device_can_control_ringing_call(self):
        API._assert_call_control_access(self.account,self.c2,SimpleNamespace(id=self.u1,role='manager'),self.d1)
    def test_owner_fleet_control_remains_allowed(self):
        API._assert_call_control_access(self.account,self.c1,SimpleNamespace(id=999999,role='owner'),None)


    def test_disposition_guard_denies_other_manager_call(self):
        with self.assertRaises(HTTPException) as ctx:
            API._assert_call_control_access(self.account,self.c1,SimpleNamespace(id=self.u1,role='manager'),None)
        self.assertEqual(ctx.exception.status_code,403)

    def test_disposition_guard_allows_assigned_manager(self):
        API._assert_call_control_access(self.account,self.c1,SimpleNamespace(id=self.u2,role='manager'),None)

if __name__=='__main__': unittest.main()
