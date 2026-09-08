import threading
import unittest
import uuid
from sqlalchemy import text

from app.api import avito
from app.db.session import SessionLocal


class DraftStorageAtomicTest(unittest.TestCase):
    def setUp(self):
        self.account = "qa-draft-atomic-" + uuid.uuid4().hex[:12]

    def tearDown(self):
        db=SessionLocal()
        try:
            db.execute(text("delete from storage where account_id=:a and key=\'drafts\'"), {"a":self.account})
            db.commit()
        finally:
            db.close()

    def test_parallel_appends_preserve_both_workers(self):
        barrier=threading.Barrier(2)
        errors=[]
        def worker(prefix):
            try:
                barrier.wait(timeout=5)
                avito._append_drafts(self.account,[{"id":prefix,"title":prefix,"images":[]}])
            except Exception as exc:
                errors.append(exc)
        ts=[threading.Thread(target=worker,args=(x,)) for x in ("a","b")]
        [t.start() for t in ts]; [t.join(10) for t in ts]
        self.assertFalse(errors)
        rows=avito._load_drafts(self.account)
        self.assertEqual({r["id"] for r in rows},{"a","b"})

    def test_append_same_id_is_idempotent_upsert(self):
        avito._append_drafts(self.account,[{"id":"same","title":"one","images":[]}])
        avito._append_drafts(self.account,[{"id":"same","title":"two","images":[]}])
        rows=avito._load_drafts(self.account)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["title"],"two")

    def test_mutation_preserves_unrelated_latest_rows(self):
        avito._append_drafts(self.account,[{"id":"a","title":"A","images":[]},{"id":"b","title":"B","images":[]}])
        def mut(rows):
            for r in rows:
                if r["id"]=="a": r["title"]="A2"
            return rows
        avito._mutate_drafts(self.account,mut)
        rows={r["id"]:r for r in avito._load_drafts(self.account)}
        self.assertEqual(rows["a"]["title"],"A2")
        self.assertEqual(rows["b"]["title"],"B")

if __name__ == "__main__": unittest.main()
