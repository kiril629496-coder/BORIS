# -*- coding: utf-8 -*-
"""Isolated DB integration contracts for BORIS Phone.

Uses synthetic account/call/device ids and always cleans them up. Provider/network,
push, CRM and billing side effects are patched out. Stdlib unittest only.
"""
import unittest, uuid, sys, types
from unittest.mock import patch
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import telephony_core as T
from app.services.telephony_adapters.base import AdapterResult

class FakeAdapter:
    key='qa'
    capabilities={'commands'}
    def __init__(self, result, retry_safe=False): self.result=result; self.retry_safe_commands={'mute'} if retry_safe else set(); self.calls=0
    def command(self, **kwargs): self.calls+=1; return self.result

class TelephonyDBIntegration(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_tel_'+uuid.uuid4().hex[:16]
        self.ids=[]
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_events','telephony_audit','telephony_call_quality','telephony_transcript_chunks','telephony_call_targets','telephony_commands','telephony_push_outbox','telephony_recordings','telephony_calls','telephony_devices','telephony_provider_configs'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def _call(self, state='new', provider='qa', provider_call_id=None):
        cid='call_qa_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state,from_number,to_number) VALUES(:c,:a,:p,:pc,'inbound',:s,'+70000000001','+70000000002')"),{'c':cid,'a':self.account,'p':provider,'pc':provider_call_id,'s':state}); db.commit()
        finally: db.close()
        return cid
    def test_create_call_redacts_provider_metadata_before_persistence(self):
        secret='CALL-METADATA-SUPER-SECRET'
        cid=T.create_call(self.account,'inbound','+70000000001','+70000000002','qa','provider_'+uuid.uuid4().hex,metadata={
            'authorization':'Bearer '+secret,
            'nested':{'access_token':secret,'message':'Authorization: Bearer '+secret},
            'safe':'ok',
        })['id']
        db=SessionLocal()
        try:
            import json as _json
            meta=db.execute(text("SELECT metadata_json FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':self.account,'c':cid}).scalar()
        finally: db.close()
        dumped=_json.dumps(meta,ensure_ascii=False)
        self.assertNotIn(secret,dumped)
        self.assertEqual(meta['authorization'],'[REDACTED]')
        self.assertEqual(meta['nested']['access_token'],'[REDACTED]')
        self.assertEqual(meta['safe'],'ok')

    def test_provider_status_does_not_count_explicit_offline_device_as_online(self):
        device='dev_qa_'+uuid.uuid4().hex[:16]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,1,'QA','web','offline',now())"),{'d':device,'a':self.account})
            db.commit()
        finally:
            db.close()
        with patch.object(T,'selected_provider',return_value=None):
            status=T.provider_status(self.account)
        self.assertEqual(status.get('online_devices'),0)

    def test_no_manual_route_is_required_for_inbound_call(self):
        device='dev_qa_default_route_'+uuid.uuid4().hex[:12]
        registered=T.register_device(
            self.account,1,'QA Web Phone','web','1.0',
            {'calls':True,'media':True},device
        )
        self.assertEqual(registered.get('status'),'ok')
        db=SessionLocal()
        try:
            routes=int(db.execute(
                text("SELECT count(*) FROM telephony_routes WHERE account_id=:a"),
                {'a':self.account}
            ).scalar() or 0)
        finally:
            db.close()
        self.assertEqual(routes,0)

        call_id=self._call(state='ringing')
        out=T.prepare_call_targets(self.account,call_id)
        self.assertEqual(out.get('status'),'ok')
        self.assertEqual((out.get('route') or {}).get('destination_kind'),'device')
        self.assertEqual((out.get('route') or {}).get('destination_value'),'all_online')
        self.assertEqual((out.get('route') or {}).get('strategy'),'ring_all')
        self.assertEqual(int((out.get('route') or {}).get('ring_timeout_sec') or 0),25)
        target_ids={str(x.get('device_id') or '') for x in (out.get('targets') or [])}
        self.assertIn(device,target_ids)

    def test_provider_health_payload_recursively_redacts_nested_secrets(self):
        import json as _json
        from app.crypto_utils import encrypt_secret
        class _HealthAdapter:
            def health(self, credentials, public_config):
                return AdapterResult(True,'ok',payload={'region':'ru','message':'echo credential-secret','meta':{'access_token':'payload-secret','nested':[{'client_secret':'deep-secret','ok':1}]}})
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,credentials_enc,public_config_json,status) VALUES(:a,'uis',:e,'{}'::jsonb,'configured_unverified') ON CONFLICT (account_id) DO UPDATE SET provider='uis',credentials_enc=:e,status='configured_unverified'"),{'a':self.account,'e':encrypt_secret(_json.dumps({'access_token':'credential-secret'}))})
            db.commit()
        finally: db.close()
        with patch('app.services.telephony_adapters.get_adapter',return_value=_HealthAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True,'capabilities':['api']}):
            out=T.verify_provider_connection(self.account)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['health']['meta']['access_token'],'[REDACTED]')
        self.assertNotIn('credential-secret',out['health']['message'])
        self.assertEqual(out['health']['meta']['nested'][0]['client_secret'],'[REDACTED]')
        db=SessionLocal()
        try:
            audit=db.execute(text("SELECT metadata_json FROM telephony_audit WHERE account_id=:a AND action='provider.verify' ORDER BY id DESC LIMIT 1"),{'a':self.account}).scalar()
        finally: db.close()
        self.assertNotIn('payload-secret',_json.dumps(audit))
        self.assertNotIn('deep-secret',_json.dumps(audit))

    def test_provider_health_status_is_normalized_before_persistence_and_output(self):
        import json as _json
        from app.crypto_utils import encrypt_secret
        secret='HEALTH-STATUS-SUPER-SECRET'
        class _HealthAdapter:
            def health(self, credentials, public_config):
                return AdapterResult(False,'bad status Authorization: Bearer '+secret,error='denied')
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,credentials_enc,public_config_json,status) VALUES(:a,'uis',:e,'{}'::jsonb,'configured_unverified') ON CONFLICT (account_id) DO UPDATE SET provider='uis',credentials_enc=:e,status='configured_unverified'"),{'a':self.account,'e':encrypt_secret(_json.dumps({'access_token':'credential-secret'}))})
            db.commit()
        finally: db.close()
        with patch('app.services.telephony_adapters.get_adapter',return_value=_HealthAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True,'capabilities':['api']}):
            out=T.verify_provider_connection(self.account)
        self.assertNotIn(secret,str(out))
        self.assertRegex(out['status'],r'^[A-Za-z0-9._:-]+$')
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT last_health_status FROM telephony_provider_configs WHERE account_id=:a"),{'a':self.account}).scalar()
            audit=db.execute(text("SELECT metadata_json,result FROM telephony_audit WHERE account_id=:a AND action='provider.verify' ORDER BY id DESC LIMIT 1"),{'a':self.account}).mappings().first()
        finally: db.close()
        self.assertNotIn(secret,str(row))
        self.assertNotIn(secret,str(dict(audit or {})))

    def test_provider_health_exception_is_secret_free_and_fail_closed(self):
        import json as _json
        from app.crypto_utils import encrypt_secret
        class _ExplodingHealthAdapter:
            def health(self, credentials, public_config):
                raise RuntimeError('Authorization: Bearer credential-secret token=credential-secret')
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,credentials_enc,public_config_json,status) VALUES(:a,'uis',:e,'{}'::jsonb,'connected') ON CONFLICT (account_id) DO UPDATE SET provider='uis',credentials_enc=:e,status='connected'"),{'a':self.account,'e':encrypt_secret(_json.dumps({'access_token':'credential-secret'}))})
            db.commit()
        finally: db.close()
        with patch('app.services.telephony_adapters.get_adapter',return_value=_ExplodingHealthAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True,'capabilities':['api']}):
            out=T.verify_provider_connection(self.account)
        self.assertEqual(out['status'],'transport_error')
        self.assertFalse(out['connected'])
        self.assertNotIn('credential-secret',_json.dumps(out))
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT status,last_health_status,last_error FROM telephony_provider_configs WHERE account_id=:a"),{'a':self.account}).first()
            audit=db.execute(text("SELECT metadata_json FROM telephony_audit WHERE account_id=:a AND action='provider.verify' ORDER BY id DESC LIMIT 1"),{'a':self.account}).scalar()
        finally: db.close()
        self.assertEqual(row[0],'configured_unverified')
        self.assertEqual(row[1],'transport_error')
        self.assertNotIn('credential-secret',str(row[2] or ''))
        self.assertNotIn('credential-secret',_json.dumps(audit))

    def test_provider_health_guardian_excludes_synthetic_accounts_by_default(self):
        import json as _json
        from app.crypto_utils import encrypt_secret
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,credentials_enc,public_config_json,status,last_health_at) VALUES(:a,'uis',:e,'{}'::jsonb,'configured_unverified',NULL) ON CONFLICT (account_id) DO UPDATE SET provider='uis',credentials_enc=:e,last_health_at=NULL"),{'a':self.account,'e':encrypt_secret(_json.dumps({'access_token':'fake-qa-token'}))})
            db.commit()
        finally: db.close()
        with patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'verify_provider_connection',return_value={'status':'ok','connected':True}) as verify:
            out=T.provider_health_guardian(100,300)
            self.assertEqual(out.get('candidates'),0)
            verify.assert_not_called()
            opted=T.provider_health_guardian(100,300,include_synthetic=True)
            self.assertGreaterEqual(int(opted.get('candidates') or 0),1)
            self.assertGreaterEqual(int(opted.get('checked') or 0),1)
            self.assertIn(((self.account,None),{}),[(tuple(c.args),dict(c.kwargs)) for c in verify.call_args_list])

    def test_provider_partial_credentials_merge_without_erasing_existing_secret_fields(self):
        first=T.save_provider_config(
            self.account,'telphin',
            credentials={'client_id':'api-client-qa','client_secret':'api-secret-qa'},
            public_config={'extension_id':'12345','src_num':'101'}
        )
        self.assertEqual(first.get('status'),'ok')
        second=T.save_provider_config(
            self.account,'telphin',
            credentials={'sip_line':'sip-line-qa','sip_password':'sip-password-qa'},
            public_config=None
        )
        self.assertEqual(second.get('status'),'ok')
        provider,creds=T.provider_credentials(self.account)
        self.assertEqual(provider,'telphin')
        self.assertEqual(creds.get('client_id'),'api-client-qa')
        self.assertEqual(creds.get('client_secret'),'api-secret-qa')
        self.assertEqual(creds.get('sip_line'),'sip-line-qa')
        self.assertEqual(creds.get('sip_password'),'sip-password-qa')
        public=T.provider_config_public(self.account).get('config') or {}
        self.assertEqual((public.get('public_config_json') or {}).get('extension_id'),'12345')

    def test_provider_switch_never_carries_old_provider_secrets_or_webhook(self):
        first=T.save_provider_config(
            self.account,'uis',
            credentials={'access_token':'uis-secret-token'},
            public_config={'portal':'uis'},
            webhook_secret='uis-webhook-secret-qa'
        )
        self.assertEqual(first.get('status'),'ok')
        switched=T.save_provider_config(
            self.account,'telphin',
            credentials=None,
            public_config={'extension_id':'54321'}
        )
        self.assertEqual(switched.get('status'),'ok')
        provider,creds=T.provider_credentials(self.account)
        self.assertEqual(provider,'telphin')
        self.assertEqual(creds,{})
        cfg=T.provider_config_public(self.account).get('config') or {}
        self.assertFalse(bool(cfg.get('has_credentials')))
        self.assertFalse(bool(cfg.get('webhook_ready')))
        self.assertEqual(cfg.get('status'),'credentials_required')

    def test_provider_public_config_rejects_nested_secret_fields(self):
        out=T.save_provider_config(self.account,'uis',credentials=None,public_config={'portal':'uis','nested':{'access_token':'should-not-store'}})
        self.assertEqual(out.get('status'),'public_config_contains_secret_field')
        embedded=T.save_provider_config(self.account,'uis',credentials=None,public_config={'endpoint':'https://example.test/hook?access_token=plaintext-secret'})
        self.assertEqual(embedded.get('status'),'public_config_contains_secret_field')
        header=T.save_provider_config(self.account,'uis',credentials=None,public_config={'note':'Authorization: Bearer plaintext-secret'})
        self.assertEqual(header.get('status'),'public_config_contains_secret_field')
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT 1 FROM telephony_provider_configs WHERE account_id=:a"),{'a':self.account}).first()
        finally: db.close()
        self.assertIsNone(row)

    def test_call_postprocessing_exception_does_not_expose_secret_text(self):
        secret='POSTPROCESS-SECRET-TOKEN'
        cid=self._call()
        with patch.object(T,'autoanswer_decision',return_value={'should_answer':False}), \
             patch.object(T,'prepare_call_targets',side_effect=RuntimeError('Authorization: Bearer '+secret)):
            out=T.apply_event(self.account,'call.ringing',cid,'qa',None,'qa-redact-ring-'+uuid.uuid4().hex,{})
        self.assertEqual((out.get('targets') or {}).get('status'),'error')
        self.assertEqual((out.get('targets') or {}).get('error_code'),'RuntimeError')
        self.assertNotIn(secret,str(out))

    def test_recording_refresh_exception_does_not_expose_secret_text(self):
        secret='RECORDING-REFRESH-SECRET'
        class ExplodingAdapter:
            def recording_link(self, **kwargs):
                raise RuntimeError('token='+secret+' Authorization: Bearer '+secret)
        recording={'id':1,'account_id':self.account,'call_id':'qa-call','provider':'mango','provider_recording_id':'qa-rec'}
        with patch.object(T,'provider_credentials',return_value=('mango',{'token':secret})), \
             patch('app.services.telephony_adapters.get_adapter',return_value=ExplodingAdapter()):
            out=T._refresh_recording_source(recording)
        self.assertEqual(out.get('status'),'refresh_failed')
        self.assertEqual(out.get('message'),'provider_transport_error:RuntimeError')
        self.assertNotIn(secret,str(out))

    def test_recording_public_views_hide_signed_source_url_and_server_path(self):
        secret='SIGNED-RECORDING-SECRET'
        cid=self._call('ended','qa','provider-rec-'+uuid.uuid4().hex)
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET recording_url=:u WHERE account_id=:a AND id=:c"),{'u':'https://provider.invalid/file.mp3?token='+secret,'a':self.account,'c':cid})
            db.execute(text("""INSERT INTO telephony_recordings(account_id,call_id,provider,provider_recording_id,source_url,local_path,status)
              VALUES(:a,:c,'qa',:r,:u,:p,'available')"""),{'a':self.account,'c':cid,'r':'provider-recording-'+uuid.uuid4().hex,'u':'https://provider.invalid/file.mp3?token='+secret,'p':'/root/BORIS/backend/telephony_recordings/'+secret+'.wav'})
            db.commit()
        finally: db.close()
        items=T.recording_list(self.account,10)
        self.assertEqual(len(items),1)
        self.assertNotIn('source_url',items[0]); self.assertNotIn('local_path',items[0])
        self.assertTrue(items[0].get('source_received')); self.assertTrue(items[0].get('local_materialized'))
        self.assertNotIn(secret,str(items))
        detail=T.call_detail(self.account,cid)
        self.assertNotIn('recording_url',detail['call'])
        self.assertTrue(detail['call'].get('recording_source_received'))
        self.assertNotIn('source_url',detail['recordings'][0]); self.assertNotIn('local_path',detail['recordings'][0])
        self.assertNotIn(secret,str(detail))

    def test_recording_refresh_status_cannot_echo_current_credential(self):
        secret='RECORDINGSTATUSSECRET'
        class NegativeAdapter:
            def recording_link(self, **kwargs):
                return AdapterResult(False,secret,error='synthetic')
        recording={'id':1,'account_id':self.account,'call_id':'qa-call','provider':'mango','provider_recording_id':'qa-rec'}
        with patch.object(T,'provider_credentials',return_value=('mango',{'token':secret})), \
             patch('app.services.telephony_adapters.get_adapter',return_value=NegativeAdapter()):
            out=T._refresh_recording_source(recording)
        self.assertEqual(out.get('status'),'refresh_failed')
        self.assertEqual(out.get('message'),'provider did not return recording URL')
        self.assertNotIn(secret,str(out))

    def test_event_payload_secrets_are_redacted_before_db_and_sse(self):
        secret='EVENT-PAYLOAD-SECRET-TOKEN'
        payload={
            'direction':'inbound','from':'+70000000001','to':'+70000000002','source':'qa',
            'access_token':secret,'nested':{'client_secret':secret},
            'message':'Authorization: Bearer '+secret+' token='+secret+' client_secret='+secret+' private_key='+secret+' https://provider.invalid/cb?sig='+secret+'&auth='+secret,
        }
        out=T.apply_event(self.account,'call.started',None,'qa','provider-'+uuid.uuid4().hex,'event-'+uuid.uuid4().hex,payload)
        self.assertEqual(out.get('status'),'ok')
        cid=(out.get('call') or {}).get('id')
        feed=T.event_feed(self.account,0,100)
        item=next(x for x in feed if x.get('call_id')==cid)
        rendered=str(item.get('payload_json'))
        self.assertNotIn(secret,rendered)
        self.assertIn('[REDACTED]',rendered)
        db=SessionLocal()
        try:
            meta=db.execute(text('SELECT metadata_json FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':self.account,'c':cid}).scalar()
        finally: db.close()
        self.assertNotIn(secret,str(meta))

    def test_monotonic_state_machine_persists_delayed_events_without_regression(self):
        cid=self._call()
        harmless=patch.multiple(T,prepare_call_targets=lambda *a,**k:{'status':'qa'},autoanswer_decision=lambda *a,**k:{'should_answer':False},_complete_callback_if_answered=lambda *a,**k:{'status':'qa'},cancel_call_pushes=lambda *a,**k:0,sync_call_to_crm=lambda *a,**k:{'status':'qa'},meter_completed_call=lambda *a,**k:{'status':'qa'},minute_alert_guard=lambda *a,**k:{'status':'qa'})
        with harmless:
            T.apply_event(self.account,'call.ringing',cid,'qa',None,'qa-e1',{})
            T.apply_event(self.account,'call.answered',cid,'qa',None,'qa-e2',{})
            T.apply_event(self.account,'call.ringing',cid,'qa',None,'qa-e3',{})
            T.apply_event(self.account,'call.ended',cid,'qa',None,'qa-e4',{})
            T.apply_event(self.account,'call.answered',cid,'qa',None,'qa-e5',{})
        db=SessionLocal()
        try:
            state=db.execute(text('SELECT state FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':self.account,'c':cid}).scalar()
            events=db.execute(text('SELECT count(*) FROM telephony_events WHERE account_id=:a AND call_id=:c'),{'a':self.account,'c':cid}).scalar()
            ignored=db.execute(text("SELECT count(*) FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='call.state.transition' AND result='ignored_out_of_order'"),{'a':self.account,'c':cid}).scalar()
        finally: db.close()
        self.assertEqual(state,'ended'); self.assertEqual(events,5); self.assertEqual(ignored,2)
    def test_expired_ring_target_times_out_and_call_becomes_missed(self):
        cid=self._call('ringing')
        did='dev_qa_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,name,platform,presence,last_seen_at) VALUES(:d,:a,'QA','web','online',now())"),{'d':did,'a':self.account})
            db.execute(text("INSERT INTO telephony_call_targets(account_id,call_id,device_id,status,expires_at) VALUES(:a,:c,:d,'ringing',now()-interval '1 minute')"),{'a':self.account,'c':cid,'d':did}); db.commit()
        finally: db.close()
        harmless=patch.multiple(T,resolve_route=lambda *a,**k:{'route':{}},autoanswer_decision=lambda *a,**k:{'should_answer':False},cancel_call_pushes=lambda *a,**k:0,sync_call_to_crm=lambda *a,**k:{'status':'qa'},meter_completed_call=lambda *a,**k:{'status':'qa'},minute_alert_guard=lambda *a,**k:{'status':'qa'})
        with harmless: out=T.sweep_ring_targets(self.account)
        db=SessionLocal()
        try:
            target=db.execute(text('SELECT status FROM telephony_call_targets WHERE account_id=:a AND call_id=:c'),{'a':self.account,'c':cid}).scalar()
            state=db.execute(text('SELECT state FROM telephony_calls WHERE account_id=:a AND id=:c'),{'a':self.account,'c':cid}).scalar()
        finally: db.close()
        self.assertEqual(target,'timeout'); self.assertEqual(state,'missed'); self.assertEqual(out['expired'],1); self.assertEqual(out['missed'],1)
    def _command_case(self,result,attempts=0,retry_safe=False):
        cid=self._call('active','qa','provider-qa-'+uuid.uuid4().hex)
        db=SessionLocal()
        try:
            cmd=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,attempts,next_attempt_at) VALUES(:a,:c,'mute','queued','qa',:n,now()) RETURNING id"),{'a':self.account,'c':cid,'n':attempts}).scalar(); db.commit()
        finally: db.close()
        with patch('app.services.telephony_adapters.get_adapter',return_value=FakeAdapter(result,retry_safe)), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'provider_credentials',return_value=('qa',{'token':'x'})), patch.object(T,'_provider_public_config',return_value={}):
            T.dispatch_pending_commands(1,include_synthetic=True)
        db=SessionLocal()
        try: return dict(db.execute(text('SELECT status,attempts,next_attempt_at,processed_at,last_error FROM telephony_commands WHERE id=:i'),{'i':cmd}).mappings().one())
        finally: db.close()
    def test_command_retry_transient_then_hard_and_exhausted(self):
        transient=self._command_case(AdapterResult(False,'timeout',error='synthetic timeout'),0)
        retry_safe=self._command_case(AdapterResult(False,'timeout',error='synthetic timeout'),0,True)
        hard=self._command_case(AdapterResult(False,'invalid_command',error='synthetic hard'),0)
        exhausted=self._command_case(AdapterResult(False,'timeout',error='synthetic timeout'),5,True)
        self.assertEqual(transient['status'],'failed'); self.assertEqual(transient['attempts'],1); self.assertIsNone(transient['next_attempt_at']); self.assertIsNotNone(transient['processed_at'])
        self.assertEqual(retry_safe['status'],'queued'); self.assertEqual(retry_safe['attempts'],1); self.assertIsNotNone(retry_safe['next_attempt_at']); self.assertIsNone(retry_safe['processed_at'])
        self.assertEqual(hard['status'],'failed'); self.assertEqual(hard['attempts'],1); self.assertIsNone(hard['next_attempt_at']); self.assertIsNotNone(hard['processed_at'])
        self.assertEqual(exhausted['status'],'failed'); self.assertEqual(exhausted['attempts'],5); self.assertIsNone(exhausted['next_attempt_at']); self.assertIsNotNone(exhausted['processed_at'])

    def test_command_provider_error_text_is_not_persisted(self):
        secret='COMMAND-SECRET-TOKEN'
        out=self._command_case(AdapterResult(False,'provider_rejected',error='Authorization Bearer '+secret),0)
        self.assertEqual(out['status'],'failed')
        self.assertEqual(out['last_error'],'provider_rejected')
        self.assertNotIn(secret,str(out))

    def test_command_provider_status_cannot_echo_current_credential(self):
        secret='COMMANDSTATUSSECRET'
        cid=self._call('active','qa','provider-qa-'+uuid.uuid4().hex)
        db=SessionLocal()
        try:
            cmd=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,attempts,next_attempt_at) VALUES(:a,:c,'mute','queued','qa',0,now()) RETURNING id"),{'a':self.account,'c':cid}).scalar(); db.commit()
        finally: db.close()
        with patch('app.services.telephony_adapters.get_adapter',return_value=FakeAdapter(AdapterResult(False,secret),False)), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'provider_credentials',return_value=('qa',{'token':secret})), patch.object(T,'_provider_public_config',return_value={}):
            T.dispatch_pending_commands(1,include_synthetic=True)
        db=SessionLocal()
        try: row=dict(db.execute(text('SELECT status,last_error FROM telephony_commands WHERE id=:i'),{'i':cmd}).mappings().one())
        finally: db.close()
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['last_error'],'provider_command_failed')
        self.assertNotIn(secret,str(row))

    def _last_target_hangup_case(self, provider_result):
        cid=self._call('ringing','qa','provider-hangup-'+uuid.uuid4().hex)
        did='dev_qa_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,name,platform,presence,last_seen_at) VALUES(:d,:a,'QA','web','online',now())"),{'d':did,'a':self.account})
            db.execute(text("INSERT INTO telephony_call_targets(account_id,call_id,device_id,status,expires_at) VALUES(:a,:c,:d,'ringing',now()+interval '1 minute')"),{'a':self.account,'c':cid,'d':did}); db.commit()
        finally: db.close()
        adapter=FakeAdapter(provider_result); adapter.supported_commands={'hangup'}
        with patch('app.services.telephony_adapters.get_adapter',return_value=adapter), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            queued=T.queue_call_command(self.account,cid,'hangup',{},None,did)
        self.assertEqual(queued['status'],'queued')
        db=SessionLocal()
        try: before=db.execute(text("SELECT status FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':cid,'d':did}).scalar()
        finally: db.close()
        self.assertEqual(before,'ringing')
        with patch('app.services.telephony_adapters.get_adapter',return_value=adapter), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'provider_credentials',return_value=('qa',{'token':'x'})), patch.object(T,'_provider_public_config',return_value={}):
            T.dispatch_pending_commands(1,include_synthetic=True)
        db=SessionLocal()
        try:
            target=db.execute(text("SELECT status FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':cid,'d':did}).scalar()
            cmd=db.execute(text("SELECT status FROM telephony_commands WHERE account_id=:a AND call_id=:c ORDER BY id DESC LIMIT 1"),{'a':self.account,'c':cid}).scalar()
        finally: db.close()
        return target,cmd

    def test_last_target_hangup_timeout_keeps_target_ringing(self):
        target,cmd=self._last_target_hangup_case(AdapterResult(False,'timeout',error='synthetic timeout'))
        self.assertEqual(target,'ringing'); self.assertEqual(cmd,'failed')

    def test_last_target_hangup_provider_success_rejects_target_after_boundary(self):
        target,cmd=self._last_target_hangup_case(AdapterResult(True,'ok'))
        self.assertEqual(target,'rejected'); self.assertEqual(cmd,'done')

    def test_native_push_material_is_platform_bound(self):
        self.assertIsNone(T._validate_native_push_material('apns_voip','a'*64,'ios')[2])
        self.assertIsNone(T._validate_native_push_material('fcm','x'*32,'android')[2])
        self.assertEqual(T._validate_native_push_material('fcm','x'*32,'ios')[2],'push_platform_mismatch')
        self.assertEqual(T._validate_native_push_material('apns_voip','a'*64,'android')[2],'push_platform_mismatch')
        self.assertEqual(T._validate_native_push_material('fcm','x'*32,'web')[2],'push_not_supported_for_platform')

    def test_provider_answer_success_reconciles_target_expired_during_io(self):
        cid=self._call('ringing','qa','provider-answer-expire-'+uuid.uuid4().hex)
        did='dev_qa_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,1,'QA','web','online',now())"),{'d':did,'a':self.account})
            db.execute(text("INSERT INTO telephony_call_targets(account_id,call_id,device_id,user_id,status,expires_at) VALUES(:a,:c,:d,1,'ringing',now()+interval '1 minute')"),{'a':self.account,'c':cid,'d':did})
            cmd=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,device_id,requested_by,next_attempt_at) VALUES(:a,:c,'answer','queued','qa',:d,1,now()) RETURNING id"),{'a':self.account,'c':cid,'d':did}).scalar(); db.commit()
        finally: db.close()
        class ExpiringSuccessAdapter(FakeAdapter):
            supported_commands={'answer'}
            def command(adapter_self, **kwargs):
                adapter_self.calls+=1
                qdb=SessionLocal()
                try:
                    qdb.execute(text("UPDATE telephony_call_targets SET status='timeout',ended_at=now(),expires_at=now()-interval '1 second',updated_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':cid,'d':did})
                    qdb.commit()
                finally: qdb.close()
                return AdapterResult(True,'ok')
        adapter=ExpiringSuccessAdapter(AdapterResult(True,'ok'))
        with patch('app.services.telephony_adapters.get_adapter',return_value=adapter), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'provider_credentials',return_value=('qa',{'token':'x'})), patch.object(T,'_provider_public_config',return_value={}), patch.object(T,'cancel_call_pushes',return_value=0):
            out=T.dispatch_pending_commands(1,include_synthetic=True)
        db=SessionLocal()
        try:
            call=dict(db.execute(text("SELECT state,device_id,answered_at FROM telephony_calls WHERE account_id=:a AND id=:c"),{'a':self.account,'c':cid}).mappings().one())
            target=dict(db.execute(text("SELECT status,answered_at,ended_at FROM telephony_call_targets WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':cid,'d':did}).mappings().one())
            command=dict(db.execute(text("SELECT status,attempts,last_error FROM telephony_commands WHERE id=:i"),{'i':cmd}).mappings().one())
        finally: db.close()
        self.assertEqual(adapter.calls,1); self.assertEqual(out['completed'],1)
        self.assertEqual(call['state'],'active'); self.assertEqual(call['device_id'],did); self.assertIsNotNone(call['answered_at'])
        self.assertEqual(target['status'],'answered'); self.assertIsNotNone(target['answered_at']); self.assertIsNone(target['ended_at'])
        self.assertEqual(command['status'],'done'); self.assertEqual(command['attempts'],1); self.assertIsNone(command['last_error'])

    def test_stale_answer_after_other_device_claim_never_hits_provider(self):
        cid=self._call('active','qa','provider-answer-'+uuid.uuid4().hex)
        winner='dev_winner_'+uuid.uuid4().hex[:8]; loser='dev_loser_'+uuid.uuid4().hex[:8]
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET device_id=:d WHERE account_id=:a AND id=:c"),{'d':winner,'a':self.account,'c':cid})
            cmd=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,device_id,next_attempt_at) VALUES(:a,:c,'answer','queued','qa',:d,now()) RETURNING id"),{'a':self.account,'c':cid,'d':loser}).scalar(); db.commit()
        finally: db.close()
        adapter=FakeAdapter(AdapterResult(True,'ok')); adapter.supported_commands={'answer'}
        with patch('app.services.telephony_adapters.get_adapter',return_value=adapter), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'provider_credentials',return_value=('qa',{'token':'x'})), patch.object(T,'_provider_public_config',return_value={}):
            T.dispatch_pending_commands(1,include_synthetic=True)
        db=SessionLocal()
        try: status=db.execute(text("SELECT status FROM telephony_commands WHERE id=:i"),{'i':cmd}).scalar()
        finally: db.close()
        self.assertEqual(status,'failed'); self.assertEqual(adapter.calls,0)

    def test_device_revoked_after_command_claim_never_hits_provider(self):
        from app.services.telephony_adapters.base import AdapterResult
        cid=self._call('active','uis','12345')
        device='dev_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,1,'QA','web','online',now())"),{'d':device,'a':self.account})
            db.execute(text("UPDATE telephony_calls SET device_id=:d WHERE account_id=:a AND id=:c"),{'d':device,'a':self.account,'c':cid})
            db.execute(text("INSERT INTO telephony_commands(account_id,call_id,provider,command,payload_json,status,device_id) VALUES(:a,:c,'uis','hangup','{}'::jsonb,'queued',:d)"),{'a':self.account,'c':cid,'d':device}); db.commit()
        finally: db.close()
        class A:
            supported_commands={'hangup'}; retry_safe_commands=set()
            def command(self,**kwargs): raise AssertionError('revoked device must not hit provider')
        original_credentials=T.provider_credentials
        def rotate(a):
            db=SessionLocal()
            try: db.execute(text("UPDATE telephony_devices SET revoked_at=now() WHERE account_id=:a AND id=:d"),{'a':self.account,'d':device}); db.commit()
            finally: db.close()
            return ('uis',{'token':'qa'})
        with patch.object(T,'selected_provider',return_value='uis'), patch.object(T,'provider_credentials',side_effect=rotate), \
             patch('app.services.telephony_adapters.get_adapter',return_value=A()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), \
             patch.object(T,'_provider_public_config',return_value={}):
            out=T.dispatch_pending_commands(limit=5,include_synthetic=True)
        self.assertEqual(out.get('failed'),1)
        db=SessionLocal()
        try: row=db.execute(text("SELECT status,last_error FROM telephony_commands WHERE account_id=:a AND call_id=:c ORDER BY id DESC LIMIT 1"),{'a':self.account,'c':cid}).mappings().one()
        finally: db.close()
        self.assertEqual(row.get('status'),'failed'); self.assertIn('device revoked before provider send',str(row.get('last_error') or ''))

    def test_revoke_device_cancels_unsent_commands_without_provider_io(self):
        cid=self._call('active','qa','provider-revoke-'+uuid.uuid4().hex)
        did='dev_qa_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,1,'QA','web','online',now())"),{'d':did,'a':self.account})
            db.execute(text("UPDATE telephony_calls SET device_id=:d WHERE account_id=:a AND id=:c"),{'d':did,'a':self.account,'c':cid})
            q1=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,device_id,next_attempt_at) VALUES(:a,:c,'hangup','queued','qa',:d,now()) RETURNING id"),{'a':self.account,'c':cid,'d':did}).scalar()
            q2=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,device_id,next_attempt_at) VALUES(:a,:c,'mute','waiting_provider','qa',:d,now()) RETURNING id"),{'a':self.account,'c':cid,'d':did}).scalar()
            db.commit()
        finally: db.close()
        out=T.revoke_device(self.account,did,1)
        self.assertEqual(out['status'],'ok'); self.assertEqual(out['commands_cancelled'],2)
        db=SessionLocal()
        try:
            rows=[dict(x) for x in db.execute(text("SELECT status,last_error,next_attempt_at,processed_at FROM telephony_commands WHERE id=ANY(:ids) ORDER BY id"),{'ids':[q1,q2]}).mappings().all()]
        finally: db.close()
        self.assertEqual([r['status'] for r in rows],['failed','failed'])
        self.assertTrue(all(r['last_error']=='device revoked' and r['next_attempt_at'] is None and r['processed_at'] is not None for r in rows))

    def test_final_call_command_never_hits_provider(self):
        cid=self._call('ended','qa','provider-ended-'+uuid.uuid4().hex)
        db=SessionLocal()
        try:
            cmd=db.execute(text("INSERT INTO telephony_commands(account_id,call_id,command,status,provider,next_attempt_at) VALUES(:a,:c,'hangup','queued','qa',now()) RETURNING id"),{'a':self.account,'c':cid}).scalar(); db.commit()
        finally: db.close()
        adapter=FakeAdapter(AdapterResult(True,'ok')); adapter.supported_commands={'hangup'}
        with patch('app.services.telephony_adapters.get_adapter',return_value=adapter), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'provider_credentials',return_value=('qa',{'token':'x'})), patch.object(T,'_provider_public_config',return_value={}):
            T.dispatch_pending_commands(1,include_synthetic=True)
        db=SessionLocal()
        try: status=db.execute(text("SELECT status FROM telephony_commands WHERE id=:i"),{'i':cmd}).scalar()
        finally: db.close()
        self.assertEqual(status,'failed'); self.assertEqual(adapter.calls,0)


class TelephonyReadinessTruthTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_tel_ready_'+uuid.uuid4().hex[:16]
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_audit','telephony_recordings','telephony_events','telephony_calls','telephony_devices','telephony_provider_configs','telephony_push_outbox'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_app_readiness_never_masks_missing_real_telephony(self):
        out=T.phone_client_readiness(self.account)
        self.assertIn('readiness_pct',out)
        self.assertIn('telephony_readiness_pct',out)
        self.assertFalse(out['telephony_gates']['provider_selected'])
        self.assertFalse(out['telephony_gates']['native_push_delivered'])
        self.assertFalse(out['telephony_gates']['real_media_provider'])
        self.assertLess(out['telephony_readiness_pct'],100)
        self.assertIn('provider_selected',out['telephony_remaining'])
        self.assertTrue(any('оператор' in x for x in out['telephony_remaining_human']))
        self.assertIn('native_release_environment',out)
        self.assertIn('android',out['native_release_environment'])
        self.assertIn('ios',out['native_release_environment'])
        self.assertEqual(out['release_blocker_codes']['android'],out['native_release_environment']['android']['missing'])
        self.assertEqual(out['release_blocker_codes']['ios'],out['native_release_environment']['ios']['missing'])
    def test_old_provider_evidence_is_not_reused_after_provider_switch(self):
        cid='call_ready_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,status,last_health_status,last_health_at,credentials_enc) VALUES(:a,'uis','connected','ok',now(),'qa-encrypted-placeholder')"),{'a':self.account})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state,answered_at,ended_at) VALUES(:c,:a,'uis','uis-old-call','inbound','ended',now()-interval '1 minute',now())"),{'c':cid,'a':self.account})
            db.execute(text("INSERT INTO telephony_events(account_id,call_id,provider,provider_event_id,event_type,payload_json) VALUES(:a,:c,'uis','uis-old-event','call.answered','{}'::jsonb)"),{'a':self.account,'c':cid})
            db.execute(text("INSERT INTO telephony_audit(account_id,action,result,provider,call_id,metadata_json) VALUES(:a,'media.session.validated','ok','uis',:c,'{}'::jsonb)"),{'a':self.account,'c':cid})
            db.execute(text("INSERT INTO telephony_audit(account_id,action,result,provider,call_id,metadata_json) VALUES(:a,'media.transport.proven','ok','uis',:c,'{}'::jsonb)"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        before=T.phone_client_readiness(self.account)
        self.assertEqual(before['runtime_evidence']['calls']['provider_calls'],1)
        self.assertEqual(before['runtime_evidence']['transport']['media_sessions_ok'],1)
        self.assertEqual(before['runtime_evidence']['transport']['media_transport_proven'],1)
        self.assertTrue(before['telephony_gates']['real_media_provider'])

        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_provider_configs SET provider='mango',status='connected',last_health_status='ok',last_health_at=now() WHERE account_id=:a"),{'a':self.account})
            db.commit()
        finally: db.close()
        after=T.phone_client_readiness(self.account)
        self.assertEqual(after['provider_evidence']['provider'],'mango')
        self.assertEqual(after['runtime_evidence']['calls']['provider_calls'],0)
        self.assertEqual(after['runtime_evidence']['transport']['media_sessions_ok'],0)
        self.assertFalse(after['telephony_gates']['real_provider_call_seen'])
        self.assertFalse(after['telephony_gates']['real_media_provider'])
        self.assertIn('adapter_media_contract_unimplemented',after['media_diagnostics']['blocker_codes'])
        self.assertIn('real_media_transport_not_proven',after['media_diagnostics']['blocker_codes'])

    def test_recording_url_ingress_alone_is_not_received_recording(self):
        cid='call_ready_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state) VALUES(:c,:a,'uis','provider-url-only','inbound','ended')"),{'c':cid,'a':self.account})
            db.execute(text("INSERT INTO telephony_events(account_id,call_id,provider,provider_event_id,event_type,payload_json) VALUES(:a,:c,'uis','provider-event-url-only','call.ended','{}'::jsonb)"),{'a':self.account,'c':cid})
            db.execute(text("INSERT INTO telephony_recordings(account_id,call_id,provider,status,source_url) VALUES(:a,:c,'uis','available','https://example.com/recording.mp3')"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        out=T.phone_client_readiness(self.account)
        self.assertEqual(out['runtime_evidence']['recordings']['recording_ingress'],1)
        self.assertEqual(out['runtime_evidence']['recordings']['recordings'],0)
        self.assertFalse(out['telephony_gates']['recording_received'])

    def test_real_call_evidence_is_counted_but_media_stays_hard_gate(self):
        cid='call_ready_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,status,last_health_status,last_health_at,credentials_enc) VALUES(:a,'uis','connected','ok',now(),'qa-encrypted-placeholder')"),{'a':self.account})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state,answered_at,ended_at) VALUES(:c,:a,'uis','provider-1','inbound','ended',now()-interval '1 minute',now())"),{'c':cid,'a':self.account})
            # A real inbound call is evidenced by a normalized provider webhook event,
            # not by provider_call_id alone. This fixture models that boundary.
            db.execute(text("INSERT INTO telephony_events(account_id,call_id,provider,provider_event_id,event_type,payload_json) VALUES(:a,:c,'uis','provider-event-1','call.answered','{}'::jsonb)"),{'a':self.account,'c':cid})
            db.execute(text("INSERT INTO telephony_recordings(account_id,call_id,provider,status,local_path,checksum,transcript,transcript_status,analysis_status) VALUES(:a,:c,'uis','available','/root/BORIS/backend/telephony_recordings/qa.wav','qa-checksum','тест','done','done')"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        out=T.phone_client_readiness(self.account)
        self.assertTrue(out['telephony_gates']['provider_connected'])
        self.assertTrue(out['telephony_gates']['real_provider_call_seen'])
        self.assertTrue(out['telephony_gates']['recording_received'])
        self.assertTrue(out['telephony_gates']['transcription_completed'])
        self.assertTrue(out['telephony_gates']['ai_analysis_completed'])
        self.assertFalse(out['telephony_gates']['real_media_provider'])
        self.assertLess(out['telephony_readiness_pct'],100)


class TelephonyMediaProofTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_media_'+uuid.uuid4().hex[:16]
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_audit','telephony_events','telephony_calls','telephony_provider_configs'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_media_gate_requires_rtp_transport_proof_not_only_safe_session_issue(self):
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_provider_configs(account_id,provider,status,last_health_status,last_health_at,credentials_enc) VALUES(:a,'uis','connected','ok',now(),'qa-encrypted-placeholder')"),{'a':self.account})
            db.commit()
        finally: db.close()
        before=T.phone_client_readiness(self.account)
        self.assertFalse(before['telephony_gates']['real_media_provider'])
        db=SessionLocal()
        try:
            cid='call_real_'+uuid.uuid4().hex
            db.execute(text("INSERT INTO telephony_calls(id,account_id,direction,state,provider,provider_call_id) VALUES(:c,:a,'inbound','active','uis','provider-real-1')"),{'c':cid,'a':self.account})
            db.execute(text("INSERT INTO telephony_events(account_id,call_id,event_type,provider,provider_event_id,payload_json) VALUES(:a,:c,'call.started','uis','evt-real-1','{}'::jsonb)"),{'a':self.account,'c':cid})
            db.execute(text("INSERT INTO telephony_audit(account_id,action,result,provider,call_id,metadata_json) VALUES(:a,'media.session.validated','ok','uis',:c,'{}'::jsonb)"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        contract_only=T.phone_client_readiness(self.account)
        self.assertEqual(contract_only['runtime_evidence']['transport']['media_sessions_ok'],1)
        self.assertEqual(contract_only['runtime_evidence']['transport']['media_transport_proven'],0)
        self.assertFalse(contract_only['telephony_gates']['real_media_provider'])
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_audit(account_id,action,result,provider,call_id,metadata_json) VALUES(:a,'media.transport.proven','ok','uis',:c,'{}'::jsonb)"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        after=T.phone_client_readiness(self.account)
        self.assertTrue(after['telephony_gates']['real_media_provider'])
        self.assertEqual(after['runtime_evidence']['transport']['media_sessions_ok'],1)
        self.assertEqual(after['runtime_evidence']['transport']['media_transport_proven'],1)


class TelephonyPushReceiptProofTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_push_'+uuid.uuid4().hex[:16]; self.device='dev_'+uuid.uuid4().hex[:12]; self.call_id='call_real_'+uuid.uuid4().hex; self.receipt_token='qa-push-token-'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,name,platform,app_version,presence,last_seen_at,push_kind,push_token_hash) VALUES(:d,:a,'QA Android','android','0.1.0','online',now(),'fcm',:h)"),{'d':self.device,'a':self.account,'h':__import__('hashlib').sha256(self.receipt_token.encode()).hexdigest()})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,direction,state,provider,provider_call_id) VALUES(:c,:a,'inbound','ringing','uis','provider-real-push')"),{'c':self.call_id,'a':self.account})
            db.execute(text("INSERT INTO telephony_events(account_id,call_id,event_type,provider,provider_event_id,payload_json) VALUES(:a,:c,'call.started','uis','evt-real-push','{}'::jsonb)"),{'a':self.account,'c':self.call_id})
            self.push_id=int(db.execute(text("INSERT INTO telephony_push_outbox(account_id,device_id,call_id,event_type,status,sent_at,sent_push_token_hash,payload_json) VALUES(:a,:d,:c,'call.ringing','sent',now(),:h,'{}'::jsonb) RETURNING id"),{'a':self.account,'d':self.device,'c':self.call_id,'h':__import__('hashlib').sha256(self.receipt_token.encode()).hexdigest()}).scalar())
            db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_audit','telephony_push_outbox','telephony_events','telephony_calls','telephony_devices'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_provider_acceptance_is_not_device_delivery(self):
        before=T.phone_client_readiness(self.account)
        self.assertTrue(before['telephony_gates']['native_push_provider_accepted'])
        self.assertFalse(before['telephony_gates']['native_push_delivered'])
        self.assertEqual(before['runtime_evidence']['native_push_provider_accepted'],1)
        self.assertEqual(before['runtime_evidence']['native_push_device_received'],0)
    def test_receipt_is_device_scoped_and_idempotent(self):
        wrong=T.ack_mobile_push(self.account,'other-device',self.push_id,'android',self.receipt_token)
        self.assertEqual(wrong['status'],'not_found_or_not_sent_or_unverified_device')
        unverified=T.ack_mobile_push(self.account,self.device,self.push_id,'android','wrong-token')
        self.assertEqual(unverified['status'],'not_found_or_not_sent_or_unverified_device')
        first=T.ack_mobile_push(self.account,self.device,self.push_id,'android',self.receipt_token)
        second=T.ack_mobile_push(self.account,self.device,self.push_id,'android',self.receipt_token)
        self.assertEqual(first['status'],'ok'); self.assertEqual(second['status'],'ok')
        after=T.phone_client_readiness(self.account)
        self.assertTrue(after['telephony_gates']['native_push_delivered'])
        self.assertEqual(after['runtime_evidence']['native_push_device_received'],1)
        health=T.device_runtime_health(self.account,self.device)
        self.assertEqual(health['push_provider_accepted_count'],1)
        self.assertEqual(health['push_device_received_count'],1)
        self.assertIsNotNone(health['last_push_received_at'])
    def test_active_push_intent_has_db_exactly_once_boundary(self):
        # The helper advisory lock is not the final correctness boundary: even a
        # direct SQL/future-generation bypass must not create a second live intent.
        db=SessionLocal()
        try:
            with self.assertRaises(Exception):
                db.execute(text("INSERT INTO telephony_push_outbox(account_id,device_id,call_id,event_type,status,payload_json) VALUES(:a,:d,:c,'call.ringing','pending','{}'::jsonb)"),{'a':self.account,'d':self.device,'c':self.call_id})
                db.commit()
            db.rollback()
            n=db.execute(text("SELECT count(*) FROM telephony_push_outbox WHERE account_id=:a AND device_id=:d AND call_id=:c AND event_type='call.ringing' AND status IN ('pending','retry','waiting_configuration','sent')"),{'a':self.account,'d':self.device,'c':self.call_id}).scalar()
            self.assertEqual(int(n or 0),1)
        finally: db.close()

    def test_receipt_is_bound_to_token_used_for_that_push(self):
        import hashlib
        rotated='rotated-token-'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_devices SET push_token_hash=:h WHERE account_id=:a AND id=:d"),{'h':hashlib.sha256(rotated.encode()).hexdigest(),'a':self.account,'d':self.device})
            db.commit()
        finally: db.close()
        wrong=T.ack_mobile_push(self.account,self.device,self.push_id,'android',rotated)
        self.assertEqual(wrong['status'],'not_found_or_not_sent_or_unverified_device')
        original=T.ack_mobile_push(self.account,self.device,self.push_id,'android',self.receipt_token)
        self.assertEqual(original['status'],'not_found_or_not_sent_or_unverified_device')
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_devices SET push_token_hash=:h WHERE account_id=:a AND id=:d"),{'h':hashlib.sha256(self.receipt_token.encode()).hexdigest(),'a':self.account,'d':self.device})
            db.commit()
        finally: db.close()
        current=T.ack_mobile_push(self.account,self.device,self.push_id,'android',self.receipt_token)
        self.assertEqual(current['status'],'ok')

    def test_push_readiness_requires_current_token_evidence(self):
        import hashlib
        first=T.ack_mobile_push(self.account,self.device,self.push_id,'android',self.receipt_token)
        self.assertEqual(first['status'],'ok')
        before=T.phone_client_readiness(self.account)
        self.assertTrue(before['telephony_gates']['native_push_delivered'])
        rotated='rotated-token-'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_devices SET push_token_hash=:h WHERE account_id=:a AND id=:d"),{'h':hashlib.sha256(rotated.encode()).hexdigest(),'a':self.account,'d':self.device})
            db.commit()
        finally: db.close()
        after=T.phone_client_readiness(self.account)
        self.assertFalse(after['telephony_gates']['native_push_provider_accepted'])
        self.assertFalse(after['telephony_gates']['native_push_delivered'])
        self.assertEqual(after['runtime_evidence']['native_push_provider_accepted'],0)
        self.assertEqual(after['runtime_evidence']['native_push_device_received'],0)
        health=T.device_runtime_health(self.account,self.device)
        self.assertEqual(health['push_provider_accepted_count'],0)
        self.assertEqual(health['push_device_received_count'],0)
        self.assertIsNone(health['last_push_received_at'])


class TelephonyReadinessSyntheticIsolationTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_synth_'+uuid.uuid4().hex[:16]
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_audit','telephony_call_quality','telephony_transcript_chunks','telephony_recordings','telephony_events','telephony_calls','telephony_devices','telephony_provider_configs','telephony_push_outbox'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_internal_answered_call_is_not_real_provider_evidence(self):
        cid='call_synth_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,direction,state,answered_at,ended_at) VALUES(:c,:a,'inbound','ended',now()-interval '1 minute',now())"),{'c':cid,'a':self.account})
            db.execute(text("INSERT INTO telephony_recordings(account_id,call_id,status,local_path,transcript,transcript_status,analysis_status) VALUES(:a,:c,'available','/tmp/synthetic.wav','synthetic','done','done')"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        out=T.phone_client_readiness(self.account)
        self.assertFalse(out['telephony_gates']['real_provider_call_seen'])
        self.assertFalse(out['telephony_gates']['real_inbound_seen'])
        self.assertFalse(out['telephony_gates']['real_answer_seen'])
        self.assertFalse(out['telephony_gates']['real_call_completed'])
        self.assertFalse(out['telephony_gates']['recording_received'])
        self.assertFalse(out['telephony_gates']['transcription_completed'])
        self.assertFalse(out['telephony_gates']['ai_analysis_completed'])

    def test_forged_provider_identity_without_boundary_evidence_is_not_real(self):
        cid='call_forged_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state,answered_at,ended_at) VALUES(:c,:a,'uis','fake-provider-call','inbound','ended',now()-interval '1 minute',now())"),{'c':cid,'a':self.account})
            db.execute(text("INSERT INTO telephony_recordings(account_id,call_id,status,local_path,transcript,transcript_status,analysis_status) VALUES(:a,:c,'available','/tmp/forged.wav','forged','done','done')"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        out=T.phone_client_readiness(self.account)
        for gate in ('real_provider_call_seen','real_inbound_seen','real_answer_seen','real_call_completed','recording_received','transcription_completed','ai_analysis_completed'):
            self.assertFalse(out['telephony_gates'][gate],gate)

    def test_authenticated_provider_event_makes_inbound_call_real_evidence(self):
        cid='call_realish_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state,answered_at,ended_at) VALUES(:c,:a,'uis','real-provider-call','inbound','ended',now()-interval '1 minute',now())"),{'c':cid,'a':self.account})
            db.execute(text("INSERT INTO telephony_events(account_id,call_id,provider,provider_event_id,event_type,payload_json) VALUES(:a,:c,'uis','signed-event-1','call.answered','{}'::jsonb)"),{'a':self.account,'c':cid})
            db.commit()
        finally: db.close()
        out=T.phone_client_readiness(self.account)
        self.assertTrue(out['telephony_gates']['real_provider_call_seen'])
        self.assertTrue(out['telephony_gates']['real_inbound_seen'])
        self.assertTrue(out['telephony_gates']['real_answer_seen'])
        self.assertTrue(out['telephony_gates']['real_call_completed'])


class TelephonyPushIntentIdempotencyTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_pushintent_'+uuid.uuid4().hex[:12]; self.device='dev_'+uuid.uuid4().hex[:12]; self.call='call_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            import hashlib
            db.execute(text("INSERT INTO telephony_devices(id,account_id,name,platform,push_kind,push_token_hash,push_token_enc,presence,last_seen_at) VALUES(:d,:a,'QA Android','android','fcm',:h,'qa-encrypted-placeholder','online',now())"),{'d':self.device,'a':self.account,'h':hashlib.sha256(b'qa-device-token').hexdigest()})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,direction,state) VALUES(:c,:a,'inbound','ringing')"),{'c':self.call,'a':self.account})
            db.execute(text("INSERT INTO telephony_call_targets(account_id,call_id,device_id,status,expires_at) VALUES(:a,:c,:d,'ringing',now()+interval '1 minute')"),{'a':self.account,'c':self.call,'d':self.device})
            db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_push_outbox','telephony_calls','telephony_devices'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_queue_does_not_create_ringing_push_for_finished_call(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='ended',ended_at=now() WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call})
            db.execute(text("UPDATE telephony_call_targets SET status='cancelled',ended_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':self.call,'d':self.device})
            db.commit()
        finally: db.close()
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        db=SessionLocal()
        try: n=int(db.execute(text("SELECT count(*) FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).scalar() or 0)
        finally: db.close()
        self.assertEqual(n,0)

    def test_retry_and_waiting_configuration_do_not_create_second_intent(self):
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_push_outbox SET status='retry' WHERE account_id=:a"),{'a':self.account}); db.commit()
        finally: db.close()
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        db=SessionLocal()
        try:
            n=db.execute(text("SELECT count(*) FROM telephony_push_outbox WHERE account_id=:a AND device_id=:d AND call_id=:c AND event_type='call.ringing'"),{'a':self.account,'d':self.device,'c':self.call}).scalar()
            db.execute(text("UPDATE telephony_push_outbox SET status='waiting_configuration' WHERE account_id=:a"),{'a':self.account}); db.commit()
        finally: db.close()
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        db=SessionLocal()
        try: n2=db.execute(text("SELECT count(*) FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).scalar()
        finally: db.close()
        self.assertEqual(n,1); self.assertEqual(n2,1)

    def test_default_push_worker_excludes_broad_synthetic_namespace(self):
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        with patch.object(T,'_send_fcm_push',side_effect=AssertionError('default production worker must not send synthetic push')) as send:
            out=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=False)
        self.assertEqual(out.get('processed'),0); self.assertEqual(send.call_count,0)
        db=SessionLocal()
        try: row=db.execute(text("SELECT status,attempts FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).mappings().one()
        finally: db.close()
        self.assertEqual(row.get('status'),'pending'); self.assertEqual(int(row.get('attempts') or 0),0)

    def test_waiting_configuration_sleeps_without_queue_churn(self):
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        with patch.dict(T.os.environ,{'BORIS_FCM_PROJECT_ID':'','BORIS_FCM_SERVICE_ACCOUNT_JSON':''},clear=False):
            first=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=True)
            second=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=True)
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT status,next_attempt_at,attempts FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).mappings().one()
        finally: db.close()
        self.assertEqual(first.get('processed'),1); self.assertEqual(first.get('waiting_configuration'),1)
        self.assertEqual(second.get('processed'),0)
        self.assertEqual(row.get('status'),'waiting_configuration'); self.assertIsNone(row.get('next_attempt_at')); self.assertEqual(int(row.get('attempts') or 0),0)

    def test_revoke_after_queue_claim_prevents_provider_send(self):
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        def revoke_during_decrypt(_value):
            db=SessionLocal()
            try:
                db.execute(text("UPDATE telephony_devices SET revoked_at=now(),presence='offline' WHERE account_id=:a AND id=:d"),{'a':self.account,'d':self.device}); db.commit()
            finally: db.close()
            return 'qa-device-token'
        with patch.dict(T.os.environ,{'BORIS_FCM_PROJECT_ID':'qa-project','BORIS_FCM_SERVICE_ACCOUNT_JSON':'{}'},clear=False), \
             patch('app.crypto_utils.decrypt_secret',side_effect=revoke_during_decrypt), \
             patch.object(T,'_send_fcm_push',side_effect=AssertionError('revoked device must never cross provider boundary')) as send:
            out=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=True)
        self.assertEqual(send.call_count,0); self.assertEqual(out.get('processed'),1)
        db=SessionLocal()
        try: row=db.execute(text("SELECT status,last_error,attempts FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).mappings().one()
        finally: db.close()
        self.assertEqual(row.get('status'),'cancelled'); self.assertIn('revoked before provider send',str(row.get('last_error') or '')); self.assertEqual(int(row.get('attempts') or 0),0)

    def test_call_end_after_queue_claim_prevents_stale_ringing_push(self):
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        def end_call_during_decrypt(_value):
            db=SessionLocal()
            try:
                db.execute(text("UPDATE telephony_calls SET state='ended',ended_at=now() WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call})
                db.execute(text("UPDATE telephony_call_targets SET status='cancelled',ended_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':self.call,'d':self.device})
                db.commit()
            finally: db.close()
            return 'qa-device-token'
        with patch.dict(T.os.environ,{'BORIS_FCM_PROJECT_ID':'qa-project','BORIS_FCM_SERVICE_ACCOUNT_JSON':'{}'},clear=False), \
             patch('app.crypto_utils.decrypt_secret',side_effect=end_call_during_decrypt), \
             patch.object(T,'_send_fcm_push',side_effect=AssertionError('ended call must never emit a stale ringing push')) as send:
            out=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=True)
        self.assertEqual(send.call_count,0); self.assertEqual(out.get('processed'),1)
        db=SessionLocal()
        try: row=db.execute(text("SELECT status,last_error,attempts FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).mappings().one()
        finally: db.close()
        self.assertEqual(row.get('status'),'cancelled'); self.assertIn('call no longer ringing before provider send',str(row.get('last_error') or '')); self.assertEqual(int(row.get('attempts') or 0),0)

    def test_terminal_push_missing_receipt_retries_same_durable_intent(self):
        import hashlib
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='ended',ended_at=now() WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call})
            db.execute(text("UPDATE telephony_call_targets SET status='cancelled',ended_at=now() WHERE account_id=:a AND call_id=:c AND device_id=:d"),{'a':self.account,'c':self.call,'d':self.device})
            push_id=int(db.execute(text("""INSERT INTO telephony_push_outbox
              (account_id,device_id,call_id,event_type,status,attempts,sent_at,sent_push_token_hash,payload_json)
              VALUES(:a,:d,:c,'call.ended','sent',1,now()-interval '30 seconds',:h,
                     CAST(:p AS jsonb)) RETURNING id"""),
              {'a':self.account,'d':self.device,'c':self.call,
               'h':hashlib.sha256(b'qa-device-token').hexdigest(),
               'p':__import__('json').dumps({'call_id':self.call,'event':'call.ended'})}).scalar())
            db.commit()
        finally: db.close()
        with patch.dict(T.os.environ,{'BORIS_FCM_PROJECT_ID':'qa-project','BORIS_FCM_SERVICE_ACCOUNT_JSON':'{}'},clear=False), \
             patch('app.crypto_utils.decrypt_secret',return_value='qa-device-token'), \
             patch.object(T,'_send_fcm_push',return_value=('sent',None)) as send:
            out=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=True)
        self.assertEqual(out.get('receipt_requeued'),1)
        self.assertEqual(out.get('sent'),1)
        self.assertEqual(send.call_count,1)
        db=SessionLocal()
        try:
            rows=db.execute(text("""SELECT id,status,attempts,event_type FROM telephony_push_outbox
              WHERE account_id=:a AND device_id=:d AND call_id=:c AND event_type='call.ended'"""),
              {'a':self.account,'d':self.device,'c':self.call}).mappings().all()
        finally: db.close()
        self.assertEqual(len(rows),1)
        self.assertEqual(int(rows[0]['id']),push_id)
        self.assertEqual(rows[0]['status'],'sent')
        self.assertEqual(int(rows[0]['attempts']),2)

    def test_waiting_configuration_requeues_when_fcm_becomes_ready(self):
        T._queue_device_push(self.account,self.device,self.call,'call.ringing')
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_push_outbox SET status='waiting_configuration',next_attempt_at=NULL WHERE account_id=:a"),{'a':self.account}); db.commit()
        finally: db.close()
        with patch.dict(T.os.environ,{'BORIS_FCM_PROJECT_ID':'qa-project','BORIS_FCM_SERVICE_ACCOUNT_JSON':'{}'},clear=False), \
             patch('app.crypto_utils.decrypt_secret',return_value='qa-device-token'), \
             patch.object(T,'_send_fcm_push',return_value=('sent',None)) as send:
            out=T.dispatch_mobile_pushes(limit=5,account_id=self.account,include_synthetic=True)
        self.assertEqual(out.get('configuration_requeued'),1); self.assertEqual(out.get('sent'),1); self.assertEqual(send.call_count,1)
        db=SessionLocal()
        try: row=db.execute(text("SELECT status,attempts FROM telephony_push_outbox WHERE account_id=:a"),{'a':self.account}).mappings().one()
        finally: db.close()
        self.assertEqual(row.get('status'),'sent'); self.assertEqual(int(row.get('attempts') or 0),1)


class TelephonyProviderEventIdempotencyTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_event_'+uuid.uuid4().hex[:12]
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_audit','telephony_events','telephony_calls'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_same_provider_event_is_clean_duplicate(self):
        first=T.apply_event(self.account,'call.ringing',None,'qa-provider','provider-call-1','provider-event-1',{'direction':'inbound','from':'100','to':'200'})
        second=T.apply_event(self.account,'call.ringing',None,'qa-provider','provider-call-1','provider-event-1',{'direction':'inbound','from':'100','to':'200'})
        self.assertEqual(first.get('status'),'ok')
        self.assertEqual(second.get('status'),'duplicate')
        db=SessionLocal()
        try:
            events=db.execute(text("SELECT count(*) FROM telephony_events WHERE account_id=:a AND provider_event_id='provider-event-1'"),{'a':self.account}).scalar()
            calls=db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a AND provider_call_id='provider-call-1'"),{'a':self.account}).scalar()
        finally: db.close()
        self.assertEqual(events,1); self.assertEqual(calls,1)


class TelephonyRecordingSSRFTests(unittest.TestCase):
    class _Sock:
        def __init__(self,host): self.host=host
        def getpeername(self): return (self.host,443)
    class _Raw:
        def __init__(self,host): self._sock=TelephonyRecordingSSRFTests._Sock(host) if False else None
    @staticmethod
    def _resp(host=None):
        class O: pass
        r=O(); r.fp=O(); r.fp.raw=O()
        if host is not None: r.fp.raw._sock=TelephonyRecordingSSRFTests._Sock(host)
        return r
    def test_connected_private_peer_is_blocked(self):
        ok,reason=T._recording_response_peer_is_public(self._resp('127.0.0.1'))
        self.assertFalse(ok); self.assertEqual(reason,'non_public_peer_ip')
    def test_connected_public_peer_is_accepted(self):
        ok,peer=T._recording_response_peer_is_public(self._resp('8.8.8.8'))
        self.assertTrue(ok); self.assertEqual(peer,'8.8.8.8')
    def test_missing_peer_evidence_fails_closed(self):
        ok,reason=T._recording_response_peer_is_public(self._resp())
        self.assertFalse(ok); self.assertEqual(reason,'peer_ip_unavailable')


class TelephonyBootstrapCapabilityTruthTests(unittest.TestCase):
    def test_unverified_or_unselected_provider_advertises_no_controls_or_media(self):
        from unittest.mock import patch
        with patch.object(T,'provider_status',return_value={'provider':None,'provider_verified':False,'provider_state':'not_selected','adapter':{'capabilities':[]}}):
            out=T.client_bootstrap('__qa_bootstrap__','web','0.0.0')
        self.assertEqual(out['commands'],[])
        self.assertFalse(out['media_enabled'])

    def test_verified_uis_advertises_only_proven_controls_and_not_media(self):
        from unittest.mock import patch
        with patch.object(T,'provider_status',return_value={'provider':'uis','provider_verified':True,'provider_state':'connected','adapter':{'capabilities':['api','call_control']}}):
            out=T.client_bootstrap('__qa_bootstrap__','web','0.0.0')
        self.assertEqual(out['commands'],['hangup','hold','resume'])
        self.assertFalse(out['media_enabled'])


class TelephonyProviderCommandContractTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_cmdcontract_'+uuid.uuid4().hex[:12]; self.call='call_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state) VALUES(:c,:a,'uis','12345','outbound','active')"),{'c':self.call,'a':self.account}); db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_commands WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':self.account}); db.commit()
        finally: db.close()
    def test_uis_only_queues_proven_commands(self):
        blocked=T.queue_call_command(self.account,self.call,'transfer',{'to_number':'100'})
        self.assertEqual(blocked.get('status'),'provider_command_unsupported')
        self.assertEqual(blocked.get('supported_commands'),['hangup','hold','resume'])
        allowed=T.queue_call_command(self.account,self.call,'hold',{})
        self.assertEqual(allowed.get('status'),'queued')
        db=SessionLocal()
        try:
            cmds=[tuple(r) for r in db.execute(text("SELECT command,status FROM telephony_commands WHERE account_id=:a ORDER BY id"),{'a':self.account})]
        finally: db.close()
        self.assertEqual(cmds,[('hold','queued')])

    def test_control_idempotency(self):
        idem='ctrl_'+uuid.uuid4().hex
        first=T.queue_call_command(self.account,self.call,'hold',{},1,None,idem)
        replay=T.queue_call_command(self.account,self.call,'hold',{},1,None,idem)
        conflict=T.queue_call_command(self.account,self.call,'resume',{},1,None,idem)
        self.assertEqual(first.get('status'),'queued')
        self.assertEqual(replay.get('status'),'queued')
        self.assertTrue(replay.get('replayed'))
        self.assertEqual(conflict.get('status'),'idempotency_conflict')
        db=SessionLocal()
        try:
            n=db.execute(text("SELECT count(*) FROM telephony_commands WHERE account_id=:a AND call_id=:c AND idempotency_key=:i"),{'a':self.account,'c':self.call,'i':idem}).scalar()
        finally: db.close()
        self.assertEqual(n,1)


class TelephonyMediaCallBindingTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_media_bind_'+uuid.uuid4().hex[:12]; self.device='dev_'+uuid.uuid4().hex[:10]; self.other='dev_'+uuid.uuid4().hex[:10]; self.call='call_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            for d in (self.device,self.other):
                db.execute(text("INSERT INTO telephony_devices(id,account_id,name,platform,app_version,presence,last_seen_at) VALUES(:d,:a,'QA','android','0.1.0','online',now())"),{'d':d,'a':self.account})
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,provider_call_id,direction,state,device_id) VALUES(:c,:a,'uis','provider-bind-1','inbound','ringing',:d)"),{'c':self.call,'a':self.account,'d':self.device})
            db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_media_sessions','telephony_audit','telephony_call_targets','telephony_calls','telephony_devices','telephony_provider_configs'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_media_is_blocked_before_call_is_active(self):
        out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'call_not_media_active')
    def test_media_is_bound_to_the_claimed_device(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        out=T.request_media_session(self.account,self.other,self.call)
        self.assertEqual(out['status'],'call_not_claimed_by_device')
    def test_media_provider_success_is_discarded_if_call_ends_during_io(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        class EndingMediaAdapter:
            def media_session(adapter_self, **kwargs):
                qdb=SessionLocal()
                try:
                    qdb.execute(text("UPDATE telephony_calls SET state='ended',ended_at=now(),updated_at=now() WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); qdb.commit()
                finally: qdb.close()
                return AdapterResult(True,'ok',payload={'transport':'webrtc','signaling_protocol':'boris-webrtc-v1','signaling_url':'wss://media.example/ws','session_id':'qa-media-session','session_token':'qa-short-lived-token-123456','ttl_seconds':60,'ice_servers':[]})
        adapter=EndingMediaAdapter()
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':'x'})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=adapter), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'media_session_stale')
        self.assertEqual(out['reason'],'call_not_media_active_after_provider')
        self.assertNotIn('media',out)
        db=SessionLocal()
        try:
            validated=db.execute(text("SELECT count(*) FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='media.session.validated' AND result='ok'"),{'a':self.account,'c':self.call}).scalar() or 0
            blocked=db.execute(text("SELECT count(*) FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='media.session.postprovider_revalidate' AND result='blocked'"),{'a':self.account,'c':self.call}).scalar() or 0
        finally: db.close()
        self.assertEqual(validated,0); self.assertEqual(blocked,1)

    def test_missing_call_id_fails_closed(self):
        out=T.request_media_session(self.account,self.device,'')
        self.assertEqual(out['status'],'invalid_media_request')

    def test_media_transport_proof_requires_exact_activated_lease_and_bidirectional_rtp(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call})
            lease_id=int(db.execute(text("""INSERT INTO telephony_media_sessions(
                account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
                session_fingerprint,expires_at,activated_at
            ) VALUES(:a,:c,:d,'uis',:e,'webrtc','boris_gateway','active','qa-proof',now()+interval '5 minutes',now())
            RETURNING id"""),{'a':self.account,'c':self.call,'d':self.device,'e':'bp_'+uuid.uuid4().hex[:20]}).scalar())
            db.commit()
        finally: db.close()
        weak=T.report_media_transport_proof(self.account,self.device,self.call,lease_id,{
            'candidate_pair_succeeded':True,'dtls_state':'connected',
            'packets_sent':10,'packets_received':0,'bytes_sent':800,'bytes_received':0,
        })
        self.assertEqual(weak['status'],'media_transport_not_proven')
        good_stats={'candidate_pair_succeeded':True,'dtls_state':'connected','packets_sent':12,'packets_received':15,'bytes_sent':1600,'bytes_received':2100}
        proven=T.report_media_transport_proof(self.account,self.device,self.call,lease_id,good_stats)
        self.assertEqual(proven['status'],'ok'); self.assertTrue(proven['transport_proven'])
        replay=T.report_media_transport_proof(self.account,self.device,self.call,lease_id,good_stats)
        self.assertTrue(replay.get('replayed'))
        db=SessionLocal()
        try:
            ok_count=int(db.execute(text("SELECT count(*) FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='media.transport.proven' AND result='ok'"),{'a':self.account,'c':self.call}).scalar() or 0)
        finally: db.close()
        self.assertEqual(ok_count,1)



    def test_media_transport_proof_provider_managed_uses_rtp_as_activation_evidence(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call})
            lease_id=int(db.execute(text("""INSERT INTO telephony_media_sessions(
                account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
                session_fingerprint,expires_at,activated_at
            ) VALUES(:a,:c,:d,'uis',:e,'webrtc','provider','active','qa-provider-proof',now()+interval '5 minutes',NULL)
            RETURNING id"""),{'a':self.account,'c':self.call,'d':self.device,'e':'provider_'+uuid.uuid4().hex[:20]}).scalar())
            db.commit()
        finally: db.close()
        stats={'candidate_pair_succeeded':True,'dtls_state':'connected','packets_sent':12,'packets_received':15,'bytes_sent':1600,'bytes_received':2100}
        proven=T.report_media_transport_proof(self.account,self.device,self.call,lease_id,stats)
        self.assertEqual(proven['status'],'ok'); self.assertTrue(proven['transport_proven'])

    def test_media_transport_proof_boris_gateway_still_requires_bridge_activation(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call})
            lease_id=int(db.execute(text("""INSERT INTO telephony_media_sessions(
                account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
                session_fingerprint,expires_at,activated_at
            ) VALUES(:a,:c,:d,'mcn',:e,'sip_ws','boris_gateway','active','qa-gateway-proof',now()+interval '5 minutes',NULL)
            RETURNING id"""),{'a':self.account,'c':self.call,'d':self.device,'e':'bp_'+uuid.uuid4().hex[:20]}).scalar())
            db.commit()
        finally: db.close()
        stats={'candidate_pair_succeeded':True,'dtls_state':'connected','packets_sent':12,'packets_received':15,'bytes_sent':1600,'bytes_received':2100}
        blocked=T.report_media_transport_proof(self.account,self.device,self.call,lease_id,stats)
        self.assertEqual(blocked['status'],'media_transport_not_activated')

    def test_media_provider_error_text_is_never_exposed(self):
        secret='MEDIA-SECRET-TOKEN'
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        class NegativeMediaAdapter:
            def media_session(self, **kwargs):
                return AdapterResult(False,'provider_rejected',error='Authorization Bearer '+secret)
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':secret})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=NegativeMediaAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'provider_rejected')
        self.assertEqual(out['error_code'],'provider_provider_rejected')
        self.assertNotIn(secret,str(out))

    def test_media_provider_status_cannot_echo_current_credential(self):
        secret='MEDIASTATUSSECRET'
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        class NegativeMediaAdapter:
            def media_session(self, **kwargs):
                return AdapterResult(False,secret,error='synthetic')
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':secret})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=NegativeMediaAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'media_provider_error')
        self.assertEqual(out['error_code'],'provider_media_provider_error')
        self.assertNotIn(secret,str(out))
        db=SessionLocal()
        try: audit=db.execute(text("SELECT result,metadata_json FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='media.session' ORDER BY id DESC LIMIT 1"),{'a':self.account,'c':self.call}).mappings().one()
        finally: db.close()
        self.assertNotIn(secret,str(audit))

    def test_media_success_cannot_echo_long_lived_provider_credential_in_url(self):
        secret='MEDIA-LONG-LIVED-SECRET-123'
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        class LeakingMediaAdapter:
            def media_session(self, **kwargs):
                return AdapterResult(True,'ok',payload={'transport':'webrtc','signaling_protocol':'boris-webrtc-v1','signaling_url':'wss://media.invalid/ws?access='+secret,'session_id':'qa-media-session','session_token':'qa-short-lived-token-123456','ttl_seconds':60,'ice_servers':[]})
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':secret})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=LeakingMediaAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'media_contract_invalid')
        self.assertEqual(out['reason'],'media_provider_credential_echo')
        self.assertNotIn(secret,str(out))
        db=SessionLocal()
        try: audit=db.execute(text("SELECT metadata_json FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='media.session.invalid' ORDER BY id DESC LIMIT 1"),{'a':self.account,'c':self.call}).scalar() or {}
        finally: db.close()
        self.assertNotIn(secret,str(audit))

    def test_media_provider_exception_is_secret_free_fail_closed(self):
        secret='MEDIA-EXCEPTION-SECRET'
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        class ExplodingMediaAdapter:
            def media_session(self, **kwargs):
                raise RuntimeError('token='+secret)
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':secret})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=ExplodingMediaAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'media_provider_unavailable')
        self.assertEqual(out['error_code'],'provider_transport_error')
        self.assertNotIn(secret,str(out))
        db=SessionLocal()
        try: audit=db.execute(text("SELECT metadata_json FROM telephony_audit WHERE account_id=:a AND call_id=:c AND action='media.session' ORDER BY id DESC LIMIT 1"),{'a':self.account,'c':self.call}).scalar() or {}
        finally: db.close()
        self.assertNotIn(secret,str(audit))


    def test_successful_media_session_has_durable_secret_free_lease_and_expires(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        session_secret='SHORT-LIVED-MEDIA-SECRET-'+uuid.uuid4().hex
        session_id='media_'+uuid.uuid4().hex
        class GoodMediaAdapter:
            def media_session(self, **kwargs):
                return AdapterResult(True,'ok',payload={'transport':'webrtc','signaling_protocol':'boris-webrtc-v1','signaling_url':'wss://media.example/ws','session_id':session_id,'session_token':session_secret,'ttl_seconds':60,'ice_servers':[]})
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':'provider-secret-different'})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=GoodMediaAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'ok')
        self.assertGreater(int(out.get('media_lease_id') or 0),0)
        self.assertEqual(out.get('contract_version'),5)
        lease_id=int(out['media_lease_id'])
        db=SessionLocal()
        try:
            row=dict(db.execute(text("SELECT * FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).mappings().one())
        finally: db.close()
        self.assertEqual(row['account_id'],self.account)
        self.assertEqual(row['call_id'],self.call)
        self.assertEqual(row['device_id'],self.device)
        self.assertEqual(row['managed_by'],'provider')
        self.assertNotIn(session_secret,str(row))
        self.assertNotIn('session_token',row)
        released=T.release_media_session(self.account,self.device,self.call,lease_id,'qa_release')
        self.assertEqual(released['status'],'await_expiry')
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_media_sessions SET expires_at=now()-interval '1 second',next_cleanup_at=now()-interval '1 second' WHERE id=:i"),{'i':lease_id}); db.commit()
        finally: db.close()
        guarded=T.media_session_guardian(20,include_synthetic=True)
        self.assertGreaterEqual(guarded.get('expired',0),1)
        db=SessionLocal()
        try: status=db.execute(text("SELECT status FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).scalar()
        finally: db.close()
        self.assertEqual(status,'expired')

    def test_gateway_managed_stale_payload_is_revoked_and_lease_closed(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        endpoint='bp_'+uuid.uuid4().hex[:20]
        class EndingGatewayAdapter:
            def media_session(adapter_self, **kwargs):
                qdb=SessionLocal()
                try:
                    qdb.execute(text("UPDATE telephony_calls SET state='ended',ended_at=now(),updated_at=now() WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); qdb.commit()
                finally: qdb.close()
                return AdapterResult(True,'ok',payload={
                    'transport':'sip_ws','managed_by':'boris_gateway','endpoint_id':endpoint,
                    'signaling_protocol':'boris-sip-v1','signaling_url':'wss://boris-ai.pro/phone-media/ws',
                    'session_id':endpoint,'session_token':'temporary-browser-secret-123456789',
                    'authorization_username':endpoint,'sip_uri':f'sip:{endpoint}@boris-ai.pro',
                    'register_expires_seconds':60,'ttl_seconds':60,'ice_servers':[]})
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':'provider-secret-xyz'})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=EndingGatewayAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch('app.services.phone_media_gateway.delete_webrtc_endpoint',return_value={'status':'ok','endpoint_id':endpoint}) as cleanup:
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'media_session_stale')
        self.assertNotIn('media',out)
        cleanup.assert_called_once_with(endpoint)
        db=SessionLocal()
        try: row=db.execute(text("SELECT status,cleaned_at FROM telephony_media_sessions WHERE account_id=:a AND endpoint_id=:e"),{'a':self.account,'e':endpoint}).mappings().one()
        finally: db.close()
        self.assertEqual(row['status'],'closed')
        self.assertIsNotNone(row['cleaned_at'])

    def test_gateway_credential_is_not_released_without_durable_ownership(self):
        db=SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET state='active' WHERE account_id=:a AND id=:c"),{'a':self.account,'c':self.call}); db.commit()
        finally: db.close()
        endpoint='bp_'+uuid.uuid4().hex[:20]
        class GatewayAdapter:
            def media_session(self, **kwargs):
                return AdapterResult(True,'ok',payload={
                    'transport':'sip_ws','managed_by':'boris_gateway','endpoint_id':endpoint,
                    'signaling_protocol':'boris-sip-v1','signaling_url':'wss://boris-ai.pro/phone-media/ws',
                    'session_id':endpoint,'session_token':'temporary-browser-secret-987654321',
                    'authorization_username':endpoint,'sip_uri':f'sip:{endpoint}@boris-ai.pro',
                    'register_expires_seconds':60,'ttl_seconds':60,'ice_servers':[]})
        with patch.object(T,'provider_credentials',return_value=('uis',{'token':'provider-secret-abc'})), patch.object(T,'_provider_public_config',return_value={}), patch('app.services.telephony_adapters.get_adapter',return_value=GatewayAdapter()), patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}), patch.object(T,'_persist_media_lease',return_value=(None,'qa_persistence_failed')), patch('app.services.phone_media_gateway.delete_webrtc_endpoint',return_value={'status':'ok','endpoint_id':endpoint}) as cleanup:
            out=T.request_media_session(self.account,self.device,self.call)
        self.assertEqual(out['status'],'media_lease_unavailable')
        self.assertNotIn('media',out)
        cleanup.assert_called_once_with(endpoint)


class TelephonyMediaApiContractTests(unittest.TestCase):
    def test_media_endpoint_requires_call_id(self):
        import inspect
        from app.api import telephony as API
        src=inspect.getsource(API.media_session)
        self.assertIn("body.get('call_id')",src)
        self.assertIn('request_media_session(account_id,device_id,call_id)',src)


class TelephonyProviderIdentityRaceTests(unittest.TestCase):
    def test_provider_call_identity_is_exactly_once_across_sessions(self):
        import concurrent.futures
        account='__qa_tel_callid_'+uuid.uuid4().hex[:12]
        provider_call_id='pc_'+uuid.uuid4().hex
        try:
            def create(_):
                return T.create_call(account,'inbound','+79990000001','+79990000002','uis',provider_call_id,metadata={'qa':True})['id']
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                ids=list(ex.map(create,range(6)))
            db=SessionLocal()
            try:
                count=int(db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a AND provider='uis' AND provider_call_id=:pc"),{'a':account,'pc':provider_call_id}).scalar() or 0)
            finally: db.close()
            self.assertEqual(len(set(ids)),1)
            self.assertEqual(count,1)
        finally:
            db=SessionLocal()
            try:
                db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':account}); db.commit()
            finally: db.close()

    def test_provider_recording_identity_is_exactly_once_across_sessions(self):
        import concurrent.futures
        account='__qa_tel_recid_'+uuid.uuid4().hex[:12]
        call_id='call_'+uuid.uuid4().hex
        recording_id='rec_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,provider,direction,state) VALUES(:c,:a,'uis','inbound','ended')"),{'c':call_id,'a':account}); db.commit()
        finally: db.close()
        try:
            def upsert(_):
                return T.recording_upsert(account,call_id,'uis',recording_id,'https://example.invalid/recording.wav',12)['status']
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                statuses=list(ex.map(upsert,range(6)))
            db=SessionLocal()
            try:
                count=int(db.execute(text("SELECT count(*) FROM telephony_recordings WHERE account_id=:a AND provider='uis' AND provider_recording_id=:r"),{'a':account,'r':recording_id}).scalar() or 0)
            finally: db.close()
            self.assertEqual(count,1)
            self.assertEqual(statuses.count('ok'),1)
            self.assertEqual(statuses.count('existing'),5)
        finally:
            db=SessionLocal()
            try:
                db.execute(text("DELETE FROM telephony_recordings WHERE account_id=:a"),{'a':account})
                db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':account}); db.commit()
            finally: db.close()

if __name__=='__main__': unittest.main()


class TelephonyCRMPhoneIdentityConcurrencyTests(unittest.TestCase):
    def test_calltracking_contact_identity_is_cross_process_safe(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from app.crm import calltracking_sync as CTS
        owner=700000000 + int(uuid.uuid4().hex[:6],16)%100000000
        phone='+79995550123'; barrier=Barrier(2)
        def worker():
            db=CTS.SessionLocal()
            try:
                barrier.wait(timeout=5)
                cid,created=CTS._contact(db,owner,'__qa_tel_identity',phone)
                db.commit()
                return int(cid),bool(created)
            finally:
                db.close()
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                results=[f.result(timeout=10) for f in [ex.submit(worker),ex.submit(worker)]]
            ids={x[0] for x in results}
            self.assertEqual(len(ids),1)
            self.assertEqual(sum(1 for _,created in results if created),1)
            db=SessionLocal()
            try:
                n=db.execute(text("SELECT count(*) FROM boris_crm_contacts WHERE owner_user_id=:o AND RIGHT(regexp_replace(COALESCE(primary_phone,''),'\\D','','g'),10)='9995550123'"),{'o':owner}).scalar()
            finally: db.close()
            self.assertEqual(int(n or 0),1)
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM boris_crm_contacts WHERE owner_user_id=:o'),{'o':owner}); db.commit()
            finally: db.close()


class TelephonyReportDeliveryExactlyOnceTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_report_'+uuid.uuid4().hex[:12]; self.key='qa-report-'+uuid.uuid4().hex
    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text('DELETE FROM telephony_report_deliveries WHERE account_id=:a'),{'a':self.account}); db.commit()
        finally: db.close()
    def _patches(self, sender):
        return (patch.object(T,'telephony_report_settings',return_value={'settings':{'email_to':'qa@example.invalid','telegram_chat_id':''}}),
                patch.object(T,'build_rop_phone_report',return_value={'text':'qa report'}),
                patch('app.services.email_service.send_email',side_effect=sender))
    def test_surviving_sending_claim_is_never_replayed(self):
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_report_deliveries(account_id,report_key,channel,recipient,status) VALUES(:a,:k,'email','qa@example.invalid','sending')"),{'a':self.account,'k':self.key}); db.commit()
        finally: db.close()
        p1,p2,p3=self._patches(AssertionError('must not resend ambiguous report'))
        with p1,p2,p3 as sender:
            out=T.deliver_rop_phone_report(self.account,self.key,1,['email'])
        self.assertEqual(out['results']['email']['status'],'sending'); self.assertEqual(sender.call_count,0)
    def test_transport_exception_becomes_ambiguous_terminal_tombstone(self):
        p1,p2,p3=self._patches(ConnectionError('socket closed after send'))
        with p1,p2,p3 as sender:
            first=T.deliver_rop_phone_report(self.account,self.key,1,['email'])
        self.assertEqual(first['results']['email']['status'],'ambiguous'); self.assertEqual(sender.call_count,1)
        p1,p2,p3=self._patches(AssertionError('ambiguous report must not replay'))
        with p1,p2,p3 as sender2:
            second=T.deliver_rop_phone_report(self.account,self.key,1,['email'])
        self.assertEqual(second['results']['email']['status'],'ambiguous'); self.assertEqual(sender2.call_count,0)



class TelephonyDevicePlatformPushResetTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_tel_'+uuid.uuid4().hex[:16]
        self.device='dev_qa_'+uuid.uuid4().hex
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_push_outbox','telephony_devices'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_device_list_online_respects_explicit_offline_presence(self):
        import app.services.telephony_core as T
        device='qa-list-offline-'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,1,'QA','web','offline',now())"),{'d':device,'a':self.account}); db.commit()
        finally:
            db.close()
        try:
            row=next(x for x in T.list_devices(self.account) if x.get('id')==device)
            self.assertEqual(row.get('presence'),'offline')
            self.assertFalse(row.get('online'))
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':device}); db.commit()
            finally:
                db.close()

    def test_invalid_heartbeat_presence_is_fail_closed(self):
        import app.services.telephony_core as T
        device='qa-presence-'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at) VALUES(:d,:a,1,'QA','web','dnd',now())"),{'d':device,'a':self.account}); db.commit()
        finally:
            db.close()
        try:
            result=T.heartbeat_device(self.account,device,'background_typo')
            self.assertEqual(result.get('status'),'invalid_presence')
            db=SessionLocal()
            try:
                presence=db.execute(text('SELECT presence FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':device}).scalar()
            finally:
                db.close()
            self.assertEqual(presence,'dnd')
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':device}); db.commit()
            finally:
                db.close()

    def test_android_notification_permission_is_routing_gate(self):
        off=self.device+'-off'; on=self.device+'-on'
        first=T.register_device(self.account,1,'Android no notifications','android','1.0',{'calls':True,'notifications':False,'native_call_ui':True},off)
        second=T.register_device(self.account,1,'Android ready','android','1.0',{'calls':True,'notifications':True,'native_call_ui':True},on)
        self.assertEqual(first.get('status'),'ok'); self.assertEqual(second.get('status'),'ok')
        off_health=T.device_runtime_health(self.account,off); on_health=T.device_runtime_health(self.account,on)
        self.assertFalse(off_health.get('notifications_capable')); self.assertFalse(off_health.get('incoming_ui_ready'))
        self.assertTrue(on_health.get('notifications_capable')); self.assertTrue(on_health.get('incoming_ui_ready'))
        ids={str(x.get('id')) for x in T._route_local_devices(self.account,{'destination_kind':'device','destination_value':'all_online','strategy':'ring_all'})}
        self.assertNotIn(off,ids); self.assertIn(on,ids)

    def test_terminal_push_targets_only_native_devices_bound_to_call(self):
        call_id='qa-terminal-'+uuid.uuid4().hex[:12]
        android=self.device+'-android'; web=self.device+'-web'
        self.assertEqual(T.register_device(self.account,1,'Android','android','1.0',{'notifications':True},android).get('status'),'ok')
        self.assertEqual(T.register_device(self.account,1,'Web','web','1.0',{'notifications':True},web).get('status'),'ok')
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_call_targets(account_id,call_id,device_id,status)
              VALUES(:a,:c,:d1,'ringing'),(:a,:c,:d2,'ringing')"""),{'a':self.account,'c':call_id,'d1':android,'d2':web})
            db.commit()
        finally: db.close()
        with patch.object(T,'_queue_device_push') as q:
            out=T._queue_terminal_call_pushes(self.account,call_id,'call.ended')
        self.assertEqual(out.get('status'),'ok'); self.assertEqual(out.get('candidates'),1)
        q.assert_called_once_with(self.account,android,call_id,'call.ended')
        with patch.object(T,'_queue_device_push') as q2:
            ignored=T._queue_terminal_call_pushes(self.account,call_id,'call.ringing')
        self.assertEqual(ignored.get('status'),'ignored'); q2.assert_not_called()

    def test_register_device_normalizes_platform_for_native_push_routing(self):
        import app.services.telephony_core as T
        device='qa-platform-case-'+uuid.uuid4().hex[:12]
        try:
            result=T.register_device(self.account,1,'QA Phone','iOS','1.0',{},device,'apns_voip','a'*64)
            self.assertEqual(result.get('status'),'ok')
            db=SessionLocal()
            try:
                row=db.execute(text('SELECT platform,push_kind FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':device}).mappings().one()
            finally:
                db.close()
            self.assertEqual(row.get('platform'),'ios')
            self.assertEqual(row.get('push_kind'),'apns_voip')
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':device}); db.commit()
            finally:
                db.close()

    def test_platform_change_clears_incompatible_native_push_material(self):
        # A stable device id may survive app reinstall/migration. Changing native
        # platform without supplying a new compatible token must not retain the
        # previous platform's secret/token kind.
        fake_crypto=types.ModuleType('app.crypto_utils')
        fake_crypto.encrypt_secret=lambda value:'enc-ios-token'
        with patch.dict('os.environ', {'FERNET_KEY':'qa-present-only'}, clear=False), patch.dict(sys.modules, {'app.crypto_utils':fake_crypto}):
            first=T.register_device(self.account,1,'QA Phone','ios','1.0',{},self.device,'apns_voip','a'*64)
        self.assertEqual(first.get('status'),'ok')
        second=T.register_device(self.account,1,'QA Phone','android','2.0',{},self.device,None,None)
        self.assertEqual(second.get('status'),'ok')
        db=SessionLocal()
        try:
            row=db.execute(text('SELECT platform,push_kind,push_token_hash,push_token_enc FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':self.device}).mappings().one()
        finally: db.close()
        self.assertEqual(row['platform'],'android')
        self.assertIsNone(row['push_kind'])
        self.assertIsNone(row['push_token_hash'])
        self.assertIsNone(row['push_token_enc'])


    def test_reconnect_replaces_client_capabilities_but_preserves_owner_extension(self):
        first=T.register_device(self.account,1,'QA Phone','web','1.0',{'calls':True,'media':True,'background':True},self.device)
        self.assertEqual(first.get('status'),'ok')
        ext=T.set_device_extension(self.account,self.device,'123')
        self.assertEqual(ext.get('status'),'ok')
        second=T.register_device(self.account,1,'QA Phone','web','2.0',{'calls':False},self.device)
        self.assertEqual(second.get('status'),'ok')
        db=SessionLocal()
        try:
            caps=db.execute(text('SELECT capabilities_json FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':self.account,'d':self.device}).scalar() or {}
        finally: db.close()
        self.assertEqual(caps.get('calls'),False)
        self.assertNotIn('media',caps)
        self.assertNotIn('background',caps)
        self.assertEqual(caps.get('extension'),'123')


class TelephonyDeviceCapabilityPrivilegeTests(unittest.TestCase):
    def test_registration_cannot_overwrite_owner_managed_extension(self):
        account='__qa_tel_cap_'+uuid.uuid4().hex[:12]; device='qa-dev-'+uuid.uuid4().hex[:12]
        try:
            first=T.register_device(account,None,'QA','web','0.0.0',{'calls':True,'extension':'999'},device)
            self.assertEqual(first.get('status'),'ok')
            db=SessionLocal()
            try: caps=db.execute(text('SELECT capabilities_json FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':account,'d':device}).scalar() or {}
            finally: db.close()
            self.assertNotIn('extension',caps); self.assertTrue(caps.get('calls'))
            owner=T.set_device_extension(account,device,'123')
            self.assertEqual(owner.get('status'),'ok')
            second=T.register_device(account,None,'QA','web','0.0.1',{'media':True,'extension':'999'},device)
            self.assertEqual(second.get('status'),'ok')
            db=SessionLocal()
            try: caps=db.execute(text('SELECT capabilities_json FROM telephony_devices WHERE account_id=:a AND id=:d'),{'a':account,'d':device}).scalar() or {}
            finally: db.close()
            self.assertEqual(caps.get('extension'),'123'); self.assertTrue(caps.get('media'))
        finally:
            db=SessionLocal()
            try: db.execute(text('DELETE FROM telephony_devices WHERE account_id=:a'),{'a':account}); db.commit()
            finally: db.close()


class TelephonyOutboundNumberSafetyTests(unittest.TestCase):
    def setUp(self):
        self._entitlement_patch=patch.object(T,'phone_entitlement_status',return_value={'status':'active','active':True})
        self._entitlement_patch.start()

    def tearDown(self):
        self._entitlement_patch.stop()

    def test_malformed_or_short_destination_fails_before_provider_lookup(self):
        for value in ('','1','12345','+0000000','abc'):
            out=T.request_outbound_call('__qa_tel_number_gate',value,idempotency_key='qa-safe-number-1234')
            self.assertEqual(out.get('status'),'invalid_number',value)

    def test_canonical_e164_shape_reaches_normal_provider_gate(self):
        out=T.request_outbound_call('__qa_tel_number_gate','+79991234567',idempotency_key='qa-safe-number-5678')
        self.assertEqual(out.get('status'),'provider_not_selected')

    def test_unpaid_phone_stops_before_provider_credentials_or_adapter_io(self):
        with patch.object(T,'selected_provider',return_value='qa'), \
             patch.object(T,'phone_entitlement_status',return_value={'status':'not_entitled','active':False}), \
             patch.object(T,'provider_credentials') as credentials, \
             patch('app.services.telephony_adapters.get_adapter') as adapter:
            out=T.request_outbound_call(
                'real-unpaid-account',
                '+79991234567',
                idempotency_key='unpaid-runtime-gate-1234',
            )
        self.assertEqual(out.get('status'),'phone_entitlement_required')
        credentials.assert_not_called()
        adapter.assert_not_called()


    def test_provider_transport_exception_never_exposes_secret_material(self):
        account='__qa_tel_outbound_redact_'+uuid.uuid4().hex[:12]
        secret='SUPERSECRET-PROVIDER-TOKEN'
        class ExplodingAdapter:
            def make_call(self, **kwargs):
                raise RuntimeError('Authorization: Bearer '+secret+' https://provider.invalid?token='+secret)
        try:
            with patch.object(T,'selected_provider',return_value='qa'), \
                 patch.object(T,'provider_credentials',return_value=('qa',{'token':secret})), \
                 patch.object(T,'_provider_public_config',return_value={}), \
                 patch('app.services.telephony_adapters.get_adapter',return_value=ExplodingAdapter()), \
                 patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
                out=T.request_outbound_call(account,'+79991234567',user_id=910001,idempotency_key='qa-redact-provider-1234')
            self.assertEqual(out.get('status'),'outbound_ambiguous')
            self.assertEqual(out.get('error_code'),'provider_transport_ambiguous')
            self.assertNotIn(secret,str(out))
            db=SessionLocal()
            try:
                row=db.execute(text("SELECT status,last_error FROM telephony_outbound_intents WHERE account_id=:a"),{'a':account}).mappings().one()
                audit=db.execute(text("SELECT metadata_json FROM telephony_audit WHERE account_id=:a ORDER BY id DESC LIMIT 1"),{'a':account}).scalar() or {}
            finally: db.close()
            self.assertEqual(row['status'],'ambiguous')
            self.assertNotIn(secret,str(row['last_error']))
            self.assertNotIn(secret,str(audit))
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_audit WHERE account_id=:a'),{'a':account})
                db.execute(text('DELETE FROM telephony_outbound_intents WHERE account_id=:a'),{'a':account})
                db.commit()
            finally: db.close()


    def test_outbound_replay_never_exposes_legacy_persisted_error_text(self):
        account='__qa_tel_outbound_legacy_'+uuid.uuid4().hex[:12]
        secret='LEGACY-OUTBOUND-SECRET-TOKEN'
        class NegativeAdapter:
            def __init__(self): self.calls=0
            def make_call(self, **kwargs):
                self.calls+=1
                return AdapterResult(False,'provider_rejected',error='synthetic')
        adapter=NegativeAdapter()
        try:
            patches=(patch.object(T,'selected_provider',return_value='qa'),
                     patch.object(T,'provider_credentials',return_value=('qa',{'token':'qa-token'})),
                     patch.object(T,'_provider_public_config',return_value={}),
                     patch('app.services.telephony_adapters.get_adapter',return_value=adapter),
                     patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}))
            with patches[0],patches[1],patches[2],patches[3],patches[4]:
                first=T.request_outbound_call(account,'+79991234567',user_id=910001,idempotency_key='qa-legacy-replay-1234')
            self.assertEqual(first.get('status'),'provider_rejected'); self.assertEqual(adapter.calls,1)
            db=SessionLocal()
            try:
                db.execute(text("UPDATE telephony_outbound_intents SET last_error=:e WHERE account_id=:a"),{'a':account,'e':'Authorization: Bearer '+secret}); db.commit()
            finally: db.close()
            with patch.object(T,'selected_provider',return_value='qa'), \
                 patch.object(T,'provider_credentials',return_value=('qa',{'token':'qa-token'})), \
                 patch.object(T,'_provider_public_config',return_value={}), \
                 patch('app.services.telephony_adapters.get_adapter',return_value=adapter), \
                 patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
                replay=T.request_outbound_call(account,'+79991234567',user_id=910001,idempotency_key='qa-legacy-replay-1234')
            self.assertEqual(replay.get('status'),'outbound_failed')
            self.assertTrue(replay.get('replayed'))
            self.assertEqual(adapter.calls,1)
            self.assertNotIn(secret,str(replay)); self.assertNotIn('Authorization',str(replay))
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_audit WHERE account_id=:a'),{'a':account})
                db.execute(text('DELETE FROM telephony_outbound_intents WHERE account_id=:a'),{'a':account})
                db.commit()
            finally: db.close()

    def test_provider_negative_status_cannot_echo_current_credential(self):
        account='__qa_tel_outbound_status_'+uuid.uuid4().hex[:12]
        secret='OUTBOUNDSTATUSSECRET'
        class NegativeAdapter:
            def make_call(self, **kwargs):
                return AdapterResult(False,secret,error='synthetic')
        try:
            with patch.object(T,'selected_provider',return_value='qa'), \
                 patch.object(T,'provider_credentials',return_value=('qa',{'token':secret})), \
                 patch.object(T,'_provider_public_config',return_value={}), \
                 patch('app.services.telephony_adapters.get_adapter',return_value=NegativeAdapter()), \
                 patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
                out=T.request_outbound_call(account,'+79991234567',user_id=910001,idempotency_key='qa-status-redact-1234')
            self.assertEqual(out.get('status'),'provider_error')
            self.assertEqual(out.get('error_code'),'provider_provider_error')
            self.assertNotIn(secret,str(out))
            db=SessionLocal()
            try:
                row=db.execute(text("SELECT status,last_error FROM telephony_outbound_intents WHERE account_id=:a"),{'a':account}).mappings().one()
                audit=db.execute(text("SELECT metadata_json FROM telephony_audit WHERE account_id=:a ORDER BY id DESC LIMIT 1"),{'a':account}).scalar() or {}
            finally: db.close()
            self.assertEqual(row['status'],'failed')
            self.assertEqual(row['last_error'],'provider status: provider_error')
            self.assertNotIn(secret,str(row)); self.assertNotIn(secret,str(audit))
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_audit WHERE account_id=:a'),{'a':account})
                db.execute(text('DELETE FROM telephony_outbound_intents WHERE account_id=:a'),{'a':account})
                db.commit()
            finally: db.close()

    def test_provider_negative_result_error_text_is_never_exposed_or_persisted(self):
        account='__qa_tel_outbound_result_'+uuid.uuid4().hex[:12]
        secret='RESULT-SECRET-TOKEN'
        class NegativeAdapter:
            def make_call(self, **kwargs):
                return AdapterResult(False,'provider_rejected',error='token='+secret)
        try:
            with patch.object(T,'selected_provider',return_value='qa'), \
                 patch.object(T,'provider_credentials',return_value=('qa',{'token':secret})), \
                 patch.object(T,'_provider_public_config',return_value={}), \
                 patch('app.services.telephony_adapters.get_adapter',return_value=NegativeAdapter()), \
                 patch('app.services.telephony_adapters.adapter_status',return_value={'implemented':True}):
                out=T.request_outbound_call(account,'+79991234567',user_id=910001,idempotency_key='qa-result-redact-1234')
            self.assertEqual(out.get('status'),'provider_rejected')
            self.assertEqual(out.get('error_code'),'provider_provider_rejected')
            self.assertNotIn(secret,str(out))
            db=SessionLocal()
            try: row=db.execute(text("SELECT status,last_error FROM telephony_outbound_intents WHERE account_id=:a"),{'a':account}).mappings().one()
            finally: db.close()
            self.assertEqual(row['status'],'failed')
            self.assertEqual(row['last_error'],'provider status: provider_rejected')
            self.assertNotIn(secret,str(row))
        finally:
            db=SessionLocal()
            try:
                db.execute(text('DELETE FROM telephony_audit WHERE account_id=:a'),{'a':account})
                db.execute(text('DELETE FROM telephony_outbound_intents WHERE account_id=:a'),{'a':account})
                db.commit()
            finally: db.close()


class TelephonyTranscriptEventIdempotencyTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); self.account='__qa_tel_transcript_'+uuid.uuid4().hex[:12]; self.call='call_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("INSERT INTO telephony_calls(id,account_id,direction,state) VALUES(:c,:a,'inbound','active')"),{'c':self.call,'a':self.account}); db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            for table in ('telephony_events','telephony_transcript_chunks','telephony_calls'):
                db.execute(text(f'DELETE FROM {table} WHERE account_id=:a'),{'a':self.account})
            db.commit()
        finally: db.close()
    def test_source_event_id_is_cross_process_atomic_even_when_seq_disagrees(self):
        from concurrent.futures import ThreadPoolExecutor
        source_event='provider-stt-'+uuid.uuid4().hex
        def run(seq): return T.ingest_transcript_chunk(self.account,self.call,seq,f'chunk-{seq}',source='provider_stream',source_event_id=source_event)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,[101,202]))
        statuses=sorted(str(x.get('status')) for x in results)
        self.assertEqual(statuses,['duplicate','ok'])
        db=SessionLocal()
        try:
            n=int(db.execute(text("SELECT count(*) FROM telephony_transcript_chunks WHERE account_id=:a AND source=:s AND source_event_id=:e"),{'a':self.account,'s':'provider_stream','e':source_event}).scalar() or 0)
        finally: db.close()
        self.assertEqual(n,1)
