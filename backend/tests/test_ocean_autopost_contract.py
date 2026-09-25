import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -*- coding: utf-8 -*-
import inspect
import unittest

import posting_runner as pr
import boris_background_worker as bw


class OceanAutopostContractTest(unittest.TestCase):
    def setUp(self):
        self.signature = "Клиентская подпись Ocean для contract-теста."
        self.project = {
            "name": "Океан Клиентов и Борис",
            "platforms": "both",
            "channel_tg": "@oko_marketing_i_rost",
            "vk_owner_id": -121265252,
            "image_source": "ai",
            "footer_signature": self.signature,
        }

    def test_compact_media_post_keeps_owner_signature_hashtags_and_no_video(self):
        source = ("Большой экспертный разбор рекламы, продаж, Авито и аналитики. " * 80).strip()
        text = pr._project_delivery_text_tracked(source, self.project, "tg", tracking_id="p001")
        self.assertLessEqual(len(text), 1000)
        self.assertIn("Большой экспертный разбор", text)
        self.assertIn(self.signature, text)
        self.assertIn("Подробнее: https://boris-ai.pro/?utm_source=tg&utm_medium=social&utm_campaign=ocean&utm_content=p001", text)
        self.assertEqual(text.count("https://boris-ai.pro/"), 1)
        self.assertNotIn("software-dev", text)
        self.assertNotIn("utm_campaign=dev", text)
        self.assertNotIn("AI-РОП — контроль продаж", text)
        self.assertNotIn("Настройка BORIS и сопровождение первого месяца — бесплатно", text)
        self.assertNotIn("Напишите в MAX или WhatsApp:", text)
        tags = [x for x in text.split() if x.startswith("#")]
        self.assertGreaterEqual(len(tags), 5)
        self.assertLessEqual(len(tags), 10)
        self.assertNotIn("http://", text.lower())
        self.assertNotIn("youtu.be", text.lower())
        self.assertNotIn("rutube.ru", text.lower())
        self.assertNotIn("🎥 BORIS в работе", text)

    def test_both_platforms_are_required(self):
        self.assertEqual(pr._required_delivery_platforms(self.project), ["tg", "vk"])
        self.assertFalse(pr._all_required_delivery_ok(self.project, {"tg": {"ok": True}, "vk": {"ok": False}}))
        self.assertTrue(pr._all_required_delivery_ok(self.project, {"tg": {"ok": True}, "vk": {"ok": True}}))

    def test_delivery_ids_are_persistable(self):
        ids = pr._merge_delivery_ids({}, self.project, {
            "tg": {"ok": True, "result": {"message_id": 12}},
            "vk": {"ok": True, "post_id": 34},
        })
        self.assertEqual(ids["tg"]["message_id"], 12)
        self.assertEqual(ids["vk"]["post_id"], 34)

    def test_partial_retry_does_not_duplicate_completed_platform(self):
        src = inspect.getsource(pr.due_posts_all)
        self.assertIn('not in ("draft", "partial")', src)
        self.assertIn("skipped_existing", src)
        self.assertIn("_all_required_delivery_ok", src)
        self.assertIn("ЧАСТИЧНАЯ доставка", src)

    def test_partial_vk_external_wait_is_guarded_before_delivery(self):
        src = inspect.getsource(pr.due_posts_all)
        helper = inspect.getsource(pr._social_delivery_external_wait_map)
        self.assertIn("WAITING_EXTERNAL", src)
        self.assertIn("missing_required.issubset(waits)", src)
        self.assertIn("retryable_external", helper)
        self.assertIn("waiting_external", helper)
        self.assertIn('health.get("fresh")', helper)

    def test_paid_generation_retries_error_but_not_materialized_slot(self):
        src = inspect.getsource(pr._posting_generation_exists)
        self.assertIn('(\"draft\", \"published\", \"partial\")', src)
        self.assertNotIn('== \"error\"', src)

    def test_scheduler_is_singleton_worker_owned(self):
        loop = inspect.getsource(bw._social_posting_loop)
        start = inspect.getsource(bw.main)
        self.assertIn("autopost_all(respect_time=True)", loop)
        self.assertIn("due_posts_all(dry=False)", loop)
        self.assertIn('(\"social_posting\", _social_posting_loop)', start)

    def test_vk_media_transport_is_direct(self):
        src = inspect.getsource(pr._send_vk)
        self.assertNotIn("proxies=", src)
        self.assertIn("photos.getWallUploadServer", src)
        self.assertIn("photos.saveWallPhoto", src)
        self.assertIn("attachment", src)

    def test_moderate_auto_publish_delay_wins_over_next_slot(self):
        src = inspect.getsource(pr._autopost_projects_impl)
        self.assertIn('_delay_hours = int(proj.get("auto_publish_hours") or 0)', src)
        self.assertIn('_publish_at = "" if _delay_hours > 0 else _next_slot(proj)', src)

    def test_ocean_slots_rotate_real_themes_without_forced_ecosystem(self):
        src = inspect.getsource(pr._scheduled_generation_plan)
        helper = inspect.getsource(pr._choose_rotating_project_theme)
        self.assertIn('proj or {}).get("themes")', helper)
        self.assertIn('candidates = [x for x in pool if x not in recent] or pool', helper)
        direct = '_choose_rotating_project_theme(account_id, proj, _random, recent_limit=8)' in src
        guarded = all(marker in src for marker in ("configured =", "_safe_theme", "_recent_plan_themes"))
        self.assertTrue(direct or guarded, src)
        combined = src + "\n" + helper
        self.assertNotIn("виртуальная команда маркетинга и продаж", combined)
        self.assertNotIn("коротко показать всю экосистему", combined)

    def test_ocean_delivery_appends_owner_signature_without_video(self):
        text = pr._project_delivery_text("Автоматизация продаж и рекламы", self.project, "tg")
        self.assertIn("Автоматизация продаж и рекламы", text)
        self.assertIn(self.signature, text)
        self.assertNotIn("анализ звонков — качество продаж", text)
        self.assertNotIn("Command Center — план/факт", text)
        self.assertNotIn("Напишите в MAX или WhatsApp:", text)
        tags = [x for x in text.split() if x.startswith("#")]
        self.assertGreaterEqual(len(tags), 5)
        self.assertLessEqual(len(tags), 10)
        self.assertNotIn("youtu.be", text.lower())
        self.assertNotIn("rutube.ru", text.lower())
        self.assertNotIn("🎥", text)
        self.assertLessEqual(len(text), 1000)

    def test_ocean_media_safety_floor_forces_reuse(self):
        src = inspect.getsource(pr.ensure_ocean_media_reuse)
        self.assertIn('project["image_source"] = "reuse"', src)
        self.assertIn('"image_source": "reuse"', src)
        self.assertNotIn("generate_post(", src)

    def test_social_worker_blocks_generation_without_media_floor(self):
        src = inspect.getsource(bw._social_posting_loop)
        self.assertIn('ensure_ocean_media_reuse("u2")', src)
        self.assertIn('details["ocean_media_safety_floor"]', src)
        self.assertIn('media_floor.get("image_source") == "reuse"', src)

    def test_runner_counts_by_project_local_day_and_passes_project_id(self):
        src = inspect.getsource(pr._autopost_projects_impl)
        self.assertIn("_project_offset", src)
        self.assertIn("timedelta(hours=_project_offset)", src)
        self.assertIn("project_id=pid", src)

    def test_social_trial_is_three_days(self):
        from app.api import posting as posting_api
        src = inspect.getsource(posting_api.start_trial)
        self.assertIn('"period_days": 3', src)
        self.assertIn("3 дня / до 6 постов", src)

    def test_scheduler_has_bounded_missed_slot_catchup(self):
        from datetime import datetime, timedelta
        local_hour = int((datetime.now() + timedelta(hours=3)).strftime("%H"))
        scheduled = local_hour - 1 if local_hour > 0 else local_hour
        proj = {"times": [str(scheduled)], "tz_offset": 3}
        self.assertEqual(pr._scheduled_due_hour(proj, catchup_hours=6), scheduled)
        if local_hour >= 7:
            stale = local_hour - 7
            self.assertIsNone(pr._scheduled_due_hour({"times": [str(stale)], "tz_offset": 3}, catchup_hours=6))
        src = inspect.getsource(pr._autopost_projects_impl)
        self.assertIn("_scheduled_due_hour(proj)", src)
        self.assertIn("slot_hour=_scheduled_hour", src)

    def test_project_api_exposes_scheduler_truth(self):
        from app.api import posting as posting_api
        src = inspect.getsource(posting_api.get_projects)
        self.assertIn("social_posting_scheduler", src)
        self.assertIn("scheduler_ok", src)
        self.assertIn("scheduler_age_seconds", src)

    def test_social_daily_health_tracks_due_slots_and_both_platforms(self):
        src = inspect.getsource(pr.social_project_daily_delivery_health)
        self.assertIn('generation_key', src)
        self.assertIn('social-slot:', src)
        self.assertIn('"tg": bool', src)
        self.assertIn('"vk": bool', src)
        self.assertIn('minutes=15', src)
        self.assertIn('"overdue_due":len(overdue)', src)
        ocean = inspect.getsource(pr.ocean_daily_delivery_health)
        dushi = inspect.getsource(pr.dushi_daily_delivery_health)
        self.assertIn('social_project_daily_delivery_health', ocean)
        self.assertIn('Океан Клиентов и Борис', ocean)
        self.assertIn('social_project_daily_delivery_health', dushi)
        self.assertIn('От Души', dushi)

    def test_social_worker_surfaces_daily_plan_fact_without_blocking_self_heal(self):
        src = inspect.getsource(bw._social_posting_loop)
        self.assertIn('ocean_daily_delivery_health("u2")', src)
        self.assertIn('details["ocean_daily_delivery"]', src)
        self.assertIn('details["daily_delivery_self_heal_active"]', src)
        self.assertIn('autopost_all(respect_time=True)', src)
        self.assertIn('due_posts_all(dry=False)', src)


if __name__ == "__main__":
    unittest.main()
