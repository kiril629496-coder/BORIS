import re, unittest
from pathlib import Path
class VisualGuardTimeoutContractTests(unittest.TestCase):
    def test_lock_wait_fits_systemd_budget_and_busy_is_clean_retry(self):
        s=Path('scripts/avito_visual_guard_watchdog.sh').read_text()
        m=re.search(r'flock -w (\d+) /run/lock/boris-backend-rolling\.lock',s)
        self.assertIsNotNone(m)
        self.assertLess(int(m.group(1)),180)
        self.assertIn('SKIP_DEPLOY_BUSY',s)
        block=s[s.index('if ! flock'):s.index('echo "[$STAMP] STEP deploy_quiescence PASS"')]
        self.assertIn('exit 0',block)
if __name__=='__main__': unittest.main()
