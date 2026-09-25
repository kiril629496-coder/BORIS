import inspect
import unittest
from unittest.mock import patch

import marketer_rollout_runner as rollout


class MarketerRolloutEntitlementAlignmentTests(unittest.TestCase):
    def test_unlimited_uses_same_fail_closed_authority_as_mandate_executor(self):
        with patch.object(rollout, "_load", return_value={"tier":"none","unlimited":True}), \
             patch("app.services.control_plane_adapters_ext.marketing_service_entitlement",
                   return_value={"state":"expired","source":"payment_status","until":"2026-08-01T00:00:00+00:00"}):
            ok,evidence=rollout._rollout_marketer_entitlement(object(),"garik")
        self.assertFalse(ok)
        self.assertEqual(evidence["state"],"expired")
        self.assertEqual(evidence["source"],"payment_status")

    def test_active_paid_period_is_authorized(self):
        with patch.object(rollout, "_load", return_value={"tier":"tariff_1","unlimited":False}), \
             patch("app.services.control_plane_adapters_ext.marketing_service_entitlement",
                   return_value={"state":"active","source":"billing.paid_until"}):
            ok,evidence=rollout._rollout_marketer_entitlement(object(),"active")
        self.assertTrue(ok)
        self.assertEqual(evidence["state"],"active")

    def test_preflight_and_stale_reconcile_share_one_entitlement_helper(self):
        preflight=inspect.getsource(rollout._account_preflight)
        stale=inspect.getsource(rollout._reconcile_stale_rollout_states)
        self.assertIn("_rollout_marketer_entitlement",preflight)
        self.assertIn("_rollout_marketer_entitlement",stale)
        self.assertNotIn("marketing_service_entitlement(db, a)",preflight)
        self.assertNotIn("marketing_service_entitlement(db,str(a))",stale)

    def test_rollout_contract_marker_is_present(self):
        src=inspect.getsource(rollout._rollout_marketer_entitlement)
        self.assertIn("MARKETER_ROLLOUT_CANONICAL_ENTITLEMENT_V1",src)


if __name__=="__main__":
    unittest.main()
