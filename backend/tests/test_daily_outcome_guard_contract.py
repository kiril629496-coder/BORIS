import inspect
import unittest

import posting_runner as pr
from app.services import prospect_campaigns
from app.services import incident_reconciler
import boris_background_worker as bw


class DailyOutcomeGuardContractTest(unittest.TestCase):
    def test_email_window_close_becomes_owner_action(self):
        src = inspect.getsource(prospect_campaigns.owner_daily_delivery_health)
        self.assertIn("phase=='window_closed' and lag > 0", src)
        self.assertIn("window_closed_shortfall", src)
        self.assertIn("'owner_action_required':bool(state=='critical' and status not in {'daily_cap_exceeded','window_closed_shortfall'})", src)

    def test_social_rollover_remembers_previous_day(self):
        src = inspect.getsource(pr.social_project_daily_delivery_health)
        self.assertIn("previous_day_shortfall", src)
        self.assertIn("previous_day_missed", src)
        self.assertIn("failed_service_date", src)
        self.assertIn("guard_start = min(tracked_dates)", src)
        self.assertIn("owner_action_required = True", src)

    def test_social_daily_health_does_not_flag_intentionally_disabled_project(self):
        src = inspect.getsource(pr.social_project_daily_delivery_health)
        self.assertIn('target.get("expected_auto_generation") is False', src)
        self.assertIn('str(target.get("mode") or "").lower() == "off"', src)
        self.assertIn('"status":"disabled_intentionally"', src)

    def test_social_worker_keeps_owner_action_signal(self):
        src = inspect.getsource(bw._social_posting_loop)
        self.assertIn('daily_delivery_checks = {', src)
        self.assertIn('"ocean": ocean_daily_delivery', src)
        self.assertIn('"dushi": dushi_daily_delivery', src)
        self.assertIn('"critical" in daily_states.values()', src)
        self.assertIn('ocean_daily_delivery.get("owner_action_required")', src)
        self.assertIn('dushi_daily_delivery.get("owner_action_required")', src)
        self.assertIn('or unavailable.get("owner_action_required")', src)

    def test_control_plane_only_escalates_non_self_healable_daily_failure(self):
        src = inspect.getsource(incident_reconciler._sync_daily_outcome_incidents)
        self.assertIn("OWNER_EMAIL_DAILY_PLAN_FAILED", src)
        self.assertIn("OWNER_SOCIAL_DAILY_PLAN_FAILED", src)
        self.assertIn("item.get(\"owner_action_required\")", src)
        self.assertIn("daily.get(\"owner_action_required\")", src)
        self.assertIn("ocean_daily_delivery", src)
        self.assertIn("dushi_daily_delivery", src)
        self.assertIn("Океан Клиентов и Борис", src)
        self.assertIn("От Души", src)
        self.assertIn("same service-date incident", src)
        self.assertIn("Older missed days", src)

    def test_incident_reconciler_runs_daily_outcome_sync_even_without_other_incidents(self):
        src = inspect.getsource(incident_reconciler.run_once)
        self.assertIn("_sync_daily_outcome_incidents", src)
        self.assertIn('"daily_outcomes":daily_outcomes', src)

    def test_owner_report_contains_compact_email_social_plan_fact(self):
        from app.api import inbox_daily
        src = inspect.getsource(inbox_daily._owner_runtime_outcome_summary)
        self.assertIn("Email: факт", src)
        self.assertIn("Email · версия текста", src)
        self.assertIn("quality_versioned_sent", src)
        self.assertIn("quality_untracked_sent", src)
        self.assertIn("quality_unversioned_after_enforcement", src)
        self.assertIn("Social ·", src)
        self.assertIn("Действие владельца", src)
        self.assertIn("email_delivery_health", src)
        self.assertIn("ocean_daily_delivery_health", src)
        self.assertIn("dushi_daily_delivery_health", src)
        self.assertIn("'social_projects':social_projects", src)

    def test_owner_report_fail_closes_if_runtime_summary_is_unavailable(self):
        from app.api import inbox_daily
        src = inspect.getsource(inbox_daily.send_owner_daily_report)
        self.assertIn("_owner_runtime_outcome_summary()", src)
        self.assertIn("'stage':'runtime_outcome_summary'", src)
        self.assertIn("runtime_outcome['text']", src)
        self.assertIn("'runtime_outcome':{", src)


if __name__ == "__main__":
    unittest.main()
