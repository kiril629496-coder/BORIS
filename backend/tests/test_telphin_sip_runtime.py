# -*- coding: utf-8 -*-
import unittest
import uuid
import tempfile
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import asterisk_gateway as G
from app.services import telephony_core as T


class TelphinSipRuntimeTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema(); G.ensure_schema()
        self.account='__qa_telphin_sip_'+uuid.uuid4().hex[:12]
        self.endpoint='telphin-trunk-'+uuid.uuid4().hex[:16]

    def tearDown(self):
        db=SessionLocal()
        try:
            for table in (
                'telephony_media_sessions','telephony_events','telephony_audit','telephony_call_targets',
                'telephony_commands','telephony_push_outbox','telephony_asterisk_recordings','telephony_recordings','telephony_calls',
                'telephony_devices','telephony_provider_configs','telephony_telphin_trunk_state','telephony_recording_settings',
            ):
                try: db.execute(text(f"DELETE FROM {table} WHERE account_id=:a"),{'a':self.account})
                except Exception: db.rollback()
            db.commit()
        finally: db.close()

    def _trunk(self, status='registered'):
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_telphin_trunk_state(
              account_id,enabled,registrar,registration_id,endpoint_id,status,last_health_status,last_health_at,last_registered_at
            ) VALUES(:a,true,'sip.telphin.com',:r,:e,:s,:s,now(),CASE WHEN :s='registered' THEN now() ELSE NULL END)
            ON CONFLICT(account_id) DO UPDATE SET enabled=true,endpoint_id=:e,status=:s,last_health_status=:s,last_health_at=now()"""),
              {'a':self.account,'r':'telphin-reg-'+uuid.uuid4().hex[:16],'e':self.endpoint,'s':status})
            db.commit()
        finally: db.close()

    def _device_and_call(self, state='active'):
        device='dev_'+uuid.uuid4().hex[:16]; call='call_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_devices(id,account_id,user_id,name,platform,presence,last_seen_at)
              VALUES(:d,:a,1,'QA Browser','web','online',now())"""),{'d':device,'a':self.account})
            db.execute(text("""INSERT INTO telephony_calls(
              id,account_id,provider,provider_call_id,direction,state,from_number,to_number,device_id,started_at,answered_at
            ) VALUES(:c,:a,'telphin',:pc,'inbound',:s,'+79990001122','+74951234567',:d,now(),now())"""),
              {'c':call,'a':self.account,'pc':'telphin-channel-'+uuid.uuid4().hex[:16],'s':state,'d':device})
            db.commit()
        finally: db.close()
        return device,call

    def test_recording_policy_defaults_fail_closed_and_requires_owner_ack(self):
        current=T.recording_policy_settings(self.account)
        self.assertEqual(current['settings']['policy'],'manual')
        self.assertEqual(current['settings']['effective_notice_status'],'unknown')
        blocked=T.save_recording_policy_settings(self.account,{'policy':'not_required','acknowledged':False},1)
        self.assertEqual(blocked['status'],'acknowledgement_required')
        saved=T.save_recording_policy_settings(self.account,{'policy':'not_required','acknowledged':True},1)
        self.assertEqual(saved['status'],'ok')
        self.assertEqual(saved['settings']['policy'],'not_required')
        call=T.create_call(self.account,'inbound','+79990001122','+74951234567','telphin','policy-'+uuid.uuid4().hex[:16])
        self.assertEqual(call['recording_notice_status'],'not_required')

    def test_recording_policy_not_required_cannot_be_enabled_without_owner_identity(self):
        out=T.save_recording_policy_settings(
            self.account,{'policy':'not_required','acknowledged':True},None
        )
        self.assertEqual(out['status'],'owner_identity_required')
        current=T.recording_policy_settings(self.account)
        self.assertEqual(current['settings']['policy'],'manual')


    def test_recording_policy_internal_actor_none_is_valid(self):
        saved=T.save_recording_policy_settings(
            self.account,{'policy':'disabled','acknowledged':False},None
        )
        self.assertEqual(saved['status'],'ok')
        self.assertEqual(saved['settings']['policy'],'disabled')
        self.assertIsNone(saved['settings']['acknowledged_by_user_id'])


    def test_recording_policy_disabled_applies_to_new_calls(self):
        saved=T.save_recording_policy_settings(self.account,{'policy':'disabled','acknowledged':False},1)
        self.assertEqual(saved['status'],'ok')
        call=T.create_call(self.account,'outbound','+74951234567','+79990001122','telphin','disabled-'+uuid.uuid4().hex[:16])
        self.assertEqual(call['recording_notice_status'],'disabled')


    def test_inbound_stasis_creates_one_telphin_call_for_endpoint_account(self):
        self._trunk()
        channel='telphin-in-'+uuid.uuid4().hex[:16]
        event={
            'type':'StasisStart','timestamp':'2026-09-04T18:00:00Z',
            'args':['telphin-inbound',self.endpoint,'+74951234567'],
            'channel':{'id':channel,'state':'Ring','caller':{'number':'+79990001122'},
                       'dialplan':{'exten':'+74951234567'}},
        }
        with patch.object(G,'_claim_event',return_value=('claimed',None)), patch.object(G,'runtime_mark'), patch.object(G,'_finish_event'):
            out=G.process_ari_event(event)
        self.assertEqual(out['status'],'ok'); self.assertEqual(out['provider'],'telphin')
        db=SessionLocal()
        try:
            rows=db.execute(text("""SELECT provider,provider_call_id,direction,from_number,to_number,source
              FROM telephony_calls WHERE account_id=:a"""),{'a':self.account}).mappings().all()
        finally: db.close()
        self.assertEqual(len(rows),1)
        row=dict(rows[0])
        self.assertEqual(row['provider'],'telphin'); self.assertEqual(row['provider_call_id'],channel)
        self.assertEqual(row['direction'],'inbound'); self.assertEqual(row['source'],'telphin_sip')

    def test_telphin_answered_call_can_start_asterisk_recording(self):
        self._trunk(); device,call=self._device_and_call()
        db=SessionLocal()
        try:
            db.execute(text("""UPDATE telephony_calls
              SET recording_notice_status='not_required',recording_status='not_requested'
              WHERE account_id=:a AND id=:c"""),{'a':self.account,'c':call})
            channel=db.execute(text("SELECT provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),
                               {'a':self.account,'c':call}).scalar_one()
            db.commit()
        finally: db.close()
        with patch.object(G,'_request',return_value=(201,{'name':'ok'})) as req:
            out=G.ensure_call_recording(self.account,call,channel)
        self.assertEqual(out['status'],'ok')
        req.assert_called_once()
        db=SessionLocal()
        try:
            rec=db.execute(text("""SELECT status,account_id,call_id FROM telephony_asterisk_recordings
              WHERE account_id=:a AND call_id=:c"""),{'a':self.account,'c':call}).mappings().one()
            call_status=db.execute(text("SELECT recording_status FROM telephony_calls WHERE account_id=:a AND id=:c"),
                                   {'a':self.account,'c':call}).scalar_one()
        finally: db.close()
        self.assertEqual(rec['status'],'recording')
        self.assertEqual(call_status,'recording')

    def test_telphin_recording_notice_change_retries_recording_start(self):
        self._trunk(); device,call=self._device_and_call()
        db=SessionLocal()
        try:
            channel=db.execute(text("SELECT provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),
                               {'a':self.account,'c':call}).scalar_one()
        finally: db.close()
        with patch('app.services.asterisk_gateway.ensure_call_recording',return_value={'status':'ok'}) as start:
            out=T.set_recording_notice_status(self.account,call,'not_required')
        self.assertEqual(out['status'],'ok')
        start.assert_called_once_with(self.account,call,channel)

    def test_telphin_recording_finished_enters_shared_stt_pipeline_with_correct_provider(self):
        self._trunk(); device,call=self._device_and_call()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            spool=root/'spool'; recordings=root/'recordings'
            spool.mkdir(); recordings.mkdir()
            name=G._recording_name(self.account,call)
            source=spool/f'{name}.wav'
            source.write_bytes(b'RIFFqa-wave-bytes')
            db=SessionLocal()
            try:
                channel=db.execute(text("SELECT provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),
                                   {'a':self.account,'c':call}).scalar_one()
                db.execute(text("""INSERT INTO telephony_asterisk_recordings(
                  recording_name,account_id,call_id,channel_id,status,started_at
                ) VALUES(:n,:a,:c,:ch,'recording',now())"""),
                  {'n':name,'a':self.account,'c':call,'ch':channel})
                db.commit()
            finally: db.close()
            event={'type':'RecordingFinished','recording':{'name':name,'format':'wav','duration':7}}
            with patch.object(G,'ASTERISK_RECORDING_SPOOL',spool),                  patch.object(G,'BORIS_RECORDING_ROOT',recordings),                  patch.object(T,'TELEPHONY_RECORDING_ROOT',str(recordings)),                  patch.object(G,'_finish_event'),                  patch.object(G,'_request',return_value=(204,{})):
                out=G._recording_event(event,None)
            self.assertEqual(out['status'],'ok')
            self.assertEqual(out['recording'],'attached')
            db=SessionLocal()
            try:
                row=db.execute(text("""SELECT provider,status,local_path,checksum,transcript_status,analysis_status
                  FROM telephony_recordings WHERE account_id=:a AND call_id=:c"""),
                  {'a':self.account,'c':call}).mappings().one()
                ast=db.execute(text("""SELECT status,attached_recording_id FROM telephony_asterisk_recordings
                  WHERE account_id=:a AND call_id=:c"""),{'a':self.account,'c':call}).mappings().one()
            finally: db.close()
            self.assertEqual(row['provider'],'telphin')
            self.assertEqual(row['status'],'available')
            self.assertTrue(row['local_path'])
            self.assertTrue(row['checksum'])
            self.assertEqual(row['transcript_status'],'pending')
            self.assertEqual(row['analysis_status'],'pending')
            self.assertEqual(ast['status'],'attached')
            self.assertIsNotNone(ast['attached_recording_id'])

    def test_account_recording_policy_flows_through_real_ari_answer_path(self):
        self._trunk()
        saved=T.save_recording_policy_settings(self.account,{'policy':'not_required','acknowledged':True},1)
        self.assertEqual(saved['status'],'ok')
        channel='telphin-e2e-'+uuid.uuid4().hex[:16]
        start={
            'type':'StasisStart','timestamp':'2026-09-04T18:10:00Z',
            'args':['telphin-inbound',self.endpoint,'+74951234567'],
            'channel':{'id':channel,'state':'Ring','caller':{'number':'+79990001122'},
                       'dialplan':{'exten':'+74951234567'}},
        }
        with patch.object(G,'_claim_event',return_value=('claimed',None)),              patch.object(G,'runtime_mark'), patch.object(G,'_finish_event'):
            created=G.process_ari_event(start)
        self.assertEqual(created['status'],'ok')
        call_id=created['call_id']
        db=SessionLocal()
        try:
            notice=db.execute(text("SELECT recording_notice_status FROM telephony_calls WHERE account_id=:a AND id=:c"),
                              {'a':self.account,'c':call_id}).scalar_one()
        finally: db.close()
        self.assertEqual(notice,'not_required')

        answered={
            'type':'ChannelStateChange','timestamp':'2026-09-04T18:10:02Z',
            'channel':{'id':channel,'state':'Up','caller':{'number':'+79990001122'}},
        }
        with patch.object(G,'_claim_event',return_value=('claimed',None)),              patch.object(G,'runtime_mark'), patch.object(G,'_finish_event'),              patch.object(G,'_request',return_value=(201,{'name':'ok'})) as ari:
            result=G.process_ari_event(answered)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['mapped'],'call.answered')
        self.assertEqual((result.get('recording') or {}).get('status'),'ok')
        self.assertTrue(any('/record' in str(call.args[1]) for call in ari.call_args_list if len(call.args)>1))
        db=SessionLocal()
        try:
            row=db.execute(text("""SELECT c.state,c.recording_status,r.status AS ast_recording_status
              FROM telephony_calls c JOIN telephony_asterisk_recordings r
                ON r.account_id=c.account_id AND r.call_id=c.id
              WHERE c.account_id=:a AND c.id=:c"""),{'a':self.account,'c':call_id}).mappings().one()
        finally: db.close()
        self.assertEqual(row['state'],'active')
        self.assertEqual(row['recording_status'],'recording')
        self.assertEqual(row['ast_recording_status'],'recording')


    def test_media_leg_attaches_to_existing_lease_without_creating_second_call(self):
        self._trunk(); device,call=self._device_and_call()
        db=SessionLocal()
        try:
            lease_id=db.execute(text("""INSERT INTO telephony_media_sessions(
              account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
              session_fingerprint,expires_at,bridge_id
            ) VALUES(:a,:c,:d,'telphin',:e,'sip_ws','boris_gateway','active',:f,now()+interval '90 seconds','pending')
            RETURNING id"""),{'a':self.account,'c':call,'d':device,'e':'bp_'+uuid.uuid4().hex[:20],'f':uuid.uuid4().hex}).scalar_one()
            bridge='bm-'+str(lease_id)
            db.execute(text("UPDATE telephony_media_sessions SET bridge_id=:b WHERE id=:i"),{'b':bridge,'i':lease_id}); db.commit()
        finally: db.close()
        media_channel='bml-'+str(lease_id)
        event={'type':'StasisStart','timestamp':'2026-09-04T18:00:01Z',
               'args':['media-leg',self.account,call,str(lease_id),bridge],
               'channel':{'id':media_channel,'state':'Up'}}
        with patch.object(G,'_claim_event',return_value=('claimed',None)), patch.object(G,'runtime_mark'), patch.object(G,'_finish_event'), patch.object(G,'attach_media_leg',return_value={'status':'ok','http_status':204}) as attach:
            out=G.process_ari_event(event)
        self.assertEqual(out['status'],'ok'); attach.assert_called_once_with(bridge,media_channel)
        db=SessionLocal()
        try:
            lease=db.execute(text("SELECT media_channel_id,activated_at FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).mappings().one()
            calls=db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a"),{'a':self.account}).scalar_one()
        finally: db.close()
        self.assertEqual(lease['media_channel_id'],media_channel); self.assertIsNotNone(lease['activated_at'])
        self.assertEqual(calls,1)

    def test_activation_requires_exact_claimed_device_and_binds_browser_endpoint(self):
        self._trunk(); device,call=self._device_and_call()
        db=SessionLocal()
        try:
            lease_id=db.execute(text("""INSERT INTO telephony_media_sessions(
              account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
              session_fingerprint,expires_at
            ) VALUES(:a,:c,:d,'telphin',:e,'sip_ws','boris_gateway','active',:f,now()+interval '90 seconds')
            RETURNING id"""),{'a':self.account,'c':call,'d':device,'e':'bp_'+uuid.uuid4().hex[:20],'f':uuid.uuid4().hex}).scalar_one()
            provider_call_id=db.execute(text("SELECT provider_call_id FROM telephony_calls WHERE id=:c"),{'c':call}).scalar_one()
            endpoint_id=db.execute(text("SELECT endpoint_id FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).scalar_one()
            db.commit()
        finally: db.close()
        with patch('app.services.phone_media_gateway.webrtc_endpoint_health',return_value={'status':'registered','ready':True,'state':'registered'}), \
             patch('app.services.asterisk_gateway.bind_browser_media',return_value={
                 'status':'activating','bridge_id':'bm-'+str(lease_id),'media_channel_id':'bml-'+str(lease_id)
             }) as bind:
            out=T.activate_media_session(self.account,device,call,lease_id)
        self.assertEqual(out['status'],'activating')
        bind.assert_called_once_with(self.account,call,provider_call_id,endpoint_id,lease_id)
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT bridge_id,media_channel_id,last_bridge_error FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).mappings().one()
        finally: db.close()
        self.assertEqual(row['bridge_id'],'bm-'+str(lease_id)); self.assertEqual(row['media_channel_id'],'bml-'+str(lease_id))
        self.assertIsNone(row['last_bridge_error'])

    def test_media_lease_renewal_extends_only_same_live_call_device(self):
        self._trunk(); device,call=self._device_and_call()
        db=SessionLocal()
        try:
            lease_id=db.execute(text("""INSERT INTO telephony_media_sessions(
              account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
              session_fingerprint,expires_at
            ) VALUES(:a,:c,:d,'telphin',:e,'sip_ws','boris_gateway','active',:f,now()+interval '70 seconds')
            RETURNING id"""),{'a':self.account,'c':call,'d':device,'e':'bp_'+uuid.uuid4().hex[:20],'f':uuid.uuid4().hex}).scalar_one()
            before=db.execute(text("SELECT expires_at FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).scalar_one()
            db.commit()
        finally: db.close()
        out=T.renew_media_session(self.account,device,call,lease_id,180)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['media_lease_id'],lease_id)
        db=SessionLocal()
        try:
            after=db.execute(text("SELECT expires_at FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).scalar_one()
        finally: db.close()
        self.assertGreater((after-before).total_seconds(),60)

    def test_media_lease_renewal_never_resurrects_finished_call(self):
        self._trunk(); device,call=self._device_and_call()
        db=SessionLocal()
        try:
            lease_id=db.execute(text("""INSERT INTO telephony_media_sessions(
              account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
              session_fingerprint,expires_at
            ) VALUES(:a,:c,:d,'telphin',:e,'sip_ws','boris_gateway','active',:f,now()+interval '70 seconds')
            RETURNING id"""),{'a':self.account,'c':call,'d':device,'e':'bp_'+uuid.uuid4().hex[:20],'f':uuid.uuid4().hex}).scalar_one()
            db.execute(text("UPDATE telephony_calls SET state='ended',ended_at=now() WHERE account_id=:a AND id=:c"),{'a':self.account,'c':call})
            before=db.execute(text("SELECT expires_at FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).scalar_one()
            db.commit()
        finally: db.close()
        out=T.renew_media_session(self.account,device,call,lease_id,180)
        self.assertEqual(out['status'],'call_not_media_active')
        db=SessionLocal()
        try:
            after=db.execute(text("SELECT expires_at FROM telephony_media_sessions WHERE id=:i"),{'i':lease_id}).scalar_one()
        finally: db.close()
        self.assertEqual(after,before)


    def test_queued_telphin_sip_control_reaches_same_asterisk_channel(self):
        self._trunk()
        device,call=self._device_and_call()
        saved=T.save_provider_config(
            self.account,'telphin',
            credentials={'client_id':'qa-client','client_secret':'qa-api-secret','sip_line':'qa-line','sip_password':'qa-sip-secret'},
            public_config={'extension_id':'12345','src_num':'101','sip_enabled':True}
        )
        self.assertEqual(saved.get('status'),'ok')
        db=SessionLocal()
        try:
            provider_call_id=db.execute(text("SELECT provider_call_id FROM telephony_calls WHERE account_id=:a AND id=:c"),
                                        {'a':self.account,'c':call}).scalar_one()
        finally: db.close()
        queued=T.queue_call_command(self.account,call,'hold',{},1,device,'qa-hold-12345678')
        self.assertEqual(queued.get('status'),'queued')
        with patch('app.services.asterisk_gateway.command_telphin',return_value={'status':'ok','http_status':204}) as ctl:
            out=T.dispatch_pending_commands(20,include_synthetic=True)
        self.assertGreaterEqual(out.get('completed',0),1)
        ctl.assert_called_once_with(self.account,provider_call_id,'hold',{})
        db=SessionLocal()
        try:
            status=db.execute(text("""SELECT status FROM telephony_commands
              WHERE account_id=:a AND call_id=:c AND idempotency_key='qa-hold-12345678'"""),
              {'a':self.account,'c':call}).scalar_one()
        finally: db.close()
        self.assertEqual(status,'done')


if __name__=='__main__':
    unittest.main()
