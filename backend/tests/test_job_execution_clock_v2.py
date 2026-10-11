from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import job_execution_clock as T


def test_timing_v2_splits_active_provider_and_system_wait():
    start=datetime(2026,10,7,tzinfo=timezone.utc)
    state=T.transition({},"waiting",start)
    state=T.transition(state,"active",start+timedelta(seconds=10))
    state=T.transition(state,"provider",start+timedelta(seconds=13))
    state=T.transition(state,"active",start+timedelta(seconds=33))
    state=T.transition(state,"waiting",start+timedelta(seconds=35))
    state=T.transition(state,"active",start+timedelta(seconds=45))
    state=T.transition(state,"waiting",start+timedelta(seconds=49))
    job=SimpleNamespace(created_at=start,finished_at=start+timedelta(seconds=49),status="completed")
    result=T.summary(job,state,at=start+timedelta(seconds=100))
    assert result == {
        "duration_total_seconds":49.0,
        "duration_active_seconds":9.0,
        "duration_waiting_seconds":20.0,
        "duration_provider_seconds":20.0,
        "timing_contract":2,
    }
    assert result["duration_total_seconds"] == (
        result["duration_active_seconds"]
        + result["duration_waiting_seconds"]
        + result["duration_provider_seconds"]
    )


def test_timing_v2_dead_heartbeat_becomes_system_wait_not_active():
    start=datetime(2026,10,7,tzinfo=timezone.utc)
    state=T.transition({},"active",start+timedelta(seconds=10))
    job=SimpleNamespace(created_at=start,finished_at=None,status="running")
    dead=T.summary(job,state,at=start+timedelta(seconds=100))
    assert dead["duration_active_seconds"]==0.0
    assert dead["duration_provider_seconds"]==0.0
    assert dead["duration_waiting_seconds"]==100.0
    assert dead["timing_contract"]==2
