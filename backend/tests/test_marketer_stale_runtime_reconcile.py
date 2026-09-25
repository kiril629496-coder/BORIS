import inspect
import unittest

import marketer_rollout_runner as rollout
from app.services import brain_recovery


class MarketerStaleRuntimeReconcileTests(unittest.TestCase):
    def test_money_authority_claim_detects_mandate_flag(self):
        self.assertTrue(rollout._runtime_claims_marketer_money({
            "mandate_active": True,
            "allowed_operations": [],
        }))

    def test_money_authority_claim_detects_cpx_operations(self):
        self.assertTrue(rollout._runtime_claims_marketer_money({
            "mandate_active": False,
            "allowed_operations": ["cpx.raise_bid"],
        }))
        self.assertFalse(rollout._runtime_claims_marketer_money({
            "mandate_active": False,
            "allowed_operations": [],
        }))
        self.assertTrue(rollout._runtime_claims_marketer_money({
            "mandate_active": False,
            "allowed_operations": [],
            "plan_diagnostics": {"allowed_operations": ["cpx.raise_bid"]},
        }))
        self.assertTrue(rollout._runtime_claims_marketer_money({
            "mandate_active": False,
            "allowed_operations": [],
            "plan_diagnostics": {"selected_for_execution": 2},
        }))

    def test_inactive_projection_removes_owner_facing_money_authority(self):
        out=rollout._inactive_runtime_projection(
            {
                "mandate_active": True,
                "allowed_operations": ["cpx.raise_bid","cpx.lower_bid"],
                "status": "ok",
                "planned": 4,
                "applied": 1,
                "owner_action_required": True,
                "plan_diagnostics": {
                    "decision": "RAISE_CANDIDATES",
                    "selected_for_execution": 4,
                    "planned_after_guard": 3,
                    "proven_after_guard": 2,
                    "allowed_operations": ["cpx.raise_bid","cpx.lower_bid"],
                },
            },
            "expired",
            {"state":"expired","source":"billing.paid_until"},
            "2026-09-04T12:00:00+00:00",
        )
        self.assertFalse(out["mandate_active"])
        self.assertEqual(out["allowed_operations"],[])
        self.assertEqual(out["status"],"inactive")
        self.assertEqual(out["planned"],0)
        self.assertEqual(out["applied"],0)
        self.assertFalse(out["owner_action_required"])
        self.assertEqual(out["service_entitlement"]["state"],"expired")
        self.assertEqual(out["plan_diagnostics"]["allowed_operations"],[])
        self.assertEqual(out["plan_diagnostics"]["selected_for_execution"],0)
        self.assertEqual(out["plan_diagnostics"]["planned_after_guard"],0)
        self.assertEqual(out["plan_diagnostics"]["proven_after_guard"],0)
        self.assertEqual(out["plan_diagnostics"]["decision"],"INACTIVE_PAID_PERIOD")

    def test_reconcile_scans_runtime_and_rollout_state_union(self):
        src=inspect.getsource(rollout._reconcile_stale_rollout_states)
        self.assertIn("virtual_marketer_runtime",src)
        self.assertIn("MARKETER_STALE_RUNTIME_SOURCE_UNION_V2",src)
        self.assertIn("_runtime_claims_marketer_money(runtime)",src)

    def test_reconcile_runs_before_global_money_preflight(self):
        src=inspect.getsource(rollout.main)
        self.assertLess(
            src.index("_reconcile_stale_rollout_states"),
            src.index("_global_preflight"),
        )
        self.assertIn("MARKETER_STALE_RUNTIME_PRE_GLOBAL_GUARD_V1",src)

    def test_system_brain_wires_same_safe_reconciler(self):
        helper=inspect.getsource(brain_recovery._safe_marketer_runtime_recovery)
        runner=inspect.getsource(brain_recovery.run_safe_recovery)
        self.assertIn("MARKETER_STALE_RUNTIME_BRAIN_SELFHEAL_V1",helper)
        self.assertIn("_reconcile_stale_rollout_states",helper)
        self.assertIn("_safe_marketer_runtime_recovery",runner)

    def test_production_contract_contains_live_stale_authority_invariant(self):
        src=open("/root/BORIS/backend/production_contract_runner.py",encoding="utf-8").read()
        self.assertIn("MARKETER_STALE_RUNTIME_LIVE_CONTRACT_V1",src)
        self.assertIn('checks["marketer_stale_runtime_authority_zero"]',src)


if __name__=="__main__":
    unittest.main()
