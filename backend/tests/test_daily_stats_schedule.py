import unittest
from pathlib import Path

class DailyStatsScheduleTests(unittest.TestCase):
    def test_collector_has_runtime_schedule(self):
        timer=Path('/etc/systemd/system/boris-daily-stats-collector.timer').read_text()
        service=Path('/etc/systemd/system/boris-daily-stats-collector.service').read_text()
        self.assertIn('OnCalendar=*-*-* *:05:00 UTC', timer)
        self.assertIn('Persistent=true', timer)
        self.assertIn('/root/BORIS/backend/daily_stats_collector.py', service)
        self.assertIn('TimeoutStartSec=1200', service)

if __name__=='__main__': unittest.main()
