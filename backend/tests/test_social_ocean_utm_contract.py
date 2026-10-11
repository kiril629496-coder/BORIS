# -*- coding: utf-8 -*-
import inspect
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import posting_runner as pr


class OceanUtmContractTest(unittest.TestCase):
    def setUp(self):
        self.project = {
            "id": "ocean-utm-test",
            "name": "Океан Клиентов и Борис",
            "platforms": "both",
            "channel_tg": "@test",
            "vk_owner_id": -1,
            "footer_signature": "BORIS",
        }
        self.boris = "Авито, МОП, РОП, CRM, аналитика и телефония для роста продаж."
        self.dev = "Разработка SaaS, сайта и мобильного приложения для бизнеса."

    def render(self, text, platform, tid="p001"):
        return pr._project_delivery_text_tracked(text, self.project, platform, tracking_id=tid)

    def test_01_tg_boris(self):
        out = self.render(self.boris, "tg")
        self.assertIn("https://boris-ai.pro/audit?utm_source=tg&utm_medium=social&utm_campaign=ocean&utm_content=p001", out)
        self.assertIn("Получите бесплатно аудит за пару минут + тест BORIS для повышения эффективности рекламы", out)
        self.assertNotIn("software-dev", out)

    def test_02_vk_boris(self):
        out = self.render(self.boris, "vk")
        self.assertIn("https://boris-ai.pro/audit?utm_source=vk&utm_medium=social&utm_campaign=ocean&utm_content=p001", out)
        self.assertNotIn("software-dev", out)

    def test_03_tg_development(self):
        out = self.render(self.dev, "tg")
        self.assertIn("https://boris-ai.pro/software-dev/?utm_source=tg&utm_medium=social&utm_campaign=dev&utm_content=p001", out)

    def test_04_vk_development(self):
        out = self.render(self.dev, "vk")
        self.assertIn("https://boris-ai.pro/software-dev/?utm_source=vk&utm_medium=social&utm_campaign=dev&utm_content=p001", out)

    def test_05_missing_url_fails_gate(self):
        gate = pr._ocean_publication_gate("Текст без ссылки", self.boris, "tg", "p001")
        self.assertFalse(gate["ok"])
        self.assertIn("url_count", gate["errors"])

    def test_06_url_without_utm_fails_gate(self):
        gate = pr._ocean_publication_gate("Подробнее: https://boris-ai.pro/", self.boris, "tg", "p001")
        self.assertFalse(gate["ok"])
        self.assertIn("utm_source", gate["errors"])
        self.assertIn("utm_content", gate["errors"])

    def test_07_wrong_source_fails_gate(self):
        url = "https://boris-ai.pro/?utm_source=vk&utm_medium=social&utm_campaign=ocean&utm_content=p001"
        gate = pr._ocean_publication_gate(url, self.boris, "tg", "p001")
        self.assertFalse(gate["ok"])
        self.assertIn("utm_source", gate["errors"])

    def test_08_wrong_campaign_fails_gate(self):
        url = "https://boris-ai.pro/?utm_source=tg&utm_medium=social&utm_campaign=dev&utm_content=p001"
        gate = pr._ocean_publication_gate(url, self.boris, "tg", "p001")
        self.assertFalse(gate["ok"])
        self.assertIn("utm_campaign", gate["errors"])

    def test_09_wrong_landing_fails_gate(self):
        url = "https://boris-ai.pro/software-dev/?utm_source=tg&utm_medium=social&utm_campaign=ocean&utm_content=p001"
        gate = pr._ocean_publication_gate(url, self.boris, "tg", "p001")
        self.assertFalse(gate["ok"])
        self.assertIn("landing", gate["errors"])

    def test_10_unique_utm_content_for_new_records(self):
        a = pr._social_utm_content("aaaaaaaa1111", self.boris)
        b = pr._social_utm_content("bbbbbbbb2222", self.boris)
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("p"))
        self.assertLessEqual(len(a), 9)

    def test_11_retry_keeps_same_utm_content(self):
        first = pr._social_utm_content("abcdef123456", self.boris)
        retry = pr._social_utm_content("abcdef123456", self.boris)
        self.assertEqual(first, retry)
        self.assertEqual(
            pr._ocean_tracking_spec(self.boris, "tg", first)["final_url"],
            pr._ocean_tracking_spec(self.boris, "tg", retry)["final_url"],
        )

    def test_12_live_redirect_preserves_utm(self):
        spec = pr._ocean_tracking_spec(self.dev, "tg", "predirect")
        r = requests.get(spec["final_url"], allow_redirects=True, timeout=15)
        self.assertLess(r.status_code, 400)
        qs = parse_qs(urlsplit(r.url).query)
        self.assertEqual(qs.get("utm_source"), ["tg"])
        self.assertEqual(qs.get("utm_medium"), ["social"])
        self.assertEqual(qs.get("utm_campaign"), ["dev"])
        self.assertEqual(qs.get("utm_content"), ["predirect"])

    def test_13_generator_cannot_remove_system_link(self):
        source = self.boris + " Смотрите https://example.com/no-track"
        out = self.render(source, "tg", "p013")
        urls = [x for x in out.split() if x.startswith("http")]
        self.assertEqual(len(urls), 1)
        self.assertIn("utm_content=p013", urls[0])
        self.assertNotIn("example.com", out)

    def test_14_publication_gate_blocks_bad_domain(self):
        bad = "https://example.com/?utm_source=tg&utm_medium=social&utm_campaign=ocean&utm_content=p014"
        gate = pr._ocean_publication_gate(bad, self.boris, "tg", "p014")
        self.assertFalse(gate["ok"])
        self.assertIn("domain", gate["errors"])

    def test_15_autocorrection_rebuilds_one_valid_link(self):
        rendered = "Полезный текст https://example.com/bad\n\nПодробнее: https://boris-ai.pro/?utm_source=vk&utm_medium=bad"
        out = pr._ocean_finalize_delivery(rendered, self.boris, "tg", "p015")
        gate = pr._ocean_publication_gate(out, self.boris, "tg", "p015")
        self.assertTrue(gate["ok"], gate)
        self.assertEqual(out.count("http"), 1)

    def test_16_dev_intent_wins(self):
        mixed = "Авито и продажи плюс разработка CRM на заказ и API-интеграция."
        self.assertEqual(pr._ocean_direction(mixed), "software_dev")

    def test_17_generic_sales_falls_back_to_boris(self):
        text = "Автоматизация отдела продаж, CRM BORIS, звонки и аналитика."
        self.assertEqual(pr._ocean_direction(text), "boris")

    def test_18_tracking_record_persists_required_fields(self):
        rec = pr._ocean_tracking_record(self.dev, self.project, "abc123def456")
        for key in ("channel", "direction", "landing_url", "utm_medium", "utm_campaign", "utm_content", "utm_sources", "final_urls"):
            self.assertIn(key, rec)
        self.assertEqual(rec["utm_medium"], "social")
        self.assertEqual(rec["utm_campaign"], "dev")
        self.assertEqual(set(rec["utm_sources"]), {"tg", "vk"})

    def test_19_manual_publish_api_uses_same_ocean_gate_and_tracking(self):
        from app.api import posting as posting_api
        src = inspect.getsource(posting_api.publish_post)
        self.assertIn("_social_is_ocean", src)
        self.assertIn("pr._ocean_publication_gate", src)
        self.assertIn("tracking_id=tracking_id", src)
        self.assertIn("p.update(tracking)", src)
        self.assertIn('p["external_post_id"]', src)
        self.assertIn('p["publication_status"] = "partial"', src)

    def test_20_audit_cta_is_idempotent_on_retry(self):
        first = self.render(self.boris, "tg", "pretry")
        second = pr._ocean_finalize_delivery(first, self.boris, "tg", "pretry")
        self.assertEqual(second.count("https://boris-ai.pro/audit?"), 1)
        self.assertEqual(
            second.count("Получите бесплатно аудит за пару минут + тест BORIS для повышения эффективности рекламы"),
            1,
        )

    def test_21_legacy_plain_offer_in_signature_is_deduplicated(self):
        self.project["footer_signature"] = (
            "BORIS\n\n"
            "🎁 Получите бесплатно аудит за пару минут + тест BORIS для повышения эффективности рекламы\n"
            "Напишите в MAX или WhatsApp."
        )
        out = self.render(self.boris, "tg", "pdedup")
        offer = "Получите бесплатно аудит за пару минут + тест BORIS для повышения эффективности рекламы"
        self.assertEqual(out.count(offer), 1)
        self.assertEqual(out.count("https://boris-ai.pro/audit?"), 1)
        self.assertIn("Напишите в MAX или WhatsApp.", out)

    def test_22_vk_text_only_fallback_disabled_by_default(self):
        rec = {"vk_media_failure_cycles": 9}
        self.assertFalse(pr._vk_text_only_fallback_enabled({}, rec))

    def test_23_vk_text_only_fallback_requires_threshold(self):
        project = {"vk_text_only_fallback_enabled": True, "vk_text_only_fallback_after_cycles": 3}
        self.assertFalse(pr._vk_text_only_fallback_enabled(project, {"vk_media_failure_cycles": 2}))
        self.assertTrue(pr._vk_text_only_fallback_enabled(project, {"vk_media_failure_cycles": 3}))

    def test_24_vk_cooldown_poll_does_not_increment_media_failure_cycles(self):
        project = {"vk_text_only_fallback_enabled": True, "vk_text_only_fallback_after_cycles": 3}
        rec = {"vk_media_failure_cycles": 2}
        pr._apply_vk_delivery_outcome(rec, project, {
            "ok": False,
            "stage": "rate_backoff",
            "reason": "vk_rate_limited",
            "error": "VK cooldown",
        })
        self.assertEqual(rec["vk_media_failure_cycles"], 2)
        self.assertNotIn("vk_text_only_fallback_ready", rec)

    def test_25_vk_real_media_failure_enables_owner_approved_fallback(self):
        project = {"vk_text_only_fallback_enabled": True, "vk_text_only_fallback_after_cycles": 3}
        rec = {"vk_media_failure_cycles": 2}
        pr._apply_vk_delivery_outcome(rec, project, {
            "ok": False,
            "stage": "photos.getWallUploadServer",
            "reason": "vk_rate_limited",
            "error": "загрузка фото ВК: photos.getWallUploadServer: Flood control",
        })
        self.assertEqual(rec["vk_media_failure_cycles"], 3)
        self.assertTrue(rec["vk_text_only_fallback_ready"])
        self.assertTrue(pr._vk_text_only_fallback_enabled(project, rec))

    def test_26_vk_text_only_success_resets_failure_state(self):
        project = {"vk_text_only_fallback_enabled": True, "vk_text_only_fallback_after_cycles": 3}
        rec = {"vk_media_failure_cycles": 3, "vk_text_only_fallback_ready": True}
        pr._apply_vk_delivery_outcome(rec, project, {"ok": True, "post_id": 123}, used_text_only=True)
        self.assertEqual(rec["vk_media_failure_cycles"], 0)
        self.assertNotIn("vk_text_only_fallback_ready", rec)
        self.assertIn("vk_text_only_fallback_used_at", rec)

    def test_27_posting_project_api_exposes_owner_fallback_fields(self):
        from app.api.posting import ProjectBody
        body = ProjectBody(vk_text_only_fallback_enabled=True, vk_text_only_fallback_after_cycles=4)
        self.assertTrue(body.vk_text_only_fallback_enabled)
        self.assertEqual(body.vk_text_only_fallback_after_cycles, 4)


if __name__ == "__main__":
    unittest.main()
