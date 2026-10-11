from pathlib import Path


def test_premium_validation_snapshot_queries_only_requested_scope():
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _campaign_premium_validation_snapshot")
    end = src.index("\ndef ", start + 4)
    body = src[start:end]
    assert "CampaignItem.id.in_(ids)" in body
    assert 'if [int(r.id) for r in rows] != ids:' in body
    assert '"PREMIUM_READY_SCOPE_CHANGED"' in body


def test_subset_snapshot_keeps_account_and_superseded_guards():
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _campaign_premium_validation_snapshot")
    end = src.index("\ndef ", start + 4)
    body = src[start:end]
    assert "CampaignItem.account_id == account_id" in body
    assert 'CampaignItem.status != "superseded"' in body


def test_subset_continuation_reuses_existing_materialization_scope():
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _run_campaign_prepare")
    end = src.index("\ndef _run_campaign_deliver", start)
    body = src[start:end]
    assert "SUBSET_CONTINUATION_REUSES_EXISTING_MATERIALIZATION_V1" in body
    assert 'if _requested_scope and bool(payload.get("_subset_continuation")):' in body
    assert '"item_ids": list(_requested_scope)' in body
    assert '"subset_reuse": True' in body


def test_normal_prepare_keeps_full_materialize_path():
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _run_campaign_prepare")
    end = src.index("\ndef _run_campaign_deliver", start)
    body = src[start:end]
    assert "C.ff_workflow_materialize(campaign.id, C.FFMaterializeBody(account_id=account_id), db, user)" in body
