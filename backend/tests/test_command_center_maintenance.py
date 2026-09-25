import unittest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import command_store as store
from app.services.command_audit import reconcile_lifecycle

class CommandCenterMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.db=SessionLocal(); self.ids=[]
        r=self.db.execute(text("select account_id,owner_user_id from accounts where owner_user_id is not null order by id limit 1")).fetchone()
        if not r: self.skipTest('no account')
        self.account_id=str(r[0]); self.uid=int(r[1])
    def tearDown(self):
        for cid in self.ids:
            try:self.db.execute(text("delete from boris_command_events where command_id=cast(:c as uuid)"),{'c':cid})
            except Exception:self.db.rollback()
            try:self.db.execute(text("delete from boris_commands where command_id=cast(:c as uuid)"),{'c':cid})
            except Exception:self.db.rollback()
        self.db.commit(); self.db.close()
    def mk(self,status,minutes=30,expires=False):
        cid=str(uuid.uuid4()); self.ids.append(cid)
        self.assertTrue(store.create(self.db,cid,self.uid,'user',self.uid,self.account_id,'text',raw_text='qa'))
        ts=datetime.utcnow()-timedelta(minutes=minutes)
        self.db.execute(text("update boris_commands set status=:s,created_at=:ts,confirmed_at=:ts,expires_at=:ex where command_id=cast(:c as uuid)"),
                        {'s':status,'ts':ts,'ex':ts if expires else None,'c':cid}); self.db.commit(); return cid
    def status(self,cid):
        return self.db.execute(text("select status,error_code from boris_commands where command_id=cast(:c as uuid)"),{'c':cid}).fetchone()
    def test_expired_confirmation_is_terminal(self):
        cid=self.mk('awaiting_confirm',minutes=30,expires=True)
        out=reconcile_lifecycle(self.db)
        self.assertEqual(out['expired_confirm'],1); self.assertEqual(tuple(self.status(cid)),('expired','expired'))
    def test_executing_is_failed_uncertain_never_requeued(self):
        cid=self.mk('executing',minutes=30)
        out=reconcile_lifecycle(self.db,executing_timeout_minutes=10)
        self.assertEqual(out['uncertain_executing'],1)
        self.assertEqual(tuple(self.status(cid)),('failed','executor_outcome_uncertain'))
    def test_stale_confirmed_is_failed_not_executed(self):
        cid=self.mk('confirmed',minutes=30)
        out=reconcile_lifecycle(self.db,parsed_timeout_minutes=10)
        self.assertEqual(out['stale_preexecution'],1); self.assertEqual(tuple(self.status(cid)),('failed','stale_preexecution'))
    def test_fresh_live_rows_untouched(self):
        c1=self.mk('awaiting_confirm',minutes=0,expires=False); c2=self.mk('executing',minutes=0)
        out=reconcile_lifecycle(self.db)
        self.assertEqual(out['expired_confirm'],0); self.assertEqual(out['uncertain_executing'],0)
        self.assertEqual(self.status(c1)[0],'awaiting_confirm'); self.assertEqual(self.status(c2)[0],'executing')
if __name__=='__main__': unittest.main()
