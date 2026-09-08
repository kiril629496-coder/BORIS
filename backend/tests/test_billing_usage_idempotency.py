import json
import unittest
import uuid
from datetime import datetime
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.api.billing import check_and_consume

class BillingUsageIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.account='qa_billing_idem_'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            payload={"tier":"tariff_1","period_start":datetime.utcnow().isoformat(),
                     "usage_start":datetime.utcnow().isoformat(),
                     "usage":{"listings":0,"banners":0,"ai_templates":0,"images":0},
                     "unlimited":False}
            db.add(Storage(account_id=self.account,key='billing',value=json.dumps(payload)))
            db.commit()
        finally: db.close()
    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("delete from boris_billing_usage_intents where account_id=:a"),{'a':self.account})
            db.execute(text("delete from storage where account_id=:a"),{'a':self.account})
            db.commit()
        finally: db.close()
    def used(self):
        db=SessionLocal()
        try:
            row=db.query(Storage).filter(Storage.account_id==self.account,Storage.key=='billing').order_by(Storage.id.desc()).first()
            return int((json.loads(row.value).get('usage') or {}).get('banners') or 0)
        finally: db.close()
    def test_same_intent_consumes_once(self):
        key='qa:'+uuid.uuid4().hex
        first=check_and_consume(self.account,'banners',2,idempotency_key=key)
        second=check_and_consume(self.account,'banners',2,idempotency_key=key)
        self.assertTrue(first['allowed']); self.assertFalse(first['idempotency_replay'])
        self.assertTrue(second['allowed']); self.assertTrue(second['idempotency_replay'])
        self.assertEqual(self.used(),2)
    def test_same_key_different_amount_conflicts_without_charge(self):
        key='qa:'+uuid.uuid4().hex
        self.assertTrue(check_and_consume(self.account,'banners',1,idempotency_key=key)['allowed'])
        conflict=check_and_consume(self.account,'banners',2,idempotency_key=key)
        self.assertFalse(conflict['allowed'])
        self.assertEqual(conflict['blocked_reason'],'billing_idempotency_conflict')
        self.assertEqual(self.used(),1)
    def test_concurrent_same_intent_consumes_once(self):
        from concurrent.futures import ThreadPoolExecutor
        key='concurrent:'+uuid.uuid4().hex
        with ThreadPoolExecutor(max_workers=6) as ex:
            results=list(ex.map(lambda _: check_and_consume(self.account,'banners',1,idempotency_key=key), range(6)))
        self.assertTrue(all(r.get('allowed') for r in results))
        self.assertEqual(sum(1 for r in results if not r.get('idempotency_replay')),1)
        self.assertEqual(sum(1 for r in results if r.get('idempotency_replay')),5)
        self.assertEqual(self.used(),1)

    def test_different_intents_consume_independently(self):
        self.assertTrue(check_and_consume(self.account,'banners',1,idempotency_key='a:'+uuid.uuid4().hex)['allowed'])
        self.assertTrue(check_and_consume(self.account,'banners',1,idempotency_key='b:'+uuid.uuid4().hex)['allowed'])
        self.assertEqual(self.used(),2)

if __name__=='__main__': unittest.main()
