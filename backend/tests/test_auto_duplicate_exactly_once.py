import unittest
import uuid
from unittest.mock import patch
from sqlalchemy import text

from app.api import avito as A
from app.db.session import SessionLocal


class AutoDuplicateExactlyOnceTest(unittest.TestCase):
    def src(self):
        return A.FeedItem(id="source-1", title="Тестовый товар", description="Описание", address="Москва", images=[])

    def test_preview_is_zero_paid_and_zero_mutation(self):
        with patch.object(A, "_load_feed_items", return_value=[self.src()]), \
             patch.object(A, "rewrite_text") as rewrite, \
             patch.object(A, "_upsert_feed_items") as upsert, \
             patch.object(A, "_dup_manifest_record") as manifest, \
             patch("app.api.billing.check_and_consume") as consume:
            out = A.auto_duplicate(A.AutoDuplicateRequest(account_id="qa", item_id="source-1", dry_run=True))
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["dry_run"])
        rewrite.assert_not_called(); consume.assert_not_called(); upsert.assert_not_called(); manifest.assert_not_called()

    def test_real_duplicate_requires_business_idempotency_before_paid_call(self):
        with patch.object(A, "_load_feed_items", return_value=[self.src()]), \
             patch.object(A, "rewrite_text") as rewrite, \
             patch.object(A, "_upsert_feed_items") as upsert, \
             patch("app.api.billing.check_and_consume") as consume:
            out = A.auto_duplicate(A.AutoDuplicateRequest(account_id="qa", item_id="source-1", dry_run=False))
        self.assertEqual(out["code"], "paid_idempotency_required")
        rewrite.assert_not_called(); consume.assert_not_called(); upsert.assert_not_called()

    def test_same_key_has_same_duplicate_and_tariff_intent(self):
        charges=[]; ids=[]
        def consume(account, unit, amount, idempotency_key=""):
            charges.append(idempotency_key); return {"allowed":True,"idempotency_replay":len(charges)>1}
        def upsert(account, items):
            ids.append(items[0].id); return [self.src(), items[0]]
        with patch.object(A, "_load_feed_items", return_value=[self.src()]), \
             patch.object(A, "rewrite_text", return_value={"status":"ok","title":"Новый","description":"Новый текст"}), \
             patch.object(A, "_upsert_feed_items", side_effect=upsert), \
             patch.object(A, "_dup_manifest_record", return_value={"created":True,"total":1}), \
             patch("app.api.billing.check_and_consume", side_effect=consume):
            one=A.auto_duplicate(A.AutoDuplicateRequest(account_id="qa",item_id="source-1",dry_run=False,idempotency_key="intent"))
            two=A.auto_duplicate(A.AutoDuplicateRequest(account_id="qa",item_id="source-1",dry_run=False,idempotency_key="intent"))
        self.assertEqual(one["new_id"], two["new_id"])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(charges[0], charges[1])
        self.assertTrue(two["idempotency_replay"])

    def test_run_mutation_requires_run_key(self):
        with patch.object(A, "_dup_manifest_snapshot") as manifest, patch.object(A, "auto_duplicate") as duplicate:
            out=A.auto_duplicate_run(A.AutoDuplicateRunRequest(account_id="qa",dry_run=False))
        self.assertEqual(out["code"], "paid_idempotency_required")
        manifest.assert_not_called(); duplicate.assert_not_called()

    def test_manifest_record_is_idempotent_by_duplicate_id(self):
        account="qa-dup-manifest-"+uuid.uuid4().hex[:10]
        try:
            one=A._dup_manifest_record(account,"src","dup","run")
            two=A._dup_manifest_record(account,"src","dup","run")
            rows=A._dup_manifest_snapshot(account)
            self.assertTrue(one["created"])
            self.assertFalse(two["created"])
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["duplicate_id"],"dup")
        finally:
            db=SessionLocal()
            try:
                db.execute(text("delete from storage where account_id=:a and key='auto_duplicate_manifest'"),{"a":account})
                db.commit()
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
