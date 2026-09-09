from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import forum_discovery
from app.services import platform_rules
from app.services import service_marketplace as marketplace


class PlatformRuleGuardTests(unittest.TestCase):
    def test_listing_word_posledniy_does_not_fake_after_messages_restriction(self):
        text = (
            "Биржа услуг. Хотите предложить свои услуги? Тогда вам сюда. "
            "Массовые ссылки на форумах - залог успеха. "
            "Создано: 2017-10-24. Последний: 2024-12-19. Ответы 1. "
            "Сообщения 490."
        )
        result = platform_rules.classify_rules(text)
        self.assertNotEqual(result["decision"], "blocked")
        self.assertEqual(result["evidence"]["links"], [])

    def test_real_links_after_messages_restriction_still_blocks(self):
        text = (
            "Биржа услуг. Предложения об услугах. "
            "Ссылки разрешены только после 10 сообщений на форуме."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertTrue(result["evidence"]["links"])

    def test_explicit_services_advertising_without_permission_blocks(self):
        text = (
            "Правила форума. Запрещено: публиковать ложную информацию, оскорблять участников. "
            "Рекламировать в сообщениях любые товары и услуги без специального разрешения администрации форума."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertTrue(result["evidence"]["prohibited"])

    def test_links_and_other_advertising_not_allowed_blocks(self):
        text = (
            "Предлагаю услуги. В разделе размещаются объявления с предложением услуг. "
            "Не СПАМЬ! Распространение ссылок и любой другой рекламы на форуме не допускается."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertTrue(result["evidence"]["prohibited"] or result["evidence"]["links"])

    def test_paid_service_topic_for_new_account_blocks_zero_cost_mode(self):
        text = (
            "Работа и услуги. Перечень услуг, цена и контактная информация. "
            "Открытие любых тем с платными услугами для пользователей, у которых меньше 100 сообщений, "
            "будет стоить: одна тема = 1000 рублей. Пользователи от 100 сообщений могут открывать бесплатно."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertFalse(result["zero_cost_compatible"])
        self.assertTrue(result["evidence"]["paid"])

    def test_sbup_rule_audit_includes_commercial_section_rules(self):
        urls = platform_rules.RULE_URLS["sbup_seo_forum"]
        self.assertIn(
            "https://www.sbup.com/seo-forum/rabota_dlya_optimizatora_i_vebmastera/pravila_razdela_rabota_dlya_optimizatora_i_vebmastera/",
            urls,
        )
        self.assertIn(
            "https://www.sbup.com/seo-forum/o_saite_i_forume/pravila_foruma/",
            urls,
        )

    def test_wmboard_services_is_forum(self):
        platform = marketplace.Platform(
            "wmboard_services",
            "WMBoard · Биржа услуг",
            "https://qa.wmboard.net/birzha-uslug-fc1240",
            "direct_post",
            1,
            "SEO",
            "обычная регистрация",
            "Биржа услуг",
        )
        self.assertEqual(marketplace._channel_type(platform), "forum")

    def test_site_rejection_and_missing_registration_route_are_terminal(self):
        for checkpoint in ("registration_rejected_by_site", "registration_route_unavailable"):
            row = {"status": "blocked", "checkpoint": checkpoint}
            self.assertTrue(marketplace.registration_is_terminally_blocked(row))
            self.assertFalse(marketplace.registration_is_recoverable(row))

    def test_platform_bootstrap_queue_is_one_time_and_not_owner_work(self):
        platforms = [
            {
                "key": "captcha_forum", "name": "Captcha forum", "url": "https://captcha.example/",
                "channel_type": "forum", "enabled_for_outreach": True, "publication_ready": False, "priority": 1,
            },
            {
                "key": "dead_forum", "name": "Dead forum", "url": "https://dead.example/",
                "channel_type": "forum", "enabled_for_outreach": True, "publication_ready": False, "priority": 2,
            },
            {
                "key": "ready_forum", "name": "Ready forum", "url": "https://ready.example/",
                "channel_type": "forum", "enabled_for_outreach": True, "publication_ready": True, "priority": 3,
            },
        ]
        registrations = [
            {"platform": "captcha_forum", "status": "verification_required", "checkpoint": "captcha_required"},
            {"platform": "dead_forum", "status": "blocked", "checkpoint": "registration_disabled_by_site"},
            {"platform": "ready_forum", "status": "ready", "checkpoint": None},
        ]
        with (
            patch.object(marketplace, "list_platforms", return_value=platforms),
            patch.object(marketplace, "registration_plan", return_value=registrations),
        ):
            queue = marketplace.platform_bootstrap_queue()
        self.assertEqual(queue["needed"], 1)
        self.assertFalse(queue["owner_action_required"])
        self.assertTrue(queue["human_action_required"])
        self.assertTrue(queue["reusable_across_clients"])
        item = queue["items"][0]
        self.assertEqual(item["platform"], "captcha_forum")
        self.assertTrue(item["one_time"])
        self.assertFalse(item["owner_action_required"])
        self.assertTrue(item["reusable_across_clients"])

    def test_webledi_services_is_forum(self):
        platform = marketplace.Platform(
            "webledi_services",
            "Webledi Club · Предлагаю услуги",
            "https://webledi.club/section/predlagaju-uslugi.59/",
            "direct_post",
            1,
            "вебмастера",
            "обычная регистрация",
            "Предлагаю услуги",
        )
        self.assertEqual(marketplace._channel_type(platform), "forum")


    def test_english_services_marketplace_is_allowed_when_no_paid_or_prohibited_rule(self):
        text = (
            "Marketplace - Buy, Sell & Trade Forum for buying or selling services. "
            "Services: Do you want to offer services or looking for, this is the forum to use."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "allowed")
        self.assertTrue(result["evidence"]["positive"])
        self.assertTrue(result["zero_cost_compatible"])

    def test_english_premium_only_marketplace_is_blocked_for_zero_cost_mode(self):
        text = (
            "The World's Online Marketplace is designated for Premium and Corporate members "
            "to offer special products, tools and services."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertFalse(result["zero_cost_compatible"])
        self.assertTrue(result["evidence"]["paid"])

    def test_english_no_advertising_self_promotion_blocks_discovered_goods_candidate(self):
        text = (
            "LEGO Classifieds & Marketplace Ads. Please No Advertising/Self-Promotion/Posting Affiliate Links In Forums. "
            "Do not register for the purpose of advertising or promoting your online business."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertTrue(result["evidence"]["prohibited"])

    def test_discovered_rule_sources_include_public_rules_not_only_category_page(self):
        self.assertIn("https://mastergrad.com/faq/", platform_rules.RULE_URLS["disc_mastergrad_com"])
        self.assertIn("https://www.toysnbricks.com/terms-conditions/", platform_rules.RULE_URLS["disc_toysnbricks_com"])


    def test_english_service_listing_with_promotional_link_ban_is_blocked(self):
        text = (
            "Services: If you want to offer your web services, this is place to start. "
            "All outbound links are checked. If linked pages primarily promote products/services "
            "the link will be removed. You are not allowed to add any links."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "blocked")
        self.assertFalse(result["mandatory_contacts_compatible"])
        self.assertTrue(result["evidence"]["links"])

    def test_webmastersun_audit_includes_marketplace_and_forum_rules(self):
        urls = platform_rules.RULE_URLS["webmastersun_marketplace"]
        self.assertIn("https://www.webmastersun.com/threads/1414-webmaster-sun-marketplace-rules/", urls)
        self.assertIn("https://www.webmastersun.com/threads/296-webmaster-sun-forum-rules/", urls)


    def test_digitalpoint_services_text_is_allowed_when_zero_cost_rules_are_compatible(self):
        text = (
            "If you are looking for (or offering) services, this is the place. "
            "Services marketplace for programming, design and web services."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "allowed")
        self.assertTrue(result["evidence"]["positive"])

    def test_namepros_promotional_free_ads_are_allowed(self):
        text = (
            "Ads can be posted for free in the Promotional section. "
            "The place to buy, sell, and promote products, goods, services."
        )
        result = platform_rules.classify_rules(text)
        self.assertEqual(result["decision"], "allowed")
        self.assertTrue(result["zero_cost_compatible"])

    def test_digitalpoint_and_namepros_rule_sources_are_registered(self):
        self.assertIn("https://www.digitalpoint.com/help/established", platform_rules.RULE_URLS["digitalpoint_services"])
        self.assertIn("https://www.namepros.com/threads/official-rules-of-namepros.848752/", platform_rules.RULE_URLS["namepros_promotional"])

    def test_surface_rule_requirement_hides_unproven_goods_section(self):
        def policy_without_goods_proof(_key):
            return {
                "free": True,
                "reason": "rules_allowed",
                "rule_decision": "allowed",
                "rule_requirements": [],
            }

        def policy_with_goods_proof(_key):
            return {
                "free": True,
                "reason": "rules_allowed",
                "rule_decision": "allowed",
                "rule_requirements": ["use_verified_wholesale_goods_section_only"],
            }

        with patch.object(marketplace, "platform_policy", side_effect=policy_without_goods_proof):
            row = next(x for x in marketplace.list_platforms() if x["key"] == "partnersearch")
            surface_ids = {x.get("id") for x in row.get("publication_surfaces") or []}
            self.assertIn("b2b_services", surface_ids)
            self.assertNotIn("wholesale_goods", surface_ids)

        with patch.object(marketplace, "platform_policy", side_effect=policy_with_goods_proof):
            row = next(x for x in marketplace.list_platforms() if x["key"] == "partnersearch")
            surface_ids = {x.get("id") for x in row.get("publication_surfaces") or []}
            self.assertIn("b2b_services", surface_ids)
            self.assertIn("wholesale_goods", surface_ids)

    def test_hard_automation_and_commercial_bans_cannot_be_unlocked_by_old_rule_snapshot(self):
        expected = {
            "n8n_jobs": "automated_access_prohibited",
            "weweb_jobs": "automated_access_prohibited",
            "airtable_jobs": "commercial_use_prohibited",
            "bubble_jobs": "automated_access_not_verified",
        }
        for key, reason in expected.items():
            policy = marketplace.platform_policy(key)
            self.assertFalse(policy["free"], key)
            self.assertEqual(policy["reason"], reason, key)

    def test_dynamic_exact_service_surface_can_leave_review(self):
        candidate = {
            "key": "disc_test_service",
            "name": "Предлагаю услуги",
            "url": "https://example.test/forum/services",
            "domain": "example.test",
            "niche": "services",
            "commercial_context": True,
            "create_topic_hint": True,
            "source_surface_label": "Предлагаю услуги",
            "evidence": [],
        }
        inspection = {
            "decision": "review",
            "reason": "rules_ambiguous",
            "requirements": [],
            "evidence": {"positive": [], "paid": [], "prohibited": [], "links": [], "reply_only": [], "frequency": []},
            "url": candidate["url"],
            "http_status": 200,
            "text_sha256": "same",
            "text_excerpt": "Предлагаю услуги. Новая тема. Регистрация.",
        }
        platform = type("P", (), {"name": "Example Services", "url": candidate["url"]})()
        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(platform_rules, "STATE_DIR", Path(td)),
                patch.object(marketplace, "get_platform", return_value=platform),
                patch.object(platform_rules, "inspect_url", return_value=dict(inspection)),
                patch.object(platform_rules, "inspect_url_browser", return_value=dict(inspection)),
                patch.object(forum_discovery, "list_candidates", return_value=[candidate]),
            ):
                result = platform_rules.inspect_platform("disc_test_service")

        self.assertEqual(result["decision"], "allowed")
        self.assertIn("use_exact_discovered_service_surface_only", result["requirements"])

    def test_dynamic_private_ads_only_stays_review(self):
        candidate = {
            "key": "disc_test_private_service",
            "name": "Services Offered",
            "url": "https://example.test/forum/services",
            "domain": "example.test",
            "niche": "services",
            "commercial_context": True,
            "create_topic_hint": True,
            "source_surface_label": "Services Offered",
            "evidence": [],
        }
        inspection = {
            "decision": "review",
            "reason": "rules_ambiguous",
            "requirements": [],
            "evidence": {"positive": [], "paid": [], "prohibited": [], "links": [], "reply_only": [], "frequency": []},
            "url": candidate["url"],
            "http_status": 200,
            "text_sha256": "same",
            "text_excerpt": "Services Offered. New Topic. Private ads only.",
        }
        platform = type("P", (), {"name": "Private Ads", "url": candidate["url"]})()
        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(platform_rules, "STATE_DIR", Path(td)),
                patch.object(marketplace, "get_platform", return_value=platform),
                patch.object(platform_rules, "inspect_url", return_value=dict(inspection)),
                patch.object(platform_rules, "inspect_url_browser", return_value=dict(inspection)),
                patch.object(forum_discovery, "list_candidates", return_value=[candidate]),
            ):
                result = platform_rules.inspect_platform("disc_test_private_service")

        self.assertEqual(result["decision"], "review")


if __name__ == "__main__":
    unittest.main(verbosity=2)
