from app.services import banner_full_ai_premium as premium


def test_diversity_profiles_stable_for_replay_and_rotate_for_new_version():
    c1, i1 = premium._select_diversity_profiles("acct|banner|idem", version=1)
    c1b, i1b = premium._select_diversity_profiles("acct|banner|idem", version=1)
    c2, i2 = premium._select_diversity_profiles("acct|banner|idem", version=2)
    assert c1 == c1b
    assert i1 == i1b
    assert c1[0] != c2[0]
    assert c1[0] in {x[0] for x in premium.COMPOSITION_PROFILES}
    assert i1[0] in {x[0] for x in premium.ICON_STYLE_PROFILES}
    assert i2[0] in {x[0] for x in premium.ICON_STYLE_PROFILES}


def test_prompt_enforces_composition_and_icon_diversity_contracts():
    comp, icon = premium._select_diversity_profiles("acct|banner|idem", version=1)
    prompt = premium.build_prompt(
        description="Кухни на заказ по индивидуальным размерам",
        exact_title="Кухни на заказ",
        exact_subtitle="По вашим размерам",
        advantages=["Фасады", "Материалы", "Цвета"],
        composition_profile=comp,
        icon_profile=icon,
    )
    assert "COMPOSITION DIVERSITY CONTRACT" in prompt
    assert comp[0] in prompt
    assert "ICON DIVERSITY CONTRACT" in prompt
    assert icon[0] in prompt
    assert "identical circular-benefit row" in prompt


def test_diversity_change_does_not_relax_cost_or_paid_call_policy():
    status = premium.status_payload()
    assert status["model"] == "gpt-image-2"
    assert status["quality"] == "low"
    assert status["single_paid_call"] is True
    assert status["estimated_cost_rub"] <= 4.0


def test_owner_reference_contracts_are_active_for_beauty_niches():
    niche, style = premium._owner_style_contract(
        "Маникюр Евпатория, премиальная beauty-реклама",
        "Удобная длина ногтей",
        "Красиво в вашем ритме",
    )
    assert niche == "manicure"
    assert "electric violet" in style["palette"]
    assert "selling bold modern grotesk" in style["typography"]
    prompt = premium.build_prompt(
        description="Маникюр Евпатория, премиальная beauty-реклама",
        exact_title="Удобная длина ногтей",
        exact_subtitle="Красиво в вашем ритме",
        advantages=["Комфорт каждый день", "Аккуратная форма", "Современный стиль"],
    )
    assert premium.OWNER_REFERENCE_VERSION in prompt
    assert "OWNER-APPROVED VISUAL BENCHMARK [manicure]" in prompt
    assert "REFERENCE LEARNING" in prompt


def test_owner_reference_images_exist_for_supported_niches():
    from pathlib import Path
    for niche in ("manicure", "pedicure", "windows", "software", "kitchen"):
        path = premium.OWNER_REFERENCE_IMAGES[niche]
        assert Path(path).is_file()
        assert Path(path).stat().st_size > 1024


def test_pedicure_owner_contract_has_distinct_palette_and_typography():
    niche, style = premium._owner_style_contract(
        "Педикюр Евпатория осенью",
        "Педикюр нужен и осенью",
        "Уход и комфорт круглый год",
    )
    assert niche == "pedicure"
    assert "emerald" in style["palette"]
    assert "bold premium sans" in style["typography"]


def test_roofing_niche_wins_over_windows_for_grand_line_profile_language():
    niche, style = premium._owner_style_contract(
        "Металлочерепица Grand Line, кровля, фактура профиля, строительные материалы Санкт-Петербург",
        "Металлочерепица Grand Line",
        "Кровля в наличии в Санкт-Петербурге",
    )
    assert niche == "roofing"
    assert "vivid orange" in style["palette"]
    assert "building-material" in style["composition"]
