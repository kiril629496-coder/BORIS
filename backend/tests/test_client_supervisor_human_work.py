import inspect
import unittest

from app.services import client_supervisor


class ClientSupervisorHumanWorkTests(unittest.TestCase):
    def test_overdue_canonical_crm_work_is_not_internal_p1(self):
        src=inspect.getsource(client_supervisor.build_client_state)
        marker="CLIENT_SUPERVISOR_OVERDUE_HUMAN_WORK_V"
        self.assertIn(marker,src)
        block=src.split(marker,1)[1].split(
            "incidents=db.query(ControlIncident)",1
        )[0]
        self.assertIn("human_work.append",block)
        self.assertIn("CRM_OVERDUE_HUMAN_TASKS",block)
        self.assertNotIn("internal_bad.append",block)

    def test_managed_mop_handoff_is_human_work_not_owner_work(self):
        src=inspect.getsource(client_supervisor.build_client_state)
        self.assertIn('CLIENT_SUPERVISOR_MOP_MANAGER_HANDOFF_V1',src)
        self.assertIn('MOP_MANAGER_HANDOFF_ACTIVE',src)
        block=src.split('CLIENT_SUPERVISOR_MOP_MANAGER_HANDOFF_V1',1)[1].split(
            'CLIENT_SUPERVISOR_OVERDUE_HUMAN_WORK_V3',1
        )[0]
        self.assertIn("'owner_action_required':False",block)
        self.assertIn('human_work.append',block)

    def test_human_work_keeps_client_working_without_hiding_internal_failures(self):
        src=inspect.getsource(client_supervisor.build_client_state)
        self.assertIn("elif human_work or any(m['health'] not in {'pass'} for m in modules): overall='working'",src)
        self.assertIn("'internal_problems':internal_bad,'human_work':human_work",src)
        self.assertIn("elif internal_bad: overall='needs_self_heal'",src)

    def test_waiting_external_dependency_is_not_internal_self_heal(self):
        src=inspect.getsource(client_supervisor.build_client_state)
        self.assertIn("CLIENT_SUPERVISOR_EXTERNAL_DEPENDENCY_V1",src)
        self.assertIn("_dep=str(d.get('dependency_state') or '')",src)
        self.assertIn("elif _dep=='waiting_external':",src)
        block=src.split("CLIENT_SUPERVISOR_EXTERNAL_DEPENDENCY_V1",1)[1].split(
            "elif h=='critical'",1
        )[0]
        self.assertNotIn("internal_bad.append",block)
        self.assertNotIn("needs_owner.append",block)


if __name__=="__main__":
    unittest.main()
