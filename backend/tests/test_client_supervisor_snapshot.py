import uuid
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services import client_supervisor


class ClientSupervisorSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.aid="qa_snapshot_"+uuid.uuid4().hex[:12]

    def tearDown(self):
        db=SessionLocal()
        try:
            db.query(Storage).filter(
                Storage.account_id==self.aid,
                Storage.key==client_supervisor.SUPERVISOR_SNAPSHOT_KEY,
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _save(self, checked_at=None, overall="working"):
        db=SessionLocal()
        try:
            state={
                "account_id":self.aid,
                "name":"QA snapshot",
                "client_state":"active",
                "overall":overall,
                "expected_modules":["messages"],
                "modules":[],
                "plan_fact":{},
                "operational":{},
                "queue":{},
                "needs_owner":[],
                "internal_problems":[],
                "human_work":[],
                "checked_at":(checked_at or datetime.now(timezone.utc)).isoformat(),
            }
            client_supervisor.save_supervisor_snapshot(db,state)
            db.commit()
        finally:
            db.close()

    def test_fresh_snapshot_prevents_rebuild_in_fleet(self):
        self._save()
        db=SessionLocal()
        try:
            with patch.object(client_supervisor,"candidate_accounts",return_value=[self.aid]),                  patch.object(client_supervisor,"build_client_state",side_effect=AssertionError("live rebuild must not run")):
                out=client_supervisor.build_fleet_state(db)
            self.assertEqual(out["snapshot"]["fresh_hits"],1)
            self.assertEqual(out["snapshot"]["live_reads"],0)
            self.assertEqual(out["accounts"][0]["account_id"],self.aid)
            self.assertEqual(out["accounts"][0]["snapshot_source"],"background_client_supervisor")
        finally:
            db.close()

    def test_stale_snapshot_never_masks_live_state(self):
        self._save(checked_at=datetime.now(timezone.utc)-timedelta(minutes=10),overall="healthy")
        live={
            "account_id":self.aid,
            "name":"QA live",
            "client_state":"active",
            "overall":"needs_self_heal",
            "checked_at":datetime.now(timezone.utc).isoformat(),
        }
        db=SessionLocal()
        try:
            with patch.object(client_supervisor,"candidate_accounts",return_value=[self.aid]),                  patch.object(client_supervisor,"build_client_state",return_value=live) as rebuilt:
                out=client_supervisor.build_fleet_state(db)
            rebuilt.assert_called_once()
            self.assertEqual(out["snapshot"]["fresh_hits"],0)
            self.assertEqual(out["snapshot"]["live_reads"],1)
            self.assertEqual(out["accounts"][0]["overall"],"needs_self_heal")
        finally:
            db.close()


if __name__=="__main__":
    unittest.main()
