# -*- coding: utf-8 -*-
import uuid
import unittest
from unittest.mock import patch

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import telephony_core as T


class MCNCRMSelfHealDBTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_mcn_crm_heal_'+uuid.uuid4().hex[:12]
        self.call='call_'+uuid.uuid4().hex
        self.qa_email=f"{self.account}@example.invalid"
        db=SessionLocal()
        try:
            self.owner=int(db.execute(text("""INSERT INTO users(
                email,password_hash,role,is_active,email_verified,status,email_normalized
            ) VALUES(:e,'qa-not-a-real-password','owner',true,true,'active',:e)
            RETURNING id"""),{'e':self.qa_email}).scalar_one())
            db.execute(text("""INSERT INTO accounts(account_id,name,owner_user_id)
                               VALUES(:a,'QA MCN CRM self-heal',:o)"""),
                       {'a':self.account,'o':self.owner})
            db.execute(text("""INSERT INTO telephony_calls(
                id,account_id,provider,provider_call_id,direction,state,
                from_number,to_number,started_at,answered_at,ended_at,updated_at
            ) VALUES(
                :c,:a,'mcn','mcn-crm-heal-qa','inbound','ended',
                '+79995550124','+74951230000',
                now()-interval '5 minutes',
                now()-interval '4 minutes',
                now()-interval '3 minutes',
                now()-interval '2 minutes'
            )"""),{'c':self.call,'a':self.account})
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_commands WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_call_targets WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM boris_crm_tasks WHERE owner_user_id=:o"),{'o':self.owner})
            db.execute(text("DELETE FROM boris_crm_activities WHERE owner_user_id=:o"),{'o':self.owner})
            db.execute(text("DELETE FROM boris_crm_deals WHERE owner_user_id=:o"),{'o':self.owner})
            db.execute(text("DELETE FROM boris_crm_contacts WHERE owner_user_id=:o"),{'o':self.owner})
            db.execute(text("""DELETE FROM boris_crm_stages
                               WHERE pipeline_id IN (SELECT id FROM boris_crm_pipelines WHERE owner_user_id=:o)"""),
                       {'o':self.owner})
            db.execute(text("DELETE FROM boris_crm_pipelines WHERE owner_user_id=:o"),{'o':self.owner})
            db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM accounts WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM users WHERE id=:o AND email=:e"),{'o':self.owner,'e':self.qa_email})
            db.commit()
        finally:
            db.close()

    def test_stale_final_mcn_call_is_detected_and_repaired_without_owner(self):
        before=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertGreaterEqual(before['stale_mcn_crm_sync'],1)

        out=T.mcn_crm_sync_guardian(10,include_synthetic=True)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['picked'],1)
        self.assertEqual(out['completed'],1)
        self.assertEqual(out['failed'],0)

        after=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertEqual(after['stale_mcn_crm_sync'],0)

        db=SessionLocal()
        try:
            call=db.execute(text("""SELECT crm_contact_id,crm_deal_id
                                    FROM telephony_calls WHERE account_id=:a AND id=:c"""),
                            {'a':self.account,'c':self.call}).mappings().one()
            activity=int(db.execute(text("""SELECT count(*) FROM boris_crm_activities
                                           WHERE owner_user_id=:o AND source_ref=:r"""),
                                    {'o':self.owner,'r':f'boris_call:{self.account}:{self.call}'}).scalar() or 0)
        finally:
            db.close()
        self.assertIsNotNone(call['crm_contact_id'])
        self.assertIsNotNone(call['crm_deal_id'])
        self.assertEqual(activity,1)

    def test_full_autonomy_guardian_repairs_crm_and_terminal_residue_together(self):
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_call_targets(
                account_id,call_id,device_id,status,expires_at
            ) VALUES(:a,:c,'qa-dev-autonomy','ringing',now()+interval '10 minutes')"""),
                       {'a':self.account,'c':self.call})
            db.execute(text("""INSERT INTO telephony_commands(
                account_id,call_id,command,status,provider
            ) VALUES(:a,:c,'hold','queued','mcn')"""),
                       {'a':self.account,'c':self.call})
            db.commit()
        finally:
            db.close()

        before=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertGreaterEqual(before['stale_mcn_crm_sync'],1)
        self.assertGreaterEqual(before['stale_mcn_terminal_cleanup'],1)

        with patch('app.services.phone_media_gateway.turn_runtime_status',
                   return_value={'configured':True,'runtime_ready':True}):
            out=T.telephony_autonomy_guardian(20,include_synthetic=True)
        self.assertEqual(out['status'],'ok')
        self.assertIn('mcn_crm_sync',out['actions'])
        self.assertIn('mcn_terminal_cleanup',out['actions'])
        self.assertFalse(out['owner_action_required'])
        self.assertEqual(out['unresolved'],{})
        self.assertEqual(out['after']['stale_mcn_crm_sync'],0)
        self.assertEqual(out['after']['stale_mcn_terminal_cleanup'],0)

    def test_second_guardian_run_is_idempotent(self):
        first=T.mcn_crm_sync_guardian(10,include_synthetic=True)
        second=T.mcn_crm_sync_guardian(10,include_synthetic=True)
        self.assertEqual(first['completed'],1)
        self.assertEqual(second['picked'],0)
        self.assertEqual(second['completed'],0)


if __name__=='__main__':
    unittest.main()
