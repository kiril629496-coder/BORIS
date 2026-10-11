from __future__ import annotations

import json
from pathlib import Path

from app.services import seo_checkpoint_center as center


def test_center_direct_listing_never_exposes_account_password(monkeypatch, tmp_path):
    queue = {
        "count": 1,
        "top_5_now": [{"platform": "pubdisc_otsiv_ru"}],
        "items": [{
            "platform": "pubdisc_otsiv_ru",
            "name": "Otsiv",
            "action_url": "https://otsiv.ru/org/create/",
            "checkpoint": "captcha_required",
            "action": "CAPTCHA",
            "target_sites": ["boris-ai.pro"],
            "iks": 100,
            "iks_tier": "medium",
            "human_effort_score": 500,
        }],
    }
    monkeypatch.setattr(center, "QUEUE_FILE", tmp_path / "queue.json")
    center.QUEUE_FILE.write_text(json.dumps(queue), encoding="utf-8")
    monkeypatch.setattr(
        center.marketplace,
        "registration_plan",
        lambda: [{
            "platform": "pubdisc_otsiv_ru",
            "email": "owner@example.test",
            "account_url": "https://otsiv.ru/org/create/",
        }],
    )
    monkeypatch.setattr(center, "_phone", lambda: "+70000000000")
    monkeypatch.setattr(center, "_direct_listing_states", lambda: {})
    public_payload = center.build_center_payload(include_secrets=False)
    assert public_payload["summary"]["entries"] == 1
    public_entry = public_payload["entries"][0]
    assert public_entry["flow_kind"] == "direct_listing"
    assert public_entry["autofill"]["website"] == ""
    assert public_entry["autofill"]["username"] == ""
    assert public_entry["autofill"]["password"] == ""
    assert public_entry["autofill"]["phone"] == ""
    assert public_entry["autofill"]["address"] == ""
    assert public_entry["autofill"]["birth_date"] == ""
    assert "website" in public_entry["autofill_fields"]

    secret_payload = center.build_center_payload(include_secrets=True)
    secret_entry = secret_payload["entries"][0]
    assert secret_entry["autofill"]["website"] == "https://boris-ai.pro/"
    assert secret_entry["autofill"]["business_name"] == "BORIS AI"
    assert secret_entry["autofill"]["username"] == ""
    assert secret_entry["autofill"]["password"] == ""


def test_center_account_onboarding_uses_same_server_credentials(monkeypatch, tmp_path):
    queue = {
        "count": 1,
        "top_5_now": [{"platform": "cyberforum_freelancers"}],
        "items": [{
            "platform": "cyberforum_freelancers",
            "name": "CyberForum",
            "action_url": "https://www.cyberforum.ru/register.php",
            "checkpoint": "captcha_required",
            "action": "CAPTCHA",
            "target_sites": ["boris-ai.pro", "systems-ai-oko.ru"],
            "iks": 3200,
            "iks_tier": "high",
            "human_effort_score": 900,
        }],
    }
    monkeypatch.setattr(center, "QUEUE_FILE", tmp_path / "queue.json")
    center.QUEUE_FILE.write_text(json.dumps(queue), encoding="utf-8")
    monkeypatch.setattr(center, "ACCOUNT_SECRETS", tmp_path / "secrets.json")
    monkeypatch.setattr(
        center.marketplace,
        "registration_plan",
        lambda: [{
            "platform": "cyberforum_freelancers",
            "email": "owner@example.test",
            "account_url": "https://www.cyberforum.ru/register.php",
        }],
    )
    monkeypatch.setattr(center, "_phone", lambda: "+70000000000")
    payload1 = center.build_center_payload(include_secrets=True)
    payload2 = center.build_center_payload(include_secrets=True)
    a1 = payload1["entries"][0]["autofill"]
    a2 = payload2["entries"][0]["autofill"]
    assert a1["username"] == "BORIS_AI"
    assert a1["password"]
    assert a1["password"] == a2["password"]
    assert a1["phone"] == "+70000000000"
    rows = json.loads(center.ACCOUNT_SECRETS.read_text(encoding="utf-8"))
    assert rows[0]["password"] == a1["password"]


def test_human_completed_account_verifies_before_unlock(monkeypatch):
    entry = {
        "id": "cyberforum_freelancers",
        "platform": "cyberforum_freelancers",
        "flow_kind": "account_onboarding",
        "target_site": None,
        "action_url": "https://www.cyberforum.ru/register.php",
    }
    monkeypatch.setattr(
        center,
        "build_center_payload",
        lambda: {"entries": [entry]},
    )
    verified = []
    monkeypatch.setattr(
        center,
        "_verify_registration",
        lambda platform: verified.append(platform) or {
            "ok": True,
            "status": "ready",
            "checkpoint": None,
        },
    )
    refreshed = []
    monkeypatch.setattr(center, "_refresh_owner_projects", lambda: refreshed.append(True))
    scheduled = []
    monkeypatch.setattr(
        center,
        "_schedule_guardian",
        lambda platform: scheduled.append(platform) or {
            "status": "scheduled",
            "platform": platform,
        },
    )
    monkeypatch.setattr(center, "_record_event", lambda event: None)
    result = center.human_completed(
        entry_id=entry["id"],
        platform=entry["platform"],
        target_site=None,
        page_url=entry["action_url"],
    )
    assert verified == ["cyberforum_freelancers"]
    assert refreshed == [True]
    assert scheduled == ["cyberforum_freelancers"]
    assert result["verification"]["status"] == "ready"
    assert result["verification"]["resume"]["status"] == "scheduled"


def test_human_completed_direct_listing_never_double_posts(monkeypatch):
    entry = {
        "id": "pubdisc_otsiv_ru::systems-ai-oko.ru",
        "platform": "pubdisc_otsiv_ru",
        "flow_kind": "direct_listing",
        "target_site": "systems-ai-oko.ru",
        "action_url": "https://otsiv.ru/org/create/",
    }
    monkeypatch.setattr(center, "build_center_payload", lambda: {"entries": [entry]})
    attempts = []
    monkeypatch.setattr(
        center.marketplace,
        "record_attempt",
        lambda **kwargs: attempts.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        center.marketplace,
        "upsert_registration",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct listing must not change global registration state")
        ),
    )
    monkeypatch.setattr(center, "_refresh_owner_projects", lambda: None)
    scheduled = []
    monkeypatch.setattr(
        center,
        "_schedule_guardian",
        lambda platform: scheduled.append(platform) or {
            "status": "scheduled",
            "platform": platform,
        },
    )
    monkeypatch.setattr(center, "_record_event", lambda event: None)
    result = center.human_completed(
        entry_id=entry["id"],
        platform=entry["platform"],
        target_site=entry["target_site"],
        page_url=entry["action_url"],
    )
    assert attempts[0]["action"] == "human_listing_submit"
    assert attempts[0]["status_value"] == "submitted_pending_verification"
    assert scheduled == ["pubdisc_otsiv_ru"]
    assert result["verification"]["status"] == "submitted_pending_verification"
    assert result["verification"]["resume"]["status"] == "scheduled"


def test_public_center_payload_redacts_credentials(monkeypatch, tmp_path):
    queue = {
        "count": 1,
        "top_5_now": [{"platform": "cyberforum_freelancers"}],
        "items": [{
            "platform": "cyberforum_freelancers",
            "name": "CyberForum",
            "action_url": "https://www.cyberforum.ru/register.php",
            "checkpoint": "captcha_required",
            "action": "CAPTCHA",
            "target_sites": ["boris-ai.pro"],
            "iks": 3200,
            "iks_tier": "high",
            "human_effort_score": 900,
        }],
    }
    monkeypatch.setattr(center, "QUEUE_FILE", tmp_path / "queue.json")
    center.QUEUE_FILE.write_text(json.dumps(queue), encoding="utf-8")
    monkeypatch.setattr(center, "ACCOUNT_SECRETS", tmp_path / "secrets.json")
    monkeypatch.setattr(
        center.marketplace,
        "registration_plan",
        lambda: [{
            "platform": "cyberforum_freelancers",
            "email": "owner@example.test",
            "account_url": "https://www.cyberforum.ru/register.php",
        }],
    )
    monkeypatch.setattr(center, "_phone", lambda: "+70000000000")
    payload = center.build_center_payload(include_secrets=False)
    entry = payload["entries"][0]
    assert payload["security"]["passwords_in_response"] is False
    assert entry["autofill"]["email"] == ""
    assert entry["autofill"]["username"] == ""
    assert entry["autofill"]["password"] == ""
    assert "password" in entry["autofill_fields"]
    assert not center.ACCOUNT_SECRETS.exists()


def test_otsovik_is_single_account_onboarding_entry(monkeypatch, tmp_path):
    queue = {
        "count": 1,
        "top_5_now": [{"platform": "pubdisc_otsovik_com"}],
        "items": [{
            "platform": "pubdisc_otsovik_com",
            "name": "Otsovik",
            "action_url": "https://otsovik.com/job/add_company.php",
            "checkpoint": "captcha_required",
            "action": "CAPTCHA",
            "target_sites": list(center.OWNER_SITES),
            "iks": 280,
            "iks_tier": "medium",
            "human_effort_score": 500,
        }],
    }
    monkeypatch.setattr(center, "QUEUE_FILE", tmp_path / "queue.json")
    center.QUEUE_FILE.write_text(json.dumps(queue), encoding="utf-8")
    monkeypatch.setattr(center, "ACCOUNT_SECRETS", tmp_path / "secrets.json")
    monkeypatch.setattr(
        center.marketplace,
        "registration_plan",
        lambda: [{
            "platform": "pubdisc_otsovik_com",
            "email": "owner@example.test",
            "account_url": "https://otsovik.com/job/add_company.php",
        }],
    )
    monkeypatch.setattr(center, "_phone", lambda: "")
    payload = center.build_center_payload(include_secrets=True)
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["flow_kind"] == "account_onboarding"
    assert entry["target_site"] is None
    assert entry["autofill"]["email"] == "noreply@boris-ai.pro"
    assert entry["autofill"]["password"]
    rows = json.loads(center.ACCOUNT_SECRETS.read_text(encoding="utf-8"))
    assert rows[0]["email"] == "noreply@boris-ai.pro"


def test_direct_listing_pending_hides_only_completed_site(monkeypatch, tmp_path):
    queue = {
        "count": 1,
        "top_5_now": [{"platform": "pubdisc_otsiv_ru"}],
        "items": [{
            "platform": "pubdisc_otsiv_ru",
            "name": "Otsiv",
            "action_url": "https://otsiv.ru/org/create/",
            "checkpoint": "captcha_required",
            "action": "CAPTCHA",
            "target_sites": [
                "boris-ai.pro",
                "systems-ai-oko.ru",
                "avito-specialist.ru",
            ],
            "iks": 120,
            "iks_tier": "medium",
            "human_effort_score": 500,
        }],
    }
    monkeypatch.setattr(center, "QUEUE_FILE", tmp_path / "queue.json")
    center.QUEUE_FILE.write_text(json.dumps(queue), encoding="utf-8")
    monkeypatch.setattr(
        center.marketplace,
        "registration_plan",
        lambda: [{
            "platform": "pubdisc_otsiv_ru",
            "email": "owner@example.test",
            "account_url": "https://otsiv.ru/org/create/",
        }],
    )
    monkeypatch.setattr(center, "_phone", lambda: "")
    monkeypatch.setattr(
        center,
        "_direct_listing_states",
        lambda: {
            ("pubdisc_otsiv_ru", "https://boris-ai.pro"): {
                "status": "submitted_pending_verification"
            }
        },
    )
    payload = center.build_center_payload()
    ids = {x["id"] for x in payload["entries"]}
    assert "pubdisc_otsiv_ru::boris-ai.pro" not in ids
    assert "pubdisc_otsiv_ru::systems-ai-oko.ru" in ids
    assert "pubdisc_otsiv_ru::avito-specialist.ru" in ids
    assert payload["summary"]["direct_listing_pending"] == 1


def test_checkpoint_center_api_routes_are_owner_only():
    from app.api import crowd_seo as crowd_api

    expected = {
        "/api/prospecting/crowd-seo/checkpoint-center",
        "/api/prospecting/crowd-seo/checkpoint-center/extension-config",
        "/api/prospecting/crowd-seo/checkpoint-center/complete",
    }
    found = {}
    for route in crowd_api.router.routes:
        path = getattr(route, "path", "")
        if path not in expected:
            continue
        calls = {
            getattr(dep, "call", None)
            for dep in getattr(getattr(route, "dependant", None), "dependencies", [])
        }
        found[path] = calls
    assert set(found) == expected
    for path, calls in found.items():
        assert crowd_api.require_owner in calls, path
