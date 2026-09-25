import unittest
from unittest.mock import patch
from app.db.session import SessionLocal
from app.models.control_plane import ControlIncident
from app.models.storage import Storage
from app.reactivation_avito_health import save_inbox_sync_capability, INBOX_KEY, KEY, PERSISTENCE_KEY
from app.services.incident_reconciler import _sync_reactivation_external_incidents
from datetime import datetime, timezone
class ExternalIncidentLifecycleTests(unittest.TestCase):
 def setUp(self):
  self.db=SessionLocal(); self.a='qa_reactivation_external_incident'
  self.db.query(ControlIncident).filter(ControlIncident.account_id==self.a).delete(synchronize_session=False)
  self.db.query(Storage).filter(Storage.account_id==self.a,Storage.key.in_([INBOX_KEY,KEY,PERSISTENCE_KEY])).delete(synchronize_session=False); self.db.commit()
 def tearDown(self):
  self.db.query(ControlIncident).filter(ControlIncident.account_id==self.a).delete(synchronize_session=False); self.db.query(Storage).filter(Storage.account_id==self.a,Storage.key.in_([INBOX_KEY,KEY,PERSISTENCE_KEY])).delete(synchronize_session=False); self.db.commit(); self.db.close()
 def test_persistent_partial_opens_then_healthy_resolves(self):
  sample={'status':'partial','synced':2,'transport_errors':3,'transport_error_classes':[{'message':'402 messenger subscription','count':3}]}
  for _ in range(3): save_inbox_sync_capability(self.db,self.a,sample)
  with patch('app.api.messenger.get_manager_balance',return_value={'active':True}): r=_sync_reactivation_external_incidents(self.db,datetime.now(timezone.utc),account_ids=[self.a])
  self.db.commit()
  x=self.db.query(ControlIncident).filter(ControlIncident.account_id==self.a,ControlIncident.state=='open').one(); self.assertEqual(x.incident_type,'REACTIVATION_AVITO_PARTIAL_ACCESS'); self.assertTrue(x.details_json['service_continues'])
  save_inbox_sync_capability(self.db,self.a,{'status':'ok','synced':5,'transport_errors':0})
  with patch('app.api.messenger.get_manager_balance',return_value={'active':True}): _sync_reactivation_external_incidents(self.db,datetime.now(timezone.utc),account_ids=[self.a])
  self.db.commit(); self.assertEqual(self.db.query(ControlIncident).filter(ControlIncident.account_id==self.a,ControlIncident.state=='open').count(),0)
 def test_not_persistent_does_not_alert(self):
  save_inbox_sync_capability(self.db,self.a,{'status':'partial','synced':0,'transport_errors':2,'transport_error_classes':[{'message':'402 messenger subscription','count':2}]})
  with patch('app.api.messenger.get_manager_balance',return_value={'active':True}): _sync_reactivation_external_incidents(self.db,datetime.now(timezone.utc),account_ids=[self.a])
  self.db.commit(); self.assertEqual(self.db.query(ControlIncident).filter(ControlIncident.account_id==self.a,ControlIncident.state=='open').count(),0)
 def test_inactive_service_never_opens_external_incident(self):
  sample={'status':'partial','synced':2,'transport_errors':3,'transport_error_classes':[{'message':'402 messenger subscription','count':3}]}
  for _ in range(3): save_inbox_sync_capability(self.db,self.a,sample)
  with patch('app.api.messenger.get_manager_balance',return_value={'active':False}):
   r=_sync_reactivation_external_incidents(self.db,datetime.now(timezone.utc),account_ids=[self.a]); self.db.commit()
  self.assertEqual(r['opened'],0); self.assertEqual(self.db.query(ControlIncident).filter(ControlIncident.account_id==self.a,ControlIncident.state=='open').count(),0)
