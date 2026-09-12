import inspect
import unittest

from app.crm import calltracking_sync
from app.crm import product as crm_product
from app.services import brain_recovery


class CrmOverdueManagerLifecycleTests(unittest.TestCase):
    def test_later_answered_call_reconciles_only_callback_and_generic_call_tasks(self):
        src = inspect.getsource(calltracking_sync._reconcile_prior_call_tasks)
        self.assertIn("CALLTRACKING_NEXT_ACTION_SUPERSESSION_V1", src)
        self.assertIn("Перезвонить по пропущенному звонку Avito", src)
        self.assertIn("Зафиксировать следующий шаг после звонка", src)
        self.assertIn("answered_call_after_callback_obligation", src)
        self.assertIn("superseded_by_later_answered_call", src)
        self.assertNotIn("отправить клиенту", src.lower())
        self.assertNotIn("документ", src.lower())

    def test_exact_orphan_call_task_link_repair_never_uses_fuzzy_matching(self):
        ingest_src = inspect.getsource(calltracking_sync._ensure_task)
        recover_src = inspect.getsource(brain_recovery._safe_calltracking_orphan_task_link_repair)
        self.assertIn("CALLTRACKING_ORPHAN_TASK_EXACT_LINK_REPAIR_V1", ingest_src)
        self.assertIn("CALLTRACKING_ORPHAN_TASK_EXACT_LINK_RECOVERY_V1", recover_src)
        self.assertIn("exact_account_call_id", recover_src)
        self.assertIn("contact_mismatch", recover_src)
        self.assertIn("deal_account_mismatch", recover_src)
        self.assertNotIn("buyer_phone", recover_src)
        self.assertNotIn("display_name", recover_src)

    def test_background_recovery_has_historical_call_outcome_guard(self):
        src = inspect.getsource(brain_recovery._safe_calltracking_task_outcome_reconcile)
        self.assertIn("same contact/account", src)
        self.assertIn("talk_duration", src)
        self.assertIn("missed", src)
        self.assertIn("specific promises untouched", src)
        self.assertIn("external_action", src)
        self.assertIn("owner_action_required", src)

    def test_manager_reminders_require_active_paid_crm_snapshot_and_never_owner(self):
        src = inspect.getsource(brain_recovery._safe_crm_overdue_queue_recovery)
        self.assertIn("CRM_MANAGER_REMINDER_LIFECYCLE_V2", src)
        self.assertIn("CRM_ACTIVE_TENANT_OVERDUE_PRIORITY_V1", src)
        self.assertIn('snap.get("client_state")=="active"', src)
        self.assertIn('"crm" in list(snap.get("expected_modules")', src)
        self.assertIn("SELECT DISTINCT d.avito_account_id", src)
        self.assertIn('bindparam("active_accounts", expanding=True)', src)
        self.assertLess(src.index("active_accounts=["), src.index("ORDER BY t.due_at,t.id LIMIT 500"))
        self.assertIn("skipped_inactive=max(0,total_overdue-eligible_total)", src)
        self.assertIn('"eligible_total":eligible_total', src)
        self.assertIn('"eligible_backlog_remaining":max(0,eligible_total-len(rows))', src)
        self.assertIn("brain_crm_overdue_reminder", src)
        self.assertIn("owner_action_required", src)
        self.assertIn("False", src)
        self.assertIn("external_action", src)

    def test_crm_tasks_projection_exposes_internal_reminder_state(self):
        src = inspect.getsource(crm_product.tasks)
        for marker in (
            "overdue_seconds",
            "reminder_count",
            "last_reminded_at",
            "escalation_level",
            "manager_critical",
            "owner_action_required",
        ):
            self.assertIn(marker, src)


if __name__ == "__main__":
    unittest.main()
