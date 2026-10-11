from pathlib import Path


def test_campaign_prepare_gallery_semantic_rejection_is_retryable_not_dead_letter():
    root = Path(__file__).resolve().parents[1]
    src = (root / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _run_campaign_prepare")
    end = src.index("def _run_campaign_deliver")
    prepare = src[start:end]

    assert "ff_workflow_photo_stack" in prepare
    assert "gallery_semantic_validation_failed" in prepare
    assert "HTTPException as _CampaignMediaHTTPException" in prepare
    assert "_overflow_failed.append(int(_iid))" in prepare
    assert '"stage":"media_retry"' in prepare
    assert '"retryable":True' in prepare
    assert "raise" in prepare
