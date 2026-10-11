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
