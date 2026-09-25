from __future__ import annotations

from unittest.mock import patch

from scripts import crowd_seo_production_certifier as cert


def _matches(count: int) -> list[dict]:
    return [
        {
            "platform": f"forum_{i}",
            "surface_id": f"surface_{i}",
            "publication_ready": True,
        }
        for i in range(count)
    ]


def _project(plan_mode: str = "contract") -> dict:
    return {
        "id": "crowd_test",
        "site": "https://client.example/",
        "niche": "services",
        "keywords": [],
        "offer_type": "services",
        "plan_mode": plan_mode,
    }


def _capacity(required: int, available: int | None = None) -> dict:
    available = required if available is None else available
    return {
        "required": required,
        "eligible": available,
        "ready": available,
        "service_reachable_after_warmup": available,
        "discovery_deficit_after_warmup": max(0, required - available),
    }


def _row(project: dict, capacity: dict, match_count: int, *, live_summary: dict | None = None) -> dict:
    summary = live_summary if live_summary is not None else {"published": 0, "verified": 0}
    with (
        patch.object(cert.crowd_seo, "_forum_matches", return_value=_matches(match_count)),
        patch.object(cert.crowd_seo, "_legacy_offer_type", return_value="services"),
        patch.object(cert.crowd_seo, "_project_niche_aliases", return_value={"services"}),
        patch.object(cert.crowd_seo, "_placement_summary", return_value=summary),
        patch.object(cert.marketplace, "registration_plan", return_value=[]),
        patch.object(cert.marketplace, "platform_policy", return_value={"free": True}),
        patch.object(cert.platform_rules, "publish_gate", return_value={"allowed": True}),
    ):
        return cert._project_row(project, capacity)


def test_inventory_project_with_zero_contract_quantity_is_healthy_pool_growth():
    row = _row(_project("inventory"), _capacity(required=0, available=0), match_count=27)
    assert row["required"] == 0
    assert row["selected_slots"] == 0
    assert row["reserve_slots"] == 27
    assert row["capacity_ok"] is True
    assert row["infrastructure_ok"] is True
    assert row["delivery_ready"] is True


def test_inventory_with_operational_demand_is_not_delivery_ready_until_accounts_are_ready():
    project = _project("inventory")
    capacity = {
        "required": 5,
        "eligible": 5,
        "ready": 0,
        "service_reachable_after_warmup": 5,
        "discovery_deficit_after_warmup": 0,
    }
    waiting_matches = [
        {
            "platform": f"forum_{i}",
            "surface_id": f"surface_{i}",
            "publication_ready": False,
            "checkpoint": "captcha_required",
        }
        for i in range(5)
    ]
    with (
        patch.object(cert.crowd_seo, "_forum_matches", return_value=waiting_matches),
        patch.object(cert.crowd_seo, "_legacy_offer_type", return_value="services"),
        patch.object(cert.crowd_seo, "_project_niche_aliases", return_value={"services"}),
        patch.object(cert.marketplace, "registration_plan", return_value=[]),
        patch.object(cert.marketplace, "platform_policy", return_value={"free": True}),
        patch.object(cert.platform_rules, "publish_gate", return_value={"allowed": True}),
    ):
        row = cert._project_row(project, capacity)
    assert row["infrastructure_ok"] is True
    assert row["ready_selected"] == 0
    assert row["delivery_ready"] is False
    assert row["selected_external_checkpoints"] == {"captcha_required": 5}


def test_certifier_selects_reachable_slot_before_transient_unreachable_reserve():
    project = _project("inventory")
    capacity = {
        "required": 2,
        "eligible": 3,
        "ready": 0,
        "service_reachable_after_warmup": 2,
        "discovery_deficit_after_warmup": 0,
    }
    matches = [
        {
            "platform": "homeidea",
            "surface_id": "default",
            "publication_ready": False,
            "account_status": "in_progress",
            "checkpoint": "preflight_timeout",
            "maturity_required": False,
            "account_warming": False,
        },
        {
            "platform": "captcha_forum",
            "surface_id": "services",
            "publication_ready": False,
            "account_status": "verification_required",
            "checkpoint": "captcha_required",
            "maturity_required": False,
            "account_warming": False,
        },
        {
            "platform": "warming_forum",
            "surface_id": "services",
            "publication_ready": False,
            "account_status": "warming",
            "checkpoint": "established_member_required",
            "maturity_required": True,
            "account_warming": True,
        },
    ]
    with (
        patch.object(cert.crowd_seo, "_forum_matches", return_value=matches),
        patch.object(cert.crowd_seo, "_legacy_offer_type", return_value="services"),
        patch.object(cert.crowd_seo, "_project_niche_aliases", return_value={"services"}),
        patch.object(cert.marketplace, "registration_plan", return_value=[]),
        patch.object(cert.marketplace, "platform_policy", return_value={"free": True}),
        patch.object(cert.platform_rules, "publish_gate", return_value={"allowed": True}),
    ):
        row = cert._project_row(project, capacity)
    assert row["selected_platforms"] == [
        "captcha_forum::services",
        "warming_forum::services",
    ]
    assert row["reserve_platforms"] == ["homeidea::default"]
    assert "preflight_timeout" not in row["selected_external_checkpoints"]


def test_certificate_reports_real_verified_progress_and_remaining_count():
    project = _project("contract")
    row = _row(
        project,
        _capacity(required=5),
        match_count=8,
        live_summary={"published": 2, "verified": 2},
    )
    assert row["published_total"] == 2
    assert row["published_verified"] == 2
    assert row["remaining_verified"] == 3


def test_certificate_ignores_stale_persisted_placement_summary():
    project = _project("contract")
    project["placement_summary"] = {"published": 99, "verified": 99}
    row = _row(
        project,
        _capacity(required=5),
        match_count=8,
        live_summary={"published": 1, "verified": 1},
    )
    assert row["published_total"] == 1
    assert row["published_verified"] == 1
    assert row["remaining_verified"] == 4


def test_placement_operational_deficit_fails_infrastructure_capacity():
    capacity = _capacity(required=5)
    capacity.update({"placement_covered_live_slots": 4, "placement_operational_deficit": 1})
    row = _row(_project("contract"), capacity, match_count=8)
    assert row["placement_operational_deficit"] == 1
    assert row["capacity_ok"] is False
    assert row["infrastructure_ok"] is False


def test_contract_uses_exact_nine_slot_order_not_historical_eighteen():
    row = _row(_project("contract"), _capacity(required=9), match_count=30)
    assert row["required"] == 9
    assert row["selected_slots"] == 9
    assert row["reserve_slots"] == 21
    assert row["delivery_ready"] is True


def test_contract_uses_exact_thirty_five_slot_order_not_fixed_package():
    row = _row(_project("contract"), _capacity(required=35), match_count=50)
    assert row["required"] == 35
    assert row["selected_slots"] == 35
    assert row["reserve_slots"] == 15
    assert row["delivery_ready"] is True


def test_zero_quantity_contract_fails_closed_instead_of_inventing_a_package():
    row = _row(_project("contract"), _capacity(required=0, available=0), match_count=27)
    assert row["required"] == 0
    assert row["capacity_ok"] is False
    assert row["infrastructure_ok"] is False
    assert row["delivery_ready"] is False


def test_certifier_uses_normalized_project_keywords_for_selection():
    project = _project("inventory")
    project["keywords"] = ["raw-keyword"]
    capacity = {
        "required": 1,
        "eligible": 1,
        "ready": 1,
        "service_reachable_after_warmup": 1,
        "discovery_deficit_after_warmup": 0,
    }
    captured = {}

    def fake_matches(niche, limit=40, keywords=None, offer_type="mixed"):
        captured["keywords"] = list(keywords or [])
        return _matches(1)

    with (
        patch.object(cert.crowd_seo, "_project_keywords", return_value=["normalized-keyword"]) as normalized,
        patch.object(cert.crowd_seo, "_forum_matches", side_effect=fake_matches),
        patch.object(cert.crowd_seo, "_legacy_offer_type", return_value="services"),
        patch.object(cert.crowd_seo, "_project_niche_aliases", return_value={"services"}),
        patch.object(cert.marketplace, "registration_plan", return_value=[]),
        patch.object(cert.marketplace, "platform_policy", return_value={"free": True}),
        patch.object(cert.platform_rules, "publish_gate", return_value={"allowed": True}),
    ):
        row = cert._project_row(project, capacity)

    normalized.assert_called_once_with(project)
    assert captured["keywords"] == ["normalized-keyword"]
    assert row["matched_slots_total"] == 1
    assert row["capacity_ok"] is True


def test_external_submission_snapshot_uses_latest_state_per_platform():
    attempts = [
        {"platform": "guest_verified", "action": "verify_publication", "status": "verified", "url": "https://x/post", "created_at": "2026-09-13T12:03:00+00:00"},
        {"platform": "guest_verified", "action": "guest_catalog_submit", "status": "submitted_pending_moderation", "url": "https://x/add", "created_at": "2026-09-13T12:00:00+00:00"},
        {"platform": "guest_conti", "action": "guest_catalog_submit", "status": "submitted_pending_moderation", "url": "https://conti/add", "created_at": "2026-09-13T12:02:00+00:00"},
        {"platform": "guest_mail", "action": "guest_catalog_email_submit", "status": "submitted_pending_email", "url": "https://mail/add", "created_at": "2026-09-13T12:01:00+00:00"},
    ]
    with patch.object(cert.marketplace, "list_attempts", return_value=attempts):
        snap = cert._external_submissions_snapshot()
    assert snap["pending_total"] == 2
    assert [x["platform"] for x in snap["pending"]] == ["guest_conti", "guest_mail"]
    assert snap["verified_total"] == 1
    assert snap["verified"][0]["platform"] == "guest_verified"
