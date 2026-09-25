import inspect
import unittest

from app.services import client_supervisor


class ClientSupervisorManagerControlTests(unittest.TestCase):
    def test_human_work_exposes_internal_manager_control_evidence(self):
        src = inspect.getsource(client_supervisor.build_client_state)
        self.assertIn("manager_control", src)
        self.assertIn("reminded_tasks", src)
        self.assertIn("reminder_events", src)
        self.assertIn("last_reminded_at", src)
        self.assertIn("unassigned_tasks", src)
        self.assertIn("owner_action_required", src)
        self.assertIn("external_action", src)
        self.assertIn("brain_crm_overdue_reminder", src)

    def test_human_work_items_keep_assignee_and_escalation_evidence(self):
        src = inspect.getsource(client_supervisor.build_client_state)
        self.assertIn("assigned_user_id", src)
        self.assertIn("reminder_count", src)
        self.assertIn("escalation_level", src)
        self.assertIn("surface_in_manager_workspaces", src)


if __name__ == "__main__":
    unittest.main()
