from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import crowd_seo, forum_quality


class ForumQualityTests(unittest.TestCase):
    def test_iks_tiers_use_expected_boundaries(self):
        self.assertEqual(forum_quality.tier_for_iks(None), "unknown")
        self.assertEqual(forum_quality.tier_for_iks(99), "low")
        self.assertEqual(forum_quality.tier_for_iks(100), "medium")
        self.assertEqual(forum_quality.tier_for_iks(999), "medium")
        self.assertEqual(forum_quality.tier_for_iks(1000), "high")

    def test_save_rows_deduplicates_domains_and_sorts_descending(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "quality.json"
            with patch.object(forum_quality, "DATA_FILE", path):
                result = forum_quality.save_rows([
                    {"domain": "www.low.example", "iks": 10, "platforms": ["low"]},
                    {"domain": "high.example", "iks": 5000, "platforms": ["high"]},
                    {"domain": "low.example", "iks": 20, "platforms": ["duplicate"]},
                    {"domain": "medium.example", "iks": 400, "platforms": ["medium"]},
                ], measured_at="2026-09-09T00:00:00+00:00")
                self.assertEqual(result["domains_total"], 3)
                self.assertEqual([x["domain"] for x in result["items"]], [
                    "high.example",
                    "medium.example",
                    "low.example",
                ])
                self.assertEqual(result["high"], 1)
                self.assertEqual(result["medium"], 1)
                self.assertEqual(result["low"], 1)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["measured_at"], "2026-09-09T00:00:00+00:00")

    def test_quality_for_url_reads_saved_domain(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "quality.json"
            path.write_text(json.dumps({
                "measured_at": "2026-09-09T00:00:00+00:00",
                "items": [{"domain": "forum.example", "iks": 1250, "tier": "high"}],
            }), encoding="utf-8")
            with patch.object(forum_quality, "DATA_FILE", path):
                result = forum_quality.quality_for_url("https://www.forum.example/path")
        self.assertEqual(result["domain"], "forum.example")
        self.assertEqual(result["iks"], 1250)
        self.assertEqual(result["iks_tier"], "high")

    def test_standard_and_publish_all_plan_counts_are_separate(self):
        standard = {
            "target_count": 15,
            "bonus_count": 3,
            "publish_all_eligible": False,
        }
        live_test = {
            **standard,
            "publish_all_eligible": True,
        }
        pool = [{"platform": f"p{i}"} for i in range(23)]
        self.assertEqual(crowd_seo._project_plan_count(standard, pool), 18)
        self.assertEqual(crowd_seo._project_plan_count(live_test, pool), 23)

    def test_campaign_keyword_bank_is_merged_without_duplicates(self):
        project = {
            "keywords": ["CRM", "реклама"],
            "campaign_profile": {
                "keyword_bank": ["ИИ автоматизация", "crm", "SEO"],
            },
        }
        result = crowd_seo._project_keywords(project)
        self.assertEqual(result, ["ИИ автоматизация", "crm", "SEO", "реклама"])

    def test_campaign_profile_copy_is_transparent_and_replaces_url(self):
        project = {
            "site": "https://boris-ai.pro/",
            "domain": "boris-ai.pro",
            "offer_type": "services",
            "campaign_profile": {
                "brand": "BORIS",
                "posts_ru": [{
                    "title": "{brand} — автоматизация бизнеса",
                    "text": "Мы предлагаем BORIS для автоматизации рекламы и продаж. Подробнее: {url}",
                }],
            },
        }
        placement = {
            "surface_language": "ru",
            "target_url": "https://boris-ai.pro/",
            "keyword": "автоматизация бизнеса",
            "anchor": "https://boris-ai.pro/",
            "link_mode": "безанкорная",
        }
        title, text = crowd_seo._render_placement_text(project, placement, 1)
        self.assertEqual(title, "BORIS — автоматизация бизнеса")
        self.assertIn("Мы предлагаем BORIS", text)
        self.assertIn("https://boris-ai.pro/", text)

    def test_iks_breaks_tie_between_equal_forum_matches(self):
        platforms = [
            {
                "key": "low_forum",
                "name": "Low",
                "url": "https://low.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "niche": "services",
                "publication_ready": False,
                "publication_surfaces": [],
                "free_policy": {"rule_decision": "allowed"},
            },
            {
                "key": "high_forum",
                "name": "High",
                "url": "https://high.example/",
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "niche": "services",
                "publication_ready": False,
                "publication_surfaces": [],
                "free_policy": {"rule_decision": "allowed"},
            },
        ]

        def quality(url):
            if "high.example" in url:
                return {"iks": 2500, "iks_tier": "high", "iks_measured_at": "now"}
            return {"iks": 50, "iks_tier": "low", "iks_measured_at": "now"}

        with (
            patch.object(crowd_seo.marketplace, "list_platforms", return_value=platforms),
            patch.object(crowd_seo.marketplace, "registration_plan", return_value=[]),
            patch.object(crowd_seo.marketplace, "registration_is_terminally_blocked", return_value=False),
            patch.object(crowd_seo.marketplace, "registration_is_recoverable", return_value=True),
            patch.object(crowd_seo.marketplace, "registration_is_warming", return_value=False),
            patch.object(crowd_seo.forum_quality, "quality_for_url", side_effect=quality),
        ):
            rows = crowd_seo._forum_matches(
                "services",
                limit=10,
                keywords=["автоматизация бизнеса"],
                offer_type="services",
            )

        self.assertEqual([x["platform"] for x in rows], ["high_forum", "low_forum"])
        self.assertEqual(rows[0]["iks"], 2500)

    def test_sync_project_drafts_rejects_only_stale_unpublished_history(self):
        project = {
            "id": "crowd_test",
            "site": "https://boris-ai.pro/",
            "domain": "boris-ai.pro",
            "content_status": "approved",
            "offer_type": "services",
            "niche_label": "IT",
            "campaign_profile": {
                "brand": "BORIS",
                "posts_ru": [{
                    "title": "BORIS",
                    "text": "Мы предлагаем BORIS. Подробнее: {url}",
                }],
            },
            "placements": [{
                "id": "crowd_test_001",
                "platform": "forum_one",
                "platform_name": "Forum One",
                "surface_id": "services",
                "surface_name": "Services",
                "surface_language": "ru",
                "forum_url": "https://forum.example/services",
                "publication_surface_post_url": "https://forum.example/new",
                "target_url": "https://boris-ai.pro/",
                "keyword": "автоматизация",
                "link_mode": "безанкорная",
                "anchor": "https://boris-ai.pro/",
                "text_variant": 1,
                "status": "external_checkpoint",
            }],
        }
        old_rows = [
            {
                "id": "crowdseo_crowd_test_old",
                "crowd_project_id": "crowd_test",
                "status": "approved",
            },
            {
                "id": "crowdseo_crowd_test_posted",
                "crowd_project_id": "crowd_test",
                "status": "posted",
                "publication_url": "https://forum.example/topic/1",
            },
            {
                "id": "other_project",
                "crowd_project_id": "crowd_other",
                "status": "approved",
            },
        ]
        rejected = []
        saved = []
        with (
            patch.object(crowd_seo, "_load", return_value=[project]),
            patch.object(crowd_seo, "_save", return_value=None),
            patch.object(crowd_seo.marketplace, "list_drafts", return_value=old_rows),
            patch.object(
                crowd_seo.marketplace,
                "set_draft_status",
                side_effect=lambda draft_id, status: rejected.append((draft_id, status)),
            ),
            patch.object(
                crowd_seo.marketplace,
                "save_drafts",
                side_effect=lambda drafts: saved.extend(drafts) or drafts,
            ),
        ):
            result = crowd_seo.sync_project_drafts("crowd_test")

        self.assertEqual(result["drafts"], 1)
        self.assertEqual(result["stale_rejected"], 1)
        self.assertEqual(rejected, [("crowdseo_crowd_test_old", "rejected")])
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["status"], "approved")
        self.assertEqual(project["placements"][0]["status"], "external_checkpoint")


if __name__ == "__main__":
    unittest.main()
