from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.services import crowd_seo, forum_quality, service_marketplace
from tools import forum_acquisition_guard as guard
from tools import platform_onboarding_worker as onboarding_worker
from tools import crowd_seo_certifier_runner as certifier_runner
from scripts import crowd_seo_production_certifier as certifier


def test_crowd_runtime_state_paths_follow_current_checkout():
    backend_root = Path(crowd_seo.__file__).resolve().parents[2]
    assert crowd_seo.BACKEND_ROOT == backend_root
    assert crowd_seo.ROOT == backend_root / "data" / "crowd_seo"
    assert forum_quality.BACKEND_ROOT == backend_root
    assert forum_quality.DATA_FILE == backend_root / "data" / "service_marketplaces" / "forum_quality_iks.json"


def test_guard_worker_and_certifier_paths_follow_current_checkout():
    backend_root = Path(guard.__file__).resolve().parents[1]
    assert guard.BACKEND_ROOT == backend_root
    assert guard.STATUS_FILE == backend_root / "data" / "service_marketplaces" / "guardian_status.json"
    assert guard.GUARD_LOCK_FILE == backend_root / "run" / "forum_acquisition_guard.lock"

    assert onboarding_worker.BACKEND_ROOT == backend_root
    assert onboarding_worker.STATUS_FILE == backend_root / "data" / "service_marketplaces" / "platform_onboarding_worker_status.json"
    assert onboarding_worker.WORKER_LOCK_FILE == backend_root / "run" / "platform_onboarding_worker.lock"
    assert onboarding_worker.ASSISTANT_SCRIPT == str(backend_root / "tools" / "marketplace_browser_assistant.py")

    assert certifier_runner.ROOT == backend_root
    assert certifier.ROOT == backend_root


def _dynamic_goods_platform() -> list[dict]:
    return [{
        "key": "disc_dynamic_goods",
        "url": "https://dynamic.example/classifieds",
        "channel_type": "forum",
        "enabled_for_outreach": True,
        "publication_surfaces": [{"niches": ["goods"]}],
    }]


def test_dynamic_allowed_aggregate_without_allowed_inspection_does_not_inflate_reserve():
    rule = {
        "decision": "allowed",
        "requirements": ["use_exact_discovered_goods_surface_only"],
        "inspections": [{"decision": "review", "reason": "rules_ambiguous"}],
    }
    with (
        patch.object(guard.marketplace, "list_platforms", return_value=_dynamic_goods_platform()),
        patch.object(guard.marketplace, "registration_plan", return_value=[]),
        patch.object(guard.platform_rules, "latest", return_value=rule),
        patch.object(guard, "_active_order_targets_by_format", return_value={"goods": 0, "services": 0}),
    ):
        snap = guard._strategic_reserve_snapshot()
    assert snap["formats"]["goods"]["allowed_unique_sites"] == 0
    assert snap["allowed_unique_sites_total"] == 0


def test_dynamic_allowed_surface_counts_after_explicit_allowed_inspection():
    rule = {
        "decision": "allowed",
        "requirements": ["use_exact_discovered_goods_surface_only"],
        "inspections": [{"decision": "allowed", "reason": "commercial_surface_verified"}],
    }
    with (
        patch.object(guard.marketplace, "list_platforms", return_value=_dynamic_goods_platform()),
        patch.object(guard.marketplace, "registration_plan", return_value=[]),
        patch.object(guard.platform_rules, "latest", return_value=rule),
        patch.object(guard, "_active_order_targets_by_format", return_value={"goods": 0, "services": 0}),
    ):
        snap = guard._strategic_reserve_snapshot()
    assert snap["formats"]["goods"]["allowed_unique_sites"] == 1
    assert snap["allowed_unique_sites_total"] == 1



def test_identity_and_business_email_checkpoints_are_bootstrap_not_terminal():
    for checkpoint in (
        "business_email_required",
        "real_identity_required",
        "business_email_and_real_identity_required",
    ):
        row = {"status": "blocked", "checkpoint": checkpoint}
        assert not service_marketplace.registration_is_terminally_blocked(row)
        assert checkpoint in service_marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS


def test_default_manual_registration_belongs_to_employee_bootstrap_queue():
    platforms = [{
        "key": "verified_forum",
        "name": "Verified forum",
        "url": "https://verified.example/",
        "channel_type": "forum",
        "enabled_for_outreach": True,
        "publication_ready": False,
        "priority": 1,
    }]
    registrations = [{
        "platform": "verified_forum",
        "status": "not_registered",
        "checkpoint": "manual_verification",
    }]
    with (
        patch.object(service_marketplace, "list_platforms", return_value=platforms),
        patch.object(service_marketplace, "registration_plan", return_value=registrations),
    ):
        queue = service_marketplace.platform_bootstrap_queue()
    assert queue["needed"] == 1
    assert queue["owner_action_required"] is False
    assert queue["executor_role"] == "platform_onboarding_worker"
    assert queue["items"][0]["checkpoint"] == "manual_verification"
    assert queue["items"][0]["owner_action_required"] is False
