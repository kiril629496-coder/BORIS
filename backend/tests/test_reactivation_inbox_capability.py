import json
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.reactivation_avito_health import save_inbox_sync_capability, effective_capability, INBOX_KEY, KEY, PERSISTENCE_KEY

class InboxCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.db=SessionLocal(); self.acc='qa_reactivation_inbox_capability'
        self.db.query(Storage).filter(Storage.account_id==self.acc, Storage.key.in_([INBOX_KEY,KEY,PERSISTENCE_KEY])).delete(synchronize_session=False); self.db.commit()
    def tearDown(self):
        self.db.query(Storage).filter(Storage.account_id==self.acc, Storage.key.in_([INBOX_KEY,KEY,PERSISTENCE_KEY])).delete(synchronize_session=False); self.db.commit(); self.db.close()
    def test_all_402_is_waiting_external(self):
        p=save_inbox_sync_capability(self.db,self.acc,{'status':'partial','synced':0,'transport_errors':5,'transport_error_classes':[{'message':'Avito API error 402: подписку с API мессенджера','count':5}]})
        self.assertEqual(p['state'],'waiting_external'); self.assertEqual(p['classification'],'avito_messenger_subscription_required')
        self.assertEqual(effective_capability(self.db,self.acc)['source'],'minute_inbox_sync')
    def test_some_readable_402_is_degraded_partial(self):
        p=save_inbox_sync_capability(self.db,self.acc,{'status':'partial','synced':3,'transport_errors':2,'transport_error_classes':[{'message':'402 messenger subscription','count':2}]})
        self.assertEqual(p['state'],'degraded'); self.assertEqual(p['classification'],'avito_messenger_history_partial_402')
    def test_clean_sync_is_healthy(self):
        p=save_inbox_sync_capability(self.db,self.acc,{'status':'ok','synced':5,'transport_errors':0})
        self.assertEqual(p['state'],'healthy'); self.assertEqual(p['classification'],'flow_ok')
    def test_top_level_avito_500_is_waiting_external_and_not_internal_recovery(self):
        from app.services.control_plane_adapters import _reactivation_diagnose
        p=save_inbox_sync_capability(self.db,self.acc,{'status':'error','synced':0,'transport_errors':0,'message':'Avito API error 500: '})
        self.assertEqual(p['state'],'waiting_external')
        self.assertEqual(p['classification'],'avito_api_unavailable')
        self.assertEqual(p['top_error_classification'],'avito_api_unavailable')
        d=_reactivation_diagnose(self.db,self.acc,None,{'settings':{'enabled':True},'mop_active':True,'avito_capability':p})
        self.assertEqual(d['health'],'waiting_external')
        self.assertEqual(d['dependency_state'],'waiting_external')
        self.assertFalse(d['owner_action_required'])
        self.assertTrue(d['auto_reprobe'])
    def test_top_level_avito_401_remains_fail_closed_degraded(self):
        p=save_inbox_sync_capability(self.db,self.acc,{'status':'error','synced':0,'transport_errors':0,'message':'Avito API error 401: unauthorized'})
        self.assertEqual(p['state'],'degraded')
        self.assertEqual(p['classification'],'avito_transport_degraded')
        self.assertEqual(p['top_error_classification'],'avito_auth_failed')
    def test_persistent_external_requires_consecutive_cycles(self):
        sample={'status':'partial','synced':0,'transport_errors':5,'transport_error_classes':[{'message':'402 messenger subscription','count':5}]}
        a=save_inbox_sync_capability(self.db,self.acc,sample); b=save_inbox_sync_capability(self.db,self.acc,sample); c=save_inbox_sync_capability(self.db,self.acc,sample)
        self.assertFalse(a['persistence']['persistent']); self.assertFalse(b['persistence']['persistent']); self.assertTrue(c['persistence']['persistent']); self.assertTrue(c['persistence']['owner_action_required']); self.assertEqual(effective_capability(self.db,self.acc)['persistence']['consecutive'],3)
    def test_recovery_resets_persistent_external(self):
        sample={'status':'partial','synced':0,'transport_errors':5,'transport_error_classes':[{'message':'402 messenger subscription','count':5}]}
        for _ in range(3): save_inbox_sync_capability(self.db,self.acc,sample)
        healthy=save_inbox_sync_capability(self.db,self.acc,{'status':'ok','synced':5,'transport_errors':0}); self.assertFalse(healthy['persistence']['persistent']); self.assertEqual(healthy['persistence']['consecutive'],1)

class ReactivationExactChatCapabilityTests(InboxCapabilityTests):
    def test_exact_blocked_chat_is_persisted_and_detected(self):
        from app.reactivation_avito_health import chat_transport_blocked
        p=save_inbox_sync_capability(self.db,self.acc,{'status':'partial','synced':2,'transport_errors':1,'transport_blocked_chat_ids':['chat-402'],'transport_error_classes':[{'message':'402 messenger subscription','count':1}]})
        self.assertEqual(p['transport_blocked_chat_ids'],['chat-402'])
        self.assertTrue(chat_transport_blocked(self.db,self.acc,'chat-402'))
        self.assertFalse(chat_transport_blocked(self.db,self.acc,'chat-ok'))

class EffectiveCapabilityPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.db=SessionLocal(); self.account='qa_effective_capability_precedence'
        self.db.query(Storage).filter(Storage.account_id==self.account,Storage.key.in_([KEY,INBOX_KEY,PERSISTENCE_KEY])).delete(synchronize_session=False); self.db.commit()
    def tearDown(self):
        self.db.query(Storage).filter(Storage.account_id==self.account,Storage.key.in_([KEY,INBOX_KEY,PERSISTENCE_KEY])).delete(synchronize_session=False); self.db.commit(); self.db.close()
    def _put(self,key,payload):
        self.db.add(Storage(account_id=self.account,key=key,value=json.dumps(payload))); self.db.commit()
    def test_fresh_full_inbox_outranks_slightly_newer_sample(self):
        now=datetime.now(timezone.utc)
        self._put(INBOX_KEY,{'checked_at':(now-timedelta(seconds=20)).isoformat(),'source':'minute_inbox_sync','classification':'avito_messenger_history_partial_402','state':'degraded'})
        self._put(KEY,{'checked_at':now.isoformat(),'classification':'avito_messenger_subscription_required','state':'waiting_external'})
        self.assertEqual(effective_capability(self.db,self.account).get('storage_key'),INBOX_KEY)
    def test_newer_sample_wins_after_inbox_is_stale(self):
        now=datetime.now(timezone.utc)
        self._put(INBOX_KEY,{'checked_at':(now-timedelta(seconds=240)).isoformat(),'source':'minute_inbox_sync','classification':'avito_messenger_history_partial_402','state':'degraded'})
        self._put(KEY,{'checked_at':now.isoformat(),'classification':'flow_ok','state':'healthy'})
        self.assertEqual(effective_capability(self.db,self.account).get('storage_key'),KEY)
