from __future__ import annotations

from app.services import platform_rules
from app.services import service_marketplace as marketplace


def _inspection(text: str) -> dict:
    return {"decision": "review", "text_excerpt": text}


def test_promebelclub_exact_commercial_surface_is_allowed_only_with_complete_evidence():
    text = (
        "Продаю | Сдаю. Любые объявления рекламного характера по продаже "
        "продукции (оборудования), оказанию услуг или сдаче в аренду помещений. "
        "Правила создания тем в разделе ОБЪЯВЛЕНИЯ. При создании темы в разделе "
        "ОБЪЯВЛЕНИЯ обязательно указывать регион и контактную информацию для связи. "
        "Наш сайт – https://laserspro.ru/"
    )
    decision, requirements = platform_rules._promebelclub_rule_override(
        "promebelclub", [_inspection(text)], "review", []
    )
    assert decision == "allowed"
    assert "use_promebelclub_sell_rent_only" in requirements
    assert "region_and_contact_required" in requirements
    assert "external_site_seen_in_commercial_listing" in requirements


def test_promebelclub_fails_closed_without_section_rule_or_link_evidence():
    cases = [
        "Мебельный бизнес. Обсуждение производства мебели.",
        (
            "Продаю | Сдаю. Любые объявления рекламного характера по продаже "
            "продукции и оказанию услуг."
        ),
        (
            "Продаю | Сдаю. Любые объявления рекламного характера по продаже "
            "продукции и оказанию услуг. Правила создания тем в разделе ОБЪЯВЛЕНИЯ. "
            "Обязательно указывать регион и контактную информацию."
        ),
    ]
    for text in cases:
        decision, _ = platform_rules._promebelclub_rule_override(
            "promebelclub", [_inspection(text)], "allowed", []
        )
        assert decision == "review"


def test_promebelclub_inventory_surface_is_scoped_and_direct_post():
    platform = marketplace.get_platform("promebelclub")
    assert platform is not None
    assert platform.mode == "direct_post"
    assert platform.url.endswith("forumdisplay.php?f=115")
    surface = marketplace.VERIFIED_PUBLICATION_SURFACES["promebelclub"][0]
    assert "goods" in surface["niches"]
    assert "services" in surface["niches"]
    assert surface["required_terms"]
    assert surface["post_url"].endswith("newthread.php?do=newthread&f=115")
