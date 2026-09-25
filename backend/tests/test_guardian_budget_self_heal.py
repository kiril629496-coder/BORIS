import inspect
import unittest

import boris_guardian


class GuardianBudgetSelfHealTests(unittest.TestCase):
    def test_budget_health_detects_limit_drift_and_returns_repair_accounts(self):
        src = inspect.getsource(boris_guardian.budget_brake_health)
        self.assertIn("BUDGET_POLICY_CHANGE_SELF_HEAL_V1", src)
        self.assertIn("runtime limit drift", src)
        self.assertIn("repair_accounts", src)

    def test_quick_heals_then_rechecks_before_escalating(self):
        src = inspect.getsource(boris_guardian.quick)
        self.assertIn("BUDGET_GUARDIAN_SELF_HEAL_V2", src)
        heal_pos = src.index("heal_budget_brake_accounts")
        recheck_pos = src.index("budget_health=budget_brake_health()", heal_pos)
        escalate_pos = src.index("Контроль дневного бюджета:", recheck_pos)
        self.assertLess(heal_pos, recheck_pos)
        self.assertLess(recheck_pos, escalate_pos)

    def test_recovery_is_bounded_and_has_backoff(self):
        src = inspect.getsource(boris_guardian.heal_budget_brake_accounts)
        self.assertIn("max_accounts=3", src)
        self.assertIn("<300", src)
        self.assertIn("_collect_one", src)
        self.assertIn("brake_account", src)
        self.assertIn("apply=True", src)
        self.assertIn("BUDGET_HEAL_BACKOFF_NO_STARVATION_V1", src)
        self.assertIn("attempted=0", src)
        self.assertIn("attempted += 1", src)

    def test_stale_or_unknown_spend_is_repairable_not_warning_only(self):
        src = inspect.getsource(boris_guardian.budget_brake_health)
        self.assertIn("BUDGET_STALE_SIGNAL_AUTOHEAL_V2", src)
        self.assertIn("status in {'spend_unknown','spend_stale'}", src)
        block = src[src.index("status in {'spend_unknown','spend_stale'}"):]
        self.assertIn("repair_accounts.append(account_id)", block)
        self.assertIn("bad.append", block)

    def test_growth_freeze_is_non_destructive_and_reverified(self):
        src = inspect.getsource(boris_guardian.budget_brake_health)
        self.assertIn("KPI_CONTINUITY_BUDGET_GUARDIAN_V1", src)
        self.assertIn("growth_frozen_existing_preserved", src)
        self.assertIn("max_age_min = 15", src)
        self.assertNotIn("brake не остановил paid promotion", src)

    def test_legacy_budget_incident_is_ownerlessly_recovered(self):
        health = inspect.getsource(boris_guardian.legacy_budget_incident_health)
        heal = inspect.getsource(boris_guardian.heal_legacy_budget_incidents)
        quick = inspect.getsource(boris_guardian.quick)
        self.assertIn("cpx_budget_resume_state", health)
        self.assertIn("exact_pre_brake_bid", health)
        self.assertIn("cpx_exact_incident_recovery.py", heal)
        self.assertIn("--limit 20", heal)
        self.assertIn("<240", heal)
        self.assertIn("LEGACY_BUDGET_INCIDENT_SELF_HEAL_V1", quick)

    def test_quick_respects_explicit_systemd_condition_gate(self):
        helper = inspect.getsource(boris_guardian.intentionally_condition_gated)
        quick = inspect.getsource(boris_guardian.quick)
        self.assertIn("ConditionResult", helper)
        self.assertIn("Result", helper)
        self.assertIn("SYSTEMD_CONDITION_GATE_OWNERSHIP_V1", quick)
        self.assertIn("intentionally_condition_gated(u)", quick)


if __name__ == "__main__":
    unittest.main()
