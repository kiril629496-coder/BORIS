import json
import threading
import uuid
import unittest

import posting_runner as pr
from app.models.storage import Storage


class SocialGenerationLifecycleIdempotencyTest(unittest.TestCase):
    def test_concurrent_same_generation_key_materializes_one_record(self):
        acc="qa_social_generation_dedupe_"+uuid.uuid4().hex[:10]
        gkey="social-slot:%s:p1:20260905_09"%acc
        results=[]
        errors=[]

        def worker(i):
            try:
                rid=pr._save_post_record(
                    acc,"p1","QA",f"text-{i}",None,"draft",
                    generation_key=gkey,
                )
                results.append(rid)
            except Exception as exc:
                errors.append(repr(exc))

        threads=[threading.Thread(target=worker,args=(i,)) for i in range(2)]
        try:
            for t in threads:t.start()
            for t in threads:t.join()

            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_posts"
                ).first()
                posts=json.loads(row.value or "[]") if row else []
                same=[x for x in posts if x.get("generation_key")==gkey]
            finally:
                s.close()

            self.assertEqual(errors,[])
            self.assertEqual(len(same),1)
            self.assertEqual(len(set(results)),1)
        finally:
            s=pr.SessionLocal()
            try:
                s.query(Storage).filter(Storage.account_id==acc).delete(
                    synchronize_session=False
                )
                s.commit()
            finally:
                s.close()

    def test_error_record_does_not_block_successful_retry(self):
        acc="qa_social_generation_retry_"+uuid.uuid4().hex[:10]
        gkey="social-slot:%s:p1:20260905_09"%acc
        try:
            err_id=pr._save_post_record(
                acc,"p1","QA","bad",None,"error",
                error="BANNER_REQUIRED",
                generation_key=gkey,
            )
            ok_id=pr._save_post_record(
                acc,"p1","QA","good",None,"draft",
                generation_key=gkey,
            )
            self.assertNotEqual(err_id,ok_id)

            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(
                    Storage.account_id==acc,Storage.key=="posting_posts"
                ).first()
                posts=json.loads(row.value or "[]") if row else []
            finally:
                s.close()

            same=[x for x in posts if x.get("generation_key")==gkey]
            self.assertEqual(len(same),1)
            self.assertEqual(same[0]["status"],"draft")
            self.assertEqual(same[0]["text"],"good")
        finally:
            s=pr.SessionLocal()
            try:
                s.query(Storage).filter(Storage.account_id==acc).delete(
                    synchronize_session=False
                )
                s.commit()
            finally:
                s.close()


if __name__=="__main__":
    unittest.main()
