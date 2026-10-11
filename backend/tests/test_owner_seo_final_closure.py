from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import crowd_seo, publication_discovery
from tools import forum_acquisition_guard as guard


def test_systems_target_pages_never_fall_back_to_old_domain():
    project = {
        "site": "https://systems-ai-oko.ru/",
        "domain": "systems-ai-oko.ru",
        "niche_label": "IT",
        "target_pages": [{"url": "https://systems-ai-oko.ru/", "label": "Home"}],
        "campaign_profile": {
            "primary_url": "https://boris-ai.pro/software-dev/",
            "posts_ru": [
                {
                    "target_url": "https://boris-ai.pro/software-dev/services/crm-development",
                    "title": "Old CRM",
                }
            ],
        },
    }
    pages = crowd_seo._effective_target_pages(project)
    assert pages
    assert all("systems-ai-oko.ru" in row["url"] for row in pages)
    assert all("boris-ai.pro/software-dev" not in row["url"] for row in pages)


def test_weighted_same_site_targets_are_used():
    project = {
        "site": "https://systems-ai-oko.ru/",
        "domain": "systems-ai-oko.ru",
        "niche_label": "IT",
        "campaign_profile": {
            "seo_target_pages": [
                {"url": "https://systems-ai-oko.ru/", "label": "Home", "weight": 4},
                {"url": "https://systems-ai-oko.ru/services/telegram-bots", "label": "Bots", "weight": 2},
                {"url": "https://wrong.example/", "label": "Wrong", "weight": 20},
            ]
        },
    }
    pages = crowd_seo._effective_target_pages(project)
    assert [x["url"] for x in pages].count("https://systems-ai-oko.ru/") == 4
    assert [x["url"] for x in pages].count("https://systems-ai-oko.ru/services/telegram-bots") == 2
    assert not any("wrong.example" in x["url"] for x in pages)


def test_anchor_rotation_is_60_20_15_5_for_20_slots():
    modes = [
        crowd_seo._anchor_for_slot(i, "https://example.com/", "crm automation", "example.com")[0]
        for i in range(20)
    ]
    assert modes.count("безанкорная") == 12
    assert modes.count("брендовая") == 4
    assert modes.count("частичный анкор") == 3
    assert modes.count("точный анкор") == 1


def test_discovery_rejects_informational_action_words():
    score, evidence = publication_discovery._authoring_score(
        "https://ru.wikipedia.org/wiki/ADD",
        "ADD — Википедия",
        "Добавить — это термин",
    )
    assert "authoring_route" not in evidence

    score, evidence = publication_discovery._authoring_score(
        "https://example.com/articles/dobavit-kompaniyu-v-katalog",
        "Добавить компанию в каталог: инструкция",
        "Рассказываем как добавить компанию",
    )
    assert "authoring_route" not in evidence


def test_discovery_accepts_real_root_submit_routes():
    for url in (
        "https://launch.ignlab.net/submit.html",
        "https://www.brownbook.net/add-business",
        "https://example.com/submit/",
    ):
        score, evidence = publication_discovery._authoring_score(
            url,
            "Submit your tool",
            "Direct submission form",
        )
        assert score >= 4
        assert "authoring_route" in evidence


def test_verified_link_watch_uses_1_3_7_14_30_day_milestones():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)

    placement = {
        "publication_url": "https://donor.example/post",
        "status": "verified",
        "published_at": started.isoformat(),
        "checked_at": started.isoformat(),
    }
    assert guard._crowd_link_due(placement, started + timedelta(days=1, minutes=1))

    placement["checked_at"] = (started + timedelta(days=1, minutes=2)).isoformat()
    assert not guard._crowd_link_due(placement, started + timedelta(days=2))
    assert guard._crowd_link_due(placement, started + timedelta(days=3, minutes=1))

    placement["checked_at"] = (started + timedelta(days=14, minutes=1)).isoformat()
    assert not guard._crowd_link_due(placement, started + timedelta(days=20))
    assert guard._crowd_link_due(placement, started + timedelta(days=30, minutes=1))


def test_direct_zero_cost_form_rule_unlock_requires_form_website_and_free_copy(monkeypatch):
    monkeypatch.setattr(
        __import__("app.services.platform_rules", fromlist=["_publication_discovery_authoring_evidence"]),
        "_publication_discovery_authoring_evidence",
        lambda key, inspections: {"found": True, "candidate": {}, "authoring_url": "https://example.com/submit"},
    )
    from app.services import platform_rules
    inspections = [{
        "text_excerpt": "Submit your SaaS free, no account needed. Every submission is reviewed.",
        "form_profile": {"forms": 1, "has_website": True, "has_captcha": False, "has_checkbox": False},
        "evidence": {"paid": [], "prohibited": [], "links": []},
    }]
    decision, requirements = platform_rules._direct_zero_cost_submission_override(
        "pubdisc_example_com", inspections, "review", []
    )
    assert decision == "allowed"
    assert "free_submission_form_verified" in requirements
    assert "website_field_verified" in requirements


def test_direct_zero_cost_form_never_overrides_paid_or_prohibited(monkeypatch):
    monkeypatch.setattr(
        __import__("app.services.platform_rules", fromlist=["_publication_discovery_authoring_evidence"]),
        "_publication_discovery_authoring_evidence",
        lambda key, inspections: {"found": True, "candidate": {}, "authoring_url": "https://example.com/submit"},
    )
    from app.services import platform_rules
    inspections = [{
        "text_excerpt": "Submit free",
        "form_profile": {"forms": 1, "has_website": True, "has_captcha": False, "has_checkbox": False},
        "evidence": {"paid": ["paid advertising"], "prohibited": [], "links": []},
    }]
    decision, requirements = platform_rules._direct_zero_cost_submission_override(
        "pubdisc_example_com", inspections, "blocked", []
    )
    assert decision == "blocked"


def test_direct_zero_cost_form_records_captcha_as_human_checkpoint(monkeypatch):
    monkeypatch.setattr(
        __import__("app.services.platform_rules", fromlist=["_publication_discovery_authoring_evidence"]),
        "_publication_discovery_authoring_evidence",
        lambda key, inspections: {"found": True, "candidate": {}, "authoring_url": "https://example.com/submit"},
    )
    from app.services import platform_rules
    inspections = [{
        "text_excerpt": "Free listing submission",
        "form_profile": {"forms": 1, "has_website": True, "has_captcha": True, "has_checkbox": True},
        "evidence": {"paid": [], "prohibited": [], "links": []},
    }]
    decision, requirements = platform_rules._direct_zero_cost_submission_override(
        "pubdisc_example_com", inspections, "review", []
    )
    assert decision == "allowed"
    assert "captcha_checkpoint_may_require_human" in requirements
    assert "terms_or_consent_may_require_human" in requirements


def test_vendor_directory_surface_is_it_only():
    from app.services import service_marketplace
    candidate = {
        "domain": "launch.example",
        "url": "https://launch.example/submit",
        "title": "Submit SaaS",
        "snippet": "Submit your SaaS free",
        "kind": "vendor_directory",
        "authoring_evidence": ["authoring_route"],
    }
    surfaces = service_marketplace._publication_discovery_surface(candidate)
    assert len(surfaces) == 1
    assert surfaces[0]["niches"] == ["it"]


def test_vendor_directory_matches_it_but_not_avito_marketing():
    from app.services import service_marketplace
    candidate = {
        "domain": "launch.example",
        "url": "https://launch.example/submit",
        "title": "Submit SaaS",
        "snippet": "Submit your SaaS free",
        "kind": "vendor_directory",
        "authoring_evidence": ["authoring_route"],
    }
    surface = service_marketplace._publication_discovery_surface(candidate)[0]
    assert crowd_seo._surface_matches_project(
        surface, "it", ["разработка", "saas", "crm"], "services"
    )
    assert not crowd_seo._surface_matches_project(
        surface, "marketing", ["авитолог", "продвижение на авито", "реклама на авито"], "services"
    )


def test_structured_keyword_metadata_allows_clean_avito_copy():
    from app.services import service_marketplace
    draft = {
        "title": "Услуги авитолога и продвижение на Авито",
        "text": "Помогаю выстроить продвижение бизнеса на Авито. Подробнее: https://avito-specialist.ru/",
        "site_url": "https://avito-specialist.ru/",
        "target_url": "https://avito-specialist.ru/",
        "keyword_phrases": [
            "авитолог", "услуги авитолога", "продвижение на авито",
            "реклама на авито", "ведение авито", "аудит авито",
            "авито под ключ", "объявления авито", "настройка авито",
            "увеличение заявок с авито",
        ],
    }
    gate = service_marketplace.validate_publication_content(draft)
    assert gate["ok"] is True
    assert gate["family"] == "avito_service"


def test_structured_keyword_metadata_allows_systems_same_host_development_copy():
    from app.services import service_marketplace
    draft = {
        "title": "Разработка Telegram-бота для бизнеса",
        "text": "Разрабатываем Telegram-боты с CRM и AI. Подробнее: https://systems-ai-oko.ru/services/telegram-bots",
        "site_url": "https://systems-ai-oko.ru/",
        "target_url": "https://systems-ai-oko.ru/services/telegram-bots",
        "keyword_phrases": [
            "разработка Telegram бота", "Telegram бот для бизнеса", "Telegram бот CRM",
            "AI Telegram bot", "разработка ПО", "разработка SaaS",
            "разработка CRM", "AI автоматизация бизнеса", "API интеграция",
            "разработка веб-приложений",
        ],
    }
    gate = service_marketplace.validate_publication_content(draft)
    assert gate["ok"] is True
    assert gate["family"] == "development"


def test_publication_content_blocks_cross_host_and_retired_development_target():
    from app.services import service_marketplace
    keywords = [f"ключ {i}" for i in range(10)]
    cross_host = {
        "title": "Разработка ПО",
        "text": "Разработка под заказ. https://boris-ai.pro/",
        "site_url": "https://systems-ai-oko.ru/",
        "target_url": "https://boris-ai.pro/",
        "keyword_phrases": keywords,
    }
    assert service_marketplace.validate_publication_content(cross_host)["reason"] == "target_host_mismatch"

    retired = {
        "title": "Разработка ПО",
        "text": "Разработка под заказ. https://boris-ai.pro/software-dev/services/crm-development",
        "site_url": "https://boris-ai.pro/",
        "target_url": "https://boris-ai.pro/software-dev/services/crm-development",
        "keyword_phrases": keywords,
    }
    assert service_marketplace.validate_publication_content(retired)["reason"] == "retired_software_dev_target_forbidden"


def test_final_publish_gate_rejects_cross_project_host():
    project = {"site": "https://systems-ai-oko.ru/"}
    placement = {"target_url": "https://boris-ai.pro/"}
    drafts = [{
        "id": "draft-cross-host",
        "status": "approved",
        "site_url": "https://systems-ai-oko.ru/",
        "target_url": "https://boris-ai.pro/",
        "text": "Подробнее: https://boris-ai.pro/",
    }]
    from unittest.mock import patch
    with patch.object(guard.marketplace, "list_drafts", return_value=drafts):
        assert not guard._development_draft_target_safe(project, placement, "draft-cross-host")


def test_keyword_phrases_are_structured_not_visible_for_generic_avito_draft():
    project = {
        "site": "https://avito-specialist.ru/",
        "domain": "avito-specialist.ru",
        "niche": "marketing",
        "niche_label": "маркетинг и реклама",
        "keywords": [
            "авитолог", "продвижение на авито", "реклама на авито",
            "ведение авито", "аудит авито", "авито под ключ",
            "объявления авито", "увеличение заявок с авито",
        ],
    }
    placement = {
        "keyword": "авитолог",
        "target_url": "https://avito-specialist.ru/",
        "anchor": "https://avito-specialist.ru/",
        "link_mode": "безанкорная",
        "surface_language": "ru",
        "surface_name": "Маркетинг",
        "platform_name": "Test",
    }
    phrases = crowd_seo._draft_keyword_phrases(project, placement)
    _, text = crowd_seo._render_placement_text(project, placement, 1)
    assert 10 <= len(phrases) <= 20
    assert "Ключевые фразы:" not in text


def test_math_antibot_is_registered_as_distinct_human_checkpoint():
    from app.services import service_marketplace
    assert "math_antibot_required" in service_marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
    assert "math_antibot_required" in service_marketplace.INTERACTIVE_BROWSER_CHECKPOINTS


def test_verified_external_coverage_counts_unique_donors_and_excludes_historical_redirects(tmp_path, monkeypatch):
    publications = [
        {
            "platform": "pubdisc_aoyii_com",
            "url": "https://www.aoyii.com/tool/boris-ai/",
            "site_url": "https://boris-ai.pro/",
            "verified": True,
            "link_present": True,
            "verification": {
                "link_present": True,
                "indexable": True,
                "counts_toward_new_direct_60": True,
            },
        },
        {
            "platform": "pubdisc_aoyii_com",
            "url": "https://www.aoyii.com/en/tool/boris-ai/",
            "site_url": "https://boris-ai.pro/",
            "verified": True,
            "link_present": True,
            "verification": {
                "link_present": True,
                "indexable": True,
                "counts_toward_new_direct_60": True,
            },
        },
        {
            "platform": "pubdisc_pdfcatalog_ru",
            "url": "https://pdfcatalog.ru/katalog-statey/boris-ai.html",
            "site_url": "https://boris-ai.pro/software-dev/",
            "verified": True,
            "link_present": True,
            "verification": {
                "link_present": True,
                "indexable": True,
                "counts_toward_new_direct_60": False,
            },
        },
    ]
    monkeypatch.setattr(crowd_seo.marketplace, "list_publications", lambda: publications)
    monkeypatch.setattr(crowd_seo, "GUEST_CONFIRMED_DIR", tmp_path / "no-sidecars")
    project = {
        "site": "https://boris-ai.pro/",
        "plan_mode": "contract",
        "target_count": 60,
        "bonus_count": 0,
        "placements": [],
    }
    summary = crowd_seo._placement_summary(project)
    assert summary["verified"] == 1
    assert summary["verified_referring_domains"] == ["aoyii.com"]


def test_pdfcatalog_reconciler_uses_exact_target_not_prefix(monkeypatch):
    from app.services import seo_moderation_reconciler as reconciler
    html = """
    <div class="stdl"><div class="stdlt">
      https://boris-ai.pro/software-dev/ -
      <a href="/katalog-statey/old-dev.html">Old development</a>
    </div></div>
    <div class="stdl"><div class="stdlt">
      https://boris-ai.pro/ -
      <a href="/katalog-statey/boris.html">BORIS</a>
    </div></div>
    """
    monkeypatch.setattr(
        reconciler,
        "_fetch",
        lambda url: (200, "https://pdfcatalog.ru/", html),
    )
    reconciler._pdfcatalog_publications.cache_clear if hasattr(reconciler._pdfcatalog_publications, "cache_clear") else None
    rows = reconciler._pdfcatalog_publications()
    assert rows["https://boris-ai.pro/software-dev"] .endswith("/old-dev.html")
    assert rows["https://boris-ai.pro"].endswith("/boris.html")


def test_moderation_reconciler_marks_only_existing_public_url(monkeypatch):
    from app.services import seo_moderation_reconciler as reconciler
    project = {
        "id": "crowd_test",
        "placements": [{
            "id": "p1",
            "status": "publication_pending_verification",
            "platform": "pubdisc_pdfcatalog_ru",
            "marketplace_draft_id": "d1",
            "target_url": "https://boris-ai.pro/",
        }],
    }
    monkeypatch.setattr(reconciler.crowd_seo, "get_project", lambda pid: project)
    monkeypatch.setattr(
        reconciler,
        "_pdfcatalog_publications",
        lambda: {"https://boris-ai.pro": "https://pdfcatalog.ru/katalog-statey/boris.html"},
    )
    posted = []
    monkeypatch.setattr(
        reconciler.marketplace,
        "mark_posted",
        lambda draft_id, url: posted.append((draft_id, url)) or {"url": url},
    )
    synced = []
    monkeypatch.setattr(reconciler.crowd_seo, "sync_publications", lambda pid: synced.append(pid) or {"changed": 1})
    result = reconciler.reconcile_project("crowd_test")
    assert result["reconciled"] == 1
    assert posted == [("d1", "https://pdfcatalog.ru/katalog-statey/boris.html")]
    assert synced == ["crowd_test"]


def test_external_moderation_reconciler_persists_only_verified_public_link(monkeypatch, tmp_path):
    from app.services import external_moderation_reconciler as reconciler
    monkeypatch.setattr(reconciler, "STATE", tmp_path / "watch.json")
    pending = [{
        "platform": "pubdisc_test_example",
        "row": {"created_at": "2026-09-27T00:00:00+00:00"},
        "meta": {"domain": "example.com", "target_url": "https://boris-ai.pro/"},
        "target_url": "https://boris-ai.pro/",
        "submitted_at": "2026-09-27T00:00:00+00:00",
    }]
    monkeypatch.setattr(reconciler, "_pending_latest", lambda: pending)
    monkeypatch.setattr(reconciler, "_candidate_urls", lambda item, session: ["https://example.com/boris-ai"])
    monkeypatch.setattr(
        reconciler,
        "_verify_page",
        lambda url, target, session: {
            "ok": True,
            "publication_url": url,
            "http_status": 200,
            "link_present": True,
            "href": target,
            "rel": ["nofollow"],
            "indexable": True,
            "canonical": url,
            "title": "BORIS AI",
        },
    )
    saved = []
    monkeypatch.setattr(
        reconciler.marketplace,
        "upsert_verified_external_publication",
        lambda **kwargs: saved.append(kwargs) or {"created": True, **kwargs},
    )
    attempts = []
    monkeypatch.setattr(
        reconciler.marketplace,
        "record_attempt",
        lambda **kwargs: attempts.append(kwargs) or kwargs,
    )
    result = reconciler.run(max_checks=1, force=True)
    assert result["verified"] == 1
    assert saved[0]["target_url"] == "https://boris-ai.pro/"
    assert saved[0]["verification"]["counts_toward_new_direct_60"] is True
    assert attempts[0]["status_value"] == "verified"


def test_external_moderation_reconciler_cooldown_skips_recent_check(monkeypatch, tmp_path):
    from app.services import external_moderation_reconciler as reconciler
    state_path = tmp_path / "watch.json"
    now = datetime.now(timezone.utc)
    state_path.write_text(json.dumps({
        "version": 1,
        "platforms": {
            "pubdisc_test_example": {
                "last_checked_at": now.isoformat(),
                "state": "pending",
            }
        }
    }), encoding="utf-8")
    monkeypatch.setattr(reconciler, "STATE", state_path)
    monkeypatch.setattr(reconciler, "_now", lambda: now)
    monkeypatch.setattr(reconciler, "_pending_latest", lambda: [{
        "platform": "pubdisc_test_example",
        "row": {"created_at": "2026-09-27T00:00:00+00:00"},
        "meta": {"domain": "example.com", "target_url": "https://boris-ai.pro/"},
        "target_url": "https://boris-ai.pro/",
        "submitted_at": "2026-09-27T00:00:00+00:00",
    }])
    result = reconciler.run(max_checks=1)
    assert result["checked"] == 0
    assert result["verified"] == 0


def test_rule_form_profile_recognizes_site_field_variants():
    from app.services import platform_rules
    html = """
    <form>
      <input name="hlblock_6_uf_site" type="text">
      <input name="company[site]" type="text">
    </form>
    """
    profile = platform_rules._page_form_profile(html)
    assert profile["has_website"] is True


def test_publication_discovery_surface_honors_configured_niches():
    from app.services import service_marketplace
    candidate = {
        "domain": "agency.example",
        "url": "https://agency.example/web-agencies/add",
        "title": "Submit agency",
        "snippet": "Agency directory",
        "kind": "services",
        "niches": ["it"],
        "language": "en",
        "authoring_evidence": ["authoring_route"],
    }
    surfaces = service_marketplace._publication_discovery_surface(candidate)
    assert len(surfaces) == 1
    assert surfaces[0]["niches"] == ["it"]
    assert surfaces[0]["language"] == "en"


def test_curated_positive_low_iks_seed_survives_as_diversity_candidate(monkeypatch, tmp_path):
    from app.services import publication_discovery as discovery
    seed = {
        "domain": "low-curated.example",
        "url": "https://low-curated.example/add-business",
        "title": "Add Business",
        "snippet": "Free business directory",
        "kind": "catalog",
    }
    monkeypatch.setattr(discovery, "CURATED_STRONG_OWN_LISTING_SEEDS", (seed,))
    monkeypatch.setattr(discovery, "DATA_FILE", tmp_path / "publication_discovery.json")
    monkeypatch.setattr(discovery, "QUERY_POOL_VERSION", 99991)
    monkeypatch.setattr(discovery, "_query_pool", lambda: ["test query"])
    monkeypatch.setattr(
        discovery.forum_discovery,
        "_search_all",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(discovery.forum_quality, "by_domain", lambda: {})
    monkeypatch.setattr(
        discovery.forum_quality,
        "fetch_iks",
        lambda domain: {"domain": domain, "iks": 40, "tier": "low", "source": "test"},
    )
    result = discovery.discover(max_queries=1, per_query=1, workers=1)
    found = {x.get("domain"): x for x in result.get("items") or []}
    assert "low-curated.example" in found
    assert found["low-curated.example"]["iks_tier"] == "low"
    assert "curated_direct_verification" in found["low-curated.example"]["search_engines"]


def test_no_duplicate_site_excludes_historical_redirected_platform(monkeypatch):
    project = {
        "id": "crowd_systems",
        "site": "https://systems-ai-oko.ru/",
        "placements": [],
    }
    monkeypatch.setattr(crowd_seo.marketplace, "list_publications", lambda: [])
    monkeypatch.setattr(
        crowd_seo.marketplace,
        "list_attempts",
        lambda limit=1000: [{
            "platform": "pubdisc_pdfcatalog_ru",
            "action": "verify_publication",
            "status": "verified",
            "meta": {
                "classification": "historical_redirected",
                "project_id": "crowd_systems",
                "project_site": "https://systems-ai-oko.ru/",
                "linked_target": "https://boris-ai.pro/software-dev/",
                "redirect_target": "https://systems-ai-oko.ru/",
                "counts_toward_direct_contract": False,
            },
        }],
    )
    monkeypatch.setattr(
        crowd_seo.marketplace,
        "platform_policy",
        lambda platform: {
            "rule_requirements": ["no_duplicate_site"] if platform == "pubdisc_pdfcatalog_ru" else []
        },
    )
    matches = [
        {"platform": "pubdisc_pdfcatalog_ru", "surface_id": "listing"},
        {"platform": "other_catalog", "surface_id": "listing"},
    ]
    result = crowd_seo._usable_matches_for_project(project, matches)
    assert [x["platform"] for x in result] == ["other_catalog"]


def test_existing_listing_does_not_block_platform_without_no_duplicate_rule(monkeypatch):
    project = {
        "id": "crowd_systems",
        "site": "https://systems-ai-oko.ru/",
        "placements": [{
            "platform": "shared_forum",
            "surface_id": "services",
            "status": "verified",
            "publication_url": "https://shared.example/topic/1",
        }],
    }
    monkeypatch.setattr(crowd_seo.marketplace, "list_publications", lambda: [])
    monkeypatch.setattr(crowd_seo.marketplace, "list_attempts", lambda limit=1000: [])
    monkeypatch.setattr(
        crowd_seo.marketplace,
        "platform_policy",
        lambda platform: {"rule_requirements": []},
    )
    matches = [{"platform": "shared_forum", "surface_id": "another-service-board"}]
    result = crowd_seo._usable_matches_for_project(project, matches)
    assert result == matches


def test_otsovik_company_card_adapter_uses_exact_safe_fields():
    source = Path("/root/BORIS/backend/tools/marketplace_browser_assistant.py").read_text(encoding="utf-8")
    assert "OTSOVIK_COMPANY_CARD_ADAPTER_V1" in source
    assert "#HLBLOCK_6_UF_NAME" in source
    assert "#HLBLOCK_6_UF_DESCRIPTION" in source
    assert "#HLBLOCK_6_UF_SITE" in source
    assert "#HLBLOCK_6_UF_EMAIL" in source
    assert '#addcompanyformsend' in source
    adapter = source.split("OTSOVIK_COMPANY_CARD_ADAPTER_V1", 1)[1].split('else:\n            if platform in {"talkingcity_services"', 1)[0]
    assert "HLBLOCK_6_UF_CITY2_ID" not in adapter
    assert "HLBLOCK_6_UF_ADDRESS" not in adapter
