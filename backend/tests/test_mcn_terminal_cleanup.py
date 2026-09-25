# -*- coding: utf-8 -*-
import uuid
import unittest

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import telephony_core as T


class MCNTerminalCleanupDBTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_mcn_terminal_'+uuid.uuid4().hex[:12]
        self.call='call_'+uuid.uuid4().hex
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_calls(
                id,account_id,provider,provider_call_id,direction,state,
                from_number,to_number,started_at,ended_at,updated_at
            ) VALUES(
                :c,:a,'mcn','mcn-terminal-qa','inbound','ended',
                '+79995550125','+74951230000',
                now()-interval '5 minutes',
                now()-interval '3 minutes',
                now()-interval '2 minutes'
            )"""),{'c':self.call,'a':self.account})
            db.execute(text("""INSERT INTO telephony_call_targets(
                account_id,call_id,device_id,status,expires_at
            ) VALUES
                (:a,:c,'qa-dev-ring','ringing',now()+interval '10 minutes'),
                (:a,:c,'qa-dev-ans','answered',now()+interval '10 minutes')"""),
                       {'a':self.account,'c':self.call})
            db.execute(text("""INSERT INTO telephony_commands(
                account_id,call_id,command,status,provider
            ) VALUES
                (:a,:c,'hold','queued','mcn'),
                (:a,:c,'resume','waiting_provider','mcn')"""),
                       {'a':self.account,'c':self.call})
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_commands WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_call_targets WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':self.account})
            db.commit()
        finally:
            db.close()

    def test_terminal_mcn_residue_is_detected_and_cleaned_locally(self):
        before=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertGreaterEqual(before['stale_mcn_terminal_cleanup'],1)

        out=T.mcn_terminal_cleanup_guardian(10,include_synthetic=True)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['picked'],1)
        self.assertEqual(out['calls_cleaned'],1)
        self.assertEqual(out['targets_closed'],2)
        self.assertEqual(out['commands_closed'],2)

        db=SessionLocal()
        try:
            targets=[x[0] for x in db.execute(text("""SELECT status FROM telephony_call_targets
                                                     WHERE account_id=:a AND call_id=:c ORDER BY device_id"""),
                                               {'a':self.account,'c':self.call}).all()]
            commands=[x[0] for x in db.execute(text("""SELECT status FROM telephony_commands
                                                      WHERE account_id=:a AND call_id=:c ORDER BY id"""),
                                                {'a':self.account,'c':self.call}).all()]
        finally:
            db.close()
        self.assertEqual(sorted(targets),['cancelled','ended'])
        self.assertEqual(commands,['failed','failed'])
        after=T.telephony_autonomy_snapshot(include_synthetic=True)
        self.assertEqual(after['stale_mcn_terminal_cleanup'],0)

    def test_cleanup_is_idempotent_and_never_resends_provider_command(self):
        first=T.mcn_terminal_cleanup_guardian(10,include_synthetic=True)
        second=T.mcn_terminal_cleanup_guardian(10,include_synthetic=True)
        self.assertEqual(first['commands_closed'],2)
        self.assertEqual(second['picked'],0)
        self.assertEqual(second['commands_closed'],0)


if __name__=='__main__':
    unittest.main()
