import os
import unittest
from unittest.mock import patch

import app.usage as U


class UsageQaIsolationTests(unittest.TestCase):
    def test_test_mode_returns_cost_without_opening_ledger_session(self):
        with patch.dict(os.environ,{"BORIS_TEST_MODE":"1"}), patch.object(U,"UsageSession",side_effect=AssertionError("ledger must not open")):
            cost=U.log_usage("real-looking-account","openai","gpt-5.4",operation="qa",prompt_tokens=100,completion_tokens=10,request_id="qa-test-mode")
        self.assertGreaterEqual(cost,0)

    def test_qa_account_never_writes_ledger_without_test_env(self):
        with patch.dict(os.environ,{"BORIS_TEST_MODE":"0"}), patch.object(U,"UsageSession",side_effect=AssertionError("ledger must not open")):
            cost=U.log_usage("__qa_usage_123","openai","gpt-5.4",operation="qa",cost_rub=1.2345,request_id="qa-account")
        self.assertEqual(cost,1.2345)

    def test_real_account_still_uses_ledger_boundary(self):
        with patch.dict(os.environ,{"BORIS_TEST_MODE":"0"}), patch.object(U,"UsageSession",side_effect=RuntimeError("sentinel")) as factory:
            cost=U.log_usage("real-account","openai","gpt-5.4",operation="real",cost_rub=2.0,request_id="real-account-test")
        self.assertEqual(factory.call_count,1)
        self.assertEqual(cost,0)


if __name__=="__main__":
    unittest.main()
