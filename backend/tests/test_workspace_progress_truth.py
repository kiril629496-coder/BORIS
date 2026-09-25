import uuid
import unittest
import inspect
from types import SimpleNamespace

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.models.control_plane import ControlWorkspaceThread,ControlWorkspaceEvent
from app.api.control_plane import (
    workspace_thread_progress, WorkspaceProgressBody,
    workspace_thread_patch, WorkspaceThreadPatch,
)
from app.ext_api import dev as ext_dev


class WorkspaceProgressTruthTests(unittest.TestCase):
    def _cleanup(self, tid):
        db=SessionLocal()
        try:
            db.query(ControlWorkspaceEvent).filter(ControlWorkspaceEvent.thread_id==tid).delete(synchronize_session=False)
            db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==tid).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def test_owner_100_never_overwrites_canonical_or_marks_done(self):
        tid=str(uuid.uuid4())
        db=SessionLocal()
        db.add(ControlWorkspaceThread(
            id=tid,thread_type="task",title="QA truth",status="active",summary="",
            current_state_json={"progress_percent":25.0,"progress_status":"measured"},
            plan_json=[],source_json={"qa":True},
        ))
        db.commit();db.close()
        try:
            r=workspace_thread_progress(
                tid,WorkspaceProgressBody(progress_percent=100,note="owner estimate"),
                user=SimpleNamespace(role="owner",id=2,email="ostapenko-kirill-86@yandex.ru"),
            )
            st=r["thread"]["current_state"]
            self.assertEqual(r["thread"]["status"],"active")
            self.assertEqual(st.get("progress_percent"),25.0)
            self.assertEqual(st.get("owner_reported_progress_percent"),100.0)
        finally:
            self._cleanup(tid)

    def test_runtime_linked_task_cannot_be_manually_closed_without_runtime_completion(self):
        tid=str(uuid.uuid4())
        db=SessionLocal()
        db.add(ControlWorkspaceThread(
            id=tid,thread_type="task",title="QA runtime truth",status="waiting",
            external_chat_ref="task:999999999",summary="",
            current_state_json={
                "progress_percent":50.0,
                "progress_status":"measured",
                "runtime_status":"running",
                "current_step":"2. QA",
            },
            plan_json=[],source_json={"qa":True},
        ))
        db.commit();db.close()
        try:
            with self.assertRaises(HTTPException) as ctx:
                workspace_thread_patch(
                    tid,WorkspaceThreadPatch(status="done"),
                    user=SimpleNamespace(role="owner",id=2,email="ostapenko-kirill-86@yandex.ru"),
                )
            self.assertEqual(ctx.exception.status_code,409)
            self.assertEqual(ctx.exception.detail,"runtime_evidence_not_completed")
            db=SessionLocal()
            try:
                row=db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.id==tid).first()
                self.assertEqual(row.status,"waiting")
                self.assertEqual((row.current_state_json or {}).get("progress_percent"),50.0)
            finally:
                db.close()
        finally:
            self._cleanup(tid)

    def test_dev_complete_final_result_is_built_from_completed_truth(self):
        src=inspect.getsource(ext_dev.complete)
        self.assertIn('completed_job = get(job["id"])',src)
        self.assertIn('wait_reason=None',src)
        self.assertIn('blocked_reason=None',src)
        self.assertIn('report(completed_job, summary)',src)
        self.assertNotIn('final_result_json=json.dumps(report(job, summary)',src)


if __name__=="__main__":
    unittest.main()
