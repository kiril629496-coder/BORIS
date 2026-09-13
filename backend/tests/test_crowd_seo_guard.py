from __future__ import annotations

import json
import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import forum_acquisition_guard as guard
from tools import marketplace_browser_assistant as browser_assistant


def make_project(status: str = "needs_owner_approval") -> dict:
    return {
        "id": "crowd_test",
        "content_status": status,
        "guarantee_days": 30,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "placements": [
            {
                "id": "crowd_test_001",
                "platform": "forum_baza_1c",
                "status": "ready_to_publish",
                "marketplace_draft_id": "draft_test_001",
                "publication_url": None,
                "checked_at": None,
            }
        ],
    }


class CrowdSeoGuardTests(unittest.TestCase):

    def test_effective_target_pages_uses_specific_software_service_landings(self):
        project = {
            "site": "https://boris-ai.pro/software-dev/",
            "niche_label": "Разработка ПО",
            "campaign_profile": {
                "primary_url": "https://boris-ai.pro/software-dev/",
                "posts_ru": [
                    {"target_url": "https://boris-ai.pro/software-dev/services/saas-development", "title": "SaaS"},
                    {"target_url": "https://boris-ai.pro/software-dev/services/crm-development", "title": "CRM"},
                ],
            },
            "target_pages": [{"url": "https://boris-ai.pro/software-dev/", "label": "Разработка ПО"}],
        }
        rows = guard.crowd_seo._effective_target_pages(project)
        self.assertEqual([x["url"] for x in rows], [
            "https://boris-ai.pro/software-dev/services/saas-development",
            "https://boris-ai.pro/software-dev/services/crm-development",
        ])
        self.assertTrue(all("/software-dev/services/" in x["url"] for x in rows))

    def test_effective_target_pages_preserves_primary_url_for_generic_campaign(self):
        project = {
            "site": "https://example.test/",
            "campaign_profile": {"primary_url": "https://example.test/service", "brand": "Example"},
            "target_pages": [{"url": "https://example.test/old", "label": "Old"}],
        }
        self.assertEqual(
            guard.crowd_seo._effective_target_pages(project),
            [{"url": "https://example.test/service", "label": "Example"}],
        )

    def test_development_draft_target_gate_rejects_stale_root_copy(self):
        project = {"site": "https://boris-ai.pro/software-dev/"}
        placement = {"target_url": "https://boris-ai.pro/software-dev/services/saas-development"}
        stale = [{
            "id": "draft1", "status": "approved",
            "target_url": placement["target_url"],
            "text": "Подробнее: https://boris-ai.pro/",
        }]
        good = [{
            "id": "draft1", "status": "approved",
            "target_url": placement["target_url"],
            "text": f"Подробнее: {placement['target_url']}",
        }]
        with patch.object(guard.marketplace, "list_drafts", return_value=stale):
            self.assertFalse(guard._development_draft_target_safe(project, placement, "draft1"))
        with patch.object(guard.marketplace, "list_drafts", return_value=good):
            self.assertTrue(guard._development_draft_target_safe(project, placement, "draft1"))

    def test_rendered_development_placement_uses_exact_target_not_root(self):
        project = {
            "site": "https://boris-ai.pro/software-dev/",
            "domain": "boris-ai.pro",
            "niche_label": "IT и разработка",
            "offer_type": "services",
            "target_pages": [{
                "url": "https://boris-ai.pro/software-dev/services/saas-development",
                "label": "Разработка SaaS",
            }],
        }
        placement = {
            "target_url": "https://boris-ai.pro/software-dev/services/saas-development",
            "anchor": "https://boris-ai.pro/software-dev/services/saas-development",
            "link_mode": "безанкорная",
            "keyword": "разработка SaaS",
            "surface_language": "ru",
            "surface_name": "Услуги для бизнеса",
            "platform_name": "Test Forum",
        }
        _, text = guard.crowd_seo._render_placement_text(project, placement, 1)
        self.assertIn(placement["target_url"], text)
        self.assertNotEqual(placement["target_url"], "https://boris-ai.pro/")

    def test_campaign_profile_exact_target_beats_stale_keyword_and_variant(self):
        ip_url = "https://boris-ai.pro/software-dev/services/ip-telephony"
        project = {
            "site": "https://boris-ai.pro/software-dev/",
            "domain": "boris-ai.pro",
            "offer_type": "services",
            "campaign_profile": {
                "brand": "BORIS",
                "posts_ru": [
                    {
                        "cluster": "dev_saas",
                        "title": "Разработка SaaS-платформ под ключ",
                        "target_url": "https://boris-ai.pro/software-dev/services/saas-development",
                        "match_keywords": ["saas", "платформа"],
                        "keywords": [f"saas ключ {i}" for i in range(1, 13)],
                        "body": "Разрабатываем SaaS-платформы под заказ.",
                        "offer_family": "development",
                    },
                    {
                        "cluster": "dev_iptelephony",
                        "title": "Разработка и интеграция IP-телефонии",
                        "target_url": ip_url,
                        "match_keywords": ["sip", "asterisk", "телефония"],
                        "keywords": [f"телефония ключ {i}" for i in range(1, 13)],
                        "body": "Разрабатываем интеграции SIP/Asterisk с CRM под заказ.",
                        "offer_family": "development",
                    },
                ],
            },
        }
        # Stale routing hints deliberately point to SaaS. The exact landing is
        # still authoritative and must select the IP-telephony copy.
        placement = {
            "target_url": ip_url,
            "keyword": "saas платформа",
            "keyword_cluster": "dev_saas",
            "anchor": ip_url,
            "link_mode": "безанкорная",
            "surface_language": "ru",
            "surface_name": "Услуги",
            "platform_name": "Test Forum",
        }
        title, text = guard.crowd_seo._render_placement_text(project, placement, 1)
        self.assertIn("IP-телефонии", title)
        self.assertIn(ip_url, text)
        self.assertNotIn("SaaS-платформ", text)

    def test_campaign_profile_copy_preserves_keyword_footer_for_global_gate(self):
        target = "https://boris-ai.pro/software-dev/services/web-app-development"
        keywords = [f"разработка web ключ {i}" for i in range(1, 13)]
        project = {
            "site": "https://boris-ai.pro/software-dev/",
            "domain": "boris-ai.pro",
            "offer_type": "services",
            "campaign_profile": {
                "brand": "BORIS",
                "posts_ru": [{
                    "cluster": "dev_webapp",
                    "title": "Разработка веб-приложений под заказ",
                    "target_url": target,
                    "match_keywords": ["веб-разработка"],
                    "keywords": keywords,
                    "body": "Разрабатываем кастомные веб-приложения и MVP под заказ.",
                    "offer_family": "development",
                }],
            },
        }
        placement = {
            "target_url": target,
            "keyword": "веб-разработка",
            "keyword_cluster": "dev_webapp",
            "anchor": target,
            "link_mode": "безанкорная",
            "surface_language": "ru",
            "surface_name": "Программирование",
            "platform_name": "Test Forum",
        }
        title, text = guard.crowd_seo._render_placement_text(project, placement, 1)
        self.assertIn("\n\nКлючевые фразы: ", text)
        gate = guard.marketplace.validate_publication_content({
            "title": title,
            "text": text,
            "target_url": target,
        })
        self.assertTrue(gate["ok"], gate)
        self.assertGreaterEqual(gate["keyword_count"], 10)
        self.assertLessEqual(gate["keyword_count"], 20)

    def test_browser_assistant_blocks_bad_content_before_external_browser(self):
        draft = {
            "id": "draft_bad_content",
            "platform": "test_catalog",
            "status": "approved",
            "title": "Разработка SaaS под заказ",
            "text": "Создаём SaaS под заказ. https://boris-ai.pro/software-dev/services/saas-development",
            "target_url": "https://boris-ai.pro/software-dev/services/saas-development",
        }
        with (
            patch.object(browser_assistant.platform_rules, "inspect_platform", return_value={"decision": "allowed"}),
            patch.object(browser_assistant.platform_rules, "publish_gate", return_value={"allowed": True}),
            patch.object(browser_assistant.marketplace_svc, "get_platform", return_value=None),
            patch.object(browser_assistant, "_find_draft", return_value=draft),
            patch.object(browser_assistant.marketplace_svc, "record_attempt") as record,
            patch.object(browser_assistant, "sync_playwright", side_effect=AssertionError("external browser opened before content gate")),
        ):
            with self.assertRaisesRegex(SystemExit, "keyword_block_not_last"):
                browser_assistant.prepare_post(
                    "test_catalog",
                    "draft_bad_content",
                    headless=True,
                    publish=True,
                )
        self.assertEqual(record.call_args.kwargs["error_code"], "keyword_block_not_last")

    def test_registration_preflight_timeout_retries_after_two_hours(self):
        now = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
        fresh = {
            "status": "in_progress",
            "checkpoint": "preflight_timeout",
            "updated_at": (now - timedelta(minutes=30)).isoformat(),
        }
        stale = {
            "status": "in_progress",
            "checkpoint": "preflight_timeout",
            "updated_at": (now - timedelta(hours=3)).isoformat(),
        }
        self.assertFalse(guard._registration_retry_due(fresh, now))
        self.assertTrue(guard._registration_retry_due(stale, now))

    def test_known_forum_roots_without_forum_in_url_stay_forums(self):
        for key in ("homeidea", "itnull_services"):
            platform = guard.marketplace.get_platform(key)
            self.assertIsNotNone(platform)
            self.assertEqual(guard.marketplace._channel_type(platform), "forum")

    def test_nonforum_registration_checkpoint_blocks_publication_ready(self):
        platform = guard.marketplace.Platform(
            "board_test", "Board Test", "https://board.test/add", "direct_post",
            1, "услуги для бизнеса", "guest or registration", "test",
        )
        regs = [{
            "platform": "board_test",
            "status": "verification_required",
            "checkpoint": "terms_acceptance_required",
        }]
        with (
            patch.object(guard.marketplace, "all_platform_objects", return_value=[platform]),
            patch.object(guard.marketplace, "platform_policy", return_value={
                "free": True, "rule_decision": "allowed", "rule_requirements": []
            }),
            patch.object(guard.marketplace, "_load", return_value=regs),
            patch.object(guard.marketplace.forum_discovery if hasattr(guard.marketplace, "forum_discovery") else guard.forum_discovery, "list_candidates", return_value=[]),
        ):
            row = guard.marketplace.list_platforms()[0]
        self.assertTrue(row["enabled_for_outreach"])
        self.assertFalse(row["publication_ready"])
        self.assertEqual(row["registration_checkpoint"], "terms_acceptance_required")

    def test_nonforum_guest_surface_without_checkpoint_can_be_publication_ready(self):
        platform = guard.marketplace.Platform(
            "guest_board_test", "Guest Board", "https://guest.test/add", "direct_post",
            1, "услуги для бизнеса", "guest posting", "test",
        )
        regs = [{
            "platform": "guest_board_test",
            "status": "not_registered",
            "checkpoint": None,
        }]
        with (
            patch.object(guard.marketplace, "all_platform_objects", return_value=[platform]),
            patch.object(guard.marketplace, "platform_policy", return_value={
                "free": True, "rule_decision": "allowed", "rule_requirements": []
            }),
            patch.object(guard.marketplace, "_load", return_value=regs),
            patch.object(guard.forum_discovery, "list_candidates", return_value=[]),
        ):
            row = guard.marketplace.list_platforms()[0]
        self.assertTrue(row["publication_ready"])

    def test_pubdisc_authoring_gate_accepts_same_domain_new_route(self):
        candidate = {
            "domain": "pro.avada.shop",
            "url": "https://pro.avada.shop/razmestit-obyavlenie/",
        }
        inspections = [{
            "outbound_links": [
                "https://pro.avada.shop/rules/",
                "https://pro.avada.shop/new/",
            ]
        }]
        with patch.object(guard.publication_discovery, "load", return_value={"items": [candidate]}):
            row = guard.platform_rules._publication_discovery_authoring_evidence(
                "pubdisc_pro_avada_shop", inspections
            )
        self.assertTrue(row["found"])
        self.assertEqual(row["authoring_url"], "https://pro.avada.shop/new/")

    def test_pubdisc_authoring_gate_accepts_item_add_but_rejects_add_prefix_word(self):
        good = {"domain": "chance.ru", "url": "https://chance.ru/item/add"}
        bad = {"domain": "remontka.pro", "url": "https://remontka.pro/add-keyboard-layout-language-windows/"}
        with patch.object(guard.publication_discovery, "load", return_value={"items": [good]}):
            row = guard.platform_rules._publication_discovery_authoring_evidence(
                "pubdisc_chance_ru", []
            )
        self.assertTrue(row["found"])
        self.assertEqual(row["authoring_url"], "https://chance.ru/item/add")
        with patch.object(guard.publication_discovery, "load", return_value={"items": [bad]}):
            row = guard.platform_rules._publication_discovery_authoring_evidence(
                "pubdisc_remontka_pro", []
            )
        self.assertFalse(row["found"])

    def test_free_publication_text_is_not_misclassified_as_paid(self):
        row = guard.platform_rules.classify_rules(
            "Бесплатно разместить объявление об услугах. Публиковать объявления услуг можно бесплатно."
        )
        self.assertEqual(row["decision"], "allowed")
        self.assertEqual(row["evidence"]["paid"], [])

    def test_publication_discovery_requires_direct_authoring_signal(self):
        pub = guard.publication_discovery
        direct_score, direct_evidence = pub._authoring_score(
            "https://example.test/board/add/",
            "Добавить объявление бесплатно",
            "Разместите услугу для бизнеса",
        )
        article_score, article_evidence = pub._authoring_score(
            "https://example.test/blog/gde-razmestit-obyavlenie/",
            "Где разместить объявление: обзор площадок",
            "Статья о популярных досках объявлений",
        )
        self.assertGreaterEqual(direct_score, 4)
        self.assertIn("authoring_route", direct_evidence)
        translit_score, translit_evidence = pub._authoring_score(
            "https://cenotavr.test/dobavit-obyavlenie/index.html",
            "Добавить объявление",
            "Бесплатная доска",
        )
        self.assertGreaterEqual(translit_score, 4)
        self.assertIn("authoring_route", translit_evidence)
        self.assertLess(article_score, 4)
        self.assertIn("informational_path", article_evidence)
        editorial_score, editorial_evidence = pub._authoring_score(
            "https://projecto.pro/media/gde-besplatno-opublikovat-statyu/",
            "Где бесплатно опубликовать статью: 30+ популярных сайтов",
            "Подборка площадок для публикации",
        )
        self.assertLess(editorial_score, 4)
        self.assertIn("informational_text", editorial_evidence)
        vertical_score, vertical_evidence = pub._authoring_score(
            "https://promo.domclick.ru/razmestit-obyavlenie-o-prodazhe-nedvijimosty",
            "Разместить объявление о продаже недвижимости",
            "Опубликуйте объявление бесплатно",
        )
        self.assertLess(vertical_score, 4)
        self.assertIn("irrelevant_vertical", vertical_evidence)
        listicle_score, listicle_evidence = pub._authoring_score(
            "https://example.test/short/sajty-obyavlenij/",
            "Где можно разместить объявление бесплатно: список сайтов",
            "ТОП сайтов объявлений",
        )
        self.assertLess(listicle_score, 4)
        self.assertIn("informational_text", listicle_evidence)
        false_add_score, false_add_evidence = pub._authoring_score(
            "https://remontka.pro/add-keyboard-layout-language-windows/",
            "Как добавить язык ввода или раскладку клавиатуры",
            "Инструкция Windows",
        )
        self.assertLess(false_add_score, 4)
        self.assertNotIn("authoring_route", false_add_evidence)
        item_add_score, item_add_evidence = pub._authoring_score(
            "https://chance.ru/item/add",
            "Подать объявление бесплатно",
            "Разместить объявление без регистрации",
        )
        self.assertGreaterEqual(item_add_score, 4)
        self.assertIn("authoring_route", item_add_evidence)
        docs_score, docs_evidence = pub._authoring_score(
            "https://docs.google.com/presentation/create?hl=nl",
            "Google Presentations: sign in",
            "Create a presentation with your Google account",
        )
        self.assertLess(docs_score, 4)
        self.assertNotIn("authoring_route", docs_evidence)
        blog_score, blog_evidence = pub._authoring_score(
            "https://example.com/post/how-to-build-a-crm",
            "How to build a CRM",
            "An informational article",
        )
        self.assertLess(blog_score, 4)
        self.assertNotIn("authoring_route", blog_evidence)

    def test_publication_discovery_urgency_uses_autonomous_deficit(self):
        rows = [{
            "autonomous_deficit": 27,
            "discovery_deficit_after_bootstrap": 0,
            "placement_operational_deficit": 0,
        }]
        self.assertEqual(guard._publication_discovery_urgency(rows, 0), 27)
        self.assertEqual(guard._publication_discovery_urgency(rows, 3), 27)
        self.assertEqual(guard._publication_discovery_urgency([], 4), 4)

    def test_publication_discovery_schedule_refreshes_every_thirty_minutes_during_deficit(self):
        now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
        fresh = {"updated_at": (now - timedelta(minutes=20)).isoformat()}
        stale = {"updated_at": (now - timedelta(minutes=31)).isoformat()}
        self.assertFalse(guard._publication_discovery_schedule(fresh, 24, now=now)["due"])
        row = guard._publication_discovery_schedule(stale, 24, now=now)
        self.assertTrue(row["due"])
        self.assertEqual(row["interval_minutes"], 30)

    def test_publication_discovery_bounded_runner_uses_small_deficit_batch(self):
        payload={"strong_domains":2,"items":[]}
        proc=SimpleNamespace(returncode=0,stdout=json.dumps(payload,ensure_ascii=False)+"\n",stderr="")
        with patch.object(guard.subprocess,"run",return_value=proc) as run:
            row=guard._run_publication_discovery_bounded(24)
        self.assertEqual(row["strong_domains"],2)
        self.assertEqual(row["bounded_execution"]["max_queries"],guard.PUBLICATION_DISCOVERY_DEFICIT_QUERIES)
        self.assertEqual(run.call_args.kwargs["timeout"],guard.PUBLICATION_DISCOVERY_WALL_TIMEOUT_S)
        self.assertIn(str(guard.PUBLICATION_DISCOVERY_DEFICIT_QUERIES),run.call_args.args[0])

    def test_publication_discovery_iks_cache_avoids_repeat_network_fetch(self):
        pub=guard.publication_discovery
        prior={
            "updated_at":"2026-09-10T19:00:00+00:00",
            "next_query_offset":0,
            "items":[{
                "domain":"gde.ru","url":"https://gde.ru/post",
                "title":"Подать бесплатное объявление",
                "snippet":"Добавить объявление для бизнеса",
                "kind":"classified","authoring_score":10,
                "authoring_evidence":["authoring_route","authoring_title"],
                "queries":[],"search_engines":[],
                "iks":2790,"iks_tier":"high","iks_source":"cached",
            }],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            data=Path(tmpdir)/"publication_discovery.json"
            data.write_text(json.dumps(prior,ensure_ascii=False),encoding="utf-8")
            with (
                patch.object(pub,"DATA_FILE",data),
                patch.object(pub,"CURATED_STRONG_OWN_LISTING_SEEDS",()),
                patch.object(pub.forum_discovery,"_search_all",return_value=([],[])),
                patch.object(pub.forum_quality,"by_domain",return_value={}),
                patch.object(pub.forum_quality,"fetch_iks") as fetch,
            ):
                row=pub.discover(max_queries=1,per_query=1,workers=1)
        self.assertEqual(row["strong_domains"],1)
        self.assertEqual(row["items"][0]["domain"],"gde.ru")
        fetch.assert_not_called()

    def test_publication_discovery_query_pool_rotates_without_repeating_first_batch(self):
        pool = guard.publication_discovery._query_pool()
        self.assertEqual(len(pool), len(guard.publication_discovery.SEARCH_PATTERNS) * len(guard.publication_discovery.TERMS))
        self.assertGreater(len(pool), 18)
        self.assertNotEqual(pool[:18], pool[18:36])

    def test_publication_discovery_keeps_zero_friction_queries(self):
        pool = "\n".join(guard.publication_discovery._query_pool()).lower()
        self.assertIn('"без регистрации" "добавить сайт"', pool)
        self.assertIn('"без капчи" "добавить сайт"', pool)
        self.assertIn('"no registration" "submit website"', pool)
        self.assertIn('"прямая ссылка" "добавить сайт"', pool)

    def test_publication_discovery_full_cycle_persists_rotating_state(self):
        pub = guard.publication_discovery
        hit = {
            "title": "Подать бесплатное объявление",
            "url": "https://board.example/post",
            "domain": "board.example",
            "snippet": "Добавить объявление для бизнеса",
            "engine": "bing",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(pub, "DATA_FILE", Path(tmpdir) / "publication_discovery.json"),
                patch.object(pub.forum_discovery, "_search_all", return_value=([hit], [])) as search,
                patch.object(pub.forum_quality, "fetch_iks", return_value={"iks": 500, "tier": "medium", "source": "test"}),
            ):
                result = pub.discover(max_queries=1, per_query=2, workers=1)
        self.assertEqual(result["queries"], 1)
        self.assertEqual(result["query_offset"], 0)
        self.assertEqual(result["next_query_offset"], 1)
        self.assertEqual(result["query_pool_size"], len(pub._query_pool()))
        self.assertEqual(result["cycle_domains_found"], 1)
        self.assertEqual(
            result["strong_domains"],
            len(pub.CURATED_STRONG_OWN_LISTING_SEEDS) + 1,
        )
        self.assertIn("board.example", [x["domain"] for x in result["items"]])
        self.assertEqual(search.call_args.kwargs["request_timeout_s"], 5)
        self.assertFalse(search.call_args.kwargs["use_ddg"])

    def test_publication_discovery_development_drafts_are_prevalidated_and_product_free(self):
        platform = SimpleNamespace(key="pubdisc_test", name="Test catalog", url="https://example.test/add")
        with patch.object(guard.marketplace, "get_platform", return_value=platform), patch.object(
            guard.marketplace, "platform_policy", return_value={"free": True}
        ):
            rows = guard._publication_development_drafts({"pubdisc_test"})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["topic"], "development")
            self.assertEqual(row["status"], "approved")
            self.assertIn("/software-dev/services/", row["target_url"])
            self.assertIn(row["target_url"], row["text"])
            self.assertTrue(guard.marketplace.validate_publication_content(row)["ok"])
            lower=(row["title"]+"\n"+row["text"]).lower()
            self.assertNotIn("автоматизация avito", lower)
            self.assertNotIn("яндекс директ", lower)
            self.assertNotIn("виртуальный отдел продаж", lower)

    def test_publication_draft_repair_rejects_old_product_copy_but_not_posted_history(self):
        bad={"platform":"pubdisc_test","topic":"virtual_department","status":"approved"}
        posted={**bad,"status":"posted"}
        client={**bad,"topic":"client_crowd_seo","status":"approved"}
        self.assertTrue(guard._publication_draft_needs_repair(bad,{"pubdisc_test"}))
        self.assertFalse(guard._publication_draft_needs_repair(posted,{"pubdisc_test"}))
        self.assertFalse(guard._publication_draft_needs_repair(client,{"pubdisc_test"}))

    def test_publication_discovery_schedule_self_heals_future_timestamp(self):
        now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
        future = {"updated_at": (now + timedelta(hours=3)).isoformat()}
        row = guard._publication_discovery_schedule(future, 24, now=now)
        self.assertTrue(row["due"])
        self.assertTrue(row["clock_skew_detected"])

    def test_browser_assistant_detects_foreign_existing_threads(self):
        self.assertTrue(browser_assistant._looks_like_existing_thread_url(
            "https://forum-baza.ru/index.php?topic=84933.0"
        ))
        self.assertTrue(browser_assistant._looks_like_existing_thread_url(
            "https://example.com/threads/client-topic.123/"
        ))
        self.assertTrue(browser_assistant._looks_like_existing_thread_url(
            "https://www.mashkran.ru/forum/index.php?fid=2&id=026843&page=1#m1"
        ))

    def test_browser_assistant_allows_only_listing_or_new_topic_routes(self):
        self.assertFalse(browser_assistant._looks_like_existing_thread_url(
            "https://forum-baza.ru/index.php?action=post;board=62.0"
        ))
        self.assertFalse(browser_assistant._looks_like_existing_thread_url(
            "https://example.com/forums/services.60/create-thread"
        ))
        self.assertFalse(browser_assistant._looks_like_existing_thread_url(
            "https://www.moigruz.ru/forum-topics/18/"
        ))
        self.assertFalse(browser_assistant._looks_like_existing_thread_url(
            "https://www.mashkran.ru/forum/index.php?fid=6"
        ))

    def _flow_patches(self, project: dict, recorded: list[dict]):
        return [
            patch.object(guard.crowd_seo, "list_projects", return_value=[project]),
            patch.object(guard.crowd_seo, "refresh_project_forums", return_value={"changed": 0}),
            patch.object(guard.crowd_seo, "sync_project_drafts", return_value={"drafts": 1}),
            patch.object(guard.crowd_seo, "sync_publications", return_value={"changed": 0}),
            patch.object(guard.crowd_seo, "get_project", return_value=project),
            patch.object(guard.crowd_seo, "schedule_replacements", return_value={"created": 0}),
            patch.object(guard.crowd_seo, "verify_placement", return_value={"status": "verified"}),
            patch.object(
                guard.crowd_seo,
                "record_publish_attempt",
                side_effect=lambda pid, plid, payload: recorded.append(
                    {"project": pid, "placement": plid, "payload": payload}
                ),
            ),
        ]

    def test_inactive_duplicate_is_not_counted_as_live_project(self):
        inactive = {
            "id":"crowd_old_duplicate",
            "status":"duplicate_superseded",
            "content_status":"superseded",
            "placements":[],
        }
        with patch.object(guard.crowd_seo,"list_projects",return_value=[inactive]):
            result=guard.run_crowd_seo_cycle("/tmp/publisher.py")
        self.assertEqual(result["projects"],0)
        self.assertEqual(result["publish_attempts"],0)

    def test_publisher_prefers_new_domain_over_second_post_on_used_domain(self):
        now = datetime.now(timezone.utc)
        project = {
            "id": "crowd_test",
            "content_status": "approved",
            "guarantee_days": 30,
            "created_at": now.isoformat(),
            "placements": [
                {
                    "id": "hist",
                    "platform": "old_platform",
                    "status": "verified",
                    "forum_url": "https://old.example/forum",
                    "publication_url": "https://old.example/topic/1",
                    "checked_at": now.isoformat(),
                    "published_at": now.isoformat(),
                    "marketplace_draft_id": "hist_draft",
                },
                {
                    "id": "old_ready",
                    "platform": "old_platform",
                    "status": "ready_to_publish",
                    "forum_url": "https://old.example/forum",
                    "publication_url": None,
                    "marketplace_draft_id": "old_draft",
                },
                {
                    "id": "new_ready",
                    "platform": "new_platform",
                    "status": "ready_to_publish",
                    "forum_url": "https://new.example/services",
                    "publication_url": None,
                    "marketplace_draft_id": "new_draft",
                },
            ],
        }
        recorded = []
        patches = self._flow_patches(project, recorded)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        payload = {
            "filled": True,
            "publish_clicked": False,
            "publication_verified": False,
            "checkpoint": "captcha_required",
            "url": None,
            "next_action": "checkpoint",
        }
        fake = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch.object(guard.subprocess, "run", return_value=fake) as runner:
            result = guard.run_crowd_seo_cycle("/tmp/publisher.py")

        self.assertEqual(result["publish_attempts"], 1)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[argv.index("--platform") + 1], "new_platform")
        self.assertEqual(recorded[0]["placement"], "new_ready")

    def test_unapproved_project_never_calls_publisher(self):
        project = make_project("needs_owner_approval")
        recorded: list[dict] = []
        patches = self._flow_patches(project, recorded)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with patch.object(guard.subprocess, "run", side_effect=AssertionError("publisher called before approval")):
            result = guard.run_crowd_seo_cycle("/tmp/publisher.py")
        self.assertEqual(result["publish_attempts"], 0)
        self.assertEqual(recorded, [])

    def test_approved_ready_placement_calls_existing_publisher(self):
        project = make_project("approved")
        recorded: list[dict] = []
        patches = self._flow_patches(project, recorded)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        payload = {
            "filled": True,
            "publish_clicked": False,
            "publication_verified": False,
            "checkpoint": None,
            "url": "https://forum.example/new-topic",
            "next_action": "manual_checkpoint_or_adapter_needed",
        }
        fake = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch.object(guard.subprocess, "run", return_value=fake) as runner:
            result = guard.run_crowd_seo_cycle("/tmp/publisher.py")
        self.assertEqual(result["publish_attempts"], 1)
        args = runner.call_args.args[0]
        self.assertIn("--publish", args)
        self.assertEqual(recorded[0]["project"], "crowd_test")
        self.assertEqual(recorded[0]["placement"], "crowd_test_001")

    def test_verified_link_is_rechecked_only_after_interval(self):
        now = datetime.now(timezone.utc)
        fresh = {
            "status": "verified",
            "publication_url": "https://forum.example/topic",
            "checked_at": (now - timedelta(hours=2)).isoformat(),
        }
        stale = {
            "status": "verified",
            "publication_url": "https://forum.example/topic",
            "checked_at": (now - timedelta(hours=25)).isoformat(),
        }
        unverified = {
            "status": "published_unverified",
            "publication_url": "https://forum.example/topic",
            "checked_at": now.isoformat(),
        }
        transient_fresh = {
            "status": "verification_temporarily_blocked",
            "publication_url": "https://forum.example/topic",
            "checked_at": (now - timedelta(minutes=30)).isoformat(),
        }
        transient_stale = {
            "status": "verification_temporarily_blocked",
            "publication_url": "https://forum.example/topic",
            "checked_at": (now - timedelta(hours=3)).isoformat(),
        }
        self.assertFalse(guard._crowd_link_due(fresh, now))
        self.assertTrue(guard._crowd_link_due(stale, now))
        self.assertTrue(guard._crowd_link_due(unverified, now))
        self.assertFalse(guard._crowd_link_due(transient_fresh, now))
        self.assertTrue(guard._crowd_link_due(transient_stale, now))

    def test_verify_placement_uses_saved_browser_proof_after_http_403(self):
        publication_url = "https://forum-baza.ru/index.php?topic=84935.new#new"
        target_url = "https://boris-ai.pro/software-dev"
        project = {
            "id": "crowd_test",
            "placements": [{
                "id": "crowd_test_001",
                "platform": "forum_baza_1c",
                "status": "published_unverified",
                "publication_url": publication_url,
                "target_url": target_url,
            }],
        }
        response = SimpleNamespace(status_code=403, text="Forbidden", url=publication_url)
        browser_proof = {
            "ok": True,
            "publication_verified": True,
            "http_status": 200,
            "final_url": publication_url,
            "indexable": True,
            "rel": "nofollow noopener",
            "link_rel_type": "nofollow",
        }
        saved = []
        with (
            patch.object(guard.crowd_seo, "_load", return_value=[project]),
            patch.object(guard.crowd_seo, "_save", side_effect=lambda rows: saved.append(rows)),
            patch.object(guard.crowd_seo.requests, "get", return_value=response),
            patch.object(guard.crowd_seo, "_browser_verify_publication", return_value=browser_proof) as browser_verify,
        ):
            result = guard.crowd_seo.verify_placement("crowd_test", "crowd_test_001")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["verification_transport"], "browser_session")
        self.assertTrue(result["link_present"])
        self.assertTrue(result["indexable"])
        self.assertEqual(result["link_rel_type"], "nofollow")
        self.assertIsNone(result["error"])
        browser_verify.assert_called_once_with("forum_baza_1c", publication_url, target_url)
        self.assertTrue(saved)

    def test_verify_placement_requires_replacement_when_browser_conclusively_loads_missing_link(self):
        publication_url = "https://forum-baza.ru/index.php?topic=84935.new#new"
        target_url = "https://boris-ai.pro/software-dev"
        project = {
            "id": "crowd_test",
            "placements": [{
                "id": "crowd_test_001",
                "platform": "forum_baza_1c",
                "status": "verified",
                "publication_url": publication_url,
                "target_url": target_url,
            }],
        }
        response = SimpleNamespace(status_code=403, text="Forbidden", url=publication_url)
        browser_proof = {
            "ok": True,
            "publication_verified": False,
            "http_status": 200,
            "final_url": publication_url,
            "link_present": False,
            "indexable": False,
            "rel": None,
            "link_rel_type": None,
            "error": "target_link_missing_or_not_indexable",
        }
        with (
            patch.object(guard.crowd_seo, "_load", return_value=[project]),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo.requests, "get", return_value=response),
            patch.object(guard.crowd_seo, "_browser_verify_publication", return_value=browser_proof),
        ):
            result = guard.crowd_seo.verify_placement("crowd_test", "crowd_test_001")
        self.assertEqual(result["status"], "replacement_required")
        self.assertEqual(result["verification_transport"], "browser_session")
        self.assertFalse(result["link_present"])
        self.assertFalse(result["indexable"])

    def test_verify_placement_keeps_transient_state_when_browser_cannot_prove_link(self):
        publication_url = "https://forum-baza.ru/index.php?topic=84935.new#new"
        project = {
            "id": "crowd_test",
            "placements": [{
                "id": "crowd_test_001",
                "platform": "forum_baza_1c",
                "status": "published_unverified",
                "publication_url": publication_url,
                "target_url": "https://boris-ai.pro/software-dev",
            }],
        }
        response = SimpleNamespace(status_code=403, text="Forbidden", url=publication_url)
        with (
            patch.object(guard.crowd_seo, "_load", return_value=[project]),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo.requests, "get", return_value=response),
            patch.object(guard.crowd_seo, "_browser_verify_publication", return_value={"ok": False, "publication_verified": False, "error": "browser_unavailable"}),
        ):
            result = guard.crowd_seo.verify_placement("crowd_test", "crowd_test_001")
        self.assertEqual(result["status"], "verification_temporarily_blocked")
        self.assertEqual(result["verification_transport"], "http_blocked_browser_unconfirmed")
        self.assertEqual(result["error"], "browser_unavailable")

    def test_publication_browser_proof_rejects_foreign_domain(self):
        self.assertTrue(browser_assistant._publication_url_matches_platform(
            "forum_baza_1c", "https://forum-baza.ru/index.php?topic=1"
        ))
        self.assertFalse(browser_assistant._publication_url_matches_platform(
            "forum_baza_1c", "https://example.com/topic/1"
        ))

    def test_warming_registration_is_reverified_daily_not_reregistered(self):
        now = datetime.now(timezone.utc)
        fresh = {
            "status": "warming",
            "checkpoint": "participation_level_required",
            "updated_at": (now - timedelta(hours=2)).isoformat(),
        }
        stale = {
            "status": "warming",
            "checkpoint": "participation_level_required",
            "updated_at": (now - timedelta(hours=26)).isoformat(),
        }
        self.assertFalse(guard._registration_retry_due(fresh, now))
        self.assertFalse(guard._registration_retry_due(stale, now))
        self.assertFalse(guard._registration_verification_due(fresh, now))
        self.assertTrue(guard._registration_verification_due(stale, now))

    def test_registration_http_fallback_detects_captcha_without_submission(self):
        response = SimpleNamespace(
            url="https://forum.example/register",
            text='<html><body><form><div class="g-recaptcha" data-sitekey="x"></div></form></body></html>',
            raise_for_status=lambda: None,
        )
        with patch.object(browser_assistant.requests, "get", return_value=response) as get:
            result = browser_assistant._registration_http_fallback(
                "forum_example",
                "https://forum.example/register",
            )
        self.assertEqual(result["checkpoint"], "captcha_required")
        self.assertIn("антибот", result["detail"].lower())
        get.assert_called_once()

    def test_print_forum_requires_poligrafist_group_before_ready(self):
        status,checkpoint,error=browser_assistant._classify_post_login_readiness(
            "print_forum_goods",
            200,
            'У вас недостаточно прав. Публиковать объявления могут участники, имеющие статус "Полиграфист". Для включения в эту группу подайте заявку.',
        )
        self.assertEqual(status,"warming")
        self.assertEqual(checkpoint,"membership_group_required")
        self.assertIn("Полиграфист",error)

        ready=browser_assistant._classify_post_login_readiness(
            "print_forum_goods",
            200,
            "Создать новую тему Заголовок темы Название темы",
        )
        self.assertEqual(ready,("ready",None,None))

    def test_print_forum_goods_rule_sources_are_registered(self):
        urls=guard.platform_rules.RULE_URLS.get("print_forum_goods") or []
        self.assertTrue(any("forumdisplay.php?f=22" in x for x in urls))
        self.assertTrue(any("register.php" in x for x in urls))

    def test_nulled_post_login_permission_is_warming_until_ad_form_opens(self):
        blocked = browser_assistant._classify_post_login_readiness(
            "nulled_services",
            403,
            "У вас нет прав для создания новой темы",
        )
        ready = browser_assistant._classify_post_login_readiness(
            "nulled_services",
            200,
            "Создать тему Заголовок Сообщение",
        )
        self.assertEqual(blocked[0], "warming")
        self.assertEqual(blocked[1], "participation_level_required")
        self.assertEqual(ready, ("ready", None, None))

    def test_active_project_bootstrap_snapshot_separates_client_work_from_reserve(self):
        bootstrap = {
            "items": [
                {"platform": "urgent_a"},
                {"platform": "urgent_b"},
                {"platform": "reserve"},
            ]
        }
        priority = {
            "urgent_a": {"potential_slots": 1, "requires_warmup_after_bootstrap": False},
            "urgent_b": {"potential_slots": 1, "requires_warmup_after_bootstrap": True},
        }
        projects = [{
            "id": "p1",
            "active": True,
            "status": "expanding_publication_pool",
            "target_count": 2,
            "bonus_count": 0,
        }]
        with (
            patch.object(guard.crowd_seo, "bootstrap_priority_snapshot", return_value=priority),
            patch.object(guard.crowd_seo, "list_projects", return_value=projects),
            patch.object(guard.crowd_seo, "project_is_active", return_value=True),
        ):
            row = guard._active_project_bootstrap_snapshot(bootstrap)

        self.assertEqual(row["needed_now"], 2)
        self.assertEqual(row["potential_slots"], 2)
        self.assertEqual(row["required_slots"], 2)
        self.assertTrue(row["contract_covered_after_bootstrap"])
        self.assertEqual(row["warming_after_bootstrap"], 1)
        self.assertEqual(row["reserve_items"], 1)
        self.assertFalse(row["owner_action_required"])
        self.assertEqual(row["executor_role"], "platform_onboarding_worker")

    def test_registration_retry_due_respects_terminal_and_cooldown(self):
        now = datetime.now(timezone.utc)
        terminal = {
            "status": "blocked",
            "checkpoint": "free_only_policy",
            "updated_at": (now - timedelta(days=3)).isoformat(),
        }
        recoverable_fresh = {
            "status": "blocked",
            "checkpoint": "registration_form_not_found",
            "updated_at": (now - timedelta(hours=2)).isoformat(),
        }
        recoverable_stale = {
            "status": "blocked",
            "checkpoint": "registration_form_not_found",
            "updated_at": (now - timedelta(hours=30)).isoformat(),
        }
        verification = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(days=3)).isoformat(),
        }
        self.assertFalse(guard._registration_retry_due(terminal, now))
        self.assertFalse(guard._registration_retry_due(recoverable_fresh, now))
        self.assertTrue(guard._registration_retry_due(recoverable_stale, now))
        self.assertFalse(guard._registration_retry_due(verification, now))
        self.assertTrue(guard._registration_retry_due(None, now))

    def test_auto_submit_preflight_is_fail_closed(self):
        safe = {
            "submitted": False,
            "checkpoint": None,
            "filled": {"username": "user", "email": "email", "password": "2 fields"},
        }
        consent = {
            "submitted": False,
            "checkpoint": None,
            "filled": {"username": "user", "email": "email", "password": "2 fields", "consent": "terms"},
        }
        captcha = {
            "submitted": False,
            "checkpoint": "captcha_required",
            "filled": {"username": "user", "email": "email", "password": "2 fields"},
        }
        incomplete = {
            "submitted": False,
            "checkpoint": None,
            "filled": {"email": "email", "password": "2 fields"},
        }
        wmboard_email_only = {
            "submitted": False,
            "checkpoint": None,
            "filled": {"email": "wmboard_registration_email", "registration_mode": "email_only"},
        }
        self.assertTrue(guard._preflight_allows_auto_submit(safe))
        self.assertFalse(guard._preflight_allows_auto_submit(consent))
        self.assertFalse(guard._preflight_allows_auto_submit(captcha))
        self.assertFalse(guard._preflight_allows_auto_submit(incomplete))
        self.assertFalse(guard._preflight_allows_auto_submit(wmboard_email_only))
        self.assertTrue(guard._preflight_allows_auto_submit(wmboard_email_only, platform="wmboard_services"))
        self.assertFalse(guard._preflight_allows_auto_submit(wmboard_email_only, platform="other_forum"))
        self.assertFalse(guard._preflight_allows_auto_submit(None))

    def test_safe_submit_without_checkpoint_is_verified_immediately(self):
        self.assertTrue(
            guard._registration_submit_needs_immediate_verify(
                {"submitted": True, "checkpoint": None}
            )
        )
        self.assertTrue(
            guard._registration_submit_needs_immediate_verify(
                {"submitted": True, "checkpoint": "registered"}
            )
        )
        self.assertFalse(
            guard._registration_submit_needs_immediate_verify(
                {"submitted": False, "checkpoint": None}
            )
        )
        self.assertFalse(
            guard._registration_submit_needs_immediate_verify(
                {"submitted": True, "checkpoint": "captcha_required"}
            )
        )
        self.assertFalse(
            guard._registration_submit_needs_immediate_verify(
                {"submitted": True, "checkpoint": "terms_acceptance_required"}
            )
        )

    def test_registration_verification_due_avoids_duplicate_accounts(self):
        now = datetime.now(timezone.utc)
        stale = {
            "status": "blocked",
            "checkpoint": "account_creation_unverified",
            "updated_at": (now - timedelta(hours=30)).isoformat(),
        }
        fresh = {
            "status": "blocked",
            "checkpoint": "account_creation_unverified",
            "updated_at": (now - timedelta(hours=2)).isoformat(),
        }
        form_problem = {
            "status": "in_progress",
            "checkpoint": "registration_form_not_found",
            "updated_at": (now - timedelta(hours=30)).isoformat(),
        }
        self.assertTrue(guard._registration_verification_due(stale, now))
        self.assertFalse(guard._registration_verification_due(fresh, now))
        self.assertFalse(guard._registration_verification_due(form_problem, now))
        self.assertFalse(guard._registration_retry_due(stale, now))

    def test_bootstrap_checkpoint_is_probed_on_shorter_interval(self):
        now = datetime.now(timezone.utc)
        stale_bootstrap = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(hours=7)).isoformat(),
        }
        fresh_bootstrap = {
            "status": "verification_required",
            "checkpoint": "captcha_required",
            "updated_at": (now - timedelta(hours=2)).isoformat(),
        }
        self.assertTrue(guard._registration_verification_due(stale_bootstrap, now))
        self.assertFalse(guard._registration_verification_due(fresh_bootstrap, now))

    def test_bootstrap_probe_preserves_checkpoint_until_login_is_ready(self):
        status, checkpoint, error = browser_assistant._preserve_pending_bootstrap_checkpoint(
            "captcha_required",
            "Нужно пройти CAPTCHA",
            "blocked",
            "login_failed",
            "Вход не выполнен",
        )
        self.assertEqual(status, "verification_required")
        self.assertEqual(checkpoint, "captcha_required")
        self.assertEqual(error, "Нужно пройти CAPTCHA")

        ready = browser_assistant._preserve_pending_bootstrap_checkpoint(
            "captcha_required",
            "Нужно пройти CAPTCHA",
            "ready",
            None,
            None,
        )
        self.assertEqual(ready, ("ready", None, None))

        progressed = browser_assistant._preserve_pending_bootstrap_checkpoint(
            "captcha_required",
            "Нужно пройти CAPTCHA",
            "verification_required",
            "email_verification_required",
            "Нужно подтвердить почту",
        )
        self.assertEqual(
            progressed,
            ("verification_required", "email_verification_required", "Нужно подтвердить почту"),
        )

    def test_false_email_checkpoint_self_heals_when_registration_was_never_submitted(self):
        with patch.object(
            browser_assistant.marketplace_svc,
            "list_attempts",
            return_value=[{
                "platform":"partnersearch",
                "action":"register",
                "meta":{"submitted":False},
            }],
        ):
            repaired = browser_assistant._preserve_pending_bootstrap_checkpoint(
                "email_verification_required",
                "Старое ложное состояние",
                "blocked",
                "login_failed",
                "Вы ввели неверное имя пользователя",
                platform="partnersearch",
            )
        self.assertEqual(repaired[0], "not_registered")
        self.assertEqual(repaired[1], "registration_not_submitted")

        with patch.object(
            browser_assistant.marketplace_svc,
            "list_attempts",
            return_value=[{
                "platform":"partnersearch",
                "action":"register",
                "meta":{"submitted":True},
            }],
        ):
            legitimate = browser_assistant._preserve_pending_bootstrap_checkpoint(
                "email_verification_required",
                "Нужно подтвердить почту",
                "blocked",
                "login_failed",
                "Вход пока не выполнен",
                platform="partnersearch",
            )
        self.assertEqual(
            legitimate,
            ("verification_required", "email_verification_required", "Нужно подтвердить почту"),
        )

    def test_reconcile_migrates_legacy_offer_type_and_auto_approves_without_owner(self):
        project={
            "id":"crowd_legacy_offer",
            "site":"https://shop.example/",
            "domain":"shop.example",
            "status":"expanding_publication_pool",
            "content_status":"internal_review_required",
            "niche":"furniture",
            "niche_label":"мебель",
            "keywords":["шкафы","каталог","доставка"],
            "target_pages":[{"url":"https://shop.example/","label":"Шкафы"}],
            "text_variants":[{"variant":1,"text":"Старый текст про услугу, который должен быть пересобран для нового типа предложения."}],
            "placements":[],
            "target_count":15,
            "bonus_count":3,
            "free_only":True,
            "generated_without_openai":True,
            "created_at":datetime.now(timezone.utc).isoformat(),
        }
        saved=[]
        with (
            patch.object(guard.crowd_seo,"_load",return_value=[project]),
            patch.object(guard.crowd_seo,"_save",side_effect=lambda rows:saved.append(rows)),
            patch.object(guard.crowd_seo.marketplace,"list_drafts",return_value=[]),
        ):
            result=guard.crowd_seo.reconcile_projects()
        self.assertEqual(result["migrated_offer_type"],["crowd_legacy_offer"])
        self.assertEqual(result["auto_approved"],["crowd_legacy_offer"])
        self.assertEqual(project["content_status"],"approved")
        self.assertFalse(project["owner_action_required"])
        self.assertIn(project["offer_type"],{"goods","services","mixed"})
        self.assertTrue(project["text_variants"])
        self.assertTrue(saved)

    def test_publication_sync_copies_direct_url_and_published_time(self):
        project = {
            "id": "crowd_test",
            "placements": [
                {
                    "id": "crowd_test_001",
                    "marketplace_draft_id": "draft_test_001",
                    "publication_url": None,
                    "status": "ready_to_publish",
                }
            ],
        }
        rows = [project]
        saved = []
        posted_at = datetime.now(timezone.utc).isoformat()
        drafts = [
            {
                "id": "draft_test_001",
                "status": "posted",
                "publication_url": "https://forum.example/topic-1",
                "posted_at": posted_at,
            }
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save", side_effect=lambda value: saved.append(value)),
            patch.object(guard.crowd_seo.marketplace, "list_drafts", return_value=drafts),
        ):
            result = guard.crowd_seo.sync_publications("crowd_test")
        placement = project["placements"][0]
        self.assertEqual(result["changed"], 1)
        self.assertEqual(placement["publication_url"], "https://forum.example/topic-1")
        self.assertEqual(placement["published_at"], posted_at)
        self.assertEqual(placement["status"], "published_unverified")
        self.assertTrue(saved)

    def test_new_plan_excludes_only_terminal_registration_blocks(self):
        registrations = [
            {"platform": "terminal_forum", "status": "blocked", "checkpoint": "free_only_policy"},
            {"platform": "recoverable_forum", "status": "blocked", "checkpoint": "registration_unavailable"},
            {"platform": "open_forum", "status": "not_registered", "checkpoint": "manual_verification"},
        ]
        platforms = [
            {
                "key": "terminal_forum",
                "name": "Терминально заблокированный форум",
                "url": "https://terminal.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "business",
                "free_policy": {"reason": "rules_allowed"},
            },
            {
                "key": "recoverable_forum",
                "name": "Восстановимый форум",
                "url": "https://recoverable.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "business",
                "free_policy": {"reason": "rules_allowed"},
            },
            {
                "key": "open_forum",
                "name": "Доступный форум",
                "url": "https://open.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "business",
                "free_policy": {"reason": "rules_allowed"},
            },
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
        ):
            rows = guard.crowd_seo._forum_matches("business", limit=10)
        keys = {x["platform"] for x in rows}
        self.assertNotIn("terminal_forum", keys)
        self.assertIn("recoverable_forum", keys)
        self.assertIn("open_forum", keys)
        recoverable = next(x for x in rows if x["platform"] == "recoverable_forum")
        self.assertTrue(recoverable["account_recoverable"])

    def test_verified_surfaces_require_project_relevance(self):
        registrations = [
            {"platform": "forum_baza_1c", "status": "ready", "checkpoint": None},
        ]
        platforms = [
            {
                "key": "forum_baza_1c",
                "name": "Форум База 1С",
                "url": "https://forum-baza.ru/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": True,
                "niche": "it",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [
                    {
                        "id": "freelance_1c",
                        "name": "1С фриланс",
                        "url": "https://forum-baza.ru/index.php?board=62.0",
                        "post_url": "https://forum-baza.ru/index.php?action=post;board=62.0",
                        "niches": ["it"],
                        "required_terms": ["1с", "1c"],
                    },
                    {
                        "id": "franchisee_services",
                        "name": "Компании 1С ФРАНЧАЙЗИ",
                        "url": "https://forum-baza.ru/index.php?board=65.0",
                        "post_url": "https://forum-baza.ru/index.php?action=post;board=65.0",
                        "niches": ["it", "business"],
                        "required_terms": ["1с", "1c"],
                    },
                ],
            },
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
        ):
            marketing = guard.crowd_seo._forum_matches(
                "marketing",
                limit=10,
                keywords=["seo", "реклама", "автоматизация объявлений"],
            )
            one_c = guard.crowd_seo._forum_matches(
                "it",
                limit=10,
                keywords=["интеграция 1с", "автоматизация 1с"],
            )
        self.assertEqual(marketing, [])
        self.assertEqual(len(one_c), 2)
        self.assertEqual(
            {guard.crowd_seo._slot_key(x) for x in one_c},
            {"forum_baza_1c::freelance_1c", "forum_baza_1c::franchisee_services"},
        )
        self.assertEqual(len({x["post_url"] for x in one_c}), 2)

    def test_goods_and_services_on_same_forum_route_to_correct_surface(self):
        registrations = [
            {"platform": "multi_commerce", "status": "verification_required", "checkpoint": "captcha_required"},
        ]
        platforms = [
            {
                "key": "multi_commerce",
                "name": "Multi Commerce",
                "url": "https://multi-commerce.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "business",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [
                    {
                        "id": "services",
                        "name": "Услуги для бизнеса",
                        "url": "https://multi-commerce.example/services",
                        "post_url": "https://multi-commerce.example/services/post",
                        "niches": ["business", "services"],
                        "required_terms": [],
                    },
                    {
                        "id": "goods",
                        "name": "Товары оптом",
                        "url": "https://multi-commerce.example/goods",
                        "post_url": "https://multi-commerce.example/goods/post",
                        "niches": ["business", "goods"],
                        "required_terms": [],
                    },
                ],
            },
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
        ):
            goods = guard.crowd_seo._forum_matches(
                "goods",
                limit=10,
                keywords=["каталог товаров", "оптом", "поставщик"],
                offer_type="goods",
            )
            services = guard.crowd_seo._forum_matches(
                "services",
                limit=10,
                keywords=["услуги", "автоматизация бизнеса"],
                offer_type="services",
            )
        self.assertEqual(len(goods), 1)
        self.assertEqual(goods[0]["surface_id"], "goods")
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["surface_id"], "services")

    def test_generic_multi_surface_forum_routes_to_one_best_section(self):
        registrations = [
            {"platform": "multi_forum", "status": "verification_required", "checkpoint": "captcha_required"},
        ]
        platforms = [
            {
                "key": "multi_forum",
                "name": "Multi Forum",
                "url": "https://multi.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "marketing",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [
                    {
                        "id": "generic_marketing",
                        "name": "Интернет-маркетинг",
                        "url": "https://multi.example/marketing",
                        "post_url": "https://multi.example/marketing/post",
                        "niches": ["marketing"],
                        "required_terms": [],
                    },
                    {
                        "id": "automation",
                        "name": "Автоматизация",
                        "url": "https://multi.example/automation",
                        "post_url": "https://multi.example/automation/post",
                        "niches": ["marketing"],
                        "required_terms": ["автомат", "crm"],
                    },
                ],
            },
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
        ):
            rows = guard.crowd_seo._forum_matches(
                "marketing",
                limit=10,
                keywords=["автоматизация продаж", "crm интеграция"],
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["surface_id"], "automation")
        self.assertEqual(guard.crowd_seo._slot_key(rows[0]), "multi_forum::automation")

    def test_surface_aware_text_is_different_for_different_sections(self):
        project = {
            "niche_label": "маркетинг и реклама",
            "target_pages": [{"url": "https://client.example/", "label": "Автоматизация рекламы"}],
        }
        base = {
            "target_url": "https://client.example/",
            "anchor": "https://client.example/",
            "link_mode": "безанкорная",
            "keyword": "автоматизация рекламы",
            "platform_name": "Forum",
        }
        seo_title, seo_text = guard.crowd_seo._render_placement_text(
            project,
            {**base, "surface_name": "SEO услуги"},
            1,
        )
        smm_title, smm_text = guard.crowd_seo._render_placement_text(
            project,
            {**base, "surface_name": "Услуги SMM"},
            1,
        )
        self.assertNotEqual(seo_title, smm_title)
        self.assertNotEqual(seo_text, smm_text)
        self.assertIn("маркетинг и реклама", seo_title)
        self.assertIn("маркетинг и реклама", smm_title)
        self.assertNotIn("BORIS", seo_title + " " + seo_text + " " + smm_title + " " + smm_text)

    def test_skripters_is_one_slot_because_crossposting_is_not_allowed(self):
        registrations = [
            {"platform": "skripters", "status": "verification_required", "checkpoint": "captcha_required"},
        ]
        platforms = [
            {
                "key": "skripters",
                "name": "Skripters · форум вебмастеров",
                "url": "https://top.skripters.biz/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "marketing",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
        ):
            marketing = guard.crowd_seo._forum_matches(
                "marketing",
                limit=10,
                keywords=["автоматизация продаж", "реклама", "crm"],
            )
            furniture = guard.crowd_seo._forum_matches(
                "furniture",
                limit=10,
                keywords=["кухни", "шкафы", "мебель"],
            )

        # Cross-posting is not allowed: Skripters may expose one curated
        # commercial programmer-services surface, but it must still contribute
        # exactly one project slot rather than one slot per possible section.
        curated = guard.crowd_seo.marketplace.VERIFIED_PUBLICATION_SURFACES.get("skripters", [])
        self.assertLessEqual(len(curated), 1)
        self.assertEqual(len(marketing), 1)
        self.assertEqual(marketing[0]["platform"], "skripters")
        self.assertEqual(marketing[0]["checkpoint"], "captcha_required")
        self.assertEqual(furniture, [])

    def test_surface_post_url_flows_into_placements_and_drafts(self):
        project = {
            "id": "crowd_surface",
            "site": "https://client.example/",
            "domain": "client.example",
            "niche": "it",
            "niche_label": "IT и разработка",
            "keywords": ["1с", "интеграция 1с"],
            "target_count": 2,
            "bonus_count": 0,
            "target_pages": [{"url": "https://client.example/", "label": "Интеграция 1С"}],
            "text_variants": [{"variant": 1, "text": "Текст"}],
            "content_status": "approved",
            "forum_candidates": [
                {
                    "platform": "forum_baza_1c",
                    "name": "Форум База 1С · 1С фриланс",
                    "surface_id": "freelance_1c",
                    "surface_name": "1С фриланс",
                    "url": "https://forum-baza.ru/index.php?board=62.0",
                    "post_url": "https://forum-baza.ru/index.php?action=post;board=62.0",
                    "publication_ready": True,
                    "account_status": "ready",
                },
                {
                    "platform": "forum_baza_1c",
                    "name": "Форум База 1С · Компании 1С ФРАНЧАЙЗИ",
                    "surface_id": "franchisee_services",
                    "surface_name": "Компании 1С ФРАНЧАЙЗИ",
                    "url": "https://forum-baza.ru/index.php?board=65.0",
                    "post_url": "https://forum-baza.ru/index.php?action=post;board=65.0",
                    "publication_ready": True,
                    "account_status": "ready",
                },
            ],
        }
        rows = [project]
        saved_drafts = []
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(
                guard.crowd_seo.marketplace,
                "save_drafts",
                side_effect=lambda drafts: saved_drafts.extend(drafts) or drafts,
            ),
        ):
            guard.crowd_seo.prepare_placement_plan("crowd_surface")
            result = guard.crowd_seo.sync_project_drafts("crowd_surface")

        self.assertEqual(len(project["placements"]), 2)
        self.assertEqual(
            {guard.crowd_seo._slot_key(x) for x in project["placements"]},
            {"forum_baza_1c::freelance_1c", "forum_baza_1c::franchisee_services"},
        )
        self.assertEqual(result["drafts"], 2)
        self.assertEqual(
            {x["publication_surface_post_url"] for x in saved_drafts},
            {
                "https://forum-baza.ru/index.php?action=post;board=62.0",
                "https://forum-baza.ru/index.php?action=post;board=65.0",
            },
        )

    def test_refresh_marks_dead_prepublication_platform_for_self_heal(self):
        project = {
            "id": "crowd_test",
            "niche": "business",
            "plan_mode": "contract",
            "target_count": 1,
            "bonus_count": 0,
            "content_status": "needs_owner_approval",
            "forum_candidates": [
                {
                    "platform": "old_forum",
                    "name": "Старый форум",
                    "url": "https://old.example/",
                    "publication_ready": False,
                    "account_status": "blocked",
                }
            ],
            "placements": [
                {
                    "id": "crowd_test_001",
                    "platform": "old_forum",
                    "status": "waiting_platform_ready",
                    "publication_url": None,
                    "error": None,
                }
            ],
        }
        rows = [project]
        fresh = [
            {
                "platform": "new_forum",
                "name": "Новый форум",
                "url": "https://new.example/",
                "publication_ready": False,
                "account_status": "not_registered",
                "niche": "business",
                "relevance": 3,
            }
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh),
        ):
            result = guard.crowd_seo.refresh_project_forums("crowd_test")
        placement = project["placements"][0]
        self.assertEqual(result["changed"], 1)
        self.assertEqual(placement["status"], "platform_replacement_required")
        self.assertEqual(placement["error"], "platform_no_longer_eligible")

    def test_recoverable_prepublication_slot_is_revived_before_replacement(self):
        project = {
            "id": "crowd_test",
            "niche": "business",
            "plan_mode": "contract",
            "target_count": 2,
            "bonus_count": 0,
            "content_status": "needs_owner_approval",
            "forum_candidates": [],
            "placements": [
                {
                    "id": "crowd_test_001",
                    "platform": "recoverable_forum",
                    "status": "platform_replacement_required",
                    "publication_url": None,
                    "replacement_placement_id": None,
                    "error": "platform_no_longer_eligible",
                },
                {
                    "id": "crowd_test_002",
                    "platform": "already_replaced_forum",
                    "status": "platform_replacement_required",
                    "publication_url": None,
                    "replacement_placement_id": "crowd_test_r003",
                    "error": "platform_no_longer_eligible",
                },
            ],
        }
        rows = [project]
        fresh = [
            {
                "platform": "recoverable_forum",
                "name": "Восстановимый форум",
                "url": "https://recoverable.example/",
                "publication_ready": False,
                "account_status": "blocked",
                "account_recoverable": True,
                "niche": "business",
                "relevance": 3,
            },
            {
                "platform": "already_replaced_forum",
                "name": "Уже заменённый форум",
                "url": "https://old.example/",
                "publication_ready": True,
                "account_status": "ready",
                "account_recoverable": False,
                "niche": "business",
                "relevance": 3,
            },
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh),
        ):
            result = guard.crowd_seo.refresh_project_forums("crowd_test")
        revived, historical = project["placements"]
        self.assertEqual(result["changed"], 1)
        self.assertEqual(revived["status"], "waiting_platform_ready")
        self.assertIsNone(revived["error"])
        self.assertEqual(historical["status"], "platform_replacement_required")
        self.assertEqual(historical["replacement_placement_id"], "crowd_test_r003")

    def test_replacement_is_created_once_for_failed_link(self):
        project = {
            "id": "crowd_test",
            "site": "https://client.example/",
            "niche_label": "Тестовая услуга",
            "plan_mode": "contract",
            "target_count": 1,
            "bonus_count": 0,
            "content_status": "approved",
            "forum_candidates": [
                {
                    "platform": "old_forum",
                    "name": "Старый форум",
                    "url": "https://old.example/",
                    "publication_ready": True,
                    "account_status": "ready",
                },
                {
                    "platform": "new_forum",
                    "name": "Новый форум",
                    "url": "https://new.example/",
                    "publication_ready": True,
                    "account_status": "ready",
                },
            ],
            "placements": [
                {
                    "id": "crowd_test_001",
                    "platform": "old_forum",
                    "platform_name": "Старый форум",
                    "forum_url": "https://old.example/",
                    "target_url": "https://client.example/service",
                    "keyword": "услуга",
                    "link_mode": "безанкорная",
                    "anchor": "https://client.example/service",
                    "text_variant": 1,
                    "text": "Текст",
                    "status": "replacement_required",
                    "publication_url": "https://old.example/topic",
                }
            ],
        }
        rows = [project]
        saved_drafts = []
        fresh_candidates = [
            {
                "platform": "new_forum",
                "name": "Новый форум",
                "url": "https://new.example/",
                "publication_ready": True,
                "account_status": "ready",
                "niche": "business",
                "relevance": 3,
            }
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh_candidates),
            patch.object(
                guard.crowd_seo.marketplace,
                "save_drafts",
                side_effect=lambda drafts: saved_drafts.extend(drafts) or drafts,
            ),
        ):
            first = guard.crowd_seo.schedule_replacements("crowd_test")
            second = guard.crowd_seo.schedule_replacements("crowd_test")
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(len(project["placements"]), 2)
        old, replacement = project["placements"]
        self.assertEqual(old["replacement_placement_id"], replacement["id"])
        self.assertEqual(replacement["replacement_for"], old["id"])
        self.assertEqual(replacement["platform"], "new_forum")
        self.assertEqual(replacement["status"], "ready_to_publish")
        replacement_draft = next(x for x in saved_drafts if x.get("crowd_placement_id") == replacement["id"])
        self.assertEqual(replacement_draft["status"], "approved")

    def test_failed_unpublished_slot_can_be_reused_after_recovery(self):
        project = {
            "id": "crowd_reuse",
            "site": "https://client.example/",
            "domain": "client.example",
            "niche": "business",
            "niche_label": "Бизнес",
            "target_count": 1,
            "bonus_count": 0,
            "content_status": "needs_owner_approval",
            "target_pages": [{"url": "https://client.example/", "label": "Главная"}],
            "keywords": ["автоматизация"],
            "text_variants": [{"variant": 1, "text": "Текст"}],
            "placements": [{
                "id": "crowd_reuse_001",
                "platform": "again",
                "surface_id": "default",
                "platform_name": "Again",
                "forum_url": "https://again.example/",
                "target_url": "https://client.example/",
                "keyword": "автоматизация",
                "link_mode": "безанкорная",
                "anchor": "https://client.example/",
                "text_variant": 1,
                "text": "Текст",
                "status": "platform_replacement_required",
                "publication_url": None,
            }],
        }
        rows = [project]
        fresh = [{
            "platform": "again",
            "surface_id": "default",
            "name": "Again",
            "url": "https://again.example/",
            "post_url": "https://again.example/post",
            "publication_ready": False,
            "account_status": "verification_required",
            "niche": "business",
            "relevance": 3,
        }]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh),
        ):
            result = guard.crowd_seo.schedule_replacements("crowd_reuse")
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["placement_summary"]["active"], 1)
        self.assertEqual(result["placement_summary"]["history"], 1)
        self.assertEqual(project["placements"][-1]["platform"], "again")
        self.assertEqual(project["placements"][-1]["status"], "external_checkpoint")

    def test_replacement_scheduler_tops_up_short_initial_plan(self):
        project = {
            "id": "crowd_topup",
            "site": "https://client.example/",
            "domain": "client.example",
            "niche": "business",
            "niche_label": "Бизнес",
            "target_count": 2,
            "bonus_count": 0,
            "content_status": "needs_owner_approval",
            "target_pages": [{"url": "https://client.example/", "label": "Главная"}],
            "keywords": ["автоматизация"],
            "text_variants": [{"variant": 1, "text": "Текст"}],
            "placements": [],
        }
        rows = [project]
        fresh = [
            {"platform": "one", "surface_id": "default", "name": "One", "url": "https://one.example/", "post_url": None, "publication_ready": False, "account_status": "verification_required", "niche": "business", "relevance": 3},
            {"platform": "two", "surface_id": "default", "name": "Two", "url": "https://two.example/", "post_url": None, "publication_ready": False, "account_status": "verification_required", "niche": "business", "relevance": 3},
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh),
        ):
            result = guard.crowd_seo.schedule_replacements("crowd_topup")
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["topups"], 2)
        self.assertEqual(result["placement_summary"]["planned"], 2)
        self.assertEqual(result["placement_summary"]["active"], 2)
        self.assertEqual(result["placement_summary"]["active_deficit"], 0)

    def test_operational_status_follows_live_autonomous_capacity(self):
        project = {
            "id": "crowd_status",
            "site": "https://client.example/",
            "niche": "business",
            "target_count": 3,
            "bonus_count": 1,
            "status": "placement_plan_ready",
            "content_status": "needs_owner_approval",
        }
        insufficient = [
            {"platform": "ready", "publication_ready": True, "account_status": "ready", "checkpoint": None},
            {"platform": "prepared", "publication_ready": False, "account_status": "in_progress", "checkpoint": "form_prepared"},
        ]
        enough = insufficient + [
            {"platform": "ready2", "publication_ready": True, "account_status": "ready", "checkpoint": None},
            {"platform": "prepared2", "publication_ready": False, "account_status": "in_progress", "checkpoint": "form_prepared"},
        ]
        self.assertEqual(
            guard.crowd_seo._operational_status(project, insufficient),
            "expanding_publication_pool",
        )
        self.assertEqual(
            guard.crowd_seo._operational_status(project, enough),
            "placement_plan_ready",
        )
        project["content_status"] = "approved"
        self.assertEqual(
            guard.crowd_seo._operational_status(project, enough),
            "approved_for_placement",
        )
        self.assertEqual(
            guard.crowd_seo._operational_status(project, insufficient),
            "expanding_publication_pool",
        )

    def test_registration_verification_candidates_can_be_sorted_oldest_first(self):
        now = datetime.now(timezone.utc)
        rows = {
            "newer": {"updated_at": (now - timedelta(hours=7)).isoformat()},
            "older": {"updated_at": (now - timedelta(hours=20)).isoformat()},
            "missing": {"updated_at": None},
        }
        keys = ["newer", "older", "missing"]
        keys.sort(
            key=lambda key: guard._parse_iso(rows[key].get("updated_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        self.assertEqual(keys, ["missing", "older", "newer"])

    def test_capacity_snapshot_counts_only_autonomous_paths(self):
        projects = [{
            "id": "crowd_capacity",
            "site": "https://client.example/",
            "niche": "business",
            "target_count": 3,
            "bonus_count": 1,
            "status": "placement_plan_ready",
            "content_status": "needs_owner_approval",
        }]
        matches = [
            {"platform": "ready", "publication_ready": True, "account_status": "ready", "account_recoverable": False, "checkpoint": None},
            {"platform": "prepared", "publication_ready": False, "account_status": "in_progress", "account_recoverable": False, "checkpoint": "form_prepared"},
            {"platform": "new", "publication_ready": False, "account_status": "not_registered", "account_recoverable": False, "checkpoint": None},
            {"platform": "recoverable", "publication_ready": False, "account_status": "blocked", "account_recoverable": True, "checkpoint": "login_failed"},
            {"platform": "captcha", "publication_ready": False, "account_status": "verification_required", "account_recoverable": False, "checkpoint": "captcha_required"},
            {"platform": "terms", "publication_ready": False, "account_status": "verification_required", "account_recoverable": False, "checkpoint": "terms_acceptance_required"},
        ]
        with (
            patch.object(guard.crowd_seo, "list_projects", return_value=projects),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=matches),
        ):
            rows = guard._crowd_capacity_snapshot()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["required"], 4)
        self.assertEqual(row["eligible"], 6)
        self.assertEqual(row["autonomous"], 2)
        self.assertEqual(row["ready"], 1)
        self.assertEqual(row["bootstrap_relevant"], 2)
        self.assertEqual(row["service_reachable"], 4)
        self.assertEqual(row["autonomous_deficit"], 2)
        self.assertEqual(row["ready_deficit"], 3)
        self.assertEqual(row["discovery_deficit_after_bootstrap"], 0)

    def test_capacity_counts_one_human_bootstrap_for_many_surface_slots(self):
        project = {
            "id": "crowd_capacity_surfaces",
            "site": "https://client.example/",
            "niche": "marketing",
            "target_count": 4,
            "bonus_count": 0,
            "status": "expanding_publication_pool",
            "content_status": "needs_owner_approval",
        }
        matches = [
            {"platform": "same_forum", "surface_id": "a", "publication_ready": False, "account_status": "verification_required", "checkpoint": "captcha_required"},
            {"platform": "same_forum", "surface_id": "b", "publication_ready": False, "account_status": "verification_required", "checkpoint": "captcha_required"},
            {"platform": "same_forum", "surface_id": "c", "publication_ready": False, "account_status": "verification_required", "checkpoint": "captcha_required"},
        ]
        row = guard.crowd_seo._capacity_for_project(project, matches)
        self.assertEqual(row["bootstrap_relevant"], 1)
        self.assertEqual(row["bootstrap_surface_slots"], 3)
        self.assertEqual(row["service_reachable"], 3)
        self.assertEqual(row["discovery_deficit_after_bootstrap"], 1)

    def test_capacity_separates_bootstrap_from_account_warmup(self):
        project = {
            "id": "crowd_capacity_warmup",
            "site": "https://client.example/",
            "niche": "marketing",
            "target_count": 4,
            "bonus_count": 0,
            "status": "expanding_publication_pool",
            "content_status": "needs_owner_approval",
        }
        matches = [
            {
                "platform": "ready",
                "publication_ready": True,
                "account_status": "ready",
                "checkpoint": None,
                "maturity_required": False,
                "account_warming": False,
            },
            {
                "platform": "captcha_fast",
                "publication_ready": False,
                "account_status": "verification_required",
                "checkpoint": "captcha_required",
                "maturity_required": False,
                "account_warming": False,
            },
            {
                "platform": "captcha_then_warm",
                "publication_ready": False,
                "account_status": "verification_required",
                "checkpoint": "captcha_required",
                "maturity_required": True,
                "account_warming": False,
            },
        ]
        row = guard.crowd_seo._capacity_for_project(project, matches)
        self.assertEqual(row["bootstrap_relevant"], 2)
        self.assertEqual(row["bootstrap_immediate_relevant"], 1)
        self.assertEqual(row["warming_relevant"], 1)
        self.assertEqual(row["service_reachable_after_bootstrap"], 2)
        self.assertEqual(row["service_reachable_after_warmup"], 3)
        self.assertEqual(row["discovery_deficit_after_bootstrap"], 2)
        self.assertEqual(row["discovery_deficit_after_warmup"], 1)

    def test_detect_niche_supports_generic_goods_and_services_without_hiding_specific_verticals(self):
        goods_pages = [{"title": "Интернет-магазин товаров", "h1": "Каталог товаров купить оптом"}]
        service_pages = [{"title": "Услуги для бизнеса", "h1": "Заказать обслуживание под ключ"}]
        furniture_pages = [{"title": "Магазин мебели", "h1": "Кухни и шкафы купить"}]

        self.assertEqual(guard.crowd_seo._detect_niche(goods_pages, ["товары", "каталог"]), "goods")
        self.assertEqual(guard.crowd_seo._detect_niche(service_pages, ["услуги", "обслуживание"]), "services")
        self.assertEqual(guard.crowd_seo._detect_niche(furniture_pages, ["мебель", "купить"]), "furniture")

    def test_discovery_priority_spends_reserve_budget_only_on_real_deficits(self):
        rows=[
            {
                "niche":"marketing",
                "discovery_deficit_after_warmup":5,
            },
            {
                "niche":"construction",
                "discovery_deficit_after_warmup":0,
            },
        ]
        reserve={
            "formats":{
                "goods":{"deficit":15},
                "services":{"deficit":0},
            }
        }
        with patch.object(guard,"_strategic_reserve_snapshot",return_value=reserve):
            self.assertEqual(
                guard._discovery_priority_niches(rows),
                ["marketing","goods"],
            )
            self.assertEqual(
                guard._discovery_priority_niches([]),
                ["goods"],
            )

        complete={
            "formats":{
                "goods":{"deficit":0},
                "services":{"deficit":0},
            }
        }
        with patch.object(guard,"_strategic_reserve_snapshot",return_value=complete):
            self.assertEqual(guard._discovery_priority_niches([]),[])

    def test_discovery_priority_includes_live_placement_deficit_niche(self):
        rows = [{
            "niche": "it",
            "discovery_deficit_after_warmup": 0,
            "placement_operational_deficit": 2,
        }]
        with patch.object(guard, "_strategic_reserve_snapshot", return_value={"formats": {}}):
            self.assertEqual(guard._discovery_priority_niches(rows), ["it"])

    def test_capacity_snapshot_carries_live_placement_deficit(self):
        project = {"id": "crowd_gap", "status": "approved_for_placement"}
        capacity = [{"project": "crowd_gap", "niche": "it", "required": 4}]
        with patch.object(guard.crowd_seo, "capacity_snapshot", return_value=[{**capacity[0], "placement_operational_deficit": 2}]):
            rows = guard._crowd_capacity_snapshot()
        self.assertEqual(rows[0]["placement_operational_deficit"], 2)

    def test_discovery_priority_includes_autonomous_deficit_before_reserve(self):
        rows = [{
            "niche": "it",
            "discovery_deficit_after_warmup": 0,
            "placement_operational_deficit": 0,
            "autonomous_deficit": 12,
        }]
        reserve = {"formats": {"goods": {"deficit": 50}}}
        with patch.object(guard, "_strategic_reserve_snapshot", return_value=reserve):
            self.assertEqual(guard._discovery_priority_niches(rows), ["it", "goods"])

    def test_autonomous_discovery_uses_two_hour_force_interval(self):
        now = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
        recent = {"at": (now - timedelta(hours=1)).isoformat(), "new_or_refreshed": 2}
        row = guard._discovery_schedule(recent, 12, autonomous=True, now=now)
        self.assertFalse(row["due"])
        self.assertTrue(row["autonomous"])
        self.assertEqual(row["force_interval_minutes"], 120)

        old = {"at": (now - timedelta(hours=2, minutes=1)).isoformat(), "new_or_refreshed": 2}
        row = guard._discovery_schedule(old, 12, autonomous=True, now=now)
        self.assertTrue(row["due"])
        self.assertTrue(row["forced_due"])
        self.assertEqual(row["force_interval_minutes"], 120)

    def test_discovery_schedule_backs_off_after_empty_forced_search(self):
        now=datetime(2026,9,6,20,0,tzinfo=timezone.utc)
        fresh_empty={
            "at":(now-timedelta(minutes=30)).isoformat(),
            "new_or_refreshed":0,
        }
        row=guard._discovery_schedule(fresh_empty,17,urgent=True,now=now)
        self.assertFalse(row["due"])
        self.assertTrue(row["backoff_active"])
        self.assertFalse(row["forced_due"])
        self.assertFalse(row["normal_due"])

    def test_discovery_schedule_forces_when_previous_search_made_progress(self):
        now=datetime(2026,9,6,20,0,tzinfo=timezone.utc)
        recent_progress={
            "at":(now-timedelta(hours=2)).isoformat(),
            "new_or_refreshed":3,
        }
        row=guard._discovery_schedule(recent_progress,17,urgent=True,now=now)
        self.assertTrue(row["due"])
        self.assertTrue(row["forced_due"])
        self.assertFalse(row["backoff_active"])

    def test_discovery_schedule_retries_after_backoff_window(self):
        now=datetime(2026,9,6,20,0,tzinfo=timezone.utc)
        stale_empty={
            "at":(now-guard.DISCOVERY_NO_PROGRESS_BACKOFF-timedelta(minutes=1)).isoformat(),
            "new_or_refreshed":0,
        }
        row=guard._discovery_schedule(stale_empty,17,urgent=True,now=now)
        self.assertTrue(row["due"])
        self.assertTrue(row["forced_due"])
        self.assertFalse(row["backoff_active"])

    def test_discovery_schedule_runs_normally_without_history(self):
        row=guard._discovery_schedule({},0,now=datetime(2026,9,6,20,0,tzinfo=timezone.utc))
        self.assertTrue(row["due"])
        self.assertTrue(row["normal_due"])


    def test_offer_type_detects_goods_services_and_mixed(self):
        goods_pages=[{
            "title":"Интернет-магазин строительных материалов",
            "h1":"Каталог товаров — купить с доставкой",
            "description":"Цены, наличие, модели и доставка",
            "body":"товар каталог купить цена в наличии доставка магазин",
        }]
        service_pages=[{
            "title":"Ремонт квартир под ключ",
            "h1":"Услуги ремонта и монтажа",
            "description":"Заказать замер и ремонт",
            "body":"услуги заказать монтаж ремонт сопровождение",
        }]
        mixed_pages=[{
            "title":"Кондиционеры: продажа и монтаж",
            "h1":"Купить кондиционер с установкой",
            "description":"Каталог оборудования и услуги монтажа",
            "body":"товар каталог купить цена доставка услуги монтаж установка сервис",
        }]
        self.assertEqual(guard.crowd_seo._detect_offer_type(goods_pages,["каталог","доставка"],"goods"),"goods")
        self.assertEqual(guard.crowd_seo._detect_offer_type(service_pages,["ремонт","монтаж"],"services"),"services")
        self.assertEqual(guard.crowd_seo._detect_offer_type(mixed_pages,["кондиционер","монтаж"],"business"),"mixed")

    def test_it_service_site_is_not_mixed_only_because_it_mentions_client_shops(self):
        pages=[{
            "title":"Автоматизация рекламы и продаж для бизнеса",
            "h1":"CRM, API, боты и разработка под ключ",
            "description":"Сервис для магазинов, каталогов и компаний услуг",
            "body":(
                "услуги разработка настройка внедрение сопровождение CRM API SEO реклама "
                "автоматизация продажи для магазинов каталогов товаров и бизнеса"
            ),
        }]
        result=guard.crowd_seo._detect_offer_type(
            pages,
            ["автоматизация","CRM","API","разработка","SEO","реклама"],
            "it",
        )
        self.assertEqual(result,"services")

    def test_goods_copy_is_not_service_template(self):
        project={
            "offer_type":"goods",
            "niche":"furniture",
            "niche_label":"мебель",
            "target_pages":[{"url":"https://shop.example/catalog","label":"Шкафы-купе"}],
        }
        pl={
            "target_url":"https://shop.example/catalog",
            "anchor":"https://shop.example/catalog",
            "link_mode":"безанкорная",
            "keyword":"шкафы-купе",
            "platform_name":"Мебельный форум",
            "surface_name":"Товары для дома",
        }
        title,text=guard.crowd_seo._render_placement_text(project,pl,1)
        low=text.lower()
        self.assertIn("достав",low)
        self.assertNotIn("состав услуги",low)
        self.assertNotIn("объём работ",low)
        self.assertIn("мебель",title.lower())

    def test_service_copy_keeps_service_comparison_language(self):
        project={
            "offer_type":"services",
            "niche":"construction",
            "niche_label":"строительство",
            "target_pages":[{"url":"https://service.example/remont","label":"Ремонт квартир"}],
        }
        pl={
            "target_url":"https://service.example/remont",
            "anchor":"https://service.example/remont",
            "link_mode":"безанкорная",
            "keyword":"ремонт квартир",
            "platform_name":"Строительный форум",
            "surface_name":"Услуги ремонта",
        }
        _,text=guard.crowd_seo._render_placement_text(project,pl,1)
        self.assertIn("состав работ",text.lower())

    def test_explicit_goods_business_surface_does_not_match_service_only_project(self):
        surface={
            "id":"goods_board",
            "name":"Товары для бизнеса",
            "niches":["goods","business"],
            "required_terms":[],
        }
        self.assertTrue(
            guard.crowd_seo._surface_matches_project(
                surface,"furniture",["шкафы","каталог"],"goods"
            )
        )
        self.assertFalse(
            guard.crowd_seo._surface_matches_project(
                surface,"it",["CRM","разработка","автоматизация"],"services"
            )
        )

    def test_generic_goods_and_services_surfaces_work_for_specific_client_verticals(self):
        svc=guard.crowd_seo.marketplace
        registrations=[
            {"platform":"generic_commerce","status":"ready","checkpoint":None},
        ]
        platform={
            "key":"generic_commerce",
            "name":"Общий коммерческий форум",
            "url":"https://forum.example/",
            "channel_type":"forum",
            "enabled_for_outreach":True,
            "publication_ready":True,
            "niche":"business",
            "free_policy":{"free":True},
            "publication_surfaces":[
                {
                    "id":"goods_and_services",
                    "name":"Товары и услуги",
                    "url":"https://forum.example/commerce",
                    "post_url":"https://forum.example/commerce/post",
                    "niches":["goods","services","business"],
                    "required_terms":[],
                }
            ],
        }
        with (
            patch.object(svc,"registration_plan",return_value=registrations),
            patch.object(svc,"list_platforms",return_value=[platform]),
        ):
            goods=guard.crowd_seo._forum_matches(
                "furniture",
                keywords=["шкафы","каталог","доставка"],
                offer_type="goods",
            )
            services=guard.crowd_seo._forum_matches(
                "construction",
                keywords=["ремонт","монтаж"],
                offer_type="services",
            )
        self.assertEqual(len(goods),1)
        self.assertEqual(goods[0]["surface_id"],"goods_and_services")
        self.assertEqual(len(services),1)
        self.assertEqual(services[0]["surface_id"],"goods_and_services")

    def test_warmup_queue_exposes_15_day_eta_and_reuse_for_goods_services(self):
        svc=guard.crowd_seo.marketplace
        now=datetime.now(timezone.utc)
        regs=[{
            "platform":"nulled_services",
            "status":"warming",
            "checkpoint":"participation_level_required",
            "warming_started_at":(now-timedelta(days=4,hours=2)).isoformat(),
            "updated_at":now.isoformat(),
            "account_url":"https://nulled.cc/",
        }]
        platforms=[{
            "key":"nulled_services",
            "name":"Nulled",
            "url":"https://nulled.cc/",
            "channel_type":"forum",
            "enabled_for_outreach":True,
            "publication_ready":False,
        }]
        with (
            patch.object(svc,"registration_plan",return_value=regs),
            patch.object(svc,"list_platforms",return_value=platforms),
        ):
            row=svc.platform_warmup_queue()
        self.assertEqual(row["warming"],1)
        item=row["items"][0]
        self.assertEqual(item["requirements"]["minimum_account_age_days"],15)
        self.assertGreaterEqual(item["age_remaining_days"],10)
        self.assertIn("goods",item["supported_client_formats"])
        self.assertIn("services",item["supported_client_formats"])
        self.assertFalse(item["owner_action_required"])
        self.assertTrue(item["reusable_across_clients"])


    def test_forum_discovery_goods_queries_are_offer_aware(self):
        plan=guard.forum_discovery.query_plan(max_queries=18, priority_niches=["goods"])
        goods=[q for niche,q in plan if niche=="goods"]
        self.assertGreaterEqual(len(goods),10)
        joined=" ".join(goods).lower()
        self.assertIn("поставщики",joined)
        self.assertTrue(any(term in joined for term in [
            "электроника","одежда","обувь","косметика","товары для дома",
            "детские товары","зоотовары","инструменты","бытовая техника",
            "сантехника","освещение","упаковка","хозтовары",
            "стройматериалы","мебель","оборудование","автотовары",
            "запчасти","продукты оптом","товары для бизнеса","промышленная продукция",
        ]))
        self.assertTrue(any(term in joined for term in [
            "продам","новая тема","товары","доска объявлений",
            "куплю продам","торговая площадка","предложения поставщиков",
            "барахолка","рынок","продажа товаров","товары и услуги","объявления",
        ]))
        self.assertNotIn("биржа услуг",joined)

    def test_single_goods_deficit_uses_entire_discovery_budget(self):
        plan=guard.forum_discovery.query_plan(max_queries=48, priority_niches=["goods"])
        self.assertEqual(len(plan),48)
        self.assertEqual({niche for niche,_query in plan},{"goods"})
        self.assertEqual(len({query for _niche,query in plan}),48)

    def test_terminal_cache_self_heals_when_live_registry_is_allowed(self):
        platforms=[{
            "key":"live_forum",
            "url":"https://forum.example.com/services/",
            "channel_type":"forum",
            "enabled_for_outreach":True,
        }]
        with (
            patch.object(guard.forum_discovery,"terminal_domain_map",return_value={
                "example.com":{"reason":"captcha_required"},
            }),
            patch.object(guard.marketplace,"list_platforms",return_value=platforms),
            patch.object(guard.marketplace,"registration_plan",return_value=[{
                "platform":"live_forum","status":"verification_required","checkpoint":"captcha_required",
            }]),
            patch.object(guard.marketplace,"registration_is_terminally_blocked",return_value=False),
            patch.object(guard.platform_rules,"latest",return_value={"decision":"allowed"}),
            patch.object(guard.forum_discovery,"unmark_terminal_domain",return_value=True) as unmark,
        ):
            rows=guard._reconcile_terminal_registry_conflicts()
        self.assertEqual(rows,[{
            "domain":"example.com",
            "platform":"live_forum",
            "previous_reason":"captcha_required",
        }])
        unmark.assert_called_once_with("example.com",reason="live_registry_allowed:live_forum")

    def test_terminal_cache_keeps_real_registration_block(self):
        platforms=[{
            "key":"blocked_forum",
            "url":"https://blocked.example/",
            "channel_type":"forum",
            "enabled_for_outreach":True,
        }]
        with (
            patch.object(guard.forum_discovery,"terminal_domain_map",return_value={
                "blocked.example":{"reason":"registration_disabled_by_site"},
            }),
            patch.object(guard.marketplace,"list_platforms",return_value=platforms),
            patch.object(guard.marketplace,"registration_plan",return_value=[{
                "platform":"blocked_forum","status":"blocked","checkpoint":"registration_disabled_by_site",
            }]),
            patch.object(guard.marketplace,"registration_is_terminally_blocked",return_value=True),
            patch.object(guard.platform_rules,"latest",return_value={"decision":"allowed"}),
            patch.object(guard.forum_discovery,"unmark_terminal_domain") as unmark,
        ):
            rows=guard._reconcile_terminal_registry_conflicts()
        self.assertEqual(rows,[])
        unmark.assert_not_called()

    def test_forum_discovery_services_keeps_service_intent(self):
        plan=guard.forum_discovery.query_plan(max_queries=18, priority_niches=["services"])
        services=[q for niche,q in plan if niche=="services"]
        self.assertGreaterEqual(len(services),10)
        joined=" ".join(services).lower()
        self.assertIn("услуги",joined)
        self.assertTrue("исполнители" in joined or "подрядчики" in joined)

    def test_forum_discovery_marketing_prioritizes_development_and_automation(self):
        plan=guard.forum_discovery.query_plan(max_queries=18, priority_niches=["marketing"])
        marketing=[q for niche,q in plan if niche=="marketing"]
        self.assertGreaterEqual(len(marketing),10)
        joined=" ".join(marketing).lower()
        self.assertIn("предлагаю услуги",joined)
        self.assertTrue("автоматизация бизнеса" in joined or "crm интеграция" in joined)
        self.assertTrue("услуги программиста" in joined or "внедрение ии" in joined)


    def test_discovery_progress_ignores_plain_rediscovery(self):
        old={
            "domain":"forum.example","register_url":None,"create_topic_hint":False,
            "forum_detected":True,"commercial_context":True,
        }
        same={**old,"last_checked_at":"later","score":99}
        self.assertFalse(guard.forum_discovery._is_material_discovery_progress(old,same))

    def test_discovery_progress_counts_new_or_new_registration_path(self):
        new={"domain":"new.example","forum_detected":True,"commercial_context":True}
        self.assertTrue(guard.forum_discovery._is_material_discovery_progress(None,new))
        old={"domain":"forum.example","register_url":None,"create_topic_hint":False,"forum_detected":True,"commercial_context":True}
        improved={**old,"register_url":"https://forum.example/register"}
        self.assertTrue(guard.forum_discovery._is_material_discovery_progress(old,improved))

    def test_discovery_schedule_uses_real_progress_count_over_legacy_refresh_count(self):
        now=datetime(2026,9,7,12,0,tzinfo=timezone.utc)
        ds={
            "at":(now-timedelta(minutes=30)).isoformat(),
            "new_or_refreshed":5,
            "progress_count":0,
        }
        row=guard._discovery_schedule(ds,10,now=now)
        self.assertFalse(row["due"])
        self.assertTrue(row["backoff_active"])
        self.assertEqual(row["previous_progress_count"],0)

    def test_campaign_profile_routes_copy_by_keyword_cluster(self):
        project={
            "site":"https://boris-ai.pro/",
            "domain":"boris-ai.pro",
            "offer_type":"services",
            "campaign_profile":{
                "brand":"BORIS",
                "posts_ru":[
                    {
                        "match_keywords":["avito","автозагрузка"],
                        "title":"AVITO",
                        "text":"Ключевые фразы: автозагрузка Avito; программа для Avito. {url}",
                    },
                    {
                        "match_keywords":["crm","воронка продаж"],
                        "title":"CRM",
                        "text":"Ключевые фразы: CRM для продаж; воронка продаж CRM. {url}",
                    },
                    {
                        "match_keywords":["1с","интеграция","api"],
                        "title":"1C API",
                        "text":"Ключевые фразы: интеграция 1С с CRM; API 1С. {url}",
                    },
                ],
            },
        }
        def render(keyword):
            placement={
                "target_url":"https://boris-ai.pro/software-dev",
                "anchor":keyword,
                "keyword":keyword,
                "surface_language":"ru",
                "surface_name":"Услуги",
                "platform_name":"Test",
            }
            return guard.crowd_seo._render_placement_text(project,placement,1)

        self.assertEqual(render("автозагрузка Avito")[0],"AVITO")
        self.assertEqual(render("CRM для продаж")[0],"CRM")
        # Two matching signals (1С + интеграция) must beat the single CRM hit.
        self.assertEqual(render("интеграция 1С с CRM")[0],"1C API")

    def test_campaign_profile_explicit_cluster_rotates_variants_and_keeps_exact_keyword(self):
        project={
            "site":"https://boris-ai.pro/",
            "domain":"boris-ai.pro",
            "campaign_profile":{
                "brand":"BORIS",
                "posts_ru":[
                    {"cluster":"crm","match_keywords":["crm"],"title":"CRM v1","text":"Первый вариант. {url}"},
                    {"cluster":"crm","match_keywords":["crm"],"title":"CRM v2","text":"Второй вариант. {url}"},
                    {"cluster":"avito","match_keywords":["avito"],"title":"AVITO","text":"Avito. {url}"},
                ],
            },
        }
        placement={
            "target_url":"https://boris-ai.pro/software-dev",
            "anchor":"квалификация лидов",
            "keyword":"квалификация лидов",
            "keyword_cluster":"crm",
            "surface_language":"ru",
            "surface_name":"Услуги",
            "platform_name":"Test",
        }
        t1,x1=guard.crowd_seo._render_placement_text(project,placement,1)
        t2,x2=guard.crowd_seo._render_placement_text(project,placement,2)
        self.assertEqual(t1,"CRM v1")
        self.assertEqual(t2,"CRM v2")
        self.assertIn("квалификация лидов",x1)
        self.assertIn("квалификация лидов",x2)

    def test_english_surface_copy_is_english_transparent_and_uses_naked_url(self):
        project={
            "site":"https://boris-ai.pro/",
            "domain":"boris-ai.pro",
            "offer_type":"services",
            "niche_label":"маркетинг",
            "keywords":["автоматизация продаж"],
            "target_pages":[{"url":"https://boris-ai.pro/","label":"BORIS"}],
        }
        placement={
            "target_url":"https://boris-ai.pro/",
            "anchor":"автоматизация продаж",
            "link_mode":"частичный анкор",
            "keyword":"автоматизация продаж",
            "surface_language":"en",
            "surface_name":"Services",
            "platform_name":"WJunction · Services",
        }
        title,text=guard.crowd_seo._render_placement_text(project,placement,1)
        self.assertIn("Service",title)
        self.assertIn("https://boris-ai.pro/",text)
        self.assertNotIn("[url=",text)
        self.assertFalse(any("А" <= ch <= "я" or ch in "Ёё" for ch in title+text))

    def test_wjunction_marketplace_drafts_are_english_end_to_end(self):
        with patch.object(guard.marketplace, "platform_policy", return_value={"free":True,"rule_check":True}):
            rows=guard.marketplace.generate_drafts(
                platform="wjunction_services",
                topic="development",
                variants=1,
            )
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row.get("content_language"),"en")
        self.assertIn("Business automation",row.get("title") or "")
        self.assertIn("Consultation:",row.get("text") or "")
        self.assertNotIn("Консультация:",row.get("text") or "")
        self.assertFalse(any("А" <= ch <= "я" or ch in "Ёё" for ch in (row.get("title") or "")+(row.get("text") or "")))

    def test_spa_shell_triggers_browser_render_fallback(self):
        shell="<html><body><div>Загрузка платформы...</div><script src='/app.js'></script></body></html>"
        rendered="<html><body><h1>Автоматизация CRM</h1><p>" + ("Разработка API и создание сайтов. " * 80) + "</p></body></html>"
        self.assertTrue(guard.crowd_seo._html_needs_browser_render(shell))
        self.assertFalse(guard.crowd_seo._html_needs_browser_render(rendered))

    def test_high_signal_capabilities_beat_brand_review_noise(self):
        pages=[{
            "title":"BORIS",
            "h1":"ИИ-автоматизация бизнеса",
            "description":"CRM, API, разработка и создание сайтов",
            "body":(
                ("Клиент Бориса использует Бориса. " * 40)
                + "Автоматизация CRM API интеграции создание сайтов разработка ботов SEO реклама аналитика продажи"
            ),
        }]
        kws=guard.crowd_seo._keywords(pages,"https://boris-ai.pro/",limit=12)
        self.assertEqual(kws[:4],["автоматизация","CRM","API","интеграции"])
        self.assertIn("создание сайтов",kws)
        self.assertIn("разработка",kws)

    def test_external_sso_checkpoint_is_verification_not_adapter_failure(self):
        self.assertEqual(
            browser_assistant._registration_status_for_checkpoint("external_account_sso_required"),
            "verification_required",
        )
        self.assertIn(
            "external_account_sso_required",
            guard.marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS,
        )

    def test_project_content_validation_is_ownerless_and_fail_closed(self):
        project={
            "site":"https://client.example/",
            "offer_type":"services",
            "target_pages":[{"url":"https://client.example/remont","label":"Ремонт"}],
            "keywords":["ремонт квартир","монтаж"],
            "text_variants":[
                {"variant":1,"text":"Подробная страница клиента помогает сравнить состав работ, сроки, условия и сопровождение без лишних обещаний."}
            ],
            "free_only":True,
            "generated_without_openai":True,
        }
        ok=guard.crowd_seo._project_content_validation(project)
        self.assertEqual(ok["status"],"PASS")
        self.assertFalse(ok["owner_action_required"])

        bad={**project,"target_pages":[{"url":"https://other.example/","label":"Чужой домен"}]}
        failed=guard.crowd_seo._project_content_validation(bad)
        self.assertEqual(failed["status"],"FAIL")
        self.assertIn("target_url_cross_domain",failed["errors"])
        self.assertFalse(failed["owner_action_required"])

    def test_reconcile_projects_supersedes_same_site_duplicate_and_auto_approves_latest(self):
        older={
            "id":"crowd_old",
            "site":"https://client.example/",
            "status":"placement_plan_ready",
            "content_status":"approved",
            "created_at":"2026-09-06T10:00:00+00:00",
            "placements":[],
        }
        latest={
            "id":"crowd_new",
            "site":"https://client.example/",
            "domain":"client.example",
            "status":"placement_plan_ready",
            "content_status":None,
            "created_at":"2026-09-06T11:00:00+00:00",
            "offer_type":"services",
            "target_pages":[{"url":"https://client.example/remont","label":"Ремонт"}],
            "keywords":["ремонт квартир","монтаж"],
            "text_variants":[
                {"variant":1,"text":"Подробная страница клиента помогает сравнить состав работ, сроки, условия и сопровождение без лишних обещаний."}
            ],
            "free_only":True,
            "generated_without_openai":True,
            "placements":[],
        }
        rows=[older,latest]
        saved=[]
        with (
            patch.object(guard.crowd_seo,"_load",return_value=rows),
            patch.object(guard.crowd_seo,"_save",side_effect=lambda value:saved.append(value)),
            patch.object(guard.crowd_seo.marketplace,"list_drafts",return_value=[]),
        ):
            result=guard.crowd_seo.reconcile_projects()

        self.assertEqual(result["active_projects"],1)
        self.assertFalse(result["owner_action_required"])
        self.assertEqual(older["status"],"duplicate_superseded")
        self.assertEqual(older["content_status"],"superseded")
        self.assertEqual(older["superseded_by"],"crowd_new")
        self.assertEqual(latest["content_status"],"approved")
        self.assertEqual(latest["content_approval_mode"],"automatic_after_site_input")
        self.assertIn("crowd_new",result["auto_approved"])
        self.assertTrue(saved)

    def test_create_project_same_site_is_idempotent_and_does_not_reanalyse(self):
        existing={
            "id":"crowd_existing",
            "site":"https://client.example/",
            "domain":"client.example",
            "status":"approved_for_placement",
            "content_status":"approved",
            "created_at":"2026-09-06T11:00:00+00:00",
            "offer_type":"goods",
            "offer_type_label":"товары",
            "plan_mode":"contract",
            "target_count":15,
            "bonus_count":3,
            "target_pages":[{"url":"https://client.example/","label":"Каталог"}],
            "keywords":["товары"],
            "text_variants":[{"variant":1,"text":"Подробная страница каталога клиента с условиями, характеристиками, наличием, ценой и доставкой для сравнения."}],
            "placements":[],
        }
        with (
            patch.object(guard.crowd_seo,"_load",return_value=[existing]),
            patch.object(guard.crowd_seo,"analyse_site") as analyse,
        ):
            result=guard.crowd_seo.create_project("client.example",15,3)

        analyse.assert_not_called()
        self.assertEqual(result["id"],"crowd_existing")
        self.assertTrue(result["reused_existing"])
        self.assertFalse(result["owner_action_required"])

    def test_create_project_double_checks_after_analysis_to_close_race(self):
        existing={
            "id":"crowd_race_winner",
            "site":"https://client.example/",
            "domain":"client.example",
            "status":"approved_for_placement",
            "content_status":"approved",
            "created_at":"2026-09-06T11:00:00+00:00",
            "offer_type":"services",
            "offer_type_label":"услуги",
            "plan_mode":"contract",
            "target_count":15,
            "bonus_count":3,
            "target_pages":[{"url":"https://client.example/","label":"Услуги"}],
            "keywords":["услуги"],
            "text_variants":[{"variant":1,"text":"Подробная страница клиента помогает сравнить состав работ, условия, сроки и сопровождение до обращения."}],
            "placements":[],
        }
        analysis={
            "site":"https://client.example/",
            "domain":"client.example",
            "niche":"services",
            "niche_label":"услуги",
            "offer_type":"services",
            "offer_type_label":"услуги",
            "pages":existing["target_pages"],
            "keywords":existing["keywords"],
            "anchor_plan":[],
            "text_variants":existing["text_variants"],
            "forums":[],
        }
        with (
            patch.object(guard.crowd_seo,"_load",side_effect=[[],[existing]]),
            patch.object(guard.crowd_seo,"analyse_site",return_value=analysis),
            patch.object(guard.crowd_seo,"get_project",return_value=dict(existing)),
            patch.object(guard.crowd_seo,"prepare_placement_plan") as prepare,
            patch.object(guard.crowd_seo,"_save") as save,
        ):
            result=guard.crowd_seo.create_project("client.example",15,3)

        self.assertEqual(result["id"],"crowd_race_winner")
        self.assertTrue(result["reused_existing"])
        prepare.assert_not_called()
        save.assert_not_called()

    def test_project_state_lock_is_reentrant_for_nested_self_heal_calls(self):
        with guard.crowd_seo.project_state_lock():
            with guard.crowd_seo.project_state_lock():
                self.assertTrue(True)


    def test_guard_singleton_lock_blocks_second_runner(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            lock_path=Path(td)/"forum_guard.lock"
            first=guard._acquire_run_lock(lock_path)
            self.assertIsNotNone(first)
            second=guard._acquire_run_lock(lock_path)
            self.assertIsNone(second)
            first.close()
            third=guard._acquire_run_lock(lock_path)
            self.assertIsNotNone(third)
            third.close()


    def test_generic_post_login_readiness_never_treats_login_as_publish_permission(self):
        unknown = browser_assistant._classify_post_login_readiness(
            "generic_forum",
            200,
            "Профиль пользователя. Вы успешно вошли на форум.",
        )
        ready = browser_assistant._classify_post_login_readiness(
            "generic_forum",
            200,
            "Раздел услуг. Создать новую тему. Заголовок темы.",
        )
        denied = browser_assistant._classify_post_login_readiness(
            "generic_forum",
            403,
            "Недостаточно прав для публикации.",
        )
        self.assertEqual(unknown[0], "in_progress")
        self.assertEqual(unknown[1], "posting_permission_unverified")
        self.assertEqual(ready, ("ready", None, None))
        self.assertEqual(denied[0], "in_progress")
        self.assertEqual(denied[1], "posting_permission_required")

    def test_post_login_readiness_url_falls_back_to_verified_surface(self):
        platforms = [{
            "key": "generic_forum",
            "url": "https://forum.example/",
            "publication_surfaces": [{
                "id": "services",
                "url": "https://forum.example/services",
                "post_url": "https://forum.example/services/new-topic",
            }],
        }]
        with patch.object(browser_assistant.marketplace_svc, "list_platforms", return_value=platforms):
            self.assertEqual(
                browser_assistant._post_login_readiness_url("generic_forum"),
                "https://forum.example/services/new-topic",
            )

    def test_cyberforum_post_login_permission_enters_warming(self):
        blocked = browser_assistant._classify_post_login_readiness(
            "cyberforum_freelancers",
            403,
            "Доступ в данный раздел ограничен. Нужна группа 4 и членство в группах.",
        )
        ready = browser_assistant._classify_post_login_readiness(
            "cyberforum_freelancers",
            200,
            "Создать новую тему Заголовок Сообщение",
        )
        self.assertEqual(blocked[0], "warming")
        self.assertEqual(blocked[1], "freelance_group_required")
        self.assertEqual(ready, ("ready", None, None))


    def test_transient_rule_audit_error_retries_on_short_cadence(self):
        now = datetime.now(timezone.utc)
        transient = {
            "checked_at": (now - timedelta(hours=3)).isoformat(),
            "decision": "review",
            "errors": [{"error": "ConnectionError: Temporary failure in name resolution"}],
        }
        with (
            patch.object(guard.marketplace, "FREE_PLATFORM_POLICY", {"test_forum": {"free": False, "reason": "rule_audit_required"}}),
            patch.object(guard.platform_rules, "latest", return_value=transient),
        ):
            self.assertTrue(guard.needs_audit("test_forum"))

    def test_fresh_transient_rule_audit_error_waits_for_short_cadence(self):
        now = datetime.now(timezone.utc)
        transient = {
            "checked_at": (now - timedelta(minutes=30)).isoformat(),
            "decision": "review",
            "errors": [{"error": "Page.goto: net::ERR_NAME_NOT_RESOLVED"}],
        }
        with (
            patch.object(guard.marketplace, "FREE_PLATFORM_POLICY", {"test_forum": {"free": False, "reason": "rule_audit_required"}}),
            patch.object(guard.platform_rules, "latest", return_value=transient),
        ):
            self.assertFalse(guard.needs_audit("test_forum"))

    def test_semantic_rule_review_keeps_normal_freshness_window(self):
        now = datetime.now(timezone.utc)
        semantic_review = {
            "checked_at": (now - timedelta(hours=3)).isoformat(),
            "decision": "review",
            "errors": [],
        }
        with (
            patch.object(guard.marketplace, "FREE_PLATFORM_POLICY", {"test_forum": {"free": False, "reason": "rule_audit_required"}}),
            patch.object(guard.platform_rules, "latest", return_value=semantic_review),
        ):
            self.assertFalse(guard.needs_audit("test_forum"))


    def test_cloudflare_challenge_rule_audit_retries_on_short_cadence(self):
        now = datetime.now(timezone.utc)
        challenge = {
            "checked_at": (now - timedelta(hours=3)).isoformat(),
            "decision": "review",
            "errors": [{"error": "HTTPError: 403 Client Error: Forbidden"}],
            "inspections": [{
                "http_status": 403,
                "text_excerpt": "Выполнение проверки безопасности Cloudflare. Сайт проверяет, что вы не бот.",
            }],
        }
        with (
            patch.object(guard.marketplace, "FREE_PLATFORM_POLICY", {"test_forum": {"free": False, "reason": "rule_audit_required"}}),
            patch.object(guard.platform_rules, "latest", return_value=challenge),
        ):
            self.assertTrue(guard.needs_audit("test_forum"))

    def test_plain_403_without_challenge_does_not_force_short_retry(self):
        now = datetime.now(timezone.utc)
        denied = {
            "checked_at": (now - timedelta(hours=3)).isoformat(),
            "decision": "review",
            "errors": [{"error": "HTTPError: 403 Client Error: Forbidden"}],
            "inspections": [{"http_status": 403, "text_excerpt": "Access denied by administrator."}],
        }
        with (
            patch.object(guard.marketplace, "FREE_PLATFORM_POLICY", {"test_forum": {"free": False, "reason": "rule_audit_required"}}),
            patch.object(guard.platform_rules, "latest", return_value=denied),
        ):
            self.assertFalse(guard.needs_audit("test_forum"))


    def test_digitalpoint_post_login_permission_enters_warming_until_established(self):
        warming = browser_assistant._classify_post_login_readiness(
            "digitalpoint_services",
            403,
            "You have insufficient privileges to post threads here. Established Member required.",
        )
        ready = browser_assistant._classify_post_login_readiness(
            "digitalpoint_services",
            200,
            "Create Thread Thread Title",
        )
        self.assertEqual(warming[0], "warming")
        self.assertEqual(warming[1], "established_member_required")
        self.assertEqual(ready, ("ready", None, None))

    def test_namepros_post_login_permission_is_fail_closed_until_form_seen(self):
        denied = browser_assistant._classify_post_login_readiness(
            "namepros_promotional",
            403,
            "You have insufficient privileges to post threads here.",
        )
        ready = browser_assistant._classify_post_login_readiness(
            "namepros_promotional",
            200,
            "Post Thread Thread Title",
        )
        unknown = browser_assistant._classify_post_login_readiness(
            "namepros_promotional",
            200,
            "Promotional marketplace",
        )
        self.assertEqual(denied[0], "in_progress")
        self.assertEqual(denied[1], "posting_permission_required")
        self.assertEqual(ready, ("ready", None, None))
        self.assertEqual(unknown[1], "posting_permission_unverified")


    def test_definitive_allowed_audit_does_not_retry_only_because_aux_url_was_challenged(self):
        row = {
            "decision": "allowed",
            "errors": [{"error": "HTTPError: 403 Client Error: Forbidden"}],
            "inspections": [{
                "http_status": 403,
                "text_excerpt": "Cloudflare security check",
            }],
        }
        self.assertFalse(guard._audit_has_transient_transport_error(row))

    def test_dead_rule_audit_is_terminal_but_transient_5xx_is_not(self):
        dead = {
            "decision": "review",
            "inspections": [{"http_status": 404, "text_excerpt": "Not Found"}],
            "errors": [],
        }
        parked = {
            "decision": "review",
            "inspections": [{
                "http_status": 200,
                "text_excerpt": "The site you were looking for couldn't be found. "
                                "This domain is successfully pointed at WP Engine, but is not configured",
            }],
            "errors": [],
        }
        transient = {
            "decision": "review",
            "inspections": [{"http_status": 503, "text_excerpt": "Service Unavailable"}],
            "errors": [],
        }
        self.assertEqual(guard._audit_terminal_dead_reason(dead), "http_404")
        self.assertEqual(guard._audit_terminal_dead_reason(parked), "parked_or_unconfigured_domain")
        self.assertIsNone(guard._audit_terminal_dead_reason(transient))


    def test_talkingcity_readiness_is_fail_closed_until_explicit_authoring_action(self):
        guest = browser_assistant._classify_post_login_readiness(
            "talkingcity_services",
            200,
            "Topics Latest Activity New Topics On Off Services",
        )
        ready = browser_assistant._classify_post_login_readiness(
            "talkingcity_services",
            200,
            "Services Post New Topic",
        )
        self.assertEqual(guest[0], "in_progress")
        self.assertEqual(guest[1], "posting_permission_unverified")
        self.assertEqual(ready, ("ready", None, None))

    def test_verified_direct_post_routes_for_it_service_forums(self):
        surfaces = guard.crowd_seo.marketplace.VERIFIED_PUBLICATION_SURFACES
        self.assertEqual(
            surfaces["disc_pspx_ru"][0]["post_url"],
            "https://www.pspx.ru/forum/newthread.php?do=newthread&f=74",
        )
        self.assertEqual(surfaces["disc_pspx_ru"][0]["id"], "default")
        self.assertEqual(
            surfaces["searchengines_services"][0]["post_url"],
            "https://searchengines.guru/ru/forum/webmasters-jobs/programming?do=add",
        )
        self.assertEqual(
            surfaces["zismo_programming_services"][0]["post_url"],
            "https://zismo.biz/index.php?app=forums&module=post&section=post&do=new_post&f=92",
        )
        self.assertEqual(
            browser_assistant.POST_URLS["disc_pspx_ru"],
            surfaces["disc_pspx_ru"][0]["post_url"],
        )
        self.assertEqual(
            browser_assistant.POST_URLS["searchengines_services"],
            surfaces["searchengines_services"][0]["post_url"],
        )
        self.assertEqual(
            browser_assistant.POST_URLS["zismo_programming_services"],
            surfaces["zismo_programming_services"][0]["post_url"],
        )

    def test_it_project_with_marketing_capabilities_uses_both_niches(self):
        registrations = [
            {"platform": "it_forum", "status": "verification_required", "checkpoint": "captcha_required"},
            {"platform": "seo_forum", "status": "verification_required", "checkpoint": "captcha_required"},
        ]
        platforms = [
            {
                "key": "it_forum",
                "name": "IT Forum",
                "url": "https://it.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "it",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
            {
                "key": "seo_forum",
                "name": "SEO Forum",
                "url": "https://seo.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "marketing",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
        ):
            mixed = guard.crowd_seo._forum_matches(
                "it",
                limit=10,
                keywords=["CRM", "API", "автоматизация", "SEO", "реклама"],
            )
            pure_it = guard.crowd_seo._forum_matches(
                "it",
                limit=10,
                keywords=["CRM", "API", "автоматизация"],
            )

        self.assertEqual({x["platform"] for x in mixed}, {"it_forum", "seo_forum"})
        self.assertEqual({x["platform"] for x in pure_it}, {"it_forum"})


    def test_combined_captcha_age_terms_checkpoint_is_one_time_bootstrap(self):
        self.assertEqual(
            browser_assistant._registration_status_for_checkpoint("captcha_age_and_terms_required"),
            "verification_required",
        )
        self.assertIn(
            "captcha_age_and_terms_required",
            guard.marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS,
        )
        self.assertEqual(
            browser_assistant.REGISTRATION_URLS["digitalpoint_services"],
            "https://www.digitalpoint.com/register/",
        )


    def test_forum_matching_prefers_no_warmup_slot_on_critical_path(self):
        platforms = [
            {
                "key": "warm_forum",
                "name": "Warm Forum",
                "url": "https://warm.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "it",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
            {
                "key": "fast_forum",
                "name": "Fast Forum",
                "url": "https://fast.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "it",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
        ]
        registrations = [
            {"platform": "warm_forum", "status": "verification_required", "checkpoint": "captcha_required"},
            {"platform": "fast_forum", "status": "verification_required", "checkpoint": "captcha_required"},
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
            patch.object(
                guard.crowd_seo.marketplace,
                "PLATFORM_MATURITY_REQUIREMENTS",
                {"warm_forum": {"minimum_account_age_days": 15}},
            ),
        ):
            rows = guard.crowd_seo._forum_matches("it", limit=1, keywords=["разработка"], offer_type="services")

        self.assertEqual([x["platform"] for x in rows], ["fast_forum"])

    def test_forum_matching_prefers_known_bootstrap_over_unknown_registration(self):
        platforms = [
            {
                "key": "unknown_forum",
                "name": "Unknown Forum",
                "url": "https://unknown.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "it",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
            {
                "key": "bootstrap_forum",
                "name": "Bootstrap Forum",
                "url": "https://bootstrap.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
                "niche": "it",
                "free_policy": {"reason": "rules_allowed"},
                "publication_surfaces": [],
            },
        ]
        registrations = [
            {"platform": "unknown_forum", "status": "not_registered", "checkpoint": "manual_verification"},
            {"platform": "bootstrap_forum", "status": "verification_required", "checkpoint": "captcha_required"},
        ]
        with (
            patch.object(guard.crowd_seo.marketplace, "list_platforms", return_value=platforms),
            patch.object(guard.crowd_seo.marketplace, "registration_plan", return_value=registrations),
        ):
            rows = guard.crowd_seo._forum_matches(
                "it",
                limit=1,
                keywords=["разработка"],
                offer_type="services",
            )

        self.assertEqual([x["platform"] for x in rows], ["bootstrap_forum"])

    def test_freehostforum_rule_sources_are_registered(self):
        self.assertIn(
            "https://www.freehostforum.com/forum/advertising-forums/webmaster-marketplace/web-development-offers-and-requests",
            guard.platform_rules.RULE_URLS["freehostforum_webdev"],
        )
        self.assertEqual(
            browser_assistant.REGISTRATION_URLS["freehostforum_webdev"],
            "https://www.freehostforum.com/register",
        )


    def test_forum_baza_rule_audit_uses_exact_commercial_surfaces(self):
        self.assertEqual(
            guard.platform_rules.RULE_URLS["forum_baza_1c"],
            [
                "https://forum-baza.ru/index.php?board=62.0",
                "https://forum-baza.ru/index.php?board=65.0",
            ],
        )

    def test_rule_audit_passes_platform_key_to_browser_fallback(self):
        platform = SimpleNamespace(key="forum_baza_1c", name="Forum Baza", url="https://forum-baza.ru/")
        allowed = {
            "decision": "allowed",
            "reason": "explicit_service_or_advertising_section",
            "requirements": [],
            "text_excerpt": "Предложения об услугах разработчиков. НОВАЯ ТЕМА",
            "outbound_links": [],
            "http_status": 200,
            "url": "https://forum-baza.ru/index.php?board=62.0",
        }
        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(guard.platform_rules.marketplace, "get_platform", return_value=platform),
                patch.object(guard.platform_rules, "inspect_url", side_effect=RuntimeError("http_403")),
                patch.object(guard.platform_rules, "inspect_url_browser", return_value=dict(allowed)) as browser,
                patch.object(guard.platform_rules, "STATE_DIR", Path(td)),
            ):
                row = guard.platform_rules.inspect_platform("forum_baza_1c")
        self.assertEqual(row["decision"], "allowed")
        self.assertGreaterEqual(browser.call_count, 1)
        for call in browser.call_args_list:
            self.assertEqual(call.kwargs.get("platform_key"), "forum_baza_1c")

    def test_joomlaforum_exact_commercial_surface_and_routes_are_registered(self):
        platform = guard.crowd_seo.marketplace.get_platform("joomlaforum_jobs")
        self.assertIsNotNone(platform)
        surfaces = guard.crowd_seo.marketplace.VERIFIED_PUBLICATION_SURFACES["joomlaforum_jobs"]
        self.assertEqual(len(surfaces), 1)
        self.assertEqual(surfaces[0]["id"], "commercial_jobs")
        self.assertIn("board,29.0", surfaces[0]["url"])
        self.assertIn("action=post;board=29.0", surfaces[0]["post_url"])
        self.assertEqual(
            browser_assistant.REGISTRATION_URLS["joomlaforum_jobs"],
            "https://joomlaforum.ru/index.php?action=register",
        )
        self.assertEqual(
            browser_assistant.POST_URLS["joomlaforum_jobs"],
            "https://joomlaforum.ru/index.php?action=post;board=29.0",
        )

    def test_forum_baza_legal_consent_is_fail_closed_before_fill(self):
        class _Locator:
            def count(self):
                return 1
        class _Page:
            def locator(self, selector):
                if selector == 'input[name="legal_soglasie"]':
                    return _Locator()
                raise AssertionError(f"unexpected selector after fail-closed gate: {selector}")
        checkpoint, detail = browser_assistant._registration_policy_checkpoint(
            _Page(), "boris@example.test", {}, platform="forum_baza_1c"
        )
        self.assertEqual(checkpoint, "terms_acceptance_required")
        self.assertIn("не отмечает", detail)

    def test_joomlaforum_registration_fails_closed_on_separate_rules_consent(self):
        rule = {
            "inspections": [{
                "text_excerpt": (
                    "Нажимая кнопку вход или регистрация вы соглашаетесь "
                    "на обработку персональных данных."
                )
            }]
        }
        with patch.object(browser_assistant.platform_rules, "latest", return_value=rule):
            checkpoint, detail = browser_assistant._registration_policy_checkpoint(
                None,
                "boris@example.test",
                {},
                platform="joomlaforum_jobs",
            )
        self.assertEqual(checkpoint, "terms_acceptance_required")
        self.assertIn("не даёт", detail)

    def test_inventory_capacity_does_not_create_impossible_moving_discovery_target(self):
        project = {
            "id": "crowd_inventory_capacity",
            "plan_mode": "inventory",
            "publish_all_eligible": True,
            "target_count": 0,
            "bonus_count": 0,
        }
        matches = [
            {
                "platform": "ready_forum",
                "rule_status": "allowed",
                "publication_ready": True,
                "account_status": "ready",
                "checkpoint": None,
                "maturity_required": False,
                "account_warming": False,
            },
            {
                "platform": "terms_forum",
                "rule_status": "allowed",
                "publication_ready": False,
                "account_status": "verification_required",
                "checkpoint": "terms_acceptance_required",
                "maturity_required": False,
                "account_warming": False,
            },
            {
                "platform": "temporary_timeout",
                "rule_status": "allowed",
                "publication_ready": False,
                "account_status": "in_progress",
                "checkpoint": "preflight_timeout",
                "maturity_required": False,
                "account_warming": False,
            },
        ]
        row = guard.crowd_seo._capacity_for_project(project, matches)
        self.assertTrue(row["inventory_mode"])
        self.assertEqual(row["inventory_rule_verified_total"], 3)
        self.assertEqual(row["inventory_deferred_unreachable"], 1)
        self.assertEqual(row["operational_required"], 2)
        self.assertEqual(row["service_reachable_after_warmup"], 2)
        self.assertEqual(row["discovery_deficit_after_warmup"], 0)

    def test_contract_publish_all_flag_does_not_weaken_or_expand_paid_quantity(self):
        project = {
            "id": "crowd_contract_capacity",
            "plan_mode": "contract",
            "publish_all_eligible": True,
            "target_count": 2,
            "bonus_count": 0,
        }
        matches = [
            {
                "platform": f"forum_{i}",
                "rule_status": "allowed",
                "publication_ready": True,
                "account_status": "ready",
                "checkpoint": None,
                "maturity_required": False,
                "account_warming": False,
            }
            for i in range(4)
        ]
        row = guard.crowd_seo._capacity_for_project(project, matches)
        self.assertFalse(row["inventory_mode"])
        self.assertEqual(row["contract_required"], 2)
        self.assertEqual(row["operational_required"], 2)
        self.assertEqual(row["required"], 2)
        self.assertEqual(row["discovery_deficit_after_warmup"], 0)

    def test_bootstrap_priority_uses_inventory_as_soft_nonurgent_signal(self):
        project = {
            "id": "crowd_inventory",
            "site": "https://boris-ai.pro/",
            "status": "expanding_publication_pool",
            "content_status": "approved",
            "plan_mode": "inventory",
            "niche": "it",
            "keywords": ["crm", "api"],
            "target_count": 0,
            "bonus_count": 0,
            "offer_type": "services",
            "publish_all_eligible": True,
        }
        queue = {"items": [{"platform": "reserve_forum"}]}
        matches = [{
            "platform": "reserve_forum",
            "surface_id": "default",
            "checkpoint": "captcha_required",
            "publication_ready": False,
            "relevance": 3,
            "maturity_required": False,
            "account_warming": False,
        }]
        with (
            patch.object(guard.crowd_seo.marketplace, "platform_bootstrap_queue", return_value=queue),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=matches),
        ):
            snapshot = guard.crowd_seo.bootstrap_priority_snapshot([project])

        self.assertEqual(set(snapshot), {"reserve_forum"})
        self.assertFalse(snapshot["reserve_forum"]["urgent_for_active_projects"])
        self.assertEqual(snapshot["reserve_forum"]["relevant_projects"], 1)
        self.assertEqual(snapshot["reserve_forum"]["potential_slots"], 1)

    def test_bootstrap_priority_is_capped_to_real_project_plan(self):
        project = {
            "id": "crowd_plan",
            "site": "https://client.example/",
            "status": "expanding_publication_pool",
            "content_status": "approved",
            "niche": "it",
            "keywords": ["crm", "api"],
            "target_count": 2,
            "bonus_count": 0,
            "offer_type": "services",
            "publish_all_eligible": True,
        }
        matches = [
            {
                "platform": "first",
                "surface_id": "default",
                "checkpoint": "captcha_required",
                "publication_ready": False,
                "relevance": 3,
                "maturity_required": False,
                "account_warming": False,
            },
            {
                "platform": "second",
                "surface_id": "default",
                "checkpoint": "captcha_required",
                "publication_ready": False,
                "relevance": 2,
                "maturity_required": False,
                "account_warming": False,
            },
            {
                "platform": "reserve",
                "surface_id": "default",
                "checkpoint": "captcha_required",
                "publication_ready": False,
                "relevance": 1,
                "maturity_required": False,
                "account_warming": False,
            },
        ]
        queue = {
            "items": [
                {"platform": "first"},
                {"platform": "second"},
                {"platform": "reserve"},
            ]
        }
        with (
            patch.object(guard.crowd_seo.marketplace, "platform_bootstrap_queue", return_value=queue),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=matches),
        ):
            snapshot = guard.crowd_seo.bootstrap_priority_snapshot([project])

        self.assertEqual(set(snapshot), {"first", "second"})
        self.assertNotIn("reserve", snapshot)


    def test_refresh_rotates_still_eligible_reserve_outside_current_top_n(self):
        project = {
            "id": "crowd_test",
            "site": "https://client.example/",
            "niche": "business",
            "content_status": "approved",
            "target_count": 1,
            "bonus_count": 0,
            "forum_candidates": [],
            "placements": [
                {
                    "id": "crowd_test_001",
                    "platform": "slow_forum",
                    "surface_id": "default",
                    "status": "external_checkpoint",
                    "publication_url": None,
                    "error": None,
                }
            ],
        }
        rows = [project]
        fresh = [
            {
                "platform": "fast_forum",
                "surface_id": "default",
                "surface_name": "Fast",
                "name": "Fast",
                "url": "https://fast.example/",
                "publication_ready": False,
                "account_status": "verification_required",
                "niche": "business",
                "relevance": 3,
            },
            {
                "platform": "slow_forum",
                "surface_id": "default",
                "surface_name": "Slow",
                "name": "Slow",
                "url": "https://slow.example/",
                "publication_ready": False,
                "account_status": "verification_required",
                "niche": "business",
                "relevance": 1,
            },
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh),
        ):
            result = guard.crowd_seo.refresh_project_forums("crowd_test")

        placement = project["placements"][0]
        self.assertEqual(result["changed"], 1)
        self.assertEqual(placement["status"], "platform_replacement_required")
        self.assertEqual(placement["error"], "outside_current_priority_plan")

    def test_refresh_never_rotates_published_slot_only_for_priority(self):
        project = {
            "id": "crowd_test",
            "site": "https://client.example/",
            "niche": "business",
            "content_status": "needs_owner_approval",
            "target_count": 1,
            "bonus_count": 0,
            "forum_candidates": [],
            "placements": [
                {
                    "id": "crowd_test_001",
                    "platform": "slow_forum",
                    "surface_id": "default",
                    "status": "published_unverified",
                    "publication_url": "https://slow.example/thread/1",
                    "error": None,
                }
            ],
        }
        rows = [project]
        fresh = [
            {
                "platform": "fast_forum",
                "surface_id": "default",
                "surface_name": "Fast",
                "name": "Fast",
                "url": "https://fast.example/",
                "publication_ready": False,
                "account_status": "verification_required",
                "niche": "business",
                "relevance": 3,
            },
            {
                "platform": "slow_forum",
                "surface_id": "default",
                "surface_name": "Slow",
                "name": "Slow",
                "url": "https://slow.example/",
                "publication_ready": False,
                "account_status": "verification_required",
                "niche": "business",
                "relevance": 1,
            },
        ]
        with (
            patch.object(guard.crowd_seo, "_load", return_value=rows),
            patch.object(guard.crowd_seo, "_save"),
            patch.object(guard.crowd_seo, "_forum_matches", return_value=fresh),
        ):
            result = guard.crowd_seo.refresh_project_forums("crowd_test")

        placement = project["placements"][0]
        self.assertEqual(result["changed"], 0)
        self.assertEqual(placement["status"], "published_unverified")
        self.assertEqual(placement["publication_url"], "https://slow.example/thread/1")


    def test_verify_timeout_preserves_human_checkpoint(self):
        status, checkpoint, error = browser_assistant._verify_timeout_state(
            "captcha_required",
            "CAPTCHA уже обнаружена",
        )
        self.assertEqual(status, "verification_required")
        self.assertEqual(checkpoint, "captcha_required")
        self.assertEqual(error, "CAPTCHA уже обнаружена")

    def test_verify_timeout_without_checkpoint_stays_recoverable(self):
        status, checkpoint, error = browser_assistant._verify_timeout_state("", None)
        self.assertEqual(status, "in_progress")
        self.assertEqual(checkpoint, "login_probe_timeout")
        self.assertIn("повторит", error.lower())

    def test_verify_timeout_replaces_old_playwright_trace_with_semantic_checkpoint(self):
        status, checkpoint, error = browser_assistant._verify_timeout_state(
            "age_declaration_required",
            "playwright._impl._errors.Error: Page.goto: net::ERR_TIMED_OUT\nCall log:\n - navigating",
        )
        self.assertEqual(status, "verification_required")
        self.assertEqual(checkpoint, "age_declaration_required")
        self.assertNotIn("playwright", error.lower())
        self.assertNotIn("err_timed_out", error.lower())
        self.assertIn("перепроверит", error.lower())

    def test_network_timeout_errors_are_transient_navigation_failures(self):
        self.assertTrue(browser_assistant._is_transient_navigation_error(
            RuntimeError("Page.goto: net::ERR_TIMED_OUT at https://slow.example/")
        ))
        self.assertTrue(browser_assistant._is_transient_navigation_error(
            RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED")
        ))
        self.assertFalse(browser_assistant._is_transient_navigation_error(
            RuntimeError("Browser has been closed")
        ))


    def test_sellable_reserve_excludes_terminal_registration(self):
        platforms=[
            {"key":"goods_ok","channel_type":"forum","enabled_for_outreach":True,"url":"https://goods-ok.example/","publication_surfaces":[{"niches":["goods"]}]},
            {"key":"goods_terminal","channel_type":"forum","enabled_for_outreach":True,"url":"https://goods-terminal.example/","publication_surfaces":[{"niches":["goods"]}]},
        ]
        registrations=[
            {"platform":"goods_ok","status":"verification_required","checkpoint":"captcha_required"},
            {"platform":"goods_terminal","status":"blocked","checkpoint":"registration_disabled_by_site"},
        ]
        decisions={"goods_ok":{"decision":"allowed"},"goods_terminal":{"decision":"allowed"}}
        with (
            patch.object(guard.marketplace,"list_platforms",return_value=platforms),
            patch.object(guard.marketplace,"registration_plan",return_value=registrations),
            patch.object(guard.platform_rules,"latest",side_effect=lambda key: decisions.get(key)),
            patch.object(guard,"_active_order_targets_by_format",return_value={"goods":2,"services":0}),
        ):
            snap=guard._strategic_reserve_snapshot()
        self.assertEqual(snap["sellable_formats"]["goods"]["allowed_unique_sites"],1)
        self.assertEqual(snap["sellable_formats"]["goods"]["deficit"],1)
        self.assertEqual(snap["allowed_unique_sites_total"],1)

    def test_sellable_gap_prioritizes_goods_without_stopping_services_growth(self):
        platforms=[]
        decisions={}
        for i in range(18):
            key=f"svc_launch_{i}"
            platforms.append({"key":key,"channel_type":"forum","enabled_for_outreach":True,"url":f"https://svc-launch-{i}.example/","publication_surfaces":[{"niches":["services"]}]})
            decisions[key]={"decision":"allowed"}
        for i in range(10):
            key=f"goods_launch_{i}"
            platforms.append({"key":key,"channel_type":"forum","enabled_for_outreach":True,"url":f"https://goods-launch-{i}.example/","publication_surfaces":[{"niches":["goods"]}]})
            decisions[key]={"decision":"allowed"}
        with (
            patch.object(guard.marketplace,"list_platforms",return_value=platforms),
            patch.object(guard.platform_rules,"latest",side_effect=lambda key: decisions.get(key)),
            patch.object(guard,"_active_order_targets_by_format",return_value={"goods":18,"services":18}),
        ):
            snap=guard._strategic_reserve_snapshot()
            priorities=guard._discovery_priority_niches([])
        self.assertEqual(snap["sellable_formats"]["services"]["deficit"],0)
        self.assertEqual(snap["sellable_formats"]["goods"]["deficit"],8)
        self.assertFalse(snap["sellable_complete"])
        self.assertEqual(snap["formats"]["services"]["deficit"],82)
        self.assertEqual(snap["formats"]["goods"]["deficit"],90)
        self.assertEqual(snap["allowed_unique_sites_total"],28)
        self.assertEqual(snap["total_deficit"],172)
        self.assertEqual(priorities,["goods","services"])

    def test_strategic_search_resumes_after_both_formats_reach_sellable_floor(self):
        platforms=[]
        decisions={}
        for niche in ("goods","services"):
            for i in range(18):
                key=f"{niche}_ready_{i}"
                platforms.append({
                    "key":key,"channel_type":"forum","enabled_for_outreach":True,
                    "url":f"https://{niche}-ready-{i}.example/",
                    "publication_surfaces":[{"niches":[niche]}],
                })
                decisions[key]={"decision":"allowed"}
        with (
            patch.object(guard.marketplace,"list_platforms",return_value=platforms),
            patch.object(guard.platform_rules,"latest",side_effect=lambda key: decisions.get(key)),
            patch.object(guard,"_active_order_targets_by_format",return_value={"goods":18,"services":18}),
        ):
            snap=guard._strategic_reserve_snapshot()
            priorities=guard._discovery_priority_niches([])
        self.assertTrue(snap["sellable_complete"])
        self.assertEqual(priorities,["goods","services"])
        self.assertGreater(snap["formats"]["goods"]["deficit"],0)
        self.assertGreater(snap["formats"]["services"]["deficit"],0)

    def test_dynamic_goods_surface_keeps_narrow_directory_vertical(self):
        svc=guard.marketplace
        candidate={
            "commercial_context":True,
            "url":"https://example.test/forum/classifieds",
            "name":"Audio Marketplace",
            "niche":"goods",
            "directory_category":"Home/Subcategory/Shopping-and-Ecommerce/Electricals-and-Electronic-Goods/",
            "query":"findaforum electricals classified",
            "source_surface_label":"Audio Marketplace",
            "evidence":["classifieds","for sale"],
        }
        rows=svc._dynamic_verified_publication_surface(candidate)
        self.assertEqual(len(rows),1)
        self.assertTrue(any(x in rows[0]["required_terms"] for x in ["электрон","аудио","electronics","audio"]))
        self.assertFalse(guard.crowd_seo._surface_matches_project(rows[0],"furniture",["шкафы","мебель","доставка"],"goods"))
        self.assertTrue(guard.crowd_seo._surface_matches_project(rows[0],"goods",["электроника","аудио техника"],"goods"))

    def test_dynamic_broad_goods_board_remains_universal(self):
        svc=guard.marketplace
        candidate={
            "commercial_context":True,
            "url":"https://example.test/forum/classifieds",
            "name":"Business Marketplace",
            "niche":"goods",
            "directory_category":"Home/Subcategory/Shopping-and-Ecommerce/Classifieds/",
            "query":"business marketplace товары для бизнеса",
            "source_surface_label":"Business Marketplace",
            "evidence":["classifieds","vendors","suppliers"],
        }
        rows=svc._dynamic_verified_publication_surface(candidate)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["required_terms"],[])
        self.assertTrue(guard.crowd_seo._surface_matches_project(rows[0],"furniture",["шкафы","мебель","доставка"],"goods"))

    def test_unmapped_directory_goods_category_fails_closed(self):
        svc=guard.marketplace
        candidate={
            "commercial_context":True,
            "url":"https://example.test/forum/classifieds",
            "name":"Cycling Classifieds",
            "niche":"goods",
            "directory_category":"Home/Subcategory/Sports/Cycling/",
            "query":"findaforum cycling classifieds",
            "source_surface_label":"Classifieds",
            "evidence":["classifieds","for sale"],
        }
        rows=svc._dynamic_verified_publication_surface(candidate)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["required_terms"],["__goods_vertical_review_required__"])
        self.assertFalse(
            guard.crowd_seo._surface_matches_project(
                rows[0],"goods",["товары","каталог","доставка"],"goods"
            )
        )

    def test_hardware_directory_goods_maps_to_electronics_vertical(self):
        svc=guard.marketplace
        candidate={
            "commercial_context":True,
            "url":"https://example.test/forum/for-sale",
            "name":"Hardware For Sale",
            "niche":"goods",
            "directory_category":"Home/Subcategory/Computers-and-Internet/Hardware/",
            "query":"hardware for sale",
            "source_surface_label":"For Sale",
            "evidence":["classifieds","for sale"],
        }
        rows=svc._dynamic_verified_publication_surface(candidate)
        self.assertEqual(len(rows),1)
        self.assertIn("hardware",rows[0]["required_terms"])
        self.assertTrue(
            guard.crowd_seo._surface_matches_project(
                rows[0],"goods",["компьютерное hardware оборудование"],"goods"
            )
        )
        self.assertFalse(
            guard.crowd_seo._surface_matches_project(
                rows[0],"furniture",["шкафы","мебель"],"goods"
            )
        )

    def test_strategic_union_over_200_cannot_hide_format_deficit(self):
        platforms=[]
        decisions={}
        for i in range(100):
            key=f"svc_{i}"
            platforms.append({"key":key,"channel_type":"forum","enabled_for_outreach":True,"url":f"https://svc{i}.example/","publication_surfaces":[{"niches":["services"]}]})
            decisions[key]={"decision":"allowed"}
        for i in range(60):
            key=f"goods_{i}"
            platforms.append({"key":key,"channel_type":"forum","enabled_for_outreach":True,"url":f"https://goods{i}.example/","publication_surfaces":[{"niches":["goods"]}]})
            decisions[key]={"decision":"allowed"}
        for i in range(70):
            key=f"other_{i}"
            platforms.append({"key":key,"channel_type":"forum","enabled_for_outreach":True,"url":f"https://other{i}.example/","publication_surfaces":[{"niches":["business"]}]})
            decisions[key]={"decision":"allowed"}
        with (
            patch.object(guard.marketplace,"list_platforms",return_value=platforms),
            patch.object(guard.platform_rules,"latest",side_effect=lambda key: decisions.get(key)),
        ):
            snap=guard._strategic_reserve_snapshot()
        # Unrelated business-only forums must not inflate the goods/services
        # strategic union. Only 100 service + 60 goods sites count here.
        self.assertEqual(snap["allowed_unique_sites_total"],160)
        self.assertEqual(snap["total_deficit"],40)
        self.assertEqual(snap["formats"]["services"]["deficit"],0)
        self.assertEqual(snap["formats"]["goods"]["deficit"],40)
        self.assertEqual(snap["max_deficit"],40)
        self.assertFalse(snap["complete"])
        self.assertTrue(snap["sellable_complete"])


    def test_dynamic_exact_goods_classifieds_can_be_allowed_fail_closed(self):
        key = "disc_goods_exact_test"
        candidate = {"key": key, "name": "Camera Classifieds", "url": "https://goods.example/forums/classifieds.42/", "domain": "goods.example", "niche": "goods", "commercial_context": True, "create_topic_hint": True, "source_surface_label": "Classifieds", "evidence": ["marketplace", "create_topic"], "score": 20}
        inspection = {"decision": "review", "requirements": [], "http_status": 200, "text_sha256": "same", "text_excerpt": "Camera Classifieds Buy Sell Marketplace Post Thread", "evidence": {"paid": [], "prohibited": [], "links": []}}
        platform = SimpleNamespace(key=key, name="Camera Classifieds", url=candidate["url"])
        with tempfile.TemporaryDirectory() as td:
            with (patch.object(guard.platform_rules.marketplace, "get_platform", return_value=platform), patch.object(guard.platform_rules, "inspect_url", return_value=dict(inspection)), patch.object(guard.platform_rules, "inspect_url_browser", return_value=dict(inspection)), patch.object(guard.platform_rules, "STATE_DIR", Path(td)), patch("app.services.forum_discovery.list_candidates", return_value=[candidate])):
                row = guard.platform_rules.inspect_platform(key)
        self.assertEqual(row["decision"], "allowed")
        self.assertIn("use_exact_discovered_goods_surface_only", row["requirements"])


    def test_dynamic_generic_forum_root_never_becomes_goods_allowed(self):
        key = "disc_goods_root_test"
        candidate = {"key": key, "name": "Marketplace Community", "url": "https://goods-root.example/forums/", "domain": "goods-root.example", "niche": "goods", "commercial_context": True, "create_topic_hint": True, "evidence": ["marketplace", "create_topic"], "score": 20}
        inspection = {"decision": "review", "requirements": [], "http_status": 200, "text_sha256": "same-root", "text_excerpt": "Marketplace Community Post Thread", "evidence": {"paid": [], "prohibited": [], "links": []}}
        platform = SimpleNamespace(key=key, name="Marketplace Community", url=candidate["url"])
        with tempfile.TemporaryDirectory() as td:
            with (patch.object(guard.platform_rules.marketplace, "get_platform", return_value=platform), patch.object(guard.platform_rules, "inspect_url", return_value=dict(inspection)), patch.object(guard.platform_rules, "inspect_url_browser", return_value=dict(inspection)), patch.object(guard.platform_rules, "STATE_DIR", Path(td)), patch("app.services.forum_discovery.list_candidates", return_value=[candidate])):
                row = guard.platform_rules.inspect_platform(key)
        self.assertEqual(row["decision"], "review")


    def test_commercial_volume_is_explicit_order_not_fixed_package(self):
        self.assertEqual(
            guard.crowd_seo._project_required_count({
                "plan_mode":"contract","target_count":7,"bonus_count":2,
            }),
            9,
        )
        self.assertEqual(
            guard.crowd_seo._project_required_count({
                "plan_mode":"contract","target_count":31,"bonus_count":4,
            }),
            35,
        )
        self.assertEqual(
            guard.crowd_seo._project_required_count({
                "plan_mode":"inventory","target_count":0,"bonus_count":0,
            }),
            0,
        )
        with self.assertRaisesRegex(ValueError, "crowd_seo_contract_target_required"):
            guard.crowd_seo._project_required_count({"plan_mode":"contract"})

    def test_daily_execution_policy_counts_guard_runs_and_detects_gap(self):
        now = datetime(2026, 9, 10, 20, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "daily.json"
            with patch.object(guard, "DAILY_EXECUTION_FILE", state):
                first = guard._daily_execution_snapshot(now, mark_run=True)
                self.assertEqual(first["runs"], 1)
                self.assertTrue(first["cadence_ok"])
                self.assertEqual(
                    first["scheduled_runs_per_day"],
                    guard.reserve_policy.GUARD_SCHEDULED_RUNS_PER_DAY,
                )

                late = guard._daily_execution_snapshot(
                    now + timedelta(minutes=35),
                    mark_run=True,
                )
                self.assertEqual(late["runs"], 2)
                self.assertEqual(late["expected_runs_since_first"], 4)
                self.assertEqual(late["historical_missed_runs"], 2)
                self.assertGreater(late["last_gap_minutes"], late["max_recent_gap_minutes"])
                self.assertFalse(late["cadence_ok"])

                recovered = late
                for minute in (36, 37):
                    recovered = guard._daily_execution_snapshot(
                        now + timedelta(minutes=minute),
                        mark_run=True,
                    )
                self.assertTrue(recovered["cadence_ok"])

    def test_daily_execution_policy_resets_on_new_utc_day(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "daily.json"
            with patch.object(guard, "DAILY_EXECUTION_FILE", state):
                day1 = guard._daily_execution_snapshot(
                    datetime(2026, 9, 10, 23, 55, tzinfo=timezone.utc),
                    mark_run=True,
                )
                day2 = guard._daily_execution_snapshot(
                    datetime(2026, 9, 11, 0, 5, tzinfo=timezone.utc),
                    mark_run=True,
                )
                self.assertEqual(day1["runs"], 1)
                self.assertEqual(day2["date"], "2026-09-11")
                self.assertEqual(day2["runs"], 1)
                self.assertTrue(day2["cadence_ok"])

    def test_active_order_capacity_uses_real_order_quantities(self):
        projects=[
            {"id":"goods7","plan_mode":"contract","target_count":7,"bonus_count":2,"offer_type":"goods"},
            {"id":"goods31","plan_mode":"contract","target_count":31,"bonus_count":4,"offer_type":"goods"},
            {"id":"services12","plan_mode":"contract","target_count":12,"bonus_count":0,"offer_type":"services"},
            {"id":"inventory","plan_mode":"inventory","target_count":0,"bonus_count":0,"offer_type":"services"},
        ]
        with (
            patch.object(guard.crowd_seo,"list_projects",return_value=projects),
            patch.object(guard.crowd_seo,"project_is_active",return_value=True),
            patch.object(guard.crowd_seo,"_legacy_offer_type",side_effect=lambda p:p["offer_type"]),
        ):
            targets=guard._active_order_targets_by_format()
        self.assertEqual(targets["goods"],35)
        self.assertEqual(targets["services"],12)
        self.assertNotEqual(max(targets.values()),18)


    def test_development_only_campaign_is_always_services_not_mixed_goods(self):
        project = {
            "niche": "it",
            "offer_type": "mixed",
            "campaign_profile": {
                "purpose": "lead_generation_for_custom_software_development",
                "campaign_scope": "software_development_only_v1",
                "content_policy": "development_only_one_intent_one_landing_10_20_keywords_bottom",
            },
        }
        self.assertEqual(guard.crowd_seo._legacy_offer_type(project), "services")


    def test_sensitive_mapping_keys_are_redacted_before_persistence(self):
        raw = {"filled": {"password": "ExampleSecret123!", "username": "demo"}}
        cleaned = guard.marketplace.redact_sensitive_value(raw)
        self.assertEqual(cleaned["filled"]["password"], "[REDACTED_SECRET]")
        self.assertEqual(cleaned["filled"]["username"], "demo")

    def test_browser_run_log_never_persists_filled_password(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(browser_assistant, "RUNS", Path(td)):
                path = browser_assistant._log(
                    "example",
                    "register",
                    {"filled": {"password": "ExampleSecret123!", "username": "demo"}},
                )
                raw = path.read_text(encoding="utf-8")
                payload = json.loads(raw)
        self.assertNotIn("ExampleSecret123!", raw)
        self.assertEqual(payload["filled"]["password"], "[REDACTED_SECRET]")


def test_legacy_it_and_marketing_projects_default_to_services_when_evidence_is_empty():
    assert guard.crowd_seo._legacy_offer_type({"niche": "it", "keywords": [], "target_pages": []}) == "services"
    assert guard.crowd_seo._legacy_offer_type({"niche": "marketing", "keywords": [], "target_pages": []}) == "services"



def test_registration_flows_never_auto_accept_legal_or_identity_declarations():
    import inspect
    fill_source = inspect.getsource(browser_assistant._fill_registration_adapter)
    register_source = inspect.getsource(browser_assistant.register)
    checkpoint_source = inspect.getsource(browser_assistant.complete_human_checkpoint)
    assert ".check(" not in fill_source
    assert "agree.click(" not in register_source
    assert "data_consent.check(" not in register_source
    assert "digest.check(" not in register_source
    assert "agree.click(" not in checkpoint_source

def test_external_sso_only_registration_is_a_human_checkpoint_not_form_error():
    class _Body:
        def __init__(self, text): self.text = text
        def inner_text(self, timeout=0): return self.text

    class _Fields:
        def __init__(self, visible=False): self.visible = visible
        def count(self): return 1 if self.visible else 0
        def nth(self, _idx): return self
        def is_visible(self): return self.visible

    class _Page:
        def __init__(self, local=False): self.local = local
        def locator(self, selector):
            if selector == "body":
                return _Body("Регистрация. Быстрая регистрация с помощью Telegram. Продолжить через Google.")
            return _Fields(self.local and selector == 'input[type="email"]')

    checkpoint, detail = browser_assistant._external_sso_only_checkpoint(_Page(local=False))
    assert checkpoint == "external_account_sso_required"
    assert "внеш" in detail.lower()
    assert browser_assistant._external_sso_only_checkpoint(_Page(local=True)) == (None, None)


def test_dynamic_freelance_projects_section_is_service_surface():
    rows = guard.marketplace._dynamic_verified_publication_surface({
        "commercial_context": True,
        "url": "https://codeby.example/forums/karera-vakansii-proekty.23/",
        "name": "Карьера в ИБ: вакансии и проекты",
        "source_surface_label": "Карьера в ИБ: вакансии и проекты",
        "niche": "it",
        "evidence": ["проекты для фрилансеров"],
    })
    assert len(rows) == 1
    assert rows[0]["niches"] == ["services"]


def test_registration_preflight_never_fills_birth_date_and_exposes_combined_checkpoint():
    import inspect
    prefill_source = inspect.getsource(browser_assistant._prefill_account_fields)
    register_source = inspect.getsource(browser_assistant.register)
    assert 'profile.get("birth_date")' not in prefill_source
    assert 'filled["birth_date"]' not in prefill_source
    assert 'filled[f"birth_' not in prefill_source
    assert 'captcha_age_declaration_required' in register_source
    assert 'captcha_age_declaration_required' in guard.marketplace.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
    assert 'captcha_age_declaration_required' in guard.marketplace.INTERACTIVE_BROWSER_CHECKPOINTS


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_strategic_discovery_progress_is_not_repeated_every_guard_tick():
    now=datetime(2026,9,6,20,0,tzinfo=timezone.utc)
    recent={"at":(now-timedelta(hours=2)).isoformat(),"new_or_refreshed":3}
    row=guard._discovery_schedule(recent,80,urgent=False,now=now)
    assert row["due"] is False
    assert row["forced_due"] is False
    assert row["force_interval_minutes"] == 360


def test_strategic_discovery_reopens_after_bounded_interval():
    now=datetime(2026,9,6,20,0,tzinfo=timezone.utc)
    old={"at":(now-timedelta(hours=7)).isoformat(),"new_or_refreshed":3}
    row=guard._discovery_schedule(old,80,urgent=False,now=now)
    assert row["due"] is True
    assert row["forced_due"] is True
