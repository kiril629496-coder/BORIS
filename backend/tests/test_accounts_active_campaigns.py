from datetime import datetime, timedelta
from types import SimpleNamespace

from app.api.accounts import _latest_active_campaign_jobs


def _job(job_id, account_id, campaign_id, kind, status, updated_at):
    return SimpleNamespace(
        id=job_id,
        account_id=account_id,
        kind=kind,
        status=status,
        updated_at=updated_at,
        created_at=updated_at,
        payload_json={"campaign_id": campaign_id},
    )


def test_latest_completed_lane_suppresses_stale_active_predecessor():
    now = datetime.utcnow()
    rows = [
        _job(12, "client_a", 101, "campaign_prepare", "completed", now),
        _job(11, "client_a", 101, "campaign_prepare", "awaiting_confirmation", now - timedelta(seconds=5)),
        _job(10, "client_b", 102, "campaign_prepare", "running", now - timedelta(seconds=10)),
    ]
    selected = _latest_active_campaign_jobs(
        rows, ("queued", "running", "validating", "awaiting_confirmation")
    )
    assert [(row.id, campaign_id) for row, _, campaign_id in selected] == [(10, 102)]


def test_one_latest_active_job_per_campaign_across_kinds():
    now = datetime.utcnow()
    rows = [
        _job(25, "client_a", 201, "campaign_editor", "running", now),
        _job(24, "client_a", 201, "campaign_prepare", "running", now - timedelta(seconds=1)),
        _job(23, "client_b", 202, "campaign_prepare", "queued", now - timedelta(seconds=2)),
    ]
    selected = _latest_active_campaign_jobs(
        rows, ("queued", "running", "validating", "awaiting_confirmation")
    )
    assert [(row.id, campaign_id) for row, _, campaign_id in selected] == [(25, 201), (23, 202)]
