import uuid
import unittest

from app.db.session import SessionLocal
from app.models.control_plane import ControlWorkspaceThread, ControlWorkspaceEvent
from app.ext_api.workspace_sync import reconcile_workspace_scope_duplicates


class WorkspaceRuntimeRetypeTests(unittest.TestCase):
    def _cleanup(self, tid):
        db=SessionLocal()
        try:
            db.query(ControlWorkspaceEvent).filter(ControlWorkspaceEvent.thread_id==tid).delete(synchronize_session=False)
            db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==tid).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def test_legacy_background_task_is_retyped_to_runtime(self):
        tid=str(uuid.uuid4())
        db=SessionLocal()
        db.add(ControlWorkspaceThread(
            id=tid,thread_type="task",title="QA legacy runtime",status="active",
            external_chat_ref=f"task:qa-{tid}",summary="",
            current_state_json={"runtime_status":"queued"},plan_json=[],
            source_json={"source":"legacy_task","task_id":"qa"},
        ))
        db.commit();db.close()
        try:
            result=reconcile_workspace_scope_duplicates(limit=2000)
            self.assertGreaterEqual(result.get("runtime_retyped",0),1)
            db=SessionLocal()
            try:
                row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==tid).first()
                self.assertIsNotNone(row)
                self.assertEqual(row.thread_type,"runtime")
                self.assertEqual(row.status,"active")
            finally:
                db.close()
        finally:
            self._cleanup(tid)


if __name__=="__main__":
    unittest.main()
