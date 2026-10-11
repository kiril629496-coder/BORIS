from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.services import banner_full_ai_premium as premium


def _pattern_png(path: Path, size=(1024, 1024)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.linear_gradient("L").resize(size).convert("RGB")
    img.save(path, "PNG")
    return path


def _vision_pass(cost=0.05):
    return {
        "passed": True,
        "semantic_match": True,
        "headline_ok": True,
        "exact_text_ok": True,
        "spelling_ok": True,
        "forbidden_claims_ok": True,
        "composition_ok": True,
        "crop_safe_ok": True,
        "professional_ok": True,
        "mobile_ok": True,
        "selling_typography_ok": True,
        "palette_ok": True,
        "reference_style_match": True,
        "issues": [],
        "qa_cost_rub": cost,
    }


def test_premium_status_cost_cap(tmp_path):
    with patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"):
        data = premium.status_payload()
    assert data["engine"] == "full_ai_premium"
    assert data["model"] == "gpt-image-2"
    assert data["quality"] == "low"
    assert data["estimated_cost_rub"] <= 4.0
    assert data["single_paid_call"] is True
    assert data["preview_first"] is True
    assert data["production_preset_locked"] is True
    assert data["approved_paid_preset"] == {"model": "gpt-image-2", "quality": "low"}


def test_premium_prompt_restores_full_ai_layout():
    prompt = premium.build_prompt(
        description="AI-продажи для бизнеса с CRM и аналитикой",
        exact_title="AI-продажи для бизнеса",
        exact_subtitle="Заявки · CRM · аналитика",
        exact_cta="Запуск за 30 минут",
        fmt="1:1",
        hypothesis="dashboard_control",
    )
    assert "complete advertising creative" in prompt
    assert "AI-продажи для бизнеса" in prompt
    assert "Запуск за 30 минут" in prompt
    assert "CONTROL CENTER" in prompt
    assert "one dominant expressive hero scene" in prompt
    assert "at most one strong CTA/badge" in prompt
    assert "no extra random words" in prompt


def test_diversity_profiles_are_stable_for_replay_and_rotate_on_new_version():
    comp1, icon1 = premium._select_diversity_profiles("acct|banner|idem", version=1)
    comp1_replay, icon1_replay = premium._select_diversity_profiles("acct|banner|idem", version=1)
    comp2, icon2 = premium._select_diversity_profiles("acct|banner|idem", version=2)

    assert comp1 == comp1_replay
    assert icon1 == icon1_replay
    assert comp1[0] != comp2[0]

    prompt = premium.build_prompt(
        description="Кухни на заказ по индивидуальным размерам",
        exact_title="Кухни на заказ",
        advantages=["Фасады", "Материалы", "Цвета"],
        composition_profile=comp1,
        icon_profile=icon1,
    )
    assert "COMPOSITION DIVERSITY CONTRACT" in prompt
    assert comp1[0] in prompt
    assert "ICON DIVERSITY CONTRACT" in prompt
    assert icon1[0] in prompt
    assert "identical circular-benefit row" in prompt


def test_create_preview_single_paid_image_call_exact_cost_and_qa(tmp_path):
    calls = []
    versions = []

    def fake_core(**kwargs):
        calls.append(kwargs)
        _pattern_png(Path(kwargs["save_path"]))
        return {
            "ok": True,
            "path": kwargs["save_path"],
            "cost_rub": 1.39,
            "provider_request_id": "req-1",
            "request_id_exact": True,
        }

    def fake_load(account_id, banner_id):
        return list(versions)

    def fake_save(account_id, banner_id, rows):
        versions[:] = list(rows)

    with patch.object(premium, "IMAGES_ROOT", tmp_path / "images"), \
         patch.object(premium, "LOCK_ROOT", tmp_path / "locks"), \
         patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"), \
         patch.object(premium, "_load_versions", side_effect=fake_load), \
         patch.object(premium, "_save_versions", side_effect=fake_save), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core), \
         patch.object(premium, "_run_vision_qa", return_value=_vision_pass(0.05)):
        result = premium.create_preview(
            account_id="active-account",
            description="AI-продажи для бизнеса",
            exact_title="AI-продажи для бизнеса",
            exact_cta="Запуск за 30 минут",
            advantages=["CRM", "Аналитика", "Автоматизация"],
            fmt="1:1",
            hypothesis="product_hero",
            idempotency_key="preview-1",
        )

    assert result["status"] == "ok"
    assert result["preview_status"] == "DRAFT_PREVIEW"
    assert result["approved"] is False
    assert result["cost_reconciled"] is True
    assert result["actual_cost_rub"] == 1.39
    assert result["qa_cost_rub"] == 0.05
    assert result["total_cost_rub"] == 1.44
    assert result["cost_sla_pass"] is True
    assert result["qa_passed"] is True
    assert result["text_qa_status"] == "PASS"
    assert result["visual_qa_status"] == "PASS"
    assert result["provider_request_id"] == "req-1"
    assert result["diversity_contract_version"] == "banner_diversity_v1"
    assert result["composition_profile"] in {x[0] for x in premium.COMPOSITION_PROFILES}
    assert result["icon_profile"] in {x[0] for x in premium.ICON_STYLE_PROFILES}
    assert len(calls) == 1
    assert calls[0]["size"] == "1024x1024"
    assert "COMPOSITION DIVERSITY CONTRACT" in calls[0]["prompt"]
    assert "ICON DIVERSITY CONTRACT" in calls[0]["prompt"]


def test_high_quality_drift_blocked_before_provider(tmp_path):
    calls = []

    def fake_core(**kwargs):
        calls.append(kwargs)
        raise AssertionError("provider must not be called")

    with patch.object(premium, "QUALITY", "high"), \
         patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core):
        result = premium.create_preview(
            account_id="active-account",
            description="AI-продажи для бизнеса",
            exact_title="AI-продажи",
            idempotency_key="must-block-high",
        )

    assert result["status"] == "cost_cap_exceeded"
    assert result["code"] == "COST_CAP_EXCEEDED"
    assert result["preset_ok"] is False
    assert result["quality"] == "high"
    assert calls == []


def test_create_preview_replay_does_not_buy_again(tmp_path):
    calls = []
    saved = tmp_path / "images" / "active-account" / "banners" / "premium_ai" / "existing.png"
    _pattern_png(saved)
    existing = [{
        "status": "ok",
        "preview_status": "DRAFT_PREVIEW",
        "banner_id": "banner-x",
        "version": 1,
        "idempotency_key": "same-key",
        "absolute_path": str(saved),
        "url": "/images/active-account/banners/premium_ai/existing.png",
    }]

    def fake_core(**kwargs):
        calls.append(1)
        raise AssertionError("provider must not be called on replay")

    with patch.object(premium, "IMAGES_ROOT", tmp_path / "images"), \
         patch.object(premium, "LOCK_ROOT", tmp_path / "locks"), \
         patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"), \
         patch.object(premium, "_load_versions", return_value=existing), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core):
        result = premium.create_preview(
            account_id="active-account",
            description="AI-продажи для бизнеса",
            exact_title="AI-продажи",
            idempotency_key="same-key",
            parent_banner_id="banner-x",
        )

    assert result["status"] == "ok"
    assert result["idempotency_replay"] is True
    assert calls == []


def test_unknown_actual_cost_blocks_approval_without_extra_qa(tmp_path):
    versions = []
    qa_calls = []

    def fake_core(**kwargs):
        _pattern_png(Path(kwargs["save_path"]))
        return {"ok": True, "path": kwargs["save_path"], "cost_rub": 0.0, "provider_request_id": "req-unknown"}

    def fake_load(account_id, banner_id):
        return list(versions)

    def fake_save(account_id, banner_id, rows):
        versions[:] = list(rows)

    def fake_vision(**kwargs):
        qa_calls.append(1)
        return _vision_pass()

    with patch.object(premium, "IMAGES_ROOT", tmp_path / "images"), \
         patch.object(premium, "LOCK_ROOT", tmp_path / "locks"), \
         patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"), \
         patch.object(premium, "_load_versions", side_effect=fake_load), \
         patch.object(premium, "_save_versions", side_effect=fake_save), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core), \
         patch.object(premium, "_latest_actual_cost", return_value=0.0), \
         patch.object(premium, "_run_vision_qa", side_effect=fake_vision):
        result = premium.create_preview(
            account_id="active-account",
            description="Услуга",
            exact_title="Услуга",
            idempotency_key="unknown-cost",
        )

    assert result["preview_status"] == "COST_RECONCILIATION_REQUIRED"
    assert result["cost_reconciled"] is False
    assert result["cost_sla_pass"] is False
    assert qa_calls == []


def test_actual_overrun_trips_global_cost_circuit(tmp_path):
    versions = []
    calls = []

    def fake_core(**kwargs):
        calls.append(1)
        _pattern_png(Path(kwargs["save_path"]))
        return {"ok": True, "path": kwargs["save_path"], "cost_rub": 5.2, "provider_request_id": "req-over"}

    def fake_load(account_id, banner_id):
        return list(versions)

    def fake_save(account_id, banner_id, rows):
        versions[:] = list(rows)

    circuit = tmp_path / "circuit.json"
    with patch.object(premium, "IMAGES_ROOT", tmp_path / "images"), \
         patch.object(premium, "LOCK_ROOT", tmp_path / "locks"), \
         patch.object(premium, "CIRCUIT_PATH", circuit), \
         patch.object(premium, "_load_versions", side_effect=fake_load), \
         patch.object(premium, "_save_versions", side_effect=fake_save), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core):
        first = premium.create_preview(
            account_id="active-account",
            description="Услуга",
            exact_title="Услуга",
            idempotency_key="overrun-1",
        )
        second = premium.create_preview(
            account_id="active-account",
            description="Услуга",
            exact_title="Услуга",
            idempotency_key="overrun-2",
        )

    assert first["preview_status"] == "BLOCKED_COST"
    assert first["cost_sla_pass"] is False
    assert circuit.is_file()
    assert second["status"] == "cost_cap_exceeded"
    assert second["code"] == "COST_CIRCUIT_OPEN"
    assert calls == [1]


def test_qa_failure_is_saved_but_cannot_be_approved(tmp_path):
    versions = []

    def fake_core(**kwargs):
        _pattern_png(Path(kwargs["save_path"]))
        return {"ok": True, "path": kwargs["save_path"], "cost_rub": 1.2, "provider_request_id": "req-qa"}

    def fake_load(account_id, banner_id):
        return list(versions)

    def fake_save(account_id, banner_id, rows):
        versions[:] = list(rows)

    failed = _vision_pass(0.03)
    failed["passed"] = False
    failed["spelling_ok"] = False
    failed["issues"] = ["headline_spelling"]

    with patch.object(premium, "IMAGES_ROOT", tmp_path / "images"), \
         patch.object(premium, "LOCK_ROOT", tmp_path / "locks"), \
         patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"), \
         patch.object(premium, "_load_versions", side_effect=fake_load), \
         patch.object(premium, "_save_versions", side_effect=fake_save), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core), \
         patch.object(premium, "_run_vision_qa", return_value=failed):
        result = premium.create_preview(
            account_id="active-account",
            description="Услуга",
            exact_title="Услуга",
            idempotency_key="qa-fail",
        )
        blocked = premium.approve_preview("active-account", result["banner_id"], result["version"])

    assert result["preview_status"] == "QA_FAILED"
    assert result["qa_passed"] is False
    assert blocked["status"] == "blocked"
    assert blocked["code"] == "QA_FAILED"


def test_reject_preserves_version_history():
    versions = [{
        "status": "ok",
        "banner_id": "b1",
        "version": 1,
        "approved": False,
        "rejected": False,
        "preview_status": "DRAFT_PREVIEW",
    }]
    saved = []

    with patch.object(premium, "_load_versions", return_value=versions), \
         patch.object(premium, "_save_versions", side_effect=lambda a, b, rows: saved.extend(rows)):
        result = premium.reject_preview("a1", "b1", 1, "Не подходит композиция")

    assert result["status"] == "ok"
    assert saved[0]["rejected"] is True
    assert saved[0]["preview_status"] == "REJECTED"
    assert "Не подходит" in saved[0]["rejection_reason"]


def test_premium_api_routes_are_registered():
    from app.api import banners
    paths = {getattr(route, "path", "") for route in banners.router.routes}
    assert "/api/banners/premium-ai/status" in paths
    assert "/api/banners/premium-ai/create-one" in paths
    assert "/api/banners/premium-ai/versions" in paths
    assert "/api/banners/premium-ai/approve" in paths
    assert "/api/banners/premium-ai/reject" in paths


def test_same_intent_concurrent_calls_buy_once(tmp_path):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    calls = []
    versions = []
    state_lock = threading.Lock()

    def fake_core(**kwargs):
        with state_lock:
            calls.append(kwargs)
        time.sleep(0.08)
        _pattern_png(Path(kwargs["save_path"]))
        return {
            "ok": True,
            "path": kwargs["save_path"],
            "cost_rub": 1.25,
            "provider_request_id": "req-concurrent",
            "request_id_exact": True,
        }

    def fake_load(account_id, banner_id):
        with state_lock:
            return [dict(row) for row in versions]

    def fake_save(account_id, banner_id, rows):
        with state_lock:
            versions[:] = [dict(row) for row in rows]

    kwargs = dict(
        account_id="account-a",
        description="Услуга для бизнеса",
        exact_title="Услуга для бизнеса",
        idempotency_key="same-paid-intent",
    )
    with patch.object(premium, "IMAGES_ROOT", tmp_path / "images"), \
         patch.object(premium, "LOCK_ROOT", tmp_path / "locks"), \
         patch.object(premium, "CIRCUIT_PATH", tmp_path / "circuit.json"), \
         patch.object(premium, "_load_versions", side_effect=fake_load), \
         patch.object(premium, "_save_versions", side_effect=fake_save), \
         patch.object(premium, "_generate_image_core", side_effect=fake_core), \
         patch.object(premium, "_run_vision_qa", return_value=_vision_pass(0.03)):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: premium.create_preview(**kwargs), range(2)))

    assert len(calls) == 1
    assert first["banner_id"] == second["banner_id"]
    assert first["version"] == second["version"] == 1
    assert {bool(first.get("idempotency_replay")), bool(second.get("idempotency_replay"))} == {False, True}


def test_banner_identity_is_account_scoped():
    same_intent = "shared-intent-key"
    assert premium._banner_identity("account-a", same_intent) != premium._banner_identity("account-b", same_intent)


def test_approve_registers_media_only_after_cost_and_qa_pass(tmp_path):
    image_path = _pattern_png(tmp_path / "approved.png")
    versions = [{
        "status": "ok",
        "banner_id": "b-ok",
        "version": 1,
        "absolute_path": str(image_path),
        "url": "/images/account-a/banners/premium_ai/approved.png",
        "approved": False,
        "rejected": False,
        "cost_reconciled": True,
        "cost_sla_pass": True,
        "qa_passed": True,
    }]
    saved = []

    with patch.object(premium, "_load_versions", return_value=versions), \
         patch.object(premium, "_save_versions", side_effect=lambda a, b, rows: saved.extend(rows)), \
         patch("app.services.banner_media.register_banner", return_value={"media_id": "m1"}) as register:
        result = premium.approve_preview("account-a", "b-ok", 1)

    assert result["status"] == "ok"
    assert result["preview_status"] == "AVAILABLE_FOR_USE"
    register.assert_called_once()
    assert saved[0]["approved"] is True
    assert saved[0]["accepted_at"]


def test_banner_reference_edit_cost_guard_blocks_over_four_rubles_before_provider():
    import inspect
    from app.services import image_service

    guard = image_service.banner_reference_cost_guard(
        "campaign_openai_reference_visual",
        model="gpt-image-2",
        quality="low",
    )
    assert guard["applies"] is True
    assert guard["allowed"] is False
    assert guard["estimated_cost_rub"] == 4.6
    assert guard["max_cost_rub"] == 4.0
    source = inspect.getsource(image_service.generate)
    assert "BANNER_REFERENCE_COST_CAP_EXCEEDED" in source
    assert source.index("BANNER_REFERENCE_COST_CAP_EXCEEDED") < source.index("_paid_image_account_guard")


def test_non_banner_reference_workflow_keeps_existing_budget_policy():
    from app.services import image_service

    guard = image_service.banner_reference_cost_guard(
        "photo_master",
        model="gpt-image-2",
        quality="low",
    )
    assert guard["applies"] is False
    assert guard["allowed"] is True


def test_banner_copy_price_is_literal_owner_input_only():
    import inspect
    from app.api import banners

    assert banners._extract_verified_price("Кухни на заказ от 30 000 ₽, замер включён") == "от 30 000 ₽"
    assert banners._extract_verified_price("Кухни на заказ без указанной цены") == ""
    source = inspect.getsource(banners._generate_marketing_copy)
    assert "price_text = _extract_verified_price(source)" in source


def test_banner_copy_generated_numeric_claims_must_exist_in_source():
    from app.api import banners

    source = "Запуск за 30 минут. Работаем с 2020 года."
    assert banners._numeric_claim_supported("Запуск за 30 минут", source) is True
    assert banners._numeric_claim_supported("Опыт 10 лет", source) is False
    assert banners._numeric_claim_supported("CRM и аналитика", source) is True


def test_owner_style_contracts_are_injected():
    manicure = premium.build_prompt(
        description="Маникюр Евпатория, современный маникюр и ногти",
        exact_title="Удобная длина ногтей",
        exact_subtitle="Красиво в вашем ритме",
        fmt="1:1",
    )
    assert "OWNER-APPROVED VISUAL BENCHMARK [manicure]" in manicure
    assert "electric violet" in manicure
    assert "bold modern sales-oriented sans/grotesk" in manicure

    software = premium.build_prompt(
        description="Разработка ПО, CRM и автоматизация",
        exact_title="Разработка ПО под ключ",
        exact_subtitle="Сайты, CRM, AI и автоматизация",
        fmt="1:1",
    )
    assert "OWNER-APPROVED VISUAL BENCHMARK [software]" in software
    assert "graphite black + vivid orange + white" in software


def test_pedicure_owner_profile_wins_over_project_manicure_name():
    prompt = premium.build_prompt(
        description="Проект Маникюр Евпатория. Тема: педикюр осенью и уход за стопами",
        exact_title="Педикюр нужен и осенью",
        exact_subtitle="Уход и комфорт круглый год",
        fmt="1:1",
    )
    assert "OWNER-APPROVED VISUAL BENCHMARK [pedicure]" in prompt
    assert "deep emerald + cream + restrained gold + charcoal" in prompt
