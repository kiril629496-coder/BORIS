import inspect
import unittest

from app.services import brain_recovery
from app.services import control_plane_adapters


class CrmNextActionScopeParityTests(unittest.TestCase):
    def test_diagnosis_and_recovery_share_mop_scope_guards(self):
        recovery = inspect.getsource(brain_recovery._safe_crm_next_action_recovery)
        diagnosis = inspect.getsource(control_plane_adapters._crm_state)

        self.assertIn("CRM_NEXT_ACTION_MOP_SCOPE_PARITY_V1", recovery)
        self.assertIn("CRM_NEXT_ACTION_MOP_SCOPE_PARITY_V3", diagnosis)

        for src in (recovery, diagnosis):
            self.assertIn("messenger_item_whitelist", src)
            self.assertIn("content_type", src)
            self.assertIn("appcall", src)
            self.assertIn("item_id", src)
            self.assertIn("mop_drafts", src)
            self.assertIn("waiting_external", src)
            self.assertIn("draft_ready", src)
            self.assertIn("auto_send", src)

    def test_scope_exclusion_does_not_become_actionable_diagnosis(self):
        diagnosis = inspect.getsource(control_plane_adapters._crm_state)
        self.assertIn("recent_actionable.append(evidence)", diagnosis)
        self.assertLess(
            diagnosis.index("messenger_item_whitelist"),
            diagnosis.index("recent_actionable.append(evidence)"),
        )
        self.assertLess(
            diagnosis.index("mop_drafts"),
            diagnosis.index("recent_actionable.append(evidence)"),
        )

    def test_lead_radar_opportunities_are_not_false_crm_orphans(self):
        diagnosis = inspect.getsource(control_plane_adapters._crm_state)
        recovery = inspect.getsource(brain_recovery._safe_crm_next_action_recovery)
        self.assertIn("CRM_ORPHAN_CONTACT_SCOPE_V1", diagnosis)
        self.assertIn("CRM_LEAD_RADAR_SINGLE_OWNER_V1", recovery)
        self.assertGreaterEqual(diagnosis.count("COALESCE(d.source,'') <> 'lead_radar'"), 4)
        self.assertGreaterEqual(recovery.count("COALESCE(d.source,'') <> 'lead_radar'"), 2)
        self.assertIn("lead_radar_auto_tasks_cancelled", recovery)
        self.assertIn("t.source='brain_auto_recovery'", recovery)
        self.assertIn("t.description=:description", recovery)


if __name__ == "__main__":
    unittest.main()
