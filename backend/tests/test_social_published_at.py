import unittest
from datetime import datetime
from pathlib import Path

import posting_runner as pr


class SocialPublishedAtContractTest(unittest.TestCase):
    def test_shared_success_transition_sets_timestamp(self):
        rec={"status":"draft","error":"old","publish_at":"2026-09-05 09:00"}
        out=pr._mark_social_delivery_published(rec,{"name":"test"},preview_text="clean")
        self.assertIs(out,rec)
        self.assertEqual(rec["status"],"published")
        self.assertEqual(rec["error"],"")
        self.assertIsNone(rec["publish_at"])
        self.assertEqual(rec["preview"],"clean")
        self.assertTrue(rec["published_at"])
        datetime.fromisoformat(rec["published_at"])

    def test_immediate_publish_record_path_contains_timestamp_contract(self):
        src=Path("posting_runner.py").read_text()
        start=src.index("def _save_post_record")
        end=src.index("def _posting_generation_exists",start)
        block=src[start:end]
        self.assertIn('if status == "published" else None',block)

    def test_api_and_delayed_worker_share_helper(self):
        runner=Path("posting_runner.py").read_text()
        api=Path("app/api/posting.py").read_text()
        self.assertGreaterEqual(runner.count("_mark_social_delivery_published("),2)
        self.assertIn("pr._mark_social_delivery_published(",api)
        unpub=api[api.index('p["status"] = "unpublished"'):]
        self.assertNotIn('p["published_at"] =',unpub)


if __name__=='__main__':
    unittest.main()
