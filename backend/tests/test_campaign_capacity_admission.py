import os
from unittest.mock import patch

from app.services import reliability


class _ScalarQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return self.value


class _FakeDB:
    def __init__(self, active_resource_jobs=0):
        self.active_resource_jobs = active_resource_jobs

    def query(self, *args, **kwargs):
        return _ScalarQuery(self.active_resource_jobs)


def _cap(db_connections=57, db_max=100):
    return {
        "state": "ok",
        "db_connections": db_connections,
        "db_max_connections": db_max,
        "db_connections_pct": round(db_connections * 100.0 / db_max, 1),
        "memory_used_pct": 30.0,
        "signals": [],
    }


def test_db_admission_caps_eight_workers_to_seven_at_current_headroom(monkeypatch):
    monkeypatch.setenv("BORIS_JOB_WORKERS", "8")
    monkeypatch.setenv("BORIS_DB_SAFE_PERCENT", "75")
    monkeypatch.setenv("BORIS_DB_CONTROL_RESERVE", "4")
    monkeypatch.setenv("BORIS_DB_CONNECTIONS_PER_RESOURCE_JOB", "2")
    with patch.object(reliability, "capacity_health", return_value=_cap(57, 100)):
        snap = reliability.db_worker_admission_snapshot(_FakeDB(0))
    assert snap["db_safe_limit"] == 75
    assert snap["db_safe_parallel_resource_jobs"] == 7
    assert snap["configured_workers"] == 8


def test_eighth_campaign_job_is_deferred_by_db_safe_reserve(monkeypatch):
    monkeypatch.setenv("BORIS_JOB_WORKERS", "8")
    monkeypatch.setenv("BORIS_DB_SAFE_PERCENT", "75")
    monkeypatch.setenv("BORIS_DB_CONTROL_RESERVE", "4")
    monkeypatch.setenv("BORIS_DB_CONNECTIONS_PER_RESOURCE_JOB", "2")
    # Eight jobs are marked running; the current candidate is included but has
    # not acquired its dedicated lock connection yet. Seven prior lock
    # connections are already reflected in db_connections=64, so baseline=57.
    with patch.object(reliability, "capacity_health", return_value=_cap(64, 100)):
        snap = reliability.db_worker_admission_snapshot(
            _FakeDB(8), "campaign_prepare"
        )
    assert snap["baseline_connections_est"] == 57
    assert snap["db_safe_parallel_resource_jobs"] == 7
    assert snap["allow"] is False


def test_seventh_campaign_job_is_allowed(monkeypatch):
    monkeypatch.setenv("BORIS_JOB_WORKERS", "8")
    monkeypatch.setenv("BORIS_DB_SAFE_PERCENT", "75")
    monkeypatch.setenv("BORIS_DB_CONTROL_RESERVE", "4")
    monkeypatch.setenv("BORIS_DB_CONNECTIONS_PER_RESOURCE_JOB", "2")
    with patch.object(reliability, "capacity_health", return_value=_cap(63, 100)):
        snap = reliability.db_worker_admission_snapshot(
            _FakeDB(7), "campaign_prepare"
        )
    assert snap["baseline_connections_est"] == 57
    assert snap["db_safe_parallel_resource_jobs"] == 7
    assert snap["allow"] is True


def test_critical_job_is_never_blocked_by_resource_db_gate():
    db = _FakeDB(99)
    assert reliability.admission_control(db, "payment")["allow"] is True


def test_capacity_plan_reports_db_aware_parallelism(monkeypatch):
    monkeypatch.setenv("BORIS_JOB_WORKERS", "8")
    monkeypatch.setenv("BORIS_DB_SAFE_PERCENT", "75")
    monkeypatch.setenv("BORIS_DB_CONTROL_RESERVE", "4")
    monkeypatch.setenv("BORIS_DB_CONNECTIONS_PER_RESOURCE_JOB", "2")
    with patch.object(reliability, "capacity_health", return_value=_cap(57, 100)):
        plan = reliability.capacity_plan(_FakeDB(0))
    scenario = next(x for x in plan["scenarios"] if x["tenants"] == 10)
    assert plan["db_safe_parallel_resource_jobs"] == 7
    assert scenario["parallel_resource_jobs"] == 7
    assert scenario["waves_min"] == 2
    assert scenario["peak_db_connections_est"] <= scenario["db_safe_limit"]


def test_fair_scheduler_gives_other_tenants_a_turn_before_second_job_from_noisy_tenant():
    from datetime import datetime, timezone
    from sqlalchemy import case, or_
    from app.db.session import SessionLocal
    from app.models.background_job import BackgroundJob
    from app.services.jobs import HANDLERS
    from app.services.reliability import CRITICAL_KINDS, HEAVY_KINDS, INTERACTIVE_KINDS
    import uuid

    prefix = "qa-fair-" + uuid.uuid4().hex[:10]
    accounts = [prefix + "-A"] + [prefix + f"-{letter}" for letter in "BCDEFGHIJ"]
    db = SessionLocal()
    try:
        # One noisy tenant has 100 jobs; every other tenant has one.
        for _ in range(100):
            db.add(BackgroundJob(kind="thumbnail_sweep", status="queued", account_id=accounts[0], payload_json={"qa": True}))
        for account in accounts[1:]:
            db.add(BackgroundJob(kind="thumbnail_sweep", status="queued", account_id=account, payload_json={"qa": True}))
        db.flush()

        picked = []
        for _ in range(10):
            now = datetime.now(timezone.utc)
            priority_order = case(
                (BackgroundJob.kind.in_(tuple(CRITICAL_KINDS)), 0),
                (BackgroundJob.kind.in_(tuple(INTERACTIVE_KINDS)), 10),
                (BackgroundJob.kind.in_(tuple(HEAVY_KINDS)), 30),
                else_=20,
            )
            active_accounts = db.query(BackgroundJob.account_id).filter(
                BackgroundJob.status.in_(("running", "validating")),
                BackgroundJob.account_id.isnot(None),
                BackgroundJob.kind.notin_(tuple(CRITICAL_KINDS)),
            )
            fairness_order = case(
                (BackgroundJob.account_id.is_(None), 1),
                (BackgroundJob.account_id.in_(active_accounts), 1),
                else_=0,
            )
            job = db.query(BackgroundJob).filter(
                BackgroundJob.status == "queued",
                BackgroundJob.kind.in_(tuple(HANDLERS)),
                BackgroundJob.account_id.like(prefix + "%"),
                or_(BackgroundJob.scheduled_at.is_(None), BackgroundJob.scheduled_at <= now),
            ).order_by(
                priority_order.asc(), fairness_order.asc(), BackgroundJob.id.asc()
            ).with_for_update(skip_locked=True).first()
            assert job is not None
            picked.append(str(job.account_id))
            job.status = "running"
            job.started_at = now
            db.flush()

        assert picked[0] == accounts[0]
        assert set(picked) == set(accounts)
        assert len(picked) == 10
    finally:
        db.rollback()
        db.close()
