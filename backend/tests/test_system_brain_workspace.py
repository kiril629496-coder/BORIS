import uuid
import unittest

from app.db.session import SessionLocal
from app.models.control_plane import ControlWorkspaceThread, ControlWorkspaceEvent
from app.ext_api.workspace_sync import ensure_system_brain_workspace


class SystemBrainWorkspaceTests(unittest.TestCase):
    def test_duplicate_brain_workspace_is_archived_and_canonical_stays_single(self):
        first=ensure_system_brain_workspace()
        canonical_id=first["thread_id"]
        dup_id=str(uuid.uuid4())
        db=SessionLocal()
        try:
            db.add(ControlWorkspaceThread(
                id=dup_id,thread_type="project",title="Duplicate Brain",
                external_chat_ref="system-brain:owner-global",status="active",
                summary="duplicate",current_state_json={},plan_json=[],
                source_json={"surface":"control-plane","source":"system-brain","owner_only":True},
            ))
            db.commit()
        finally:
            db.close()
        try:
            result=ensure_system_brain_workspace()
            self.assertEqual(result["status"],"pass")
            self.assertEqual(result["thread_id"],canonical_id)
            self.assertGreaterEqual(result["deduped"],1)
            db=SessionLocal()
            try:
                live=(db.query(ControlWorkspaceThread)
                      .filter(ControlWorkspaceThread.external_chat_ref=="system-brain:owner-global")
                      .all())
                self.assertEqual(len(live),1)
                self.assertEqual(str(live[0].id),canonical_id)
                dup=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==dup_id).first()
                self.assertIsNotNone(dup)
                self.assertEqual(dup.status,"archived")
                self.assertTrue(str(dup.external_chat_ref).startswith("system-brain:duplicate:"))
            finally:
                db.close()
        finally:
            db=SessionLocal()
            try:
                db.query(ControlWorkspaceEvent).filter(ControlWorkspaceEvent.thread_id==dup_id).delete(synchronize_session=False)
                db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==dup_id).delete(synchronize_session=False)
                db.commit()
            finally:
                db.close()


if __name__=="__main__":
    unittest.main()
