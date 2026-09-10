from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import platform_rules
from tools import forum_acquisition_guard as guard


class StrategicReserveTests(unittest.TestCase):
    def test_commercial_reserve_target_is_200_unique_sites(self):
        self.assertEqual(guard.STRATEGIC_RESERVE_TOTAL_TARGET, 200)
        self.assertEqual(guard.STRATEGIC_RESERVE_TARGET, 100)
        self.assertEqual(
            guard.STRATEGIC_RESERVE_TOTAL_TARGET,
            guard.STRATEGIC_RESERVE_TARGET * len(guard.STRATEGIC_RESERVE_NICHES),
        )

    def test_reserve_counts_unique_allowed_sites_per_client_format(self):
        platforms = [
            {
                "key": "goods_a",
                "url": "https://forum.shop.example/market",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_surfaces": [{"niches": ["goods"]}],
            },
            {
                "key": "goods_a_second_section",
                "url": "https://shop.example/other",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_surfaces": [{"niches": ["goods", "services"]}],
            },
            {
                "key": "service_b",
                "url": "https://services.example.net/forum",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_surfaces": [{"niches": ["services"]}],
            },
            {
                "key": "review_only",
                "url": "https://review.example.org/forum",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_surfaces": [{"niches": ["goods", "services"]}],
            },
            {
                "key": "unrelated_allowed",
                "url": "https://unrelated.example.com/forum",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_surfaces": [{"niches": ["it"]}],
            },
            {
                "key": "blocked",
                "url": "https://blocked.example.com/forum",
                "channel_type": "forum",
                "enabled_for_outreach": False,
                "publication_surfaces": [{"niches": ["goods", "services"]}],
            },
        ]
        decisions = {
            "goods_a": "allowed",
            "goods_a_second_section": "allowed",
            "service_b": "allowed",
            "review_only": "review",
            "unrelated_allowed": "allowed",
            "blocked": "blocked",
        }
        with (
            patch.object(guard.marketplace, "list_platforms", return_value=platforms),
            patch.object(
                guard.platform_rules,
                "latest",
                side_effect=lambda key: {"decision": decisions[key]},
            ),
        ):
            snapshot = guard._strategic_reserve_snapshot()

        goods = snapshot["formats"]["goods"]
        services = snapshot["formats"]["services"]
        self.assertEqual(goods["allowed_unique_sites"], 1)
        self.assertEqual(services["allowed_unique_sites"], 2)
        self.assertEqual(goods["deficit"], guard.STRATEGIC_RESERVE_TARGET - 1)
        self.assertEqual(services["deficit"], guard.STRATEGIC_RESERVE_TARGET - 2)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["target_total"], guard.STRATEGIC_RESERVE_TARGET * len(guard.STRATEGIC_RESERVE_NICHES))
        self.assertEqual(snapshot["allowed_unique_sites_total"], 2)
        self.assertNotIn("unrelated.example.com", snapshot["formats"]["goods"]["sites"])
        self.assertNotIn("unrelated.example.com", snapshot["formats"]["services"]["sites"])
        self.assertEqual(snapshot["total_deficit"], guard.STRATEGIC_RESERVE_TOTAL_TARGET - 2)
        self.assertEqual(snapshot["max_deficit"], snapshot["total_deficit"])

    def test_dynamic_exact_commercial_surface_is_promoted(self):
        candidate = {
            "name": "Example Business Forum",
            "url": "https://example.com/forums/free-advertising/",
            "niche": "business",
            "commercial_context": True,
            "source_surface_label": "Free Advertising Forums",
            "evidence": ["marketplace", "advertising"],
        }
        surfaces = guard.marketplace._dynamic_verified_publication_surface(candidate)
        self.assertEqual(len(surfaces), 1)
        self.assertEqual(surfaces[0]["url"], candidate["url"])
        self.assertEqual(set(surfaces[0]["niches"]), {"services"})

    def test_dynamic_generic_forum_root_is_not_promoted(self):
        candidate = {
            "name": "Example Technology Forum",
            "url": "https://example.com/forums/",
            "niche": "it",
            "commercial_context": True,
            "source_surface_label": "",
            "evidence": ["forum", "services"],
        }
        self.assertEqual(
            guard.marketplace._dynamic_verified_publication_surface(candidate),
            [],
        )

    def test_dynamic_named_services_section_is_service_surface(self):
        candidate = {
            "name": "Строительство домов, строительные услуги",
            "url": "https://example.ru/forum/forum30.html",
            "niche": "construction",
            "commercial_context": True,
            "source_surface_label": "",
            "evidence": ["услуги", "форум"],
        }
        surfaces = guard.marketplace._dynamic_verified_publication_surface(candidate)
        self.assertEqual(len(surfaces), 1)
        self.assertIn("services", surfaces[0]["niches"])

    def test_allowed_root_without_verified_commercial_surface_does_not_count(self):
        platforms = [{"key": "generic_goods_root", "url": "https://generic.example/forum", "channel_type": "forum", "enabled_for_outreach": True, "niche": "goods", "publication_surfaces": []}]
        with (patch.object(guard.marketplace, "list_platforms", return_value=platforms), patch.object(guard.platform_rules, "latest", return_value={"decision": "allowed"})):
            snapshot = guard._strategic_reserve_snapshot()
        self.assertEqual(snapshot["formats"]["goods"]["allowed_unique_sites"], 0)

    def test_mmggp_verified_service_surface_counts_as_services_format(self):
        surfaces = guard.marketplace.VERIFIED_PUBLICATION_SURFACES["mmgp"]
        site_dev = next(x for x in surfaces if x["id"] == "site_development_services")
        self.assertIn("services", site_dev["niches"])

    def test_optiboard_paid_marketplace_is_fail_closed(self):
        evidence = {
            "decision": "allowed",
            "reason": "explicit_service_or_advertising_section",
            "requirements": [],
            "evidence": {"positive": ["marketplace buy sell services"]},
            "url": "https://www.optiboard.com/forums/forum/optical-forums/optical-marketplace",
            "http_status": 200,
            "text_excerpt": (
                "Optical Marketplace. In order to post new threads and items for sale "
                "in this forum, you will need to first purchase one of the OptiBoard Subscriptions."
            ),
        }
        platform = SimpleNamespace(
            key="disc_optiboard_com",
            name="OptiBoard",
            url=evidence["url"],
        )
        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(platform_rules, "STATE_DIR", Path(td)),
                patch.object(platform_rules, "inspect_url", return_value=evidence),
                patch.object(platform_rules.marketplace, "get_platform", return_value=platform),
            ):
                row = platform_rules.inspect_platform("disc_optiboard_com")
        self.assertEqual(row["decision"], "blocked")
        self.assertIn("paid_subscription_required_for_marketplace_posting", row["requirements"])

    def test_cliosport_restricted_commercial_sections_do_not_enter_free_reserve(self):
        evidence = {
            "decision": "allowed",
            "reason": "explicit_service_or_advertising_section",
            "requirements": [],
            "evidence": {"positive": ["services and any club exclusive offers"]},
            "url": "https://cliosport.net/forums/",
            "http_status": 200,
            "text_excerpt": (
                "ClioSport Traders. Forum for ClioSport Traders to post offer and promote their services "
                "and any club exclusive offers and deals they have. (ClioSport Club Members Only). "
                "Marketplace. Car Parts For Sale (Visible to ClioSport Club Members only)."
            ),
        }
        platform = SimpleNamespace(
            key="disc_cliosport_net",
            name="ClioSport",
            url=evidence["url"],
        )
        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(platform_rules, "STATE_DIR", Path(td)),
                patch.object(platform_rules, "inspect_url", return_value=evidence),
                patch.object(platform_rules.marketplace, "get_platform", return_value=platform),
            ):
                row = platform_rules.inspect_platform("disc_cliosport_net")
        self.assertEqual(row["decision"], "review")
        self.assertIn("do_not_count_in_free_goods_reserve", row["requirements"])

    def test_dynamic_discovery_preserves_canonical_niche(self):
        self.assertEqual(
            guard.marketplace._niche_label("Автоматически найдено: goods"),
            "goods",
        )
        self.assertEqual(
            guard.marketplace._niche_label("Автоматически найдено: services"),
            "services",
        )
        self.assertEqual(
            guard.marketplace._niche_label("Автоматически найдено: it"),
            "it",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
