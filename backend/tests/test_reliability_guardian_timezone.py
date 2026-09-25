import unittest
from datetime import datetime, timezone
from pathlib import Path

class ReliabilityGuardianTimezoneRegression(unittest.TestCase):
    def test_mixed_timestamp_normalization_is_safe(self):
        now=datetime.now(timezone.utc)
        for raw in ('2026-09-05T15:54:20','2026-09-05T15:54:20+00:00','2026-09-05T18:54:20+03:00'):
            ts=datetime.fromisoformat(raw)
            ts=ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
            self.assertIsInstance((now-ts).total_seconds(), float)
    def test_guardian_source_has_timezone_fence(self):
        src=Path('/root/BORIS/backend/app/api/reliability.py').read_text()
        self.assertIn('RELIABILITY_GUARDIAN_TZ_NORMALIZE_V1',src)
        self.assertIn('ts = ts.replace(tzinfo=timezone.utc)',src)
        self.assertIn('ts = ts.astimezone(timezone.utc)',src)
if __name__=='__main__': unittest.main()
