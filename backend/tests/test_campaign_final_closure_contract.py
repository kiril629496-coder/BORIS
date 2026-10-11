from types import SimpleNamespace

from app.services import jobs
from app.services import media_semantic_validator as sem


def test_planeta_legacy_banner_density_is_one_in_four():
    ff = {"order_contract": {"banner_required": True, "account_id": "planetazayavki_65985"}}
    ids = list(range(1, 121))
    selected = jobs._campaign_banner_target_ids(ff, ids, "planetazayavki_65985")
    assert len(selected) == 30
    assert selected[:4] == [1, 5, 9, 13]


def test_explicit_banner_every_one_targets_every_card():
    ff = {"order_contract": {"banner_required": True, "banner_every_n": 1}}
    assert jobs._campaign_banner_target_ids(ff, [1, 2, 3], "albertardashov_79016") == [1, 2, 3]


def test_banner_required_false_targets_no_cards():
    ff = {"order_contract": {"banner_required": False, "banner_every_n": 1}}
    assert jobs._campaign_banner_target_ids(ff, [1, 2, 3], "x") == []


def test_semantic_cache_only_reuses_exact_positive_without_provider(monkeypatch):
    payload = {
        "title": "Ипотечная консультация",
        "description": "Помощь с ипотекой",
        "category": "Услуги",
    }
    fp = sem.semantic_content_fingerprint(payload)
    payload["media_semantic_evidence"] = [{
        "media_id": 11,
        "position": 0,
        "content_fingerprint": fp,
        "checker": "openai_vision_text_image",
        "match": True,
        "confidence": 0.97,
        "subject": "mortgage consultation",
        "reason": "matches",
        "policy_version": sem.SEMANTIC_POLICY_VERSION,
    }]
    link = SimpleNamespace(position=0)
    asset = SimpleNamespace(id=11, tags=[], account_id="mbroker_27859")

    def forbidden(*args, **kwargs):
        raise AssertionError("provider must not be called in cache-only mode")

    monkeypatch.setattr(sem, "_vision_chat_json", forbidden)
    out = sem.classify_item_media_vision(
        payload, [(link, asset)], account_id="mbroker_27859", allow_provider=False
    )
    assert out[0]["match"] is True
    assert out[0]["cached"] is True


def test_semantic_cache_only_fails_closed_when_proof_missing(monkeypatch):
    payload = {
        "title": "Окна в Казани",
        "description": "Изготовление окон",
        "category": "Ремонт",
    }
    link = SimpleNamespace(position=0)
    asset = SimpleNamespace(id=12, tags=[], account_id="albertardashov_79016")

    def forbidden(*args, **kwargs):
        raise AssertionError("provider must not be called in cache-only mode")

    monkeypatch.setattr(sem, "_vision_chat_json", forbidden)
    out = sem.classify_item_media_vision(
        payload, [(link, asset)], account_id="albertardashov_79016", allow_provider=False
    )
    assert out[0]["match"] is False
    assert out[0]["checker"] == "cost_anomaly_cache_only"


def test_cost_anomaly_auto_media_never_calls_external_stock():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    src = (root / "app/api/campaigns.py").read_text(encoding="utf-8")
    start = src.index("def ff_workflow_auto_media")
    end = src.index("def _ff_is_existing_banner_asset", start)
    auto_media = src[start:end]
    assert "if len(items) < desired_count and bool(body.allow_paid_qa):" in auto_media
    assert "_campaign_provider_call(collect_stock" in auto_media


def test_non_banner_order_counts_semantically_proven_hero_as_media():
    item = {
        "banner": "/images/banner.webp",
        "gallery": [{
            "url": "/images/banner.webp",
            "source_provider": "openai_full_ai_premium",
            "role": "hero",
        }],
        "media_semantic_evidence": [{
            "match": True,
            "confidence": 0.95,
        }],
    }
    assert jobs._campaign_prepared_photo_count(item) == 0
    assert jobs._campaign_prepared_media_count(item, banner_required=False) == 1
    assert jobs._campaign_prepared_media_count(item, banner_required=True) == 0


def test_non_banner_order_does_not_count_unproven_hero():
    item = {
        "banner": "/images/banner.webp",
        "gallery": [{
            "url": "/images/banner.webp",
            "source_provider": "openai_full_ai_premium",
            "role": "hero",
        }],
        "media_semantic_evidence": [],
    }
    assert jobs._campaign_prepared_media_count(item, banner_required=False) == 0


def test_existing_positive_glazing_proof_can_seed_family_cache():
    payload = {
        "title": "Панорамные окна в частный дом",
        "direction": "Панорамные окна в частный дом",
        "category": "Окна",
    }
    evidence = {
        "match": True,
        "checker": "openai_vision_text_image_cache",
        "confidence": 0.95,
        "subject": "Private house exterior with panoramic windows",
        "media_id": 15568,
    }
    candidate = sem.reusable_family_cache_candidate(payload, evidence)
    assert candidate is not None
    assert candidate["family"] == "glazing_panoramic"
    assert candidate["media_id"] == 15568


def test_banner_qa_proof_cannot_seed_gallery_family_cache():
    payload = {
        "title": "Панорамные окна в частный дом",
        "direction": "Панорамные окна в частный дом",
    }
    evidence = {
        "match": True,
        "checker": "campaign_banner_qa",
        "confidence": 1.0,
        "subject": "QA-approved campaign banner",
        "media_id": 17648,
    }
    assert sem.reusable_family_cache_candidate(payload, evidence) is None
