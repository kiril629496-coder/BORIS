import unittest
from unittest.mock import patch
from app.ext_api import dev

class BusinessTerminalDurableProofContract(unittest.TestCase):
    def test_terminal_query_does_not_require_receipt_to_be_latest_order(self):
        seen={}
        def rows(sql, **kwargs):
            seen['sql']=sql
            return [{'id':619}]
        with patch.object(dev.db,'rows',side_effect=rows):
            self.assertEqual(dev.business_goal_terminal_job_ids(), {619})
        sql=seen['sql']
        self.assertIn("blocked_reason='business_goal_already_confirmed'", sql)
        self.assertNotIn('MAX(o2.id)', sql)

    def test_terminal_query_excludes_jobs_already_completed_or_cancelled(self):
        seen={}
        def rows(sql, **kwargs):
            seen['sql']=sql
            return []
        with patch.object(dev.db,'rows',side_effect=rows):
            self.assertEqual(dev.business_goal_terminal_job_ids(), set())
        sql=seen['sql']
        # Durable business proof fences only NON-terminal dev rows. Historical
        # production IDs eventually become completed and must then leave this set.
        self.assertIn("j.status NOT IN ('completed','cancelled')", sql)
        self.assertIn("o.blocked_reason='business_goal_already_confirmed'", sql)

if __name__=='__main__': unittest.main()
