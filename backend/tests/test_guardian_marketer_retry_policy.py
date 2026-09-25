import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import boris_guardian as guardian


class GuardianMarketerRetryPolicyTests(unittest.TestCase):
    def _sh_failed(self, failure_id="123"):
        def fake(cmd, timeout=25):
            if "NextElapseUSecRealtime" in cmd:
                return 0, "Tue 2026-09-08 17:05:00 UTC"
            if "systemctl is-failed boris-ai-marketer.service" in cmd:
                return 0, "failed"
            if "InactiveEnterTimestampMonotonic" in cmd:
                return 0, failure_id
            return 0, ""
        return fake

    def test_first_unique_failure_is_warning_when_timer_retry_is_ready(self):
        state = {}
        with patch.object(guardian, "active", return_value=True), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value=None), \
             patch.object(guardian, "sh", side_effect=self._sh_failed("100")):
            out = guardian.marketer_execution_health(state)
        self.assertEqual(out["bad"], [])
        self.assertTrue(out["warnings"])
        self.assertEqual(out["consecutive"], 1)
        self.assertEqual(state["marketer_consecutive_failed_executions"], 1)

    def test_same_failed_execution_does_not_increment_on_each_guardian_tick(self):
        state = {
            "marketer_last_failed_event": "100",
            "marketer_consecutive_failed_executions": 1,
        }
        with patch.object(guardian, "active", return_value=True), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value=None), \
             patch.object(guardian, "sh", side_effect=self._sh_failed("100")):
            out = guardian.marketer_execution_health(state)
        self.assertEqual(out["bad"], [])
        self.assertEqual(out["consecutive"], 1)

    def test_second_distinct_failed_execution_is_critical(self):
        state = {
            "marketer_last_failed_event": "100",
            "marketer_consecutive_failed_executions": 1,
        }
        with patch.object(guardian, "active", return_value=True), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value=None), \
             patch.object(guardian, "sh", side_effect=self._sh_failed("200")):
            out = guardian.marketer_execution_health(state)
        self.assertTrue(out["bad"])
        self.assertEqual(out["consecutive"], 2)

    def test_successful_execution_resets_consecutive_failure_state(self):
        state = {
            "marketer_last_failed_event": "100",
            "marketer_consecutive_failed_executions": 1,
        }
        def fake(cmd, timeout=25):
            if "NextElapseUSecRealtime" in cmd:
                return 0, "Tue 2026-09-08 17:05:00 UTC"
            if "systemctl is-failed boris-ai-marketer.service" in cmd:
                return 1, "inactive"
            return 0, ""
        with patch.object(guardian, "active", return_value=True), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value=None), \
             patch.object(guardian, "sh", side_effect=fake):
            out = guardian.marketer_execution_health(state)
        self.assertFalse(out["failed"])
        self.assertEqual(state["marketer_last_failed_event"], "")
        self.assertEqual(state["marketer_consecutive_failed_executions"], 0)

    def test_failure_is_critical_if_canonical_retry_timer_is_not_ready(self):
        state = {}
        with patch.object(guardian, "active", return_value=False), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value=None), \
             patch.object(guardian, "sh", side_effect=self._sh_failed("100")):
            out = guardian.marketer_execution_health(state)
        self.assertTrue(out["bad"])
        self.assertFalse(out["retry_ready"])

    def test_deterministic_account_local_guard_abort_is_critical_immediately(self):
        state = {}
        with patch.object(guardian, "active", return_value=True), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value="blocked_first_bid_cross_source_hourly_atomic"), \
             patch.object(guardian, "sh", side_effect=self._sh_failed("100")):
            out = guardian.marketer_execution_health(state)
        self.assertTrue(out["bad"])
        self.assertEqual(out["failure_kind"], "blocked_first_bid_cross_source_hourly_atomic")
        self.assertEqual(out["consecutive"], 1)

    def test_db_session_disconnect_abort_is_critical_immediately(self):
        state = {}
        with patch.object(guardian, "active", return_value=True), \
             patch.object(guardian, "enabled", return_value=True), \
             patch.object(guardian, "_marketer_deterministic_fleet_abort", return_value="rollout_db_session_closed"), \
             patch.object(guardian, "sh", side_effect=self._sh_failed("101")):
            out = guardian.marketer_execution_health(state)
        self.assertTrue(out["bad"])
        self.assertEqual(out["failure_kind"], "rollout_db_session_closed")
        self.assertIn("DB-session", out["bad"][0])

    def test_detector_recognizes_last_cycle_ssl_disconnect(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "logs").mkdir()
            (root / "logs" / "ai_marketer_hourly.log").write_text(
                "===== AI_MARKETER_START 2026-09-11T12:05:02Z =====\n"
                "sqlalchemy.exc.OperationalError: SSL connection has been closed unexpectedly\n"
                "STAGE_FAIL marketer_rollout rc=1\n",
                encoding="utf-8",
            )
            with patch.object(guardian, "B", root):
                kind = guardian._marketer_deterministic_fleet_abort()
        self.assertEqual(kind, "rollout_db_session_closed")


    def test_db_session_mitigation_health_recognizes_permanent_source_fix(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'marketer_rollout_runner.py').write_text(
                'MARKETER_DB_READ_RECONNECT_V2\n'
                'MARKETER_COORDINATOR_READ_TX_RELEASE_V1\n'
                'MARKETER_COORDINATOR_FRESH_SESSION_V2\n',
                encoding='utf-8',
            )
            with patch.object(guardian,'B',root), \
                 patch.object(guardian,'sh',return_value=(0,'BORIS_POLICY_SELF_HEAL=0')):
                out=guardian.marketer_db_session_mitigation_health()
        self.assertTrue(out['source_fixed'])
        self.assertFalse(out['mitigation_active'])

    def test_db_session_mitigation_health_accepts_scoped_service_override(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'marketer_rollout_runner.py').write_text('STABLE_MAX_ACTIONS=5\n',encoding='utf-8')
            with patch.object(guardian,'B',root), \
                 patch.object(guardian,'sh',return_value=(0,'PGOPTIONS=-c idle_in_transaction_session_timeout=10min')):
                out=guardian.marketer_db_session_mitigation_health()
        self.assertFalse(out['source_fixed'])
        self.assertTrue(out['mitigation_active'])

    def test_db_session_mitigation_self_heal_is_bounded_and_does_not_start_marketer(self):
        state={}
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'40-db-session-mitigation.conf'
            with patch.object(guardian,'MARKETER_DB_MITIGATION_PATH',target), \
                 patch.object(guardian,'_marketer_deterministic_fleet_abort',return_value='rollout_db_session_closed'), \
                 patch.object(guardian,'marketer_db_session_mitigation_health',side_effect=[
                     {'source_fixed':False,'mitigation_active':False,'environment_read_ok':True},
                     {'source_fixed':False,'mitigation_active':True,'environment_read_ok':True},
                 ]), \
                 patch.object(guardian,'sh',return_value=(0,'')) as sh_mock:
                out=guardian.heal_marketer_db_session_mitigation(state)
            self.assertEqual(out['status'],'healed')
            self.assertTrue(target.is_file())
            commands=[c.args[0] for c in sh_mock.call_args_list]
            self.assertEqual(commands,['systemctl daemon-reload'])
            self.assertFalse(any('start boris-ai-marketer' in c or 'restart boris-ai-marketer' in c for c in commands))


class GuardianBidCapRecoveryPolicyTests(unittest.TestCase):
    def test_clean_cap_health_is_ok(self):
        self.assertEqual(
            guardian.bid_cap_recovery_mode({'bad': [], 'hard_bad': [], 'repair_accounts': []}, False),
            'ok',
        )

    def test_hard_cap_violation_is_never_hidden_by_hourly_cycle(self):
        health={'bad':['violation'],'hard_bad':['violation'],'repair_accounts':['a']}
        self.assertEqual(guardian.bid_cap_recovery_mode(health, True), 'critical')

    def test_missing_runtime_waits_for_already_running_hourly_owner(self):
        health={'bad':['missing'],'hard_bad':[],'repair_accounts':['a']}
        self.assertEqual(guardian.bid_cap_recovery_mode(health, True), 'wait_hourly')

    def test_missing_runtime_repairs_when_hourly_owner_is_idle(self):
        health={'bad':['missing'],'hard_bad':[],'repair_accounts':['a']}
        self.assertEqual(guardian.bid_cap_recovery_mode(health, False), 'repair')

    def test_bid_cap_heal_uses_targeted_lower_only_cli_and_backoff(self):
        state={}
        fake=type('P',(),{'returncode':0,'stdout':'CPX_CAP_RECONCILE_DONE failures=[]','stderr':''})()
        with patch.object(guardian.subprocess, 'run', return_value=fake) as run:
            out=guardian.heal_bid_cap_accounts(['acct_1'],state,max_accounts=2)
        self.assertEqual(out[0]['status'],'attempted')
        args=run.call_args.args[0]
        self.assertIn('cpx_cap_reconciler.py', args[1])
        self.assertEqual(args[-3:], ['--apply','--account','acct_1'])
        with patch.object(guardian.subprocess, 'run') as run2:
            out2=guardian.heal_bid_cap_accounts(['acct_1'],state,max_accounts=2)
        self.assertEqual(out2[0]['status'],'backoff')
        run2.assert_not_called()


class GuardianMarketerRuleContractTests(unittest.TestCase):
    def test_ruleset_drift_guard_covers_full_business_rule(self):
        src = open(guardian.__file__, encoding="utf-8").read()
        block = src[src.index("def marketer_owner_rule_drift_health"):
                    src.index("def marketer_account_progress_health")]
        self.assertIn("RULESET_DRIFT_GUARD_V2", block)
        self.assertIn("target_leads_per_day", block)
        self.assertIn("max_cost_per_lead_rub", block)
        self.assertIn("daily_budget_limit_rub", block)
        self.assertIn("bid_autopilot", block)
        self.assertIn("authenticated_set_kpi_settings", block)

    def test_account_execution_obligation_is_not_status_ok_only(self):
        src = open(guardian.__file__, encoding="utf-8").read()
        block = src[src.index("def marketer_account_progress_health"):
                    src.index("def classify_mop_training_health")]
        self.assertIn("ACCOUNT_EXECUTION_OBLIGATION_V2", block)
        self.assertIn("virtual_marketer_runtime", block)
        self.assertIn("marketer_rollout_state_v1", block)
        self.assertIn("known_blocker", block)
        self.assertIn("meaningful_nonmoney", block)
        self.assertIn("fleet_not_processed", block)
        self.assertIn("no_effective_action", block)


    def test_source_contract_guard_prevents_false_green(self):
        src = open(guardian.__file__, encoding="utf-8").read()
        block = src[src.index("def marketer_source_contract_health"):
                    src.index("def marketer_account_progress_health")]
        self.assertIn("MARKETER_SOURCE_CONTRACT_GUARD_V1", block)
        self.assertIn("STABLE_MAX_ACTIONS", block)
        self.assertIn("required_min", block)
        self.assertIn("LOWVIEWS_ATOMIC_CAP_POLICY_BLOCK_V1", block)
        self.assertIn("ad_spend_rub", block)
        self.assertIn("all_spend_rub", block)
        self.assertIn("RED_CPL_EXECUTION_CONTRACT_GUARD_V1", block)
        self.assertIn("RED_CPL_LOWER_GENERAL_V1", block)
        self.assertIn("CPX_RUNNER_DRY_RUN_NO_RUNTIME_MUTATION_V1", block)



    def test_provider_lag_is_counted_per_hourly_snapshot_not_guardian_tick(self):
        src = open(guardian.__file__, encoding="utf-8").read()
        block = src[src.index("def marketer_account_progress_health"):
                    src.index("def classify_mop_training_health")]
        self.assertIn("PROVIDER_STATS_LAG_SLA_V1", block)
        self.assertIn("marketer_provider_lag:", block)
        self.assertIn("collected_at==prev_generation", block)
        self.assertIn("consecutive_cycles", block)

    def test_missing_control_baseline_self_heal_never_creates_money_authority(self):
        src = open(guardian.__file__, encoding="utf-8").read()
        block = src[src.index("def heal_missing_marketing_control_baselines"):
                    src.index("def marketer_source_contract_health")]
        self.assertIn("MARKETER_CONTROL_BASELINE_SELF_HEAL_V1", block)
        self.assertIn("authenticated_set_kpi_settings", block)
        self.assertIn("money_authority_created", block)
        self.assertIn("already_exists", block)
        self.assertNotIn("set_kpi_settings(", block)



def test_content_ai_scheduler_is_guarded_by_quick_guardian():
    from pathlib import Path
    src=Path(guardian.__file__).read_text(encoding="utf-8")
    assert "KPI_CONTENT_AI_SCHEDULER_SELF_HEAL_V1" in src
    assert "boris-kpi-content-ai.timer" in src
    assert "systemctl enable --now " in src
    assert "NextElapseUSecRealtime" in src


if __name__ == "__main__":
    unittest.main()


def test_prepare_stall_counts_hourly_generation_not_guardian_ticks():
    state={}
    one=guardian._marketer_prepare_stall_tick(state,'acct','op1','apply_in_progress',generation='g1')
    same=guardian._marketer_prepare_stall_tick(state,'acct','op1','apply_in_progress',generation='g1')
    two=guardian._marketer_prepare_stall_tick(state,'acct','op1','apply_in_progress',generation='g2')
    assert one['checks']==1
    assert same['checks']==1
    assert two['checks']==2
    assert not two['stalled']


def test_prepare_stall_migrates_old_tick_counter_without_false_alert():
    state={'marketer_prepare_pending:acct:op1':{'checks':99,'first_seen':1.0,'status':'apply_in_progress'}}
    out=guardian._marketer_prepare_stall_tick(state,'acct','op1','apply_in_progress',generation='fresh-hour')
    assert out['checks']==1
    assert not out['warning']
    assert not out['stalled']
