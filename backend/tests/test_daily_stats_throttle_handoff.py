import ast
import pathlib
import unittest
from unittest.mock import patch

import daily_stats_collector as stats

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / 'daily_stats_collector.py'

class DailyStatsThrottleHandoffContract(unittest.TestCase):
    def test_provider_throttle_is_deferred_not_slept_and_retried(self):
        src = SRC.read_text(encoding='utf-8')
        self.assertIn('STATS_TENANT_THROTTLE_HANDOFF_V2', src)
        self.assertIn('SELF_HEAL_DEFER provider throttle', src)
        self.assertNotIn('SELF_HEAL_WAIT provider throttle', src)
        tree = ast.parse(src)
        sleeps = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and isinstance(n.func.value, ast.Name) and n.func.value.id == 'time' and n.func.attr == 'sleep']
        # Internal HA transport retry may still sleep; the 429 branch itself must not.
        for node in sleeps:
            line = getattr(node, 'lineno', 0)
            self.assertFalse(125 <= line <= 155, f'429 branch still sleeps at line {line}')

    def test_throttle_branch_does_not_call_collect_twice(self):
        src = SRC.read_text(encoding='utf-8')
        start = src.index('if data.get("status") == "degraded" and data.get("reason") == "avito_account_throttled":')
        end = src.index('if data.get("status") == "ok":', start)
        branch = src[start:end]
        self.assertNotIn('_collect_one(', branch)

    def test_fleetwide_provider_throttle_returns_deferred_rc75(self):
        accounts=["qa-a","qa-b","qa-c"]
        def collect(_account_id):
            return {
                "status":"degraded",
                "reason":"avito_account_throttled",
                "message":"provider rate limited",
                "retry_after_seconds":30,
            }
        with patch.object(stats,"get_all_account_ids",return_value=accounts), \
             patch.object(stats,"_collect_one",side_effect=collect):
            rc=stats.main()
        self.assertEqual(rc,75)

    def test_real_transport_failure_is_not_hidden_as_provider_deferred(self):
        accounts=["qa-a","qa-b"]
        def collect(account_id):
            if account_id=="qa-a":
                return {
                    "status":"degraded",
                    "reason":"avito_account_throttled",
                    "message":"provider rate limited",
                    "retry_after_seconds":30,
                }
            raise RuntimeError("internal transport broken")
        with patch.object(stats,"get_all_account_ids",return_value=accounts), \
             patch.object(stats,"_collect_one",side_effect=collect):
            rc=stats.main()
        self.assertEqual(rc,1)


if __name__ == '__main__':
    unittest.main()
