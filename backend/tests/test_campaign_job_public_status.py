from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import job_execution_clock
from app.services import jobs


def _job(stage, *, status="queued", code=None, ready=None, blocked=None, next_stage=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=1,
        kind="campaign_prepare",
        status=status,
        account_id="acc",
        payload_json={"campaign_id": 77, "stage": stage},
        result_json={
            "stage": stage,
            "code": code,
            "ready_count": ready,
            "blocked_count": blocked,
            "next_stage": next_stage,
        },
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        scheduled_at=None,
        attempts=1,
        progress_done=0,
        progress_total=1,
    )


def test_funding_wait_is_owner_visible(monkeypatch):
    monkeypatch.setattr(job_execution_clock, "summary", lambda job: {"timing_contract": 0})
    out = jobs.job_public(_job(
        "awaiting_external_funding",
        code="AVITO_ADVANCE_FUNDING_REQUIRED",
    ))
    assert out["state_text"] == "Ждёт пополнения аванса Avito"
    assert out["funding_required"] is True
    assert out["stage"] == "awaiting_external_funding"
    assert out["code"] == "AVITO_ADVANCE_FUNDING_REQUIRED"


def test_providerfree_partial_exposes_ready_and_blocked(monkeypatch):
    monkeypatch.setattr(job_execution_clock, "summary", lambda job: {"timing_contract": 0})
    out = jobs.job_public(_job(
        "providerfree_repair_exhausted",
        status="partial",
        code="PROVIDERFREE_REPAIR_EXHAUSTED",
        ready=44,
        blocked=6,
        next_stage="delivery",
    ))
    assert "Готово частично" in out["state_text"]
    assert out["ready_count"] == 44
    assert out["blocked_count"] == 6
    assert out["next_stage"] == "delivery"


def test_banner_partial_has_specific_owner_state(monkeypatch):
    monkeypatch.setattr(job_execution_clock, "summary", lambda job: {"timing_contract": 0})
    out = jobs.job_public(_job("banners_partial", status="partial"))
    assert out["state_text"] == "Баннеры готовы частично"
    assert out["funding_required"] is False
