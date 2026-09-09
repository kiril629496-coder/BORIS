from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.api import service_marketplace as api


class BootstrapVerifyApiTests(unittest.TestCase):
    def test_verify_bootstrap_runs_only_existing_queue_item_and_returns_ready(self):
        queue = {
            "needed": 1,
            "items": [{"platform": "captcha_forum", "checkpoint": "captcha_required"}],
        }
        ready_platforms = [
            {
                "key": "captcha_forum",
                "publication_ready": True,
                "registration_checkpoint": None,
            }
        ]
        proc = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "platform": "captcha_forum", "ready": True}),
            stderr="",
        )
        with (
            patch.object(api.svc, "platform_bootstrap_queue", side_effect=[queue, {"needed": 0, "items": []}]),
            patch.object(api.svc, "list_platforms", return_value=ready_platforms),
            patch.object(api.subprocess, "run", return_value=proc) as run,
        ):
            result = api.verify_bootstrap("captcha_forum")

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["queue"]["needed"], 0)
        argv = run.call_args.args[0]
        self.assertIn("verify-registration", argv)
        self.assertNotIn("--submit", argv)

    def test_verify_bootstrap_rejects_platform_outside_worker_queue(self):
        with (
            patch.object(api.svc, "platform_bootstrap_queue", return_value={"needed": 0, "items": []}),
            patch.object(api.svc, "list_platforms", return_value=[{"key": "other", "publication_ready": False}]),
        ):
            with self.assertRaises(HTTPException) as ctx:
                api.verify_bootstrap("other")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_inventory_summary_counts_unique_sites_and_live_deficit(self):
        platforms = [
            {"key": "a", "url": "https://forum.example.ru/a", "channel_type": "forum", "publication_ready": True, "enabled_for_outreach": True, "publication_surfaces": [{"niches": ["services"]}]},
            {"key": "a2", "url": "https://forum.example.ru/b", "channel_type": "forum", "publication_ready": False, "enabled_for_outreach": True, "publication_surfaces": [{"niches": ["services"]}]},
            {"key": "b", "url": "https://other.example/", "channel_type": "forum", "publication_ready": False, "enabled_for_outreach": True, "publication_surfaces": [{"niches": ["goods"]}]},
            {"key": "vk", "url": "https://vk.com/x", "channel_type": "social", "publication_ready": True},
        ]
        registrations = [
            {"platform": "a", "status": "ready"},
            {"platform": "a2", "status": "verification_required", "checkpoint": "captcha_required"},
            {"platform": "b", "status": "blocked", "checkpoint": "registration_disabled_by_site"},
        ]
        project = {"id": "p1", "site": "https://client.example/", "niche": "marketing", "keywords": [], "plan_mode": "contract", "target_count": 18, "bonus_count": 0}
        matches = [{"platform": "a"}, {"platform": "a2"}]
        capacity = {
            "required": 18,
            "eligible": 7,
            "autonomous": 1,
            "ready": 1,
            "bootstrap_relevant": 6,
            "bootstrap_immediate_relevant": 5,
            "bootstrap_surface_slots": 6,
            "warming_relevant": 1,
            "service_reachable": 7,
            "service_reachable_after_bootstrap": 7,
            "service_reachable_after_warmup": 8,
            "autonomous_deficit": 17,
            "ready_deficit": 17,
            "discovery_deficit_after_bootstrap": 11,
            "discovery_deficit_after_warmup": 0,
        }
        with (
            patch.object(api.svc, "list_platforms", return_value=platforms),
            patch.object(api.svc, "registration_plan", return_value=registrations),
            patch.object(api.svc, "platform_bootstrap_queue", return_value={"needed": 1, "items": []}),
            patch.object(api.svc, "platform_warmup_queue", return_value={"warming": 0, "items": []}),
            patch.object(api.svc, "registration_is_terminally_blocked", side_effect=lambda r: r.get("status") == "blocked"),
            patch.object(api.platform_rules, "latest", side_effect=lambda key: {"decision": "allowed" if key != "b" else "blocked"}),
            patch.object(api.forum_discovery, "list_candidates", return_value=[]),
            patch.object(api.forum_discovery, "terminal_domain_map", return_value={}),
            patch.object(api.crowd_seo, "list_projects", return_value=[project]),
            patch.object(api.crowd_seo, "_forum_matches", return_value=matches),
            patch.object(api.crowd_seo, "_capacity_for_project", return_value=capacity),
        ):
            result = api.inventory_summary()

        self.assertEqual(result["forum_records_total"], 3)
        self.assertEqual(result["unique_forum_sites"], 2)
        self.assertEqual(result["unique_sites_seen_or_checked"], 2)
        self.assertEqual(result["terminal_only_not_registry"], 0)
        self.assertEqual(result["rules_allowed"], 2)
        self.assertEqual(result["rules_blocked"], 1)
        self.assertEqual(result["publication_ready"], 1)
        self.assertEqual(result["bootstrap_queue"], 1)
        self.assertEqual(result["terminal_registration_blocked"], 1)
        self.assertEqual(result["sellable_product_formats"]["services"]["allowed_unique_sites"], 1)
        self.assertEqual(result["sellable_product_formats"]["services"]["deficit"], 17)
        self.assertEqual(result["sellable_product_formats"]["goods"]["allowed_unique_sites"], 0)
        self.assertEqual(result["sellable_product_formats"]["goods"]["deficit"], 18)
        self.assertEqual(result["sellable_product_total_deficit"], 35)
        self.assertFalse(result["sellable_product_complete"])
        self.assertEqual(result["current_required"], 18)
        self.assertEqual(result["current_relevant_sites"], 7)
        self.assertEqual(result["current_relevant_after_bootstrap"], 7)
        self.assertEqual(result["current_relevant_after_warmup"], 8)
        self.assertEqual(result["current_missing_after_bootstrap"], 11)
        # A real zero is a valid value and must not fall back to the bootstrap deficit.
        self.assertEqual(result["current_missing_after_warmup"], 0)

    def test_inventory_sellable_excludes_terminal_registration_even_when_rule_allowed(self):
        platforms = [
            {"key":"goods_ok","url":"https://goods-ok.example/","channel_type":"forum","enabled_for_outreach":True,"publication_ready":False,"publication_surfaces":[{"niches":["goods"]}]},
            {"key":"goods_terminal","url":"https://goods-terminal.example/","channel_type":"forum","enabled_for_outreach":True,"publication_ready":False,"publication_surfaces":[{"niches":["goods"]}]},
        ]
        registrations = [
            {"platform":"goods_ok","status":"verification_required","checkpoint":"captcha_required"},
            {"platform":"goods_terminal","status":"blocked","checkpoint":"registration_disabled_by_site"},
        ]
        with (
            patch.object(api.svc, "list_platforms", return_value=platforms),
            patch.object(api.svc, "registration_plan", return_value=registrations),
            patch.object(api.svc, "platform_bootstrap_queue", return_value={"needed": 1, "items": []}),
            patch.object(api.svc, "platform_warmup_queue", return_value={"warming": 0, "items": []}),
            patch.object(api.svc, "registration_is_terminally_blocked", side_effect=lambda r: r.get("status") == "blocked"),
            patch.object(api.platform_rules, "latest", return_value={"decision":"allowed"}),
            patch.object(api.forum_discovery, "list_candidates", return_value=[]),
            patch.object(api.forum_discovery, "terminal_domain_map", return_value={}),
            patch.object(api.crowd_seo, "list_projects", return_value=[]),
        ):
            result = api.inventory_summary()
        self.assertEqual(result["sellable_product_formats"]["goods"]["allowed_unique_sites"], 1)
        self.assertEqual(result["sellable_product_formats"]["goods"]["deficit"], 0)

    def test_bootstrap_queue_separates_immediate_and_warmup_capacity(self):
        base_queue = {
            "needed": 2,
            "items": [
                {"platform": "fast", "name": "Fast", "priority": 10},
                {"platform": "warm", "name": "Warm", "priority": 20},
            ],
        }
        priority = {
            "fast": {
                "urgent_for_active_projects": True,
                "relevant_projects": 1,
                "potential_slots": 1,
                "max_relevance": 3,
                "requires_warmup_after_bootstrap": False,
                "relevant_sites": ["https://client.example/"],
            },
            "warm": {
                "urgent_for_active_projects": True,
                "relevant_projects": 1,
                "potential_slots": 1,
                "max_relevance": 3,
                "requires_warmup_after_bootstrap": True,
                "relevant_sites": ["https://client.example/"],
            },
        }
        with (
            patch.object(api.svc, "platform_bootstrap_queue", return_value=base_queue),
            patch.object(api.crowd_seo, "bootstrap_priority_snapshot", return_value=priority),
        ):
            result = api.bootstrap_queue()

        self.assertEqual(result["needed_for_active_projects"], 2)
        self.assertEqual(result["potential_slots_for_active_projects"], 2)
        self.assertEqual(result["potential_slots_immediate_after_bootstrap"], 1)
        self.assertEqual(result["potential_slots_after_warmup"], 2)
        self.assertEqual(result["warming_after_bootstrap"], 1)

    def test_bootstrap_queue_prioritizes_live_client_demand(self):
        base_queue = {
            "needed": 3,
            "items": [
                {"platform": "later", "name": "Later", "priority": 1},
                {"platform": "urgent_low", "name": "Urgent low", "priority": 50},
                {"platform": "urgent_high", "name": "Urgent high", "priority": 60},
            ],
        }
        priority = {
            "urgent_low": {
                "urgent_for_active_projects": True,
                "relevant_projects": 1,
                "potential_slots": 1,
                "max_relevance": 2,
                "relevant_sites": ["https://client-a.example/"],
            },
            "urgent_high": {
                "urgent_for_active_projects": True,
                "relevant_projects": 2,
                "potential_slots": 1,
                "max_relevance": 3,
                "relevant_sites": ["https://client-b.example/"],
            },
        }
        with (
            patch.object(api.svc, "platform_bootstrap_queue", return_value=base_queue),
            patch.object(api.crowd_seo, "bootstrap_priority_snapshot", return_value=priority),
        ):
            result = api.bootstrap_queue()

        self.assertEqual(
            [x["platform"] for x in result["items"]],
            ["urgent_high", "urgent_low", "later"],
        )
        self.assertEqual(result["needed_for_active_projects"], 2)
        self.assertEqual(result["potential_slots_for_active_projects"], 2)
        self.assertTrue(result["items"][0]["urgent_for_active_projects"])
        self.assertFalse(result["items"][-1]["urgent_for_active_projects"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
