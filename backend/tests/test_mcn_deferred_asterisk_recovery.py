# -*- coding: utf-8 -*-
import hashlib
import inspect
import uuid
import unittest
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import telephony_core as T
from app.services import asterisk_gateway as A


class MCNDeferredAsteriskRecoveryTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        A.ensure_schema()
        self.account='__qa_mcn_ari_deferred_'+uuid.uuid4().hex[:10]
        self.call='call_'+uuid.uuid4().hex
        self.channel='qa-mcn-deferred-'+uuid.uuid4().hex[:16]

    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_asterisk_events WHERE channel_id=:ch"),{'ch':self.channel})
            db.execute(text("DELETE FROM telephony_events WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':self.account})
            db.commit()
        finally:
            db.close()

    def _insert_call(self,state='active',answered=True,direction='inbound'):
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_calls(
                id,account_id,provider,provider_call_id,direction,state,
                from_number,to_number,started_at,answered_at,updated_at
            ) VALUES(
                :c,:a,'mcn',:pc,:dir,:st,
                '+79995550127','+74951230000',
                now()-interval '2 minutes',
                CASE WHEN :answered THEN now()-interval '1 minute' ELSE NULL END,
                now()-interval '45 seconds'
            )"""),{'c':self.call,'a':self.account,'pc':self.channel,'dir':direction,
                    'st':state,'answered':bool(answered)})
            db.commit()
        finally:
            db.close()

    def _insert_deferred(self,event_type,error_code,metadata=None):
        fp=hashlib.sha256((self.channel+'|'+event_type+'|'+uuid.uuid4().hex).encode()).hexdigest()
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_asterisk_events(
                event_fingerprint,event_type,channel_id,status,error_code,metadata_json,received_at,processed_at
            ) VALUES(:f,:t,:ch,'deferred',:e,CAST(:m AS jsonb),now()-interval '40 seconds',now()-interval '40 seconds')"""),
              {'f':fp,'t':event_type,'ch':self.channel,'e':error_code,
               'm':__import__('json').dumps(metadata or {},ensure_ascii=False)})
            db.commit()
        finally:
            db.close()
        return fp

    def test_unpaid_phone_blocks_direct_mcn_origination_before_asterisk_probe(self):
        with patch.object(
            T,
            'phone_entitlement_status',
            return_value={'status':'expired','active':False},
        ), patch.object(A,'asterisk_health') as health:
            out=A.originate_mcn(
                'real-expired',
                '+79991234567',
                '+74951234567',
                {'idempotency_key':'direct-unpaid-gate'},
            )
        self.assertEqual(out['status'],'phone_entitlement_required')
        health.assert_not_called()

    def test_rendered_mcn_pjsip_requires_current_paid_phone_entitlement(self):
        src=inspect.getsource(A.render_mcn_pjsip_config)
        self.assertIn('telephony_entitlements e',src)
        self.assertIn('e.account_id=t.account_id',src)
        self.assertIn('e.enabled=true',src)
        self.assertIn('e.paid_until>now()',src)

    def test_deferred_hold_is_applied_after_mcn_call_commit(self):
        self._insert_call(state='active',answered=True,direction='inbound')
        self._insert_deferred('ChannelHold','canonical_call_not_found',{})
        before=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertGreaterEqual(before['deferred_mcn_asterisk_events'],1)

        out=A.mcn_deferred_asterisk_event_guardian(20,include_synthetic=True)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['processed'],1)
        self.assertEqual(out['failed'],0)

        db=SessionLocal()
        try:
            call=db.execute(text("SELECT state FROM telephony_calls WHERE account_id=:a AND id=:c"),
                            {'a':self.account,'c':self.call}).scalar_one()
            ev=db.execute(text("""SELECT status,error_code,account_id,call_id
                                  FROM telephony_asterisk_events WHERE channel_id=:ch"""),
                          {'ch':self.channel}).mappings().one()
        finally:
            db.close()
        self.assertEqual(call,'on_hold')
        self.assertEqual(ev['status'],'processed')
        self.assertIsNone(ev['error_code'])
        self.assertEqual(ev['account_id'],self.account)
        self.assertEqual(ev['call_id'],self.call)
        after=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertEqual(after['deferred_mcn_asterisk_events'],0)

    def test_outbound_commit_race_links_event_without_creating_second_call(self):
        self._insert_call(state='connecting',answered=False,direction='outbound')
        self._insert_deferred('StasisStart','outbound_call_commit_race',{})
        db=SessionLocal()
        try:
            before_calls=int(db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a"),
                                        {'a':self.account}).scalar() or 0)
        finally:
            db.close()

        out=A.mcn_deferred_asterisk_event_guardian(20,include_synthetic=True)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['processed'],1)

        db=SessionLocal()
        try:
            after_calls=int(db.execute(text("SELECT count(*) FROM telephony_calls WHERE account_id=:a"),
                                       {'a':self.account}).scalar() or 0)
            ev=db.execute(text("""SELECT status,account_id,call_id
                                  FROM telephony_asterisk_events WHERE channel_id=:ch"""),
                          {'ch':self.channel}).mappings().one()
        finally:
            db.close()
        self.assertEqual(before_calls,1)
        self.assertEqual(after_calls,1)
        self.assertEqual(ev['status'],'processed')
        self.assertEqual(ev['account_id'],self.account)
        self.assertEqual(ev['call_id'],self.call)


if __name__=='__main__':
    unittest.main()
