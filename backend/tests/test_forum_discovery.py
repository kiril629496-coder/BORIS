from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import forum_discovery


class ForumDiscoveryPlanTests(unittest.TestCase):
    def test_guard_budget_covers_all_niches_and_multiple_intents(self):
        plan = forum_discovery.query_plan(max_queries=30)
        self.assertEqual(len(plan), 30)
        self.assertEqual(len(set(plan)), 30)

        by_niche = Counter(niche for niche, _ in plan)
        self.assertEqual(set(by_niche), set(forum_discovery.NICHES))
        self.assertTrue(all(count == 2 for count in by_niche.values()))

        intent_fragments = [
            "предлагаю услуги",
            "новая тема",
            "биржа услуг",
            '"услуги"',
            "предложение услуг",
            "создать тему",
            "работа и партнёрство",
            "исполнители",
            "коммерческие предложения",
            "объявления",
            "поиск подрядчика",
        ]
        used = {
            fragment
            for fragment in intent_fragments
            if any(fragment in query for _, query in plan)
        }
        self.assertGreaterEqual(len(used), 9)

    def test_priority_niche_gets_most_of_deficit_budget(self):
        plan = forum_discovery.query_plan(max_queries=48, priority_niches=["marketing"])
        self.assertEqual(len(plan), 48)
        self.assertEqual(len(set(plan)), 48)
        by_niche = Counter(niche for niche, _ in plan)
        self.assertGreaterEqual(by_niche["marketing"], 32)
        self.assertGreater(by_niche["marketing"], max(v for k, v in by_niche.items() if k != "marketing"))
        self.assertTrue(all(query.startswith("форум ") for _, query in plan))

    def test_goods_priority_uses_full_budget_and_new_verticals(self):
        plan = forum_discovery.query_plan(max_queries=48, priority_niches=["goods"])
        self.assertEqual(len(plan), 48)
        self.assertTrue(all(niche == "goods" for niche, _ in plan))
        queries = [query for _, query in plan]
        self.assertTrue(any("электроника" in query for query in queries))
        self.assertTrue(any("одежда" in query for query in queries))
        self.assertTrue(any("косметика" in query for query in queries))
        self.assertTrue(any("детские товары" in query for query in queries))
        self.assertTrue(any('"продам" "новая тема"' in query for query in queries))
        self.assertTrue(any('"товары" "новая тема"' in query for query in queries))

    def test_priority_plan_includes_guest_post_intents(self):
        plan = forum_discovery.query_plan(max_queries=48, priority_niches=["marketing"])
        marketing_queries = [query for niche, query in plan if niche == "marketing"]
        self.assertTrue(any("без регистрации" in query and "новая тема" in query for query in marketing_queries))
        self.assertTrue(any("гостям разрешено создавать темы" in query for query in marketing_queries))

    def test_unknown_priority_falls_back_to_balanced(self):
        priority = forum_discovery.query_plan(max_queries=30, priority_niches=["unknown"])
        balanced = forum_discovery.query_plan(max_queries=30)
        self.assertEqual(priority, balanced)

    def test_forumdirectory_pagination_and_tracking_cleanup(self):
        soup = forum_discovery.BeautifulSoup(
            '<a href="?page=2">2</a><a href="?page=8">Last</a>',
            "html.parser",
        )
        self.assertEqual(forum_discovery._directory_last_page(soup), 8)
        cleaned = forum_discovery._strip_directory_tracking(
            "https://example.com/forum/?id=7&utm_source=forumdirectory&utm_campaign=test"
        )
        self.assertEqual(cleaned, "https://example.com/forum/?id=7")
        self.assertEqual(
            forum_discovery._strip_directory_tracking(
                "https://example.com/forumdisplay.php?amp%3Bf=18"
            ),
            "https://example.com/forumdisplay.php?f=18",
        )
        self.assertIn("facebook.com", forum_discovery.BLOCK_DOMAINS)

    def test_directory_candidates_are_cached_and_merged(self):
        entry = {
            "title": "Example Services Forum",
            "url": "https://services.example/forum/",
            "domain": "services.example",
            "snippet": "Forum marketplace services jobs",
            "engine": "forumdirectory",
            "directory_category": "technology.14",
            "directory_niche": "services",
        }
        accepted = forum_discovery.Candidate(
            key="disc_services_example",
            name="Example Services Forum",
            url=entry["url"],
            domain=entry["domain"],
            niche="services",
            query="forumdirectory technology marketplace services",
            score=15,
            forum_detected=True,
            commercial_context=True,
            register_url="https://services.example/register",
            create_topic_hint=True,
            evidence=["services", "marketplace"],
            status="candidate",
            discovered_at=datetime.now(timezone.utc).isoformat(),
            last_checked_at=datetime.now(timezone.utc).isoformat(),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            discovered = root / "discovered.json"
            scan = root / "scan.json"
            terminal = root / "terminal.json"
            with (
                patch.object(forum_discovery, "DISCOVERY_FILE", discovered),
                patch.object(forum_discovery, "DIRECTORY_SCAN_FILE", scan),
                patch.object(forum_discovery, "TERMINAL_DOMAINS_FILE", terminal),
                patch.object(forum_discovery, "_forumdirectory_entries", return_value=([entry], [])),
                patch.object(forum_discovery, "_inspect_candidate", return_value=accepted),
            ):
                first = forum_discovery.discover_forum_directory(max_inspections=10, workers=1)
                second = forum_discovery.discover_forum_directory(max_inspections=10, workers=1)

        self.assertEqual(first["new_candidates"], 1)
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(first["total_candidates"], 1)
        self.assertEqual(second["due_inspections"], 0)
        self.assertGreaterEqual(second["cached_skips"], 1)

    def test_findaforum_seed_pages_expand_only_matching_safe_subcategories(self):
        html = """
        <html><body>
          <a href="/Home/Subcategory/Shopping-and-Ecommerce/Classifieds/">Classifieds</a>
          <a href="/Home/Subcategory/Shopping-and-Ecommerce/Gambling/">Gambling</a>
          <a href="/Home/Subcategory/Business-and-Economy/SEO/">SEO</a>
        </body></html>
        """
        soup = forum_discovery.BeautifulSoup(html, "html.parser")
        with (
            patch.object(forum_discovery, "FINDAFORUM_SEEDS", {}),
            patch.object(
                forum_discovery,
                "FINDAFORUM_CATEGORY_NICHES",
                {"Shopping-and-Ecommerce": "goods"},
            ),
            patch.object(
                forum_discovery,
                "_findaforum_get_soup",
                return_value=(
                    SimpleNamespace(
                        url="https://www.findaforum.net/Home/Category/Shopping-and-Ecommerce/"
                    ),
                    soup,
                ),
            ),
        ):
            seeds, errors = forum_discovery._findaforum_seed_pages()

        seed_map = dict(seeds)
        self.assertEqual(errors, [])
        self.assertEqual(
            seed_map["Home/Subcategory/Shopping-and-Ecommerce/Classifieds/"],
            "goods",
        )
        self.assertNotIn(
            "Home/Subcategory/Shopping-and-Ecommerce/Gambling/",
            seed_map,
        )
        self.assertNotIn("Home/Subcategory/Business-and-Economy/SEO/", seed_map)
        self.assertIn("Home/Category/Shopping-and-Ecommerce/", seed_map)

    def test_findaforum_niche_mapping(self):
        self.assertEqual(
            forum_discovery._findaforum_niche_for_subcategory("SEO", "business"),
            "marketing",
        )
        self.assertEqual(
            forum_discovery._findaforum_niche_for_subcategory("Classifieds", "services"),
            "goods",
        )
        self.assertEqual(
            forum_discovery._findaforum_niche_for_subcategory("Programming and Software Development", "services"),
            "it",
        )

    def test_findaforum_detail_prefers_commercial_forum_surface(self):
        html = """
        <html><head><title>Example Business Forum - FindAForum</title></head><body>
          <a href="https://facebook.com/example">Facebook Page</a>
          <a href="https://exampleforum.com/forum/">Main Forum</a>
          <a href="https://exampleforum.com/forumdisplay.php/34-Free-Advertising-Forums">Free Advertising Forums</a>
          <a href="https://exampleforum.com/forumdisplay.php/48-Employment-Wanted">Jobs</a>
        </body></html>
        """
        soup = forum_discovery.BeautifulSoup(html, "html.parser")
        with patch.object(
            forum_discovery,
            "_findaforum_get_soup",
            return_value=(SimpleNamespace(url="https://www.findaforum.net/Forums/example/"), soup),
        ):
            row = forum_discovery._findaforum_parse_detail({
                "detail_url": "https://www.findaforum.net/Forums/example/",
                "directory_niche": "services",
                "directory_category": "small-business",
            })

        self.assertIsNotNone(row)
        self.assertEqual(row["domain"], "exampleforum.com")
        self.assertTrue(row["commercial_surface_hint"])
        self.assertIn("Free-Advertising", row["url"])
        self.assertNotIn("facebook.com", row["url"])

    def test_findaforum_discovery_merges_candidate_but_does_not_rule_allow_it(self):
        entry = {
            "title": "Example Marketplace",
            "url": "https://market.example/forum/marketplace",
            "domain": "market.example",
            "snippet": "Forum marketplace services jobs",
            "engine": "findaforum",
            "directory_category": "small-business",
            "directory_niche": "services",
            "commercial_surface_hint": True,
            "surface_label": "Marketplace",
            "detail_url": "https://www.findaforum.net/Forums/market-example/",
        }
        accepted = forum_discovery.Candidate(
            key="disc_market_example",
            name="Example Marketplace",
            url=entry["url"],
            domain=entry["domain"],
            niche="services",
            query="findaforum marketplace",
            score=18,
            forum_detected=True,
            commercial_context=True,
            register_url="https://market.example/register",
            create_topic_hint=True,
            evidence=["marketplace", "services"],
            status="candidate",
            discovered_at=datetime.now(timezone.utc).isoformat(),
            last_checked_at=datetime.now(timezone.utc).isoformat(),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            discovered = root / "discovered.json"
            scan = root / "findaforum_scan.json"
            terminal = root / "terminal.json"
            with (
                patch.object(forum_discovery, "DISCOVERY_FILE", discovered),
                patch.object(forum_discovery, "FINDAFORUM_SCAN_FILE", scan),
                patch.object(forum_discovery, "TERMINAL_DOMAINS_FILE", terminal),
                patch.object(forum_discovery, "_findaforum_entries", return_value=([entry], [], True)),
                patch.object(forum_discovery, "_inspect_candidate", return_value=accepted),
            ):
                result = forum_discovery.discover_findaforum(max_inspections=10, workers=1)
                saved = json.loads(discovered.read_text())

        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(saved[0]["status"], "candidate")
        self.assertNotIn("rules_allowed", saved[0])

    def test_dead_directory_url_is_marked_terminal_candidate(self):
        hit = {
            "title": "Dead Forum",
            "url": "https://dead.example/forum/",
            "domain": "dead.example",
            "snippet": "forum marketplace services",
            "engine": "forumdirectory",
        }
        response = SimpleNamespace(status_code=404, url=hit["url"], text="not found")
        with patch.object(forum_discovery.requests, "get", return_value=response):
            candidate = forum_discovery._inspect_candidate(hit, "services", "forum services")

        self.assertIsNone(candidate)
        self.assertEqual(hit["_terminal_reason"], "http_404")
        self.assertEqual(hit["_terminal_evidence"], hit["url"])

    def test_parked_directory_domain_is_marked_terminal_candidate(self):
        hit = {
            "title": "Old Services Forum",
            "url": "https://parked.example/forum/",
            "domain": "parked.example",
            "snippet": "forum services",
            "engine": "findaforum",
        }
        response = SimpleNamespace(
            status_code=200,
            url=hit["url"],
            text="<html><body>The site you were looking for couldn't be found. "
                 "This domain is successfully pointed at WP Engine, but is not configured for an account.</body></html>",
        )
        with patch.object(forum_discovery.requests, "get", return_value=response):
            candidate = forum_discovery._inspect_candidate(hit, "services", "forum services")

        self.assertIsNone(candidate)
        self.assertEqual(hit["_terminal_reason"], "parked_or_unconfigured_domain")

    def test_transient_5xx_is_not_terminalized(self):
        hit = {
            "title": "Temporarily Down Forum",
            "url": "https://temporary.example/forum/",
            "domain": "temporary.example",
            "snippet": "forum marketplace",
            "engine": "forumdirectory",
        }
        response = SimpleNamespace(status_code=503, url=hit["url"], text="temporary")
        with patch.object(forum_discovery.requests, "get", return_value=response):
            candidate = forum_discovery._inspect_candidate(hit, "goods", "forum marketplace")

        self.assertIsNone(candidate)
        self.assertNotIn("_terminal_reason", hit)

    def test_ddg_circuit_opens_after_two_access_failures(self):
        calls = []

        def fake_search(query, limit=10, *, use_ddg=True):
            calls.append(use_ddg)
            if len(calls) == 1:
                return [], ["duckduckgo_lite: HTTPError: 403 Client Error"]
            if len(calls) == 2:
                return [], ["duckduckgo_lite: ConnectionError: connection reset by peer"]
            return [], []

        plan = [
            ("marketing", 'форум SEO "без регистрации" "новая тема"'),
            ("marketing", 'форум реклама "гостям разрешено создавать темы"'),
            ("marketing", 'форум SEO "предлагаю услуги"'),
            ("marketing", 'форум реклама "биржа услуг"'),
        ]
        with (
            patch.object(forum_discovery, "query_plan", return_value=plan),
            patch.object(forum_discovery, "_search_all", side_effect=fake_search),
            patch.object(forum_discovery, "_load", return_value=[]),
            patch.object(forum_discovery, "_save"),
            patch.object(forum_discovery.time, "sleep"),
        ):
            status = forum_discovery.discover(max_queries=4, per_query=3, sleep_s=0)

        self.assertEqual(calls, [True, True, False, False])
        self.assertFalse(status["search_circuit"]["duckduckgo_enabled_at_end"])
        self.assertEqual(status["search_circuit"]["duckduckgo_access_failures"], 2)
        self.assertEqual(status["search_circuit"]["duckduckgo_disabled_after_query"], plan[1][1])

    def test_terminal_domain_registry_excludes_and_reopens_after_ttl(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            terminal=root/"terminal.json"
            discovered=root/"discovered.json"
            with (
                patch.object(forum_discovery,"TERMINAL_DOMAINS_FILE",terminal),
                patch.object(forum_discovery,"DISCOVERY_FILE",discovered),
            ):
                discovered.write_text(json.dumps([{
                    "domain":"blocked.example",
                    "score":12,
                    "commercial_context":True,
                    "name":"Blocked Forum",
                    "url":"https://blocked.example/forum",
                    "query":"форум SEO услуги",
                }]),encoding="utf-8")
                row=forum_discovery.mark_terminal_domain(
                    "https://www.blocked.example/forum",
                    "rules_blocked",
                    source="disc_blocked_example",
                )
                self.assertEqual(row["domain"],"blocked.example")
                self.assertEqual(forum_discovery.list_candidates(min_score=1),[])
                saved=json.loads(discovered.read_text(encoding="utf-8"))
                self.assertEqual(saved[0]["status"],"terminal_policy_blocked")

                old=(datetime.now(timezone.utc)-timedelta(days=forum_discovery.TERMINAL_RECHECK_DAYS+1)).isoformat()
                terminal.write_text(json.dumps([{
                    "domain":"blocked.example",
                    "reason":"rules_blocked",
                    "checked_at":old,
                }]),encoding="utf-8")
                self.assertNotIn("blocked.example",forum_discovery.terminal_domain_map())
                self.assertEqual(len(forum_discovery.list_candidates(min_score=1)),1)

    def test_human_checkpoint_is_not_terminal_and_reopens_old_state(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            terminal=root/"terminal.json"
            discovered=root/"discovered.json"
            terminal.write_text(json.dumps([{
                "domain":"captcha.example",
                "reason":"captcha_required",
                "checked_at":datetime.now(timezone.utc).isoformat(),
            }]),encoding="utf-8")
            discovered.write_text(json.dumps([{
                "domain":"captcha.example",
                "score":12,
                "commercial_context":True,
                "name":"Captcha Forum",
                "url":"https://captcha.example/forum",
                "query":"форум услуги",
                "status":"terminal_policy_blocked",
            }]),encoding="utf-8")
            with (
                patch.object(forum_discovery,"TERMINAL_DOMAINS_FILE",terminal),
                patch.object(forum_discovery,"DISCOVERY_FILE",discovered),
            ):
                self.assertNotIn("captcha.example",forum_discovery.terminal_domain_map())
                row=forum_discovery.mark_terminal_domain(
                    "captcha.example","captcha_required",source="rule_guard"
                )
                self.assertFalse(row["terminal"])
                self.assertTrue(row["human_checkpoint"])
                self.assertEqual(json.loads(terminal.read_text(encoding="utf-8")),[])
                saved=json.loads(discovered.read_text(encoding="utf-8"))
                self.assertEqual(saved[0]["status"],"candidate")
                self.assertEqual(len(forum_discovery.list_candidates(min_score=1)),1)

    def test_terminal_domain_mark_updates_existing_row(self):
        with tempfile.TemporaryDirectory() as td:
            terminal=Path(td)/"terminal.json"
            discovered=Path(td)/"discovered.json"
            discovered.write_text("[]",encoding="utf-8")
            with (
                patch.object(forum_discovery,"TERMINAL_DOMAINS_FILE",terminal),
                patch.object(forum_discovery,"DISCOVERY_FILE",discovered),
            ):
                forum_discovery.mark_terminal_domain("example.com","old_reason")
                forum_discovery.mark_terminal_domain("www.example.com","new_reason",source="rule_guard")
                rows=json.loads(terminal.read_text(encoding="utf-8"))
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]["reason"],"new_reason")
                self.assertEqual(rows[0]["source"],"rule_guard")

    def test_zero_budget_is_empty(self):
        self.assertEqual(forum_discovery.query_plan(max_queries=0), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
