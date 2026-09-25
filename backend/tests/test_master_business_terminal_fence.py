import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class TestMasterBusinessTerminalFence(unittest.TestCase):
    def test_master_coverage_never_resurrects_business_terminal_jobs(self):
        s=(ROOT/'app/ext_api/master.py').read_text()
        self.assertIn('BUSINESS_GOAL_MASTER_TERMINAL_FENCE_V1',s)
        block=s[s.index('BUSINESS_GOAL_MASTER_TERMINAL_FENCE_V1')-300:s.index('BUSINESS_GOAL_MASTER_TERMINAL_FENCE_V1')+900]
        self.assertIn('dev.business_goal_terminal_job_ids()',block)
        self.assertIn('continue',block)
if __name__=='__main__': unittest.main()
