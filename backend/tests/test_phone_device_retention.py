# -*- coding: utf-8 -*-
import unittest
import uuid
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import telephony_core as T


class PhoneDeviceRetentionTests(unittest.TestCase):
    def setUp(self):
        T.ensure_schema()
        self.account='__qa_tel_devret_'+uuid.uuid4().hex[:12]
        self.ids={k:'dev_'+k+'_'+uuid.uuid4().hex[:16] for k in ('purge','native','ext','history','dup_old','dup_new')}
        db=SessionLocal()
        try:
            base="""INSERT INTO telephony_devices(id,account_id,user_id,name,platform,app_version,presence,last_seen_at,created_at,updated_at,capabilities_json)
                    VALUES(:id,:a,1,'QA',:p,'qa','offline',now()-interval '8 days',now()-interval '8 days',now()-interval '8 days',CAST(:caps AS jsonb))"""
            db.execute(text(base),{'id':self.ids['purge'],'a':self.account,'p':'web','caps':'{}'})
            db.execute(text(base),{'id':self.ids['native'],'a':self.account,'p':'android','caps':'{}'})
            db.execute(text(base),{'id':self.ids['ext'],'a':self.account,'p':'web','caps':'{"extension":"105"}'})
            db.execute(text(base),{'id':self.ids['history'],'a':self.account,'p':'web','caps':'{}'})
            dup="""INSERT INTO telephony_devices(id,account_id,user_id,name,platform,app_version,presence,last_seen_at,created_at,updated_at,capabilities_json)
                   VALUES(:id,:a,2,'BORIS Web','web','2.0','offline',now()-(:mins||' minutes')::interval,
                          now()-(:mins||' minutes')::interval,now()-(:mins||' minutes')::interval,'{}'::jsonb)"""
            db.execute(text(dup),{'id':self.ids['dup_old'],'a':self.account,'mins':30})
            db.execute(text(dup),{'id':self.ids['dup_new'],'a':self.account,'mins':20})
            cid='call_'+uuid.uuid4().hex
            db.execute(text("""INSERT INTO telephony_calls(id,account_id,direction,state,device_id,started_at)
                              VALUES(:c,:a,'outbound','ended',:d,now()-interval '8 days')"""),
                       {'c':cid,'a':self.account,'d':self.ids['history']})
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("DELETE FROM telephony_calls WHERE account_id=:a"),{'a':self.account})
            db.execute(text("DELETE FROM telephony_devices WHERE account_id=:a"),{'a':self.account})
            db.commit()
        finally:
            db.close()

    def test_retention_purges_only_unused_stale_web_device(self):
        out=T.telephony_operational_retention_guard(limit=100)
        self.assertGreaterEqual(out['purged']['stale_web_devices'],1)
        db=SessionLocal()
        try:
            rows={r[0] for r in db.execute(text("SELECT id FROM telephony_devices WHERE account_id=:a"),{'a':self.account}).all()}
        finally:
            db.close()
        self.assertNotIn(self.ids['purge'],rows)
        self.assertNotIn(self.ids['dup_old'],rows)
        self.assertIn(self.ids['dup_new'],rows)
        self.assertIn(self.ids['native'],rows)
        self.assertIn(self.ids['ext'],rows)
        self.assertIn(self.ids['history'],rows)
        self.assertGreaterEqual(out['purged']['duplicate_web_devices'],1)
        self.assertIn('native_devices',out['preserved'])
        self.assertIn('newest_empty_web_device_per_user',out['preserved'])
        self.assertEqual(out['retention']['unused_web_device_days'],7)
        self.assertEqual(out['retention']['duplicate_web_device_idle_minutes'],10)


if __name__ == '__main__':
    unittest.main()
