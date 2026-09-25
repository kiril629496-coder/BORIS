import unittest

from app.api.control_plane import _workspace_projection_match
from app.models.control_plane import ControlWorkspaceThread


def row(*, thread_type="task", account_id=None, ref=None, source=None, state=None):
    return ControlWorkspaceThread(
        id="00000000-0000-0000-0000-000000000001",
        thread_type=thread_type,
        title="QA projection",
        account_id=account_id,
        external_chat_ref=ref,
        status="active",
        summary="",
        current_state_json=state or {},
        plan_json=[],
        source_json=source or {},
    )


class WorkspaceProjectionTests(unittest.TestCase):
    def test_development_never_includes_client_memory(self):
        self.assertFalse(_workspace_projection_match(
            row(thread_type="client", account_id="client-a", source={"surface":"development"}),
            "development",
        ))

    def test_development_includes_real_ops_job_even_when_account_scoped(self):
        self.assertTrue(_workspace_projection_match(
            row(account_id="client-a", ref="ops-job:123", source={"surface":"boris_ops"}),
            "development",
        ))

    def test_development_excludes_client_runtime_task_projection(self):
        self.assertFalse(_workspace_projection_match(
            row(account_id="client-a", ref="task:555", source={"source":"legacy_task"}),
            "development",
        ))
        self.assertFalse(_workspace_projection_match(
            row(account_id="client-a", ref="execution-scope:req:client-a", source={"source":"control_execution"}),
            "development",
        ))

    def test_development_excludes_global_brain_even_after_executor_link(self):
        self.assertFalse(_workspace_projection_match(
            row(
                thread_type="project",
                ref="system-brain:owner-global",
                source={"surface":"control-plane","source":"system-brain"},
                state={"dev_job_id":"dev_999"},
            ),
            "development",
        ))

    def test_development_excludes_recovered_memory_without_real_execution(self):
        self.assertFalse(_workspace_projection_match(
            row(
                thread_type="task",
                source={"source":"recovered_context"},
                state={"progress_status":"needs_runtime_recheck"},
            ),
            "development",
        ))
        self.assertTrue(_workspace_projection_match(
            row(
                thread_type="task",
                source={"source":"recovered_context"},
                state={"dev_job_id":"dev_123","autopilot_enabled":True},
            ),
            "development",
        ))

    def test_development_includes_explicit_development_thread(self):
        self.assertTrue(_workspace_projection_match(
            row(thread_type="project", source={"surface":"development"}),
            "development",
        ))

    def test_client_projection_only_client_memory(self):
        self.assertTrue(_workspace_projection_match(row(thread_type="client", account_id="a"), "client"))
        self.assertFalse(_workspace_projection_match(row(thread_type="task", account_id="a"), "client"))

    def test_brain_projection_is_only_global_brain_surface(self):
        self.assertTrue(_workspace_projection_match(
            row(thread_type="project", ref="system-brain:owner-global", source={"surface":"control-plane","source":"system-brain"}),
            "brain",
        ))
        self.assertFalse(_workspace_projection_match(
            row(thread_type="project", source={"surface":"development"}),
            "brain",
        ))


if __name__ == "__main__":
    unittest.main()
