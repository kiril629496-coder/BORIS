import inspect
import unittest

from app.ext_api import workspace_sync
from app.services import brain_world_state
from app.api import control_plane as control_plane_api


class ClientWorkspaceActionTruthTests(unittest.TestCase):
    def test_waiting_human_diagnosis_is_not_collapsed_to_pass(self):
        src = inspect.getsource(brain_world_state.build_module_state)
        self.assertIn("WAITING_HUMAN_WORLD_HEALTH_V1", src)
        self.assertIn('diagnosis_health in {"degraded", "waiting_human", "waiting_external"}', src)
        self.assertIn("WAITING_EXTERNAL_WORLD_TRUTH_V1", src)
        self.assertIn('health = "degraded"', src)

    def test_client_plan_uses_canonical_active_paid_scope(self):
        src = inspect.getsource(workspace_sync.sync_client_action_plans)
        self.assertIn("CLIENT_PLAN_CANONICAL_ACTIVE_SCOPE_V2", src)
        self.assertIn("candidate_accounts", src)
        self.assertIn("build_client_state", src)
        self.assertIn("client_state", src)
        self.assertIn("!='active'", src)

    def test_client_plan_filters_actions_to_expected_modules(self):
        src = inspect.getsource(workspace_sync.sync_client_action_plans)
        self.assertIn("CLIENT_PLAN_EXPECTED_MODULE_SCOPE_V1", src)
        self.assertIn("expected_modules", src)
        self.assertIn("in expected_modules", src)

    def test_workspace_explicit_read_only_commands_are_marked_and_write_forbidden(self):
        src = inspect.getsource(control_plane_api._workspace_enqueue_executor)
        self.assertIn("explicit_read_only", src)
        self.assertIn('"read_only":bool(explicit_read_only)', src)
        self.assertIn("любые изменения production-файлов/данных: read-only команда", src)
        self.assertIn('startswith("UNSCOPED_DEV_JOB")', src)


if __name__ == "__main__":
    unittest.main()
