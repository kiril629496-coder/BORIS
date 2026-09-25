from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from app.services import avito_content_policy as policy
from app.api import campaigns


ROOT = Path(__file__).resolve().parents[1]


def test_paid_fallback_is_disabled_by_default_and_requires_both_limits():
    names = (
        "BORIS_CONTENT_PAID_FALLBACK",
        "BORIS_CONTENT_PAID_PER_ITEM_KOPEKS",
        "BORIS_CONTENT_PAID_DAILY_KOPEKS",
    )
    clean = {k: v for k, v in os.environ.items() if k not in names}
    with patch.dict(os.environ, clean, clear=True):
        p = policy.policy()
        assert p["mode"] == "local_first"
        assert p["paid_fallback_enabled"] is False
        assert p["paid_per_item_kopeks"] == 0
        assert p["paid_daily_kopeks"] == 0

    with patch.dict(os.environ, {
        "BORIS_CONTENT_PAID_FALLBACK": "1",
        "BORIS_CONTENT_PAID_PER_ITEM_KOPEKS": "100",
        "BORIS_CONTENT_PAID_DAILY_KOPEKS": "0",
    }, clear=False):
        assert policy.paid_fallback_enabled() is False

    with patch.dict(os.environ, {
        "BORIS_CONTENT_PAID_FALLBACK": "1",
        "BORIS_CONTENT_PAID_PER_ITEM_KOPEKS": "100",
        "BORIS_CONTENT_PAID_DAILY_KOPEKS": "500",
    }, clear=False):
        p = policy.policy()
        assert p["paid_fallback_requested"] is True
        assert p["paid_fallback_enabled"] is False
        assert p["paid_limit_adapter"] == "disabled_until_cross_provider_hard_cap_exists"


def test_local_modes_are_default_and_local_llm_is_not_required():
    names = (
        "BORIS_CONTENT_LOCAL_TEXT_FIRST",
        "BORIS_CONTENT_LOCAL_BANNER_FIRST",
        "BORIS_CONTENT_PAID_COMPETITOR_AUDIT",
    )
    clean = {k: v for k, v in os.environ.items() if k not in names}
    with patch.dict(os.environ, clean, clear=True):
        p = policy.policy()
        assert p["local_text_first"] is True
        assert p["local_banner_first"] is True
        assert p["paid_competitor_audit"] is False
        assert p["local_llm_required"] is False


def test_deterministic_copy_has_avito_safe_shape_without_model():
    payload = {"variant": 1, "city": "Москва", "price": 0}
    ff = {
        "brief": "Разработка CRM для отдела продаж без выдуманных сроков и гарантий",
        "account_context": {
            "client_description": "Разрабатываем CRM под процессы отдела продаж."
        },
    }
    title, description = campaigns._ff_bulk_safe_copy("Разработка CRM", payload, ff)
    assert title
    assert len(title) <= 50
    assert "Москва" in description
    assert "Напишите" in description or "сообщен" in description
    assert "гарантируем" not in description.lower()
    assert "<p>" not in description.lower()


def test_campaign_text_path_is_local_first_and_paid_path_is_gated():
    src = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    assert "CONTENT_LOCAL_FIRST_V1" in src
    assert "_use_local_text=bool(_content_local_text_first() or strict_bulk or not _content_paid_fallback())" in src
    assert 'p["copy_provider"]="boris_deterministic_local_v1"' in src
    assert 'p["copy_provider"]="paid_single_attempt"' in src


def test_banner_paid_generation_is_not_implicit():
    src = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    assert "CONTENT_BANNER_LOCAL_FIRST_V1" in src
    assert "owner_reference_direct_local_composition" in src
    assert "CONTENT_PAID_FALLBACK_DISABLED_NO_VERIFIED_MEDIA" in src
    assert "and _content_paid_fallback()" in src


def test_campaign_prepare_paid_competitor_audit_is_opt_in():
    src = (ROOT / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "paid_competitor_audit_enabled" in src
    assert "local_first_paid_competitor_audit_disabled" in src
    assert "safe_alternative" in src


def test_one_canonical_feed_factory_not_second_publication_pipeline():
    p = policy.policy()
    assert p["xml"] == "canonical Feed Factory projection only"
    assert "canonical BORIS lifecycle" in p["publication"]
