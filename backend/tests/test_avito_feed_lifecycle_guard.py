import json
import tempfile
import unittest
from pathlib import Path
from scripts.avito_feed_lifecycle_guard import classify_removed, load_state_file


class FeedLifecycleGuardTests(unittest.TestCase):
    def test_published_bound_removal_is_unexpected(self):
        prev={"a":["1"]}; cur={"a":[]}
        life={("a","1"):[{"id":7,"status":"published","identity_status":"published_identity_bound"}]}
        expected, unexpected, info=classify_removed(prev,cur,life)
        self.assertEqual(expected,[]); self.assertEqual(info,[])
        self.assertEqual(unexpected[0]["reason"],"removed_while_published_identity_bound")

    def test_superseded_removal_is_expected(self):
        prev={"a":["1"]}; cur={"a":[]}
        life={("a","1"):[{"id":7,"status":"superseded","identity_status":"superseded_by_replacement","replacement_completed":True}]}
        expected, unexpected, info=classify_removed(prev,cur,life)
        self.assertEqual(unexpected,[]); self.assertEqual(info,[])
        self.assertEqual(expected[0]["reason"],"expected_inactive_lifecycle")
        self.assertEqual(expected[0]["cause"],"replacement_confirmed")

    def test_authoritative_cutover_is_attributed(self):
        prev={"a":["1"]}; cur={"a":[]}
        life={("a","1"):[{"id":7,"status":"superseded","identity_status":"superseded_by_authoritative_feed","identity_resolution":"superseded_by_autoload_report"}]}
        expected, unexpected, info=classify_removed(prev,cur,life)
        self.assertEqual(unexpected,[]); self.assertEqual(info,[])
        self.assertEqual(expected[0]["cause"],"authoritative_feed_cutover")

    def test_live_removal_is_explicitly_unexplained(self):
        prev={"a":["1"]}; cur={"a":[]}
        life={("a","1"):[{"id":7,"status":"published","identity_status":"published_identity_bound"}]}
        _, unexpected, _=classify_removed(prev,cur,life)
        self.assertEqual(unexpected[0]["cause"],"unexplained_live_removal")

    def test_unowned_removal_is_informational(self):
        expected, unexpected, info=classify_removed({"a":["1"]},{"a":[]},{})
        self.assertEqual(expected,[]); self.assertEqual(unexpected,[])
        self.assertEqual(info[0]["reason"],"no_active_campaign_owner")

    def test_no_removal_is_clean(self):
        self.assertEqual(classify_removed({"a":["1"]},{"a":["1"]},{}),([],[],[]))

    def test_missing_snapshot_is_valid_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_state_file(Path(td) / "missing.json"), {})

    def test_corrupt_existing_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td) / "state.json"; p.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "snapshot_unreadable"):
                load_state_file(p)

    def test_invalid_snapshot_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td) / "state.json"; p.write_text(json.dumps({"version":2,"feeds":{}}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "snapshot_schema_invalid"):
                load_state_file(p)


if __name__ == "__main__": unittest.main()
