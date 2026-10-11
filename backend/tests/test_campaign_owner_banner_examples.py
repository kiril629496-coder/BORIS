from app.services.campaign_owner_banner_examples import (
    CONTRACT_VERSION, DEFAULT_ACCENT, PROFILES, combine_reference_style, owner_variant,
)


def test_owner_banner_examples_are_deterministic_and_account_scoped():
    a = owner_variant("albertardashov_79016", "same-intent", 1)
    b = owner_variant("albertardashov_79016", "same-intent", 1)
    assert a == b
    assert a["contract"] == CONTRACT_VERSION
    assert a["palette_key"]
    assert a["accent"].startswith("#")
    assert owner_variant("unknown", "same-intent", 1) is None


def test_owner_banner_examples_rotate_away_from_used_palette_when_possible():
    first = owner_variant("planetazayavki_65985", "intent", 1)
    second = owner_variant(
        "planetazayavki_65985", "intent", 1,
        previous_palettes=(first["palette_key"],),
    )
    assert second["palette_key"] != first["palette_key"]


def test_reference_guidance_is_style_only_and_bounded():
    variant = owner_variant("mbroker_27859", "intent", 1)
    text = combine_reference_style(variant, "Customer style")
    assert "style inspiration ONLY" in text
    assert "No made-up prices" in text
    assert "CUSTOMER STYLE: Customer style" in text
    assert len(text) <= 2350
    assert DEFAULT_ACCENT == "#F97316"
    assert set(PROFILES) >= {
        "mbroker_27859", "albertardashov_79016", "planetazayavki_65985"
    }
