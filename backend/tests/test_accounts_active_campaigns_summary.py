from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api.accounts import _latest_active_campaign_jobs


ACTIVE = ("queued", "running", "validating", "awaiting_confirmation")


def row(job_id, account, campaign_id, kind, status, offset_seconds):
    at = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    return SimpleNamespace(
        id=job_id,
        account_id=account,
        kind=kind,
        status=status,
        payload_json={"campaign_id": campaign_id, "stage": "banners"},
        updated_at=at,
        created_at=at,
    )


def test_newer_terminal_lane_suppresses_stale_active_predecessor():
    rows = [
        row(102, "client-a", 10, "campaign_prepare", "completed", 20),
        row(101, "client-a", 10, "campaign_prepare", "awaiting_confirmation", 10),
        row(90, "client-b", 11, "campaign_prepare", "running", 5),
    ]
    selected = _latest_active_campaign_jobs(rows, ACTIVE)
    assert [(r.id, cid) for r, _payload, cid in selected] == [(90, 11)]


def test_one_latest_active_job_per_campaign_across_kinds():
    rows = [
        row(210, "client-a", 20, "campaign_editor", "running", 30),
        row(209, "client-a", 20, "campaign_prepare", "running", 20),
        row(208, "client-b", 21, "campaign_prepare", "queued", 25),
        row(207, "client-c", 0, "campaign_prepare", "running", 40),
    ]
    selected = _latest_active_campaign_jobs(rows, ACTIVE)
    assert [(r.id, cid) for r, _payload, cid in selected] == [(210, 20), (208, 21)]
