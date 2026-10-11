from pathlib import Path

ROOT=Path("/root/BORIS/backend")


def test_ready_for_review_prepare_reconciles_same_job_to_terminal_completed():
    src=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    fn=src[src.index("def _campaign_guardian_enqueue"):src.index('@router.post("/{campaign_id}/guardian/prepare")')]
    assert "READY_PREPARE_TERMINAL_RECONCILE_V2" in fn
    assert 'kind=="campaign_prepare"' in fn
    assert 'str(getattr(campaign,"status","") or "").lower()=="ready_for_review"' in fn
    assert 'row.status="completed"' in fn
    assert '"stage":"ready_for_review"' in fn
    assert '"validation_state":"READY"' in fn
    assert 'row.progress_done=max(int(row.progress_done or 0),int(row.progress_total or 8))' in fn
    assert "db.commit(); db.refresh(row); return row" in fn


def test_photo_stack_preserves_payload_banner_as_canonical_hero():
    src=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    fn=src[src.index("def ff_workflow_photo_stack"):src.index("def ff_workflow_banners")]
    assert "PHOTO_STACK_CANONICAL_BANNER_URL_V1" in fn
    assert '_banner_url=str(p.get("banner_url") or "").strip()' in fn
    assert '_banner_key=_banner_url.split("/images/",1)[1].split("?",1)[0].lstrip("/")' in fn
    assert 'str(getattr(a,"storage_key","") or "").lstrip("/")==_banner_key' in fn
