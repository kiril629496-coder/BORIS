import inspect
import unittest

import cpx_advisor_runner as runner


class MarketerSameCycleDiagnosticsTests(unittest.TestCase):
    def test_created_mandate_is_visible_in_same_cycle(self):
        out=runner._cycle_permissions_after_capacity(
            set(),
            {"status":"created","allowed_operations":["cpx.raise_bid","cpx.lower_bid"]},
        )
        self.assertEqual(out,{"cpx.raise_bid","cpx.lower_bid"})

    def test_upgraded_mandate_replaces_stale_preheal_permissions(self):
        out=runner._cycle_permissions_after_capacity(
            {"cpx.raise_bid"},
            {"status":"upgraded","allowed_operations":["cpx.lower_bid"]},
        )
        self.assertEqual(out,{"cpx.lower_bid"})

    def test_blocked_capacity_clears_stale_money_permissions(self):
        out=runner._cycle_permissions_after_capacity(
            {"cpx.raise_bid","cpx.lower_bid"},
            {"status":"blocked","reason":"explicit_daily_budget_missing"},
        )
        self.assertEqual(out,set())

    def test_capacity_error_fails_closed(self):
        out=runner._cycle_permissions_after_capacity(
            {"cpx.raise_bid"},
            {"status":"error"},
        )
        self.assertEqual(out,set())

    def test_nonmoney_mode_is_never_empty(self):
        mode=runner._nonmoney_runtime_mode("no_stats","нет свежей статистики")
        self.assertTrue(mode)
        self.assertIn("NON_MONEY_DIAGNOSTICS",mode)
        self.assertIn("no_stats",mode)

    def test_main_materializes_diagnostics_even_without_preloop_mandate(self):
        src=inspect.getsource(runner.main)
        self.assertIn("cycle_allowed_ops=_cycle_permissions_after_capacity",src)
        self.assertIn("if advice:",src)
        self.assertNotIn("if acc in auto_accounts and advice:",src)
        self.assertIn("KPI_RUNTIME_DIAGNOSIS_ALWAYS_MATERIALIZED_V1",src)
        self.assertIn('runtime["mode"]=_nonmoney_runtime_mode',src)

    def test_dry_run_never_persists_production_runtime_heartbeat(self):
        src=inspect.getsource(runner.main)
        self.assertIn("CPX_RUNNER_DRY_RUN_NO_RUNTIME_MUTATION_V1",src)
        tail=src[src.index("CPX_RUNNER_DRY_RUN_NO_RUNTIME_MUTATION_V1"):]
        self.assertIn("if APPLY:",tail)
        self.assertIn("_save_runtime(cur, conn, acc, runtime)",tail)
        self.assertIn("production runtime heartbeat not persisted",tail)

    def test_push_max_mode_uses_headroom_cpl_without_nameerror(self):
        # Regression for production 2026-09-04: the live staged marketer reached
        # RAISE_CANDIDATES and crashed while formatting PUSH MAX because it read
        # an undefined local actual_cpl_rub instead of canonical cpl_headroom.
        from unittest.mock import patch

        class FakeDB:
            def close(self):
                pass

        intraday={
            "decision":"RAISE_CANDIDATES",
            "kpi_pace":{
                "state":"BEHIND","target":6,"actual":4,
                "expected_by_now":4.5,"elapsed_share":0.5,
            },
            "cpl_headroom":{
                "active":True,"actual_cpl_rub":59.25,"red_cpl_rub":400.0,
            },
            "selected_for_execution":1,
            "candidates_total":10,
            "mandate":{"max_actions_run":5},
            "top":[{
                "item_id":8253463410,
                "status":"eligible",
                "current_bid_rub":80.0,
                "promotion_active":True,
                "historical_signal":{
                    "contacts_7d":99,"views_7d":999,"days_with_data":7,
                },
                "intraday_signal":{"contacts_today":1},
            }],
        }
        with patch("app.db.session.SessionLocal",return_value=FakeDB()), \
             patch("app.services.intraday.select_intraday_raise_candidates",return_value=intraday):
            plan,mode,diagnostics=runner._build_plan("prodazha_bytovok_25677",{})

        self.assertEqual(plan,[({"id":8253463410,"source":"cpl_headroom_push"},"raise")])
        self.assertEqual(mode,"PUSH MAX: CPL 59.25 < красной 400.0")
        self.assertEqual(diagnostics["planned_after_guard"],1)
        self.assertEqual(diagnostics["reason"],"ready")


if __name__=="__main__":
    unittest.main()
