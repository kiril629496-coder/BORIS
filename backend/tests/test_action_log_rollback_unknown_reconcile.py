from datetime import datetime, timedelta, timezone

from app.api.action_log_api import _rollback_unknown_readonly_reconcile


def _receipt(*, old=4000, age_seconds=600, now=None):
    now = now or datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    return {
        "status": "delivery_unknown",
        "old_bid_penny": old,
        "updated_at": (now - timedelta(seconds=age_seconds)).isoformat(),
    }


def test_old_provider_state_after_grace_allows_reconcile_not_applied():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    out = _rollback_unknown_readonly_reconcile(_receipt(now=now), 4000, now=now)
    assert out["decision"] == "reconciled_not_applied"
    assert out["age_seconds"] == 600


def test_same_old_provider_state_inside_grace_stays_blocked():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    out = _rollback_unknown_readonly_reconcile(
        _receipt(age_seconds=299, now=now), 4000, now=now
    )
    assert out["decision"] == "blocked"


def test_third_provider_value_stays_blocked_even_after_grace():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    out = _rollback_unknown_readonly_reconcile(_receipt(now=now), 4500, now=now)
    assert out["decision"] == "blocked"


def test_missing_timestamp_stays_blocked():
    out = _rollback_unknown_readonly_reconcile(
        {"status": "delivery_unknown", "old_bid_penny": 4000},
        4000,
        now=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
    )
    assert out["decision"] == "blocked"
    assert out["age_seconds"] is None


def test_missing_old_bid_stays_blocked():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    out = _rollback_unknown_readonly_reconcile(
        {"status": "delivery_unknown", "updated_at": (now - timedelta(minutes=10)).isoformat()},
        4000,
        now=now,
    )
    assert out["decision"] == "blocked"
