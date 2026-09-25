from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services import service_marketplace as marketplace


class PlatformBootstrapQueueTests(unittest.TestCase):
    def test_warming_queue_is_separate_and_reusable(self):
        platforms = [
            {
                "key": "warming_forum",
                "name": "Warming Forum",
                "url": "https://warming.example/forum",
                "priority": 10,
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
            },
            {
                "key": "captcha_forum",
                "name": "Captcha Forum",
                "url": "https://captcha.example/forum",
                "priority": 20,
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
            },
        ]
        registrations = [
            {
                "platform": "warming_forum",
                "status": "warming",
                "checkpoint": "participation_level_required",
                "account_url": "https://warming.example/profile",
                "warming_started_at": (
                    datetime.now(timezone.utc) - timedelta(days=10, hours=1)
                ).isoformat(),
            },
            {
                "platform": "captcha_forum",
                "status": "verification_required",
                "checkpoint": "captcha_required",
                "account_url": "https://captcha.example/register",
            },
        ]
        with (
            patch.object(marketplace, "list_platforms", return_value=platforms),
            patch.object(marketplace, "registration_plan", return_value=registrations),
            patch.object(
                marketplace,
                "PLATFORM_MATURITY_REQUIREMENTS",
                {
                    "warming_forum": {
                        "minimum_account_age_days": 15,
                        "minimum_messages": 30,
                    },
                    "captcha_forum": {
                        "minimum_account_age_days": 15,
                        "minimum_messages": 30,
                        "minimum_reactions": 5,
                    },
                },
            ),
            patch.object(
                marketplace,
                "VERIFIED_PUBLICATION_SURFACES",
                {
                    "warming_forum": [{"niches": ["goods", "services", "it"]}],
                    "captcha_forum": [{"niches": ["goods", "services"]}],
                },
            ),
        ):
            warmup = marketplace.platform_warmup_queue()
            bootstrap = marketplace.platform_bootstrap_queue()

        self.assertEqual(warmup["warming"], 1)
        self.assertFalse(warmup["owner_action_required"])
        self.assertTrue(warmup["reusable_across_clients"])
        self.assertEqual(warmup["items"][0]["platform"], "warming_forum")
        item = warmup["items"][0]
        self.assertEqual(item["requirements"]["minimum_account_age_days"], 15)
        self.assertEqual(item["age_elapsed_days"], 10)
        self.assertEqual(item["age_remaining_days"], 5)
        self.assertTrue(item["earliest_age_ready_at"])
        self.assertEqual(item["supported_client_formats"], ["goods", "services"])
        self.assertIn("каждые сутки", item["next_action"])
        self.assertEqual(bootstrap["needed"], 1)
        bootstrap_item = bootstrap["items"][0]
        self.assertEqual(bootstrap_item["platform"], "captcha_forum")
        self.assertTrue(bootstrap_item["starts_warmup_after_bootstrap"])
        self.assertEqual(
            bootstrap_item["supported_client_formats"],
            ["goods", "services"],
        )
        self.assertEqual(
            bootstrap_item["maturity_requirements"]["minimum_account_age_days"],
            15,
        )
        self.assertIn("WARMING", bootstrap_item["after_completion"])

    def test_only_one_time_human_checkpoints_enter_service_queue(self):
        platforms = [
            {
                "key": "captcha_forum",
                "name": "Captcha Forum",
                "url": "https://captcha.example/forum",
                "priority": 10,
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
            },
            {
                "key": "agreement_forum",
                "name": "Agreement Forum",
                "url": "https://agreement.example/forum",
                "priority": 15,
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
            },
            {
                "key": "ready_forum",
                "name": "Ready Forum",
                "url": "https://ready.example/forum",
                "priority": 20,
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": True,
            },
            {
                "key": "dead_forum",
                "name": "Dead Forum",
                "url": "https://dead.example/forum",
                "priority": 30,
                "channel_type": "forum",
                "enabled_for_outreach": True,
                "publication_ready": False,
            },
            {
                "key": "blocked_rules",
                "name": "Rules Blocked",
                "url": "https://blocked.example/forum",
                "priority": 40,
                "channel_type": "forum",
                "enabled_for_outreach": False,
                "publication_ready": False,
            },
        ]
        registrations = [
            {
                "platform": "captcha_forum",
                "status": "verification_required",
                "checkpoint": "captcha_required",
                "account_url": "https://captcha.example/register",
                "last_error": "captcha",
            },
            {
                "platform": "agreement_forum",
                "status": "verification_required",
                "checkpoint": "registration_agreement_required",
                "account_url": "https://agreement.example/register",
                "last_error": "accept rules",
            },
            {
                "platform": "ready_forum",
                "status": "ready",
                "checkpoint": None,
                "account_url": "https://ready.example/profile",
            },
            {
                "platform": "dead_forum",
                "status": "blocked",
                "checkpoint": "registration_disabled_by_site",
                "account_url": "https://dead.example/register",
            },
            {
                "platform": "blocked_rules",
                "status": "verification_required",
                "checkpoint": "captcha_required",
                "account_url": "https://blocked.example/register",
            },
        ]

        with (
            patch.object(marketplace, "list_platforms", return_value=platforms),
            patch.object(marketplace, "registration_plan", return_value=registrations),
        ):
            queue = marketplace.platform_bootstrap_queue()

        self.assertEqual(queue["needed"], 2)
        self.assertFalse(queue["owner_action_required"])
        self.assertTrue(queue["human_action_required"])
        self.assertTrue(queue["reusable_across_clients"])
        self.assertEqual(queue["executor_role"], "platform_onboarding_worker")

        items = {item["platform"]: item for item in queue["items"]}
        captcha = items["captcha_forum"]
        self.assertEqual(captcha["account_url"], "https://captcha.example/register")
        self.assertEqual(captcha["last_error"], "captcha")
        self.assertFalse(captcha["owner_action_required"])
        self.assertTrue(captcha["human_action_required"])
        self.assertTrue(captcha["one_time"])
        self.assertTrue(captcha["reusable_across_clients"])

        agreement = items["agreement_forum"]
        self.assertEqual(agreement["checkpoint"], "registration_agreement_required")
        self.assertEqual(agreement["account_url"], "https://agreement.example/register")
        self.assertEqual(agreement["action"], "Один раз принять правила регистрации площадки")
        self.assertFalse(agreement["owner_action_required"])
        self.assertTrue(agreement["one_time"])


    def test_sms_verification_is_one_time_worker_bootstrap(self):
        platforms = [{
            "key": "sms_forum",
            "name": "SMS Forum",
            "url": "https://sms.example/forum",
            "priority": 5,
            "channel_type": "forum",
            "enabled_for_outreach": True,
            "publication_ready": False,
        }]
        registrations = [{
            "platform": "sms_forum",
            "status": "verification_required",
            "checkpoint": "sms_or_verification_code_required",
            "account_url": "https://sms.example/register",
        }]
        with (
            patch.object(marketplace, "list_platforms", return_value=platforms),
            patch.object(marketplace, "registration_plan", return_value=registrations),
        ):
            queue = marketplace.platform_bootstrap_queue()

        self.assertEqual(queue["needed"], 1)
        item = queue["items"][0]
        self.assertEqual(item["platform"], "sms_forum")
        self.assertEqual(item["action"], "Один раз подтвердить SMS или код площадки")
        self.assertFalse(item["owner_action_required"])
        self.assertEqual(item["executor_role"], "platform_onboarding_worker")
        self.assertTrue(item["reusable_across_clients"])

    def test_business_email_is_bootstrap_not_terminal(self):
        row={"platform":"b2b","status":"blocked","checkpoint":"business_email_required"}
        self.assertFalse(marketplace.registration_is_terminally_blocked(row))
        self.assertTrue(marketplace.registration_is_recoverable(row))
        platforms=[{
            "key":"b2b","name":"B2B Forum","url":"https://b2b.example/forum",
            "priority":5,"channel_type":"forum","enabled_for_outreach":True,
            "publication_ready":False,
        }]
        with (
            patch.object(marketplace,"list_platforms",return_value=platforms),
            patch.object(marketplace,"registration_plan",return_value=[row]),
            patch.object(marketplace,"VERIFIED_PUBLICATION_SURFACES",{"b2b":[{"niches":["goods"]}]}),
        ):
            queue=marketplace.platform_bootstrap_queue()
        self.assertEqual(queue["needed"],1)
        self.assertEqual(queue["items"][0]["platform"],"b2b")
        self.assertEqual(queue["items"][0]["action"],"Указать корпоративную почту для регистрации")
        self.assertFalse(queue["items"][0]["owner_action_required"])
        self.assertTrue(queue["items"][0]["requires_interactive_browser"])
        self.assertTrue(queue["items"][0]["auto_resume_after_verification"])
        self.assertFalse(queue["items"][0]["accepts_text_value"])

    def test_warming_queue_supports_hour_based_maturity(self):
        now = datetime.now(timezone.utc)
        platforms = [{
            "key": "digitalpoint_services",
            "name": "DigitalPoint",
            "url": "https://forums.digitalpoint.com/forums/services.60/",
            "priority": 93,
            "channel_type": "forum",
            "enabled_for_outreach": True,
            "publication_ready": False,
        }]
        registrations = [{
            "platform": "digitalpoint_services",
            "status": "warming",
            "checkpoint": "established_member_required",
            "warming_started_at": (now - timedelta(hours=25)).isoformat(),
        }]
        with (
            patch.object(marketplace, "list_platforms", return_value=platforms),
            patch.object(marketplace, "registration_plan", return_value=registrations),
            patch.object(
                marketplace,
                "PLATFORM_MATURITY_REQUIREMENTS",
                {"digitalpoint_services": {
                    "minimum_account_age_hours": 48,
                    "minimum_likes_from_established_members": 3,
                }},
            ),
            patch.object(
                marketplace,
                "VERIFIED_PUBLICATION_SURFACES",
                {"digitalpoint_services": [{"niches": ["services", "it"]}]},
            ),
        ):
            warmup = marketplace.platform_warmup_queue()

        item = warmup["items"][0]
        self.assertEqual(item["age_elapsed_hours"], 25)
        self.assertIn(item["age_remaining_hours"], {22, 23})
        self.assertEqual(item["age_remaining_days"], 1)
        self.assertEqual(item["requirements"]["minimum_account_age_hours"], 48)
        self.assertEqual(item["supported_client_formats"], ["services"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
