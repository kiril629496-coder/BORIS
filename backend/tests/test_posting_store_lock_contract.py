import json
import threading
import time
import unittest

from sqlalchemy import text

from app.api import posting as posting_api
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.posting_store_lock import (
    load_posting_posts_for_update,
    save_posting_posts_locked,
)


class PostingStoreLockContractTest(unittest.TestCase):
    ACCOUNT = "__qa_posting_store_lock__"

    def setUp(self):
        db = SessionLocal()
        try:
            db.execute(
                text("DELETE FROM storage WHERE account_id=:a AND key='posting_posts'"),
                {"a": self.ACCOUNT},
            )
            db.add(
                Storage(
                    account_id=self.ACCOUNT,
                    key="posting_posts",
                    value=json.dumps([
                        {"id": "A", "value": 0},
                        {"id": "B", "value": 0},
                    ], ensure_ascii=False),
                )
            )
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        db = SessionLocal()
        try:
            db.execute(
                text("DELETE FROM storage WHERE account_id=:a AND key='posting_posts'"),
                {"a": self.ACCOUNT},
            )
            db.commit()
        finally:
            db.close()

    def test_parallel_writers_preserve_sibling_updates(self):
        trace = []
        trace_lock = threading.Lock()
        first_acquired = threading.Event()

        def worker(post_id, value, hold_seconds, acquired_event=None):
            db = SessionLocal()
            try:
                started = time.time()
                row, posts = load_posting_posts_for_update(db, self.ACCOUNT)
                acquired = time.time()
                if acquired_event is not None:
                    acquired_event.set()
                for post in posts:
                    if post.get("id") == post_id:
                        post["value"] = value
                if hold_seconds:
                    # Use an Event wait rather than global time.sleep: other test
                    # modules may patch sleep for retry/backoff tests, which must
                    # not shorten this database-lock hold interval.
                    threading.Event().wait(hold_seconds)
                save_posting_posts_locked(db, self.ACCOUNT, row, posts)
                with trace_lock:
                    trace.append((post_id, acquired - started))
            finally:
                db.close()

        first = threading.Thread(target=worker, args=("A", 1, 0.35, first_acquired))
        second = threading.Thread(target=worker, args=("B", 2, 0.0, None))
        first.start()
        self.assertTrue(first_acquired.wait(timeout=2), "first writer did not acquire posting lock")
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

        db = SessionLocal()
        try:
            row = db.query(Storage).filter(
                Storage.account_id == self.ACCOUNT,
                Storage.key == "posting_posts",
            ).first()
            final = json.loads(row.value or "[]")
        finally:
            db.close()

        values = {x["id"]: x["value"] for x in final}
        self.assertEqual(values, {"A": 1, "B": 2})
        waits = {post_id: waited for post_id, waited in trace}
        self.assertGreater(waits.get("B", 0), 0.20)

    def test_api_write_without_locked_read_fails_closed(self):
        db = SessionLocal()
        try:
            with self.assertRaisesRegex(RuntimeError, "POSTING_POSTS_WRITE_WITHOUT_LOCK"):
                posting_api._save(db, self.ACCOUNT, "posting_posts", [{"id": "X"}])
            db.rollback()
        finally:
            db.close()

    def test_api_locked_read_then_write_succeeds(self):
        db = SessionLocal()
        try:
            posts = posting_api._load(
                db, self.ACCOUNT, "posting_posts", [], for_update=True
            )
            posts.append({"id": "C", "value": 3})
            posting_api._save(db, self.ACCOUNT, "posting_posts", posts)
        finally:
            db.close()

        db = SessionLocal()
        try:
            row = db.query(Storage).filter(
                Storage.account_id == self.ACCOUNT,
                Storage.key == "posting_posts",
            ).first()
            final = json.loads(row.value or "[]")
        finally:
            db.close()
        self.assertTrue(any(x.get("id") == "C" and x.get("value") == 3 for x in final))


if __name__ == "__main__":
    unittest.main()
