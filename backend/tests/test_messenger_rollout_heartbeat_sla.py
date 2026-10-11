"""Periodic worker cadence must not block rollout; expired heartbeats still do."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from app.services.reliability import heartbeat_health

@pytest.mark.parametrize("module,worker,age,stale", [
    ("runtime", "campaign_inventory_scheduler", 900, False),
    ("runtime", "campaign_inventory_scheduler", 1200, False),
    ("runtime", "campaign_inventory_scheduler", 1201, True),
    ("control_plane", "guardian", 549, False),
    ("control_plane", "guardian", 600, False),
    ("control_plane", "guardian", 601, True),
    ("runtime", "ordinary_worker", 301, True),
    ("runtime", "ordinary_worker", 299, False),
])
def test_periodic_heartbeat_keeps_real_stale_guard(module, worker, age, stale):
    row = SimpleNamespace(module=module, worker_id=worker, account_id="*",
        state="ok", details_json={},
        last_seen_at=datetime.now(timezone.utc)-timedelta(seconds=age))
    query = SimpleNamespace(all=lambda: [row])
    query.order_by = lambda *args: query
    db = SimpleNamespace(query=lambda *args: query)
    result = heartbeat_health(db)
    assert result["stale"] == int(stale)
    assert result["state"] == ("critical" if stale else "ok")
