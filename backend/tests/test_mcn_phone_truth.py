# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from app.services.telephony_adapters.mcn import MCNAdapter
from app.services.telephony_core import _phone_primary_next_action
from app.services.mcn_core import readiness as mcn_readiness


class MCNPhoneTruthTests(unittest.TestCase):
    def test_no_provider_is_neutral_and_does_not_leak_provider_brand(self):
        out=_phone_primary_next_action(
            provider_key='',provider_connected=False,provider_has_credentials=False,
            telphin_sip=None,online_devices=0,gates={}
        )
        self.assertEqual(out['code'],'connect_telephony')
        self.assertIn('телефон',out['text'].lower())
        self.assertNotIn('MCN',out['text'])
        self.assertNotIn('Телфин',out['text'])

    def test_mcn_readiness_is_specific_after_owner_selects_mcn(self):
        out=mcn_readiness('__qa_mcn_scope_boundary_no_trunk__')
        self.assertFalse(out['ready'])
        self.assertIn('mcn_trunk_missing',out['blockers'])
        self.assertEqual(out['primary_next_action']['code'],'mcn_trunk_credentials')
        self.assertIn('Вставьте настройки',out['primary_next_action']['text'])

    def test_mcn_missing_did_is_owner_step_not_fake_health_retry(self):
        out=_phone_primary_next_action(
            provider_key='mcn',provider_connected=False,provider_has_credentials=True,
            telphin_sip=None,online_devices=0,gates={},
            mcn_readiness={'ready':False,'blockers':['mcn_active_did_missing']},
            mcn_transport={'ready':True,'owner_action_required':False},
            mcn_network={'ready':True},
        )
        self.assertEqual(out['code'],'mcn_did_required')
        self.assertTrue(out['owner_action_required'])

    def test_mcn_registration_transient_stays_boris_owned(self):
        out=_phone_primary_next_action(
            provider_key='mcn',provider_connected=False,provider_has_credentials=True,
            telphin_sip=None,online_devices=0,gates={},
            mcn_readiness={'ready':False,'blockers':['mcn_trunk_not_verified']},
            mcn_transport={'ready':False,'owner_action_required':False},
            mcn_network={'ready':True},
        )
        self.assertEqual(out['code'],'mcn_registration_self_heal')
        self.assertEqual(out['actor'],'boris')
        self.assertFalse(out['owner_action_required'])

    def test_mcn_rejected_credentials_escalate_to_owner(self):
        out=_phone_primary_next_action(
            provider_key='mcn',provider_connected=False,provider_has_credentials=True,
            telphin_sip=None,online_devices=0,gates={},
            mcn_readiness={'ready':False,'blockers':['mcn_trunk_not_verified']},
            mcn_transport={'ready':False,'owner_action_required':True},
            mcn_network={'ready':True},
        )
        self.assertEqual(out['code'],'mcn_sip_rejected')
        self.assertTrue(out['owner_action_required'])

    def test_mcn_adapter_never_reports_connected_with_network_blocker(self):
        adapter=MCNAdapter()
        with patch('app.services.asterisk_gateway.asterisk_health',return_value={'ready':True,'version':'QA'}), \
             patch('app.services.asterisk_gateway.application_health',return_value={'ready':True}), \
             patch('app.services.mcn_network_guard.mcn_firewall_health',return_value={
                 'status':'pending','ready':False,'pending_actions':2
             }), \
             patch('app.services.asterisk_gateway.mcn_account_transport_health') as transport:
            out=adapter.health({'account_id':'qa-account'},{'account_id':'qa-account'})
        self.assertFalse(out.ok)
        self.assertEqual(out.status,'mcn_network_not_ready')
        transport.assert_not_called()


if __name__=='__main__':
    unittest.main()

class MCNE2ETruthDBTests(unittest.TestCase):
    def setUp(self):
        import uuid
        from app.services import telephony_core as T
        T.ensure_schema()
        from app.services import mcn_core
        mcn_core.ensure_schema()
        self.account='__qa_mcn_e2e_truth_'+uuid.uuid4().hex[:12]
        self.call='call_'+uuid.uuid4().hex
        self.device='dev_'+uuid.uuid4().hex[:20]
        self.endpoint='bp_'+uuid.uuid4().hex[:24]

    def tearDown(self):
        from sqlalchemy import text
        from app.db.session import SessionLocal
        db=SessionLocal()
        try:
            for table in (
                'telephony_call_carrier_links','telephony_media_sessions','telephony_commands',
                'telephony_push_outbox','telephony_recordings','telephony_audit','telephony_events',
                'telephony_calls','telephony_devices','telephony_provider_configs',
                'telephony_dids','telephony_trunks'
            ):
                try:
                    db.execute(text(f"DELETE FROM {table} WHERE account_id=:a"),{'a':self.account})
                except Exception:
                    db.rollback()
            db.commit()
        finally:
            db.close()

    def _seed_complete_inbound_without_hold_resume(self):
        from sqlalchemy import text
        from app.db.session import SessionLocal
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_provider_configs(
                account_id,provider,credentials_enc,status,last_health_status,last_health_at
            ) VALUES(:a,'mcn','qa-marker','configured_unverified','pending',now())"""),{'a':self.account})
            token='a'*64
            db.execute(text("""INSERT INTO telephony_devices(
                id,account_id,name,platform,app_version,push_kind,push_token_hash,presence,last_seen_at
            ) VALUES(:d,:a,'QA Android','android','0.1.0','fcm',:t,'online',now())"""),
                       {'d':self.device,'a':self.account,'t':token})
            db.execute(text("""INSERT INTO telephony_calls(
                id,account_id,provider,provider_call_id,direction,state,from_number,to_number,
                device_id,answered_at,ended_at,crm_contact_id,crm_deal_id
            ) VALUES(:c,:a,'mcn','mcn-real-qa','inbound','ended','+79990001122','+74950001122',
                :d,now()-interval '2 minutes',now()-interval '1 minute',101,202)"""),
                       {'c':self.call,'a':self.account,'d':self.device})
            db.execute(text("""INSERT INTO telephony_events(
                account_id,call_id,provider,provider_event_id,event_type,payload_json
            ) VALUES(:a,:c,'mcn','ari-real-qa','call.answered','{}'::jsonb)"""),
                       {'a':self.account,'c':self.call})
            db.execute(text("""INSERT INTO telephony_call_carrier_links(
                account_id,call_id,provider,asterisk_uniqueid,metadata_json
            ) VALUES(:a,:c,'mcn','mcn-real-qa','{}'::jsonb)"""),
                       {'a':self.account,'c':self.call})
            db.execute(text("""INSERT INTO telephony_push_outbox(
                account_id,device_id,call_id,event_type,status,sent_at,device_received_at,
                device_received_via,sent_push_token_hash
            ) VALUES(:a,:d,:c,'call.ringing','sent',now(),now(),'authenticated_receipt',:t)"""),
                       {'a':self.account,'d':self.device,'c':self.call,'t':token})
            db.execute(text("""INSERT INTO telephony_recordings(
                account_id,call_id,provider,status,local_path,checksum,transcript,
                transcript_status,analysis_status
            ) VALUES(:a,:c,'mcn','available','/tmp/qa-mcn-e2e.wav','qa-checksum',
                'реальная qa расшифровка','done','done')"""),{'a':self.account,'c':self.call})
            db.execute(text("""INSERT INTO telephony_audit(
                account_id,action,call_id,provider,result,metadata_json
            ) VALUES
                (:a,'media.session.validated',:c,'mcn','ok','{}'::jsonb),
                (:a,'media.transport.proven',:c,'mcn','ok','{}'::jsonb)"""),
                       {'a':self.account,'c':self.call})
            db.execute(text("""INSERT INTO telephony_media_sessions(
                account_id,call_id,device_id,provider,endpoint_id,transport,managed_by,status,
                session_fingerprint,expires_at,cleaned_at
            ) VALUES(:a,:c,:d,'mcn',:e,'sip_ws','boris_gateway','closed','qa-fingerprint',now(),now())"""),
                       {'a':self.account,'c':self.call,'d':self.device,'e':self.endpoint})
            db.commit()
        finally:
            db.close()

    def test_full_e2e_cannot_pass_without_hold_resume_on_same_real_mcn_call(self):
        from sqlalchemy import text
        from app.db.session import SessionLocal
        from app.services.telephony_core import phone_client_readiness
        self._seed_complete_inbound_without_hold_resume()
        before=phone_client_readiness(self.account)
        self.assertTrue(before['telephony_gates']['mcn_carrier_linked'])
        self.assertTrue(before['telephony_gates']['real_crm_linked'])
        self.assertTrue(before['telephony_gates']['real_cleanup_completed'])
        self.assertFalse(before['telephony_gates']['real_hold_completed'])
        self.assertFalse(before['telephony_gates']['real_resume_completed'])
        self.assertFalse(before['telephony_gates']['real_inbound_e2e_chain'])

        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_commands(account_id,call_id,command,status)
                               VALUES(:a,:c,'hold','done'),(:a,:c,'resume','done')"""),
                       {'a':self.account,'c':self.call})
            db.commit()
        finally:
            db.close()
        after=phone_client_readiness(self.account)
        self.assertTrue(after['telephony_gates']['real_hold_completed'])
        self.assertTrue(after['telephony_gates']['real_resume_completed'])
        self.assertTrue(after['telephony_gates']['real_inbound_e2e_chain'])

class MCNMediaQACleanupSafetyTests(unittest.TestCase):
    def test_cleanup_hard_refuses_real_account_namespace(self):
        from tests.mcn_media_qa_cleanup import _safe_state
        with self.assertRaises(RuntimeError):
            _safe_state({
                'account_id':'real_client_account',
                'channel_id':'qa-mcn-media-carrier-v1',
                'endpoint_id':'bp_qa1234',
            })

    def test_cleanup_accepts_only_synthetic_mcn_namespace(self):
        from tests.mcn_media_qa_cleanup import _safe_state
        raw={
            'account_id':'__qa_mcn_media_guardian_v1',
            'channel_id':'qa-mcn-media-carrier-v1',
            'endpoint_id':'bp_qa1234',
        }
        self.assertEqual(_safe_state(raw),raw)
