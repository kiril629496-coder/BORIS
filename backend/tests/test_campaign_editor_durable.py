"""Candidate integration tests. Isolated PostgreSQL, no provider/worker side effects."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy.orm import Session
from tests.test_campaign_run4_closure import qa
from app.api import campaigns as C
from app.services import jobs
from app.services import campaign_editor_jobs as E
from app.services import job_execution_clock as T
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.background_job import BackgroundJob
from app.models.storage import Storage

@pytest.fixture
def runtime(qa, monkeypatch):
    engine, client = qa
    monkeypatch.setattr(T, "load", lambda job: None)
    def context(db, job, payload):
        return C, db.get(Campaign, 1), SimpleNamespace(id=2, email="Анна", role="employee"), ["qa-a"], "qa-a"
    monkeypatch.setattr(jobs, "_campaign_job_context", context)
    return engine, client

def enqueue(runtime, ids=(101,)):
    engine, client = runtime
    with Session(engine) as db:
        versions={str(i):db.get(CampaignItem,i).updated_at.isoformat() for i in ids}
    body={"item_ids":list(ids),"instructions":"QA deterministic operation","expected_updated_at":versions}
    r=client.post("/api/campaign/1/workflow/texts",json=body)
    assert r.status_code==200,r.text
    assert r.json()["persistent"] and r.json()["job"]["kind"]=="campaign_editor"
    return r.json()["job_id"],body

def local_text(db, campaign_id, body, user):
    return C.ff_workflow_mutate(campaign_id,C.FFMutationBody(item_ids=body["item_ids"],patch={"title":"Durable QA"}),db,user)

def install_local(monkeypatch):
    def fake(campaign_id, body:dict, db, user):
        return local_text(db,campaign_id,body,user)
    monkeypatch.setattr(C,"ff_workflow_texts",fake)

def test_queue_refresh_and_scope(runtime):
    engine,client=runtime
    jid,body=enqueue(runtime)
    again=client.post("/api/campaign/1/workflow/texts",json=body)
    assert again.json()["job_id"]==jid
    status=client.get(f"/api/campaign/1/editor-jobs/{jid}")
    assert status.status_code==200 and status.json()["job"]["status"]=="queued"
    assert client.get(f"/api/campaign/2/editor-jobs/{jid}").status_code==404
    assert client.post("/api/campaign/1/workflow/texts",headers={"x-qa-user":"3"},json=body).status_code==403
    with Session(engine) as db:
        assert db.query(BackgroundJob).count()==1
        assert json.loads(db.get(CampaignItem,101).payload_json)["title"]=="Стол"

def test_worker_uses_pinned_revision_and_checkpoints(runtime,monkeypatch):
    engine,client=runtime
    jid,body=enqueue(runtime,(101,102))
    install_local(monkeypatch)
    with Session(engine) as db:
        job=db.get(BackgroundJob,jid)
        result,state=E.run(db,job,dict(job.payload_json))
        assert state=="retry" and result["done"]==1
        assert job.payload_json["remaining"]==[102]
    with Session(engine) as db:
        job=db.get(BackgroundJob,jid)
        result,state=E.run(db,job,dict(job.payload_json))
        assert state=="completed" and result["done"]==2
        for iid in [101,102]:
            assert json.loads(db.get(CampaignItem,iid).payload_json)["title"]=="Durable QA"
        revs=[json.loads(x.value) for x in db.query(Storage).filter(Storage.key.like("campaign_card_revision:%")).all()]
        assert sum(v.get("job_id")==jid for v in revs)==2

def test_stale_worker_does_not_overwrite(runtime,monkeypatch):
    engine,client=runtime
    jid,body=enqueue(runtime)
    with Session(engine) as db:
        row=db.get(CampaignItem,101);p=json.loads(row.payload_json);p["title"]="Another editor";row.payload_json=json.dumps(p);db.commit()
    install_local(monkeypatch)
    with Session(engine) as db:
        job=db.get(BackgroundJob,jid);result,state=E.run(db,job,dict(job.payload_json))
        assert state=="awaiting_confirmation" and result["code"]=="CARD_VERSION_CONFLICT"
        assert json.loads(db.get(CampaignItem,101).payload_json)["title"]=="Another editor"

def test_recovery_after_content_commit_does_not_repeat_mutation(runtime,monkeypatch):
    engine,client=runtime
    jid,body=enqueue(runtime)
    install_local(monkeypatch)
    with Session(engine) as db:
        job=db.get(BackgroundJob,jid);original=dict(job.payload_json)
        result,state=E.run(db,job,original);assert state=="completed"
        first=db.get(CampaignItem,101).updated_at
        # Simulates loss of job checkpoint after the content transaction committed.
        job.payload_json=original;db.commit()
    with Session(engine) as db:
        job=db.get(BackgroundJob,jid);result,state=E.run(db,job,dict(job.payload_json))
        assert state=="completed" and result["recovered_committed_revision"]
        assert db.get(CampaignItem,101).updated_at==first

def test_clock_excludes_queue_provider_and_retry():
    start=datetime(2026,10,4,tzinfo=timezone.utc)
    state=T.transition({},"waiting",start)
    state=T.transition(state,"active",start+timedelta(seconds=10))
    state=T.transition(state,"provider",start+timedelta(seconds=13))
    state=T.transition(state,"active",start+timedelta(seconds=33))
    state=T.transition(state,"waiting",start+timedelta(seconds=35))
    state=T.transition(state,"active",start+timedelta(seconds=45))
    state=T.transition(state,"waiting",start+timedelta(seconds=49))
    j=SimpleNamespace(created_at=start,finished_at=start+timedelta(seconds=49),status="completed")
    result=T.summary(j,state,at=start+timedelta(seconds=100))
    assert result["duration_total_seconds"]==49
    assert result["duration_active_seconds"]==9
    assert result["duration_waiting_seconds"]==20
    assert result["duration_provider_seconds"]==20
    assert result["timing_contract"]==2
    assert result["duration_total_seconds"] == (
        result["duration_active_seconds"]
        + result["duration_waiting_seconds"]
        + result["duration_provider_seconds"]
    )

def test_clock_running_and_dead_heartbeat_are_bounded():
    start=datetime(2026,10,4,tzinfo=timezone.utc)
    state=T.transition({},"active",start+timedelta(seconds=10))
    j=SimpleNamespace(created_at=start,finished_at=None,status="running")
    live=T.summary(j,state,at=start+timedelta(seconds=11))
    dead=T.summary(j,state,at=start+timedelta(seconds=100))
    assert live["duration_active_seconds"]==1
    assert dead["duration_active_seconds"]==0
    assert dead["duration_waiting_seconds"]==100
    for x in [live,dead]:
        assert x["duration_total_seconds"]>=x["duration_active_seconds"]>=0
        assert x["duration_total_seconds"]>=x["duration_waiting_seconds"]>=0

def test_old_job_does_not_get_invented_active_time():
    start=datetime(2026,10,4,tzinfo=timezone.utc)
    j=SimpleNamespace(created_at=start,finished_at=start+timedelta(seconds=30),status="completed")
    assert T.summary(j,{},at=start)["duration_active_seconds"] is None

@pytest.mark.parametrize("operation",["texts","auto_media","banners","photo_stack","validate"])
def test_qa_guard_precedes_any_provider(runtime,monkeypatch,operation):
    engine,client=runtime
    jid,_=enqueue(runtime)
    with Session(engine) as db:
        campaign=db.get(Campaign,1);campaign.name="QA guarded campaign";db.commit()
        job=db.get(BackgroundJob,jid);payload=dict(job.payload_json,operation=operation)
        def forbidden(*args,**kwargs):
            raise AssertionError("provider reached for QA")
        monkeypatch.setattr(C,E.OPERATIONS[operation],forbidden)
        result,state=E.run(db,job,payload)
        assert state=="awaiting_confirmation" and result["code"]=="QA_EXTERNAL_DISABLED"
        assert job.progress_done==0



def test_banner_qa_failure_auto_retries_with_new_idempotency(runtime, monkeypatch):
    engine, client = runtime
    jid, body = enqueue(runtime)
    seen = []

    monkeypatch.setattr(C, "_ff_paid_qa_forbidden", lambda campaign: False)
    monkeypatch.setattr(E, "_own_completed_revision", lambda *args, **kwargs: False)

    def fake_banner(campaign_id, body: C.FFBannerBody, db, user):
        seen.append({
            "force": bool(body.force),
            "idempotency_key": str(body.idempotency_key or ""),
            "confirmed_paid_items": int(body.confirmed_paid_items or 0),
            "item_ids": list(body.item_ids or []),
        })
        if len(seen) < 3:
            return {
                "status": "error",
                "changed": [],
                "generated": [],
                "errors": [{
                    "id": int(body.item_ids[0]),
                    "message": "Баннер не прошёл финальную проверку текста, автомобиля или мобильной читаемости",
                }],
            }
        return {
            "status": "ok",
            "changed": list(body.item_ids),
            "generated": list(body.item_ids),
            "errors": [],
        }

    monkeypatch.setattr(C, "ff_workflow_banners", fake_banner)

    with Session(engine) as db:
        job = db.get(BackgroundJob, jid)
        p = dict(job.payload_json)
        p["operation"] = "banners"
        p["body"] = {
            "item_ids": [101],
            "goal": "offer",
            "force": False,
            "idempotency_key": "owner-batch",
            "confirmed_paid_items": 1,
        }
        p["remaining"] = [101]
        p["item_ids"] = [101]
        job.payload_json = p
        db.commit()

    with Session(engine) as db:
        job = db.get(BackgroundJob, jid)
        result, state = E.run(db, job, dict(job.payload_json))
        assert state == "retry"
        assert result["stage"] == "banner_qa_retry"
        assert result["retry_number"] == 1
        assert result["retryable"] is True
        assert job.payload_json["_banner_qa_retries"]["101"] == 1
        # Production run_once persists handler-mutated payload after the handler
        # returns. Mirror that outer worker commit before opening a fresh Session.
        db.commit()

    with Session(engine) as db:
        job = db.get(BackgroundJob, jid)
        result, state = E.run(db, job, dict(job.payload_json))
        assert state == "retry"
        assert result["retry_number"] == 2
        assert job.payload_json["_banner_qa_retries"]["101"] == 2
        db.commit()

    with Session(engine) as db:
        job = db.get(BackgroundJob, jid)
        result, state = E.run(db, job, dict(job.payload_json))
        assert state == "completed"
        assert result["done"] == 1

    assert seen[0]["force"] is False
    assert seen[0]["idempotency_key"] == "owner-batch"
    assert seen[1]["force"] is True
    assert seen[1]["idempotency_key"].endswith(":101:1")
    assert seen[1]["confirmed_paid_items"] == 1
    assert seen[2]["force"] is True
    assert seen[2]["idempotency_key"].endswith(":101:2")
    assert seen[2]["confirmed_paid_items"] == 1


def test_banner_qa_retry_exhaustion_fails_closed(runtime, monkeypatch):
    engine, client = runtime
    jid, body = enqueue(runtime)

    monkeypatch.setattr(C, "_ff_paid_qa_forbidden", lambda campaign: False)
    monkeypatch.setattr(E, "_own_completed_revision", lambda *args, **kwargs: False)

    def always_fail(campaign_id, body: C.FFBannerBody, db, user):
        return {
            "status": "error",
            "changed": [],
            "generated": [],
            "errors": [{
                "id": int(body.item_ids[0]),
                "message": "Баннер не прошёл финальную проверку текста, автомобиля или мобильной читаемости",
            }],
        }

    monkeypatch.setattr(C, "ff_workflow_banners", always_fail)

    with Session(engine) as db:
        job = db.get(BackgroundJob, jid)
        p = dict(job.payload_json)
        p["operation"] = "banners"
        p["body"] = {"item_ids": [101], "goal": "offer", "confirmed_paid_items": 1}
        p["remaining"] = [101]
        p["item_ids"] = [101]
        job.payload_json = p
        db.commit()

    states = []
    with Session(engine) as db:
        job = db.get(BackgroundJob, jid)
        for _ in range(3):
            result, state = E.run(db, job, dict(job.payload_json))
            states.append((state, result))
        assert states[0][0] == "retry"
        assert states[1][0] == "retry"
        assert states[2][0] == "failed"
        assert states[2][1]["code"] == "BANNER_QA_RETRY_EXHAUSTED"
        assert states[2][1]["retryable"] is False
