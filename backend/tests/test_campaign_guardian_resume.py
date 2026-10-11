from types import SimpleNamespace
from unittest.mock import patch

from app.api import campaigns as C


class _DB:
    def __init__(self):
        self.commits = 0
        self.refreshes = 0

    def commit(self):
        self.commits += 1

    def refresh(self, _row):
        self.refreshes += 1


def test_explicit_guardian_resume_reuses_row_and_resets_only_recovery_budget():
    row = SimpleNamespace(
        id=2128,
        status="failed",
        result_json={"stage": "banners", "message": "continue"},
        error_text="Превышен лимит восстановлений",
        started_at="old-start",
        finished_at="old-finish",
        progress_done=4,
        payload_json={
            "campaign_id": 508,
            "account_id": "planetazayavki_65985",
            "stage": "banners",
            "_recovery_count": 10,
            "_last_recovered_at": "2026-10-04T16:00:00+00:00",
            "_last_recovery_reason": "worker_startup_orphan",
            "business_checkpoint": "keep-me",
        },
        attempts=85,
    )
    db = _DB()
    campaign = SimpleNamespace(id=508)
    user = SimpleNamespace(id=2)

    with patch.object(C, "_ff_state_load", return_value=({}, {})), \
         patch.object(C, "_ff_publication_locked", return_value=False), \
         patch("app.services.jobs.enqueue", return_value=(row, False)) as enqueue:
        out = C._campaign_guardian_enqueue(
            db, campaign, user, "planetazayavki_65985", "campaign_prepare"
        )

    assert out is row
    assert row.id == 2128
    assert row.status == "queued"
    assert row.result_json is None
    assert row.error_text is None
    assert row.started_at is None
    assert row.finished_at is None
    assert row.progress_done == 0
    assert row.attempts == 85
    assert row.payload_json["campaign_id"] == 508
    assert row.payload_json["account_id"] == "planetazayavki_65985"
    assert row.payload_json["stage"] == "queued"
    assert row.payload_json["business_checkpoint"] == "keep-me"
    assert "_recovery_count" not in row.payload_json
    assert "_last_recovered_at" not in row.payload_json
    assert "_last_recovery_reason" not in row.payload_json
    assert db.commits == 1
    assert db.refreshes == 1
    enqueue.assert_called_once()

import json


def test_fact_safe_title_repair_preserves_banner_row_and_reaches_unique_titles(monkeypatch):
    def row(item_id, city, title, banner=""):
        payload={
            "title":title,
            "description":"Уникальное описание "+str(item_id),
            "direction":"Кухни на заказ",
            "city":city,
            "copy_quality_contract":"sales_v4",
            "banner":banner,
            "media_semantic_evidence":[{"match":True,"confidence":0.95}],
        }
        return SimpleNamespace(
            id=item_id, account_id="acc", segment="Кухни на заказ",
            offer="Кухни на заказ", geo=city, payload_json=json.dumps(payload,ensure_ascii=False),
            content_hash="old", analysis_hash="old-analysis", status="ready",
        )

    first=row(10,"Уфа","Кухни на заказ")
    banner=row(20,"Ижевск","Кухни на заказ","/images/acc/banner.png")
    third=row(30,"Стерлитамак","Кухни на заказ")
    rows=[first,banner,third]
    db=_DB()
    campaign=SimpleNamespace(id=508)

    monkeypatch.setattr(C,"_ff_items",lambda _db,_cid,_allowed:rows)
    monkeypatch.setattr(C,"_ff_state_load",lambda _campaign:({},{"brief":""}))
    monkeypatch.setattr(C,"_ff_title_variants",lambda _topic,_brief:["Кухни на заказ","Кухни по размерам","Мебель для кухни"])
    monkeypatch.setattr(C,"_ff_push_edit_history",lambda *a,**k:None)

    out=C._ff_repair_duplicate_titles_fact_safe(db,campaign,["acc"])
    payloads={r.id:json.loads(r.payload_json) for r in rows}

    assert out["status"]=="ok"
    assert out["total"]==3
    assert out["unique"]==3
    assert len(out["changed"])==2
    assert payloads[20]["title"]=="Кухни на заказ"
    assert payloads[20]["banner"]=="/images/acc/banner.png"
    assert payloads[10]["title"]!="Кухни на заказ"
    assert payloads[30]["title"]!="Кухни на заказ"
    assert payloads[10]["media_semantic_evidence"]==[]
    assert payloads[30]["media_semantic_evidence"]==[]
    assert first.analysis_hash is None and third.analysis_hash is None
    assert first.status=="draft" and third.status=="draft"
    assert db.commits==1



def test_guardian_public_distinguishes_system_wait_from_owner_action(monkeypatch):
    from app.services import jobs as J

    monkeypatch.setattr(J, "job_public", lambda job: {"status": job.status})

    system_wait = SimpleNamespace(
        status="awaiting_confirmation",
        result_json={
            "stage": "media_source_wait",
            "retryable": True,
            "message": "BORIS ждёт резервный медиапул.",
        },
        payload_json={"stage": "media"},
    )
    public_wait = C._campaign_guardian_job_public(system_wait)
    assert public_wait["requires_owner_action"] is False
    assert public_wait["waiting_kind"] == "system_wait"
    assert public_wait["retryable"] is True
    assert public_wait["state_text"] == "BORIS продолжит автоматически"

    owner_input = SimpleNamespace(
        status="awaiting_confirmation",
        result_json={
            "stage": "needs_input",
            "retryable": False,
            "message": "Нужно уточнить обязательные данные Avito.",
            "questions": [{"field": "Color"}],
        },
        payload_json={"stage": "needs_input"},
    )
    public_owner = C._campaign_guardian_job_public(owner_input)
    assert public_owner["requires_owner_action"] is True
    assert public_owner["waiting_kind"] == "owner_action"

    billing = SimpleNamespace(
        status="awaiting_confirmation",
        result_json={
            "stage": "billing_limit",
            "retryable": False,
            "message": "Достигнут тарифный лимит.",
        },
        payload_json={"stage": "billing_limit"},
    )
    public_billing = C._campaign_guardian_job_public(billing)
    assert public_billing["requires_owner_action"] is True
    assert public_billing["waiting_kind"] == "owner_action"



def test_background_dispatcher_auto_resumes_only_due_retryable_campaign_system_waits():
    from pathlib import Path

    src = Path("/root/BORIS/backend/app/main.py").read_text(encoding="utf-8")
    helper = src[src.index("def _resume_due_campaign_system_waits"):src.index("def _background_jobs_loop")]
    loop = src[src.index("def _background_jobs_loop"):src.index("_cors_origins")]

    assert "CAMPAIGN_SYSTEM_WAIT_AUTO_RESUME_V1" in helper
    assert 'BackgroundJob.status == "awaiting_confirmation"' in helper
    assert "BackgroundJob.scheduled_at.isnot(None)" in helper
    assert "BackgroundJob.scheduled_at <= now" in helper
    assert 'bool(result.get("retryable"))' in helper
    assert '{"media_source_wait","media_retry","billing_pending"}' in helper
    assert '"needs_input"' not in helper
    assert '"billing_limit"' not in helper
    assert '"needs_avito_connection"' not in helper
    assert 'job.status = "queued"' in helper
    assert 'payload["_system_wait_resumed_stage"] = stage' in helper
    assert "if slot == 0" in loop
    assert "_resume_due_campaign_system_waits()" in loop



def test_ready_for_review_prepare_is_terminal_reconciled_without_requeue():
    from pathlib import Path
    src = Path("/root/BORIS/backend/app/api/campaigns.py").read_text(encoding="utf-8")
    fn = src[src.index("def _campaign_guardian_enqueue"):src.index('@router.post("/{campaign_id}/guardian/prepare")')]
    assert "READY_PREPARE_TERMINAL_RECONCILE_V2" in fn
    assert 'kind=="campaign_prepare"' in fn
    assert 'str(getattr(campaign,"status","") or "").lower()=="ready_for_review"' in fn
    assert 'row.status="completed"' in fn
    assert '"stage":"ready_for_review"' in fn
    assert '"validation_state":"READY"' in fn
    assert "row.progress_done=max(int(row.progress_done or 0),int(row.progress_total or 8))" in fn
    assert "db.commit(); db.refresh(row); return row" in fn
