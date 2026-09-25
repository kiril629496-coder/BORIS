# -*- coding: utf-8 -*-
import uuid
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import mcn_core
from app.services import telephony_core as T


class MCNCDRAutonomyTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        mcn_core.ensure_schema()
        self.account='__qa_mcn_cdr_guard_'+uuid.uuid4().hex[:12]
        self.call='call_'+uuid.uuid4().hex
        self.carrier='carrier_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_calls(
                id,account_id,provider,provider_call_id,direction,state,
                from_number,to_number,started_at,answered_at,ended_at,
                talk_duration_sec,updated_at
            ) VALUES(
                :c,:a,'mcn',:pc,'inbound','ended',
                '+79995550126','+74951230000',
                now()-interval '5 minutes',now()-interval '4 minutes',
                now()-interval '3 minutes',60,now()-interval '2 minutes'
            )"""),{'c':self.call,'a':self.account,'pc':self.carrier})
            db.commit()
        finally:
            db.close()
        link=mcn_core.carrier_link_upsert(self.account,self.call,{
            'carrier_call_id':self.carrier,
            'asterisk_uniqueid':'qa-'+uuid.uuid4().hex,
            'asterisk_linkedid':'qa-linked-'+uuid.uuid4().hex,
        })
        self.assertEqual(link['status'],'ok')
        cdr=mcn_core.carrier_cdr_upsert({
            'account_id':self.account,
            'carrier_call_id':self.carrier,
            'direction':'inbound',
            'from_number':'+79995550126',
            'to_number':'+74951230000',
            'started_at':datetime.now(timezone.utc),
            'actual_duration_sec':60,
            'billed_duration_sec':60,
            'carrier_amount':'0',
            'raw':{'qa':True},
        })
        self.assertEqual(cdr['status'],'ok')

    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_cdr_reconciliation WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_carrier_cdr WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_call_carrier_links WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':self.account})
            db.commit()
        finally:
            db.close()

    def test_common_guardian_reconciles_existing_mcn_cdr_without_owner(self):
        before=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertGreaterEqual(before['unreconciled_mcn_cdr'],1)
        with patch('app.services.phone_media_gateway.turn_runtime_status',
                   return_value={'configured':True,'runtime_ready':True}):
            out=T.telephony_autonomy_guardian(20,include_synthetic=True)
        self.assertEqual(out['status'],'ok')
        self.assertIn('mcn_cdr_reconcile',out['actions'])
        self.assertEqual(out['actions']['mcn_cdr_reconcile']['reconciled'],1)
        self.assertEqual(out['actions']['mcn_cdr_reconcile']['matched'],1)
        self.assertEqual(out['after']['unreconciled_mcn_cdr'],0)
        self.assertFalse(out['owner_action_required'])

    def test_global_reconcile_excludes_synthetic_by_default(self):
        out=mcn_core.reconcile_carrier_cdr(None,20,include_synthetic=False)
        self.assertEqual(out['picked'],0)
        out2=mcn_core.reconcile_carrier_cdr(None,20,include_synthetic=True)
        self.assertEqual(out2['reconciled'],1)


if __name__=='__main__':
    unittest.main()
