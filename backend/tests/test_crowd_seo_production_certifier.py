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


def _row(project: dict, capacity: dict, match_count: int) -> dict:
    with (
        patch.object(cert.crowd_seo, "_forum_matches", return_value=_matches(match_count)),
        patch.object(cert.crowd_seo, "_legacy_offer_type", return_value="services"),
        patch.object(cert.crowd_seo, "_project_niche_aliases", return_value={"services"}),
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
