from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import unittest

from app.services.reliability import heartbeat_health


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *args, **kwargs):
        return _FakeQuery(self.rows)


def _row(worker_id, age_sec, state="ok", details=None):
    return SimpleNamespace(
        module="runtime",
        worker_id=worker_id,
        account_id="*",
        state=state,
        details_json=details or {},
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=age_sec),
    )


class SocialReliabilitySlaTest(unittest.TestCase):
    def test_replacement_watch_uses_timer_aware_sla(self):
        health = heartbeat_health(
            _FakeDB([
                _row("social_vk_replacement_watch", 600),
                _row("social_due_rescue", 400),
                _row("social_terminal_reconcile", 400),
            ]),
            stale_seconds=300,
        )
        by_id = {x["worker_id"]: x for x in health["workers"]}
        self.assertEqual(by_id["social_vk_replacement_watch"]["state"], "ok")
        self.assertEqual(by_id["social_due_rescue"]["state"], "stale")
        self.assertEqual(by_id["social_terminal_reconcile"]["state"], "stale")
        self.assertEqual(health["stale"], 2)

    def test_fresh_social_aux_heartbeats_remain_green(self):
        health = heartbeat_health(
            _FakeDB([
                _row("social_vk_replacement_watch", 900),
                _row("social_due_rescue", 300),
                _row("social_terminal_reconcile", 120),
            ]),
            stale_seconds=300,
        )
        self.assertEqual(health["state"], "ok")
        self.assertEqual(health["stale"], 0)


if __name__ == "__main__":
    unittest.main()
