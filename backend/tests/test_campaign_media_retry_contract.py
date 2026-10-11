from pathlib import Path


def test_campaign_prepare_gallery_semantic_rejection_is_retryable_not_dead_letter():
    root = Path(__file__).resolve().parents[1]
    jobs_src = (root / "app/services/jobs.py").read_text(encoding="utf-8")
    campaigns_src = (root / "app/api/campaigns.py").read_text(encoding="utf-8")

    prepare = jobs_src[jobs_src.index("def _run_campaign_prepare"):jobs_src.index("def _run_campaign_deliver")]
    # Canonical photo-stack owns semantic rejection; prepare owns retry/recovery.
    assert "def ff_workflow_photo_stack" in campaigns_src
    assert "gallery_semantic_mismatch" in campaigns_src
    assert "gallery_semantic_validation_failed" in campaigns_src
    assert '"stage":"media_retry"' in prepare
    assert '"retryable":True' in prepare
    assert "BORIS автоматически заменяет фотографии" in prepare


def test_cost_anomaly_recovery_is_cache_only_not_owner_confirmation():
    root = Path(__file__).resolve().parents[1]
    src = (root / "app/services/jobs.py").read_text(encoding="utf-8")
    prepare = src[src.index("def _run_campaign_prepare"):src.index("def _run_campaign_deliver")]

    assert "_cost_anomaly_free_only" in prepare
    assert '"allow_paid_qa":not bool(payload.get("_cost_anomaly_free_only"))' in prepare
    assert "DUPLICATE_QA_COST_ANOMALY" in src
