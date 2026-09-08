import threading
import unittest
import uuid

from sqlalchemy import text

from app.api import avito
from app.db.session import SessionLocal


class FeedStorageAtomicTest(unittest.TestCase):
    def setUp(self):
        self.account = "qa-feed-atomic-" + uuid.uuid4().hex[:12]

    def tearDown(self):
        db = SessionLocal()
        try:
            db.execute(text("delete from storage where account_id=:a and key='feed_items'"), {"a": self.account})
            db.commit()
        finally:
            db.close()

    def item(self, iid, title=None, images=None):
        return avito.FeedItem(id=iid, title=title or iid, description="qa", address="Москва", images=images or [])

    def test_concurrent_upsert_preserves_both_workers(self):
        barrier = threading.Barrier(2)
        errors = []
        def worker(iid):
            try:
                barrier.wait(timeout=5)
                avito._upsert_feed_items(self.account, [self.item(iid)])
            except Exception as exc:
                errors.append(repr(exc))
        ts = [threading.Thread(target=worker,args=("a",)), threading.Thread(target=worker,args=("b",))]
        for t in ts: t.start()
        for t in ts: t.join(timeout=10)
        self.assertFalse(errors, errors)
        self.assertEqual({x.id for x in avito._load_feed_items(self.account)}, {"a","b"})

    def test_same_id_upsert_converges(self):
        avito._upsert_feed_items(self.account, [self.item("same","v1")])
        avito._upsert_feed_items(self.account, [self.item("same","v2")])
        rows = avito._load_feed_items(self.account)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "v2")

    def test_mutation_preserves_unrelated_item(self):
        avito._upsert_feed_items(self.account, [self.item("one"), self.item("two")])
        def edit(rows):
            for row in rows:
                if row.id == "one": row.title = "changed"
            return rows
        avito._mutate_feed_items(self.account, edit)
        rows = {x.id:x for x in avito._load_feed_items(self.account)}
        self.assertEqual(rows["one"].title, "changed")
        self.assertEqual(rows["two"].title, "two")

    def test_legacy_visual_fails_closed_without_persist(self):
        with self.assertRaises(Exception):
            avito._upsert_feed_items(self.account, [self.item("bad", images=["/images/qa/fullai_bad.png"])])
        self.assertEqual(avito._load_feed_items(self.account), [])


if __name__ == "__main__":
    unittest.main()
