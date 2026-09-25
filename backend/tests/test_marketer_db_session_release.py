from pathlib import Path

from sqlalchemy.exc import OperationalError

import marketer_rollout_runner as runner


class _Result:
    def fetchone(self):
        return ('{"ok": true}',)


class _FlakyReadDB:
    def __init__(self):
        self.execute_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.commit_calls = 0

    def execute(self, *args, **kwargs):
        self.execute_calls += 1
        if self.execute_calls == 1:
            raise OperationalError("select", {}, Exception("SSL connection closed"))
        return _Result()

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.close_calls += 1

    def commit(self):
        self.commit_calls += 1


def test_read_only_load_reconnects_once_after_operational_error():
    db = _FlakyReadDB()
    assert runner._load(db, "acc", "key") == {"ok": True}
    assert db.execute_calls == 2
    assert db.close_calls == 1
    assert db.rollback_calls == 0
    assert db.commit_calls == 0


def test_long_provider_steps_release_coordinator_read_transaction():
    src = Path("marketer_rollout_runner.py").read_text(encoding="utf-8")
    assert "MARKETER_DB_READ_RECONNECT_V2" in src
    assert "MARKETER_COORDINATOR_READ_TX_RELEASE_V1" in src
    assert "MARKETER_COORDINATOR_FRESH_SESSION_V2" in src
    loop = src.index("for a,stage,safe,live_successes,evidence in selected:")
    profitable = src.index("growth=profitable_day_push", loop)
    assert src.index("_release_read_tx(db)", loop, profitable) < profitable
    child = src.index("cmd=['/root/BORIS/backend/venv/bin/python'", profitable)
    assert src.rfind("_release_read_tx(db)", profitable, child) != -1


def test_release_boundary_closes_physical_session():
    db = _FlakyReadDB()
    runner._release_read_tx(db)
    assert db.close_calls == 1
    assert db.rollback_calls == 0
