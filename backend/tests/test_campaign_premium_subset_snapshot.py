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


def test_subset_finalize_marks_only_verified_subset_partial_ready():
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _campaign_premium_finalize_ready")
    end = src.index("\ndef _run_campaign_prepare", start)
    body = src[start:end]
    assert "PREMIUM_READY_SUBSET_IS_PARTIAL_V1" in body
    assert 'campaign.status = "partial"' in body
    assert 'ff["step"] = "ready_subset_for_review"' in body
    assert 'order["status"] = "PARTIAL_READY"' in body
    assert '"code": "PREMIUM_READY_SUBSET_PARTIAL"' in body
    assert '"ready_ids": [int(x) for x in item_ids]' in body
    assert '"full_order_complete": False' in body
    assert '"external_action": False' in body


def test_full_order_finalize_still_requires_full_requested_quantity():
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("def _campaign_premium_finalize_ready")
    end = src.index("\ndef _run_campaign_prepare", start)
    body = src[start:end]
    assert "requested != len(item_ids)" in body
    assert '"PREMIUM_READY_QUANTITY_UNPROVEN"' in body
    assert 'campaign.status = "ready_for_review"' in body
