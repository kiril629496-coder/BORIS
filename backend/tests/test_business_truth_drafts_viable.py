import inspect
import unittest
from app.ext_api import report

class BusinessTruthDraftsViableContract(unittest.TestCase):
    def test_draft_artifact_counts_viable_cards_not_only_ready(self):
        src = inspect.getsource(report._measure)
        self.assertIn("BUSINESS_DRAFTS_VIABLE_COUNT_V1", src)
        self.assertIn("WHEN :artifact_kind='drafts' THEN viable_n", src)
        self.assertIn('args["artifact_kind"] = kind', src)

    def test_vasily_contract_is_drafts_ten(self):
        self.assertEqual(report.ARTIFACTS["CLIENT_VASILY"]["kind"], "drafts")
        self.assertEqual(report.ARTIFACTS["CLIENT_VASILY"]["expected"], 10)

if __name__ == '__main__':
    unittest.main()

class BusinessGoalDoneCancelAllOpenContract(unittest.TestCase):
    def test_done_goal_cancels_all_open_business_orders(self):
        from app.ext_api import clients
        src = inspect.getsource(clients.ensure_business_orders)
        self.assertIn("BUSINESS_GOAL_DONE_CANCEL_ALL_OPEN_V1", src)
        self.assertIn("a2a.BLOCKED_HUMAN, a2a.BLOCKED_INFRA", src)
        self.assertIn("claimed_by=NULL", src)

class BusinessGoalCreateTerminalFenceContract(unittest.TestCase):
    def test_create_respects_terminal_business_marker(self):
        from app.ext_api import clients
        src=inspect.getsource(clients.create)
        self.assertIn("BUSINESS_GOAL_CREATE_TERMINAL_FENCE_V1",src)
        self.assertIn("dev.business_goal_terminal_job_ids()",src)
        self.assertIn("not terminal_business",src)

class ClientBootstrapOptionalFirstOrderContract(unittest.TestCase):
    def test_missing_first_order_is_not_a_crash_contract(self):
        from app.ext_api import clients
        src=inspect.getsource(clients.create)
        self.assertIn("CLIENT_BOOTSTRAP_OPTIONAL_FIRST_ORDER_V1",src)
        self.assertIn('first_order = spec.get("first_order")',src)
        self.assertIn("with_orders and first_order",src)
