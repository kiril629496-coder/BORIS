import json
import uuid
import unittest
from unittest.mock import patch

import posting_runner as pr
from app.models.storage import Storage


class SocialProjectExceptionIsolationTest(unittest.TestCase):
    def test_normal_bucket_continues_after_first_project_exception(self):
        acc="qa_social_isolation_"+uuid.uuid4().hex[:8]
        projects=[
            {"id":"p1","name":"Broken"},
            {"id":"p2","name":"Healthy"},
        ]
        s=pr.SessionLocal()
        try:
            s.add(Storage(
                account_id=acc,key="posting_projects",
                value=json.dumps(projects,ensure_ascii=False),
            ))
            s.commit()
        finally:s.close()

        calls=[]
        def fake_impl(account_id,owner=False,respect_time=False,only_id=None,scheduled_hour_override=None):
            calls.append(only_id)
            if only_id=="p1":
                raise RuntimeError("boom")
            return {"ok":True}

        try:
            with patch.object(pr,"_autopost_projects_impl",side_effect=fake_impl):
                with self.assertRaisesRegex(RuntimeError,"SOCIAL_PROJECT_ERRORS"):
                    pr.autopost_projects(acc,owner=True,respect_time=True)
            self.assertEqual(calls,["p1","p2"])
        finally:
            s=pr.SessionLocal()
            try:
                s.query(Storage).filter(Storage.account_id==acc).delete(synchronize_session=False)
                s.commit()
            finally:s.close()

    def test_targeted_rescue_keeps_fail_fast_semantics(self):
        with patch.object(
            pr,"_autopost_projects_impl",
            side_effect=RuntimeError("target failed"),
        ) as impl:
            with self.assertRaisesRegex(RuntimeError,"target failed"):
                pr.autopost_projects(
                    "u2",owner=True,respect_time=True,
                    only_id="p1",scheduled_hour_override=9,
                )
            self.assertEqual(impl.call_count,1)


if __name__=="__main__":
    unittest.main()
