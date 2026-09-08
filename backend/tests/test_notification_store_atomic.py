import json, threading, unittest, uuid
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.notification_store import append_notification, read_notifications, mark_read

class NotificationStoreAtomicTest(unittest.TestCase):
    def setUp(self):
        self.a='qa_notif_'+uuid.uuid4().hex[:12]
    def tearDown(self):
        s=SessionLocal(); s.query(Storage).filter(Storage.account_id==self.a,Storage.key=='notifications').delete(synchronize_session=False); s.commit(); s.close()
    def test_parallel_appends_preserve_both(self):
        barrier=threading.Barrier(2); errs=[]
        def run(i):
            s=SessionLocal()
            try:
                barrier.wait(); append_notification(s,self.a,{"ts":str(i),"text":f"n{i}","read":False},idempotency_key=f"k{i}")
            except Exception as e: errs.append(e)
            finally:s.close()
        ts=[threading.Thread(target=run,args=(i,)) for i in (1,2)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertFalse(errs)
        s=SessionLocal(); items=read_notifications(s,self.a); s.close()
        self.assertEqual({x['text'] for x in items},{'n1','n2'})
    def test_same_idempotency_key_is_singleton(self):
        for _ in range(2):
            s=SessionLocal(); append_notification(s,self.a,{"ts":"1","text":"same","read":False},idempotency_key='same-key'); s.close()
        s=SessionLocal(); items=read_notifications(s,self.a); s.close(); self.assertEqual(len(items),1)
    def test_mark_read_does_not_drop_neighbors(self):
        s=SessionLocal(); append_notification(s,self.a,{"ts":"1","text":"a","read":False},idempotency_key='a'); append_notification(s,self.a,{"ts":"2","text":"b","read":False},idempotency_key='b'); mark_read(s,self.a,'1'); s.close()
        s=SessionLocal(); items=read_notifications(s,self.a); s.close(); self.assertEqual(len(items),2); self.assertTrue(next(x for x in items if x['ts']=='1')['read'])
if __name__=='__main__':unittest.main()
