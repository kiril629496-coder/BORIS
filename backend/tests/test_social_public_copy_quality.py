# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import posting_runner as pr


class SocialPublicCopyQualityContractTest(unittest.TestCase):
    def test_sanitizer_removes_prompt_leaks_and_hashtag_wall(self):
        raw = (
            "<b>Живой заголовок</b>\n\n"
            "Полезная мысль про Авито.\n"
            "ТОН: разговорный\n"
            "ДЛЯ ИЗОБРАЖЕНИЯ: синий фон\n"
            "#BORIS #авито #автоматизация"
        )
        clean = pr._sanitize_public_post_text(raw)
        self.assertNotIn("ТОН:", clean)
        self.assertNotIn("ДЛЯ ИЗОБРАЖЕНИЯ:", clean)
        self.assertNotIn("#", clean)
        self.assertIn("Полезная мысль про Авито", clean)

    def test_ocean_delivery_has_no_legacy_script_footer(self):
        raw = (
            "<b>Одна конкретная мысль</b>\n\n"
            "Авито приносит заявки, но важно видеть, где они теряются.\n\n"
            "Попробовать BORIS бесплатно\n"
            "🎥 BORIS в работе: https://youtu.be/example\n"
            "#BORIS #авито #автоматизация"
        )
        signature = "Клиентская подпись Ocean для теста."
        proj = {
            "name": "Океан Клиентов и Борис",
            "footer_signature": signature,
        }
        helper_clean = pr._ocean_final_text(raw, proj)
        clean = pr._project_delivery_text(raw, proj, "tg")
        self.assertNotIn("🎥 BORIS в работе", helper_clean)
        self.assertIn("#BORIS", helper_clean)
        self.assertIn(signature, helper_clean)
        self.assertNotIn("AI-РОП — контроль продаж", helper_clean)
        self.assertNotIn("🎥 BORIS в работе", clean)
        self.assertNotIn("youtu", clean.lower())
        self.assertIn("#BORIS", clean)
        self.assertIn(signature, clean)
        self.assertNotIn("AI-РОП — контроль продаж", clean)
        self.assertLessEqual(len(clean), 1000)

    def test_dushi_delivery_has_contact_and_hashtags(self):
        raw = (
            "<b>Тёплые слова лучше шаблона</b>\n\n"
            "Вспомните одну деталь о человеке — и поздравление станет личным.\n\n"
            "#поздравление #тост #отдуши"
        )
        proj = {
            "name": "От Души",
            "contact_tg": "@kirill86oko",
            "hashtags": {"base": ["отдуши", "тост"]},
        }
        clean = pr._project_delivery_text(raw, proj, "tg")
        self.assertIn("#отдуши", clean)
        self.assertGreaterEqual(clean.count("#"), 5)
        self.assertLessEqual(clean.count("#"), 10)
        self.assertIn("@kirill86oko", clean)
        self.assertIn("Вспомните одну деталь", clean)
        self.assertNotIn("Забрать бесплатный тест", clean)
        self.assertNotIn("Остались вопросы?", clean)

    def test_manicure_fallback_uses_canonical_public_policy(self):
        raw = (
            "Совет мастера про голубой френч. "
            "Ко Дню нефтяника сделаем дизайн. "
            "Записаться: +7 999 111-22-33 "
            "https://example.invalid @master\n"
            "#маникюр #евпатория #ногти #френч #голубой #крым"
        )
        proj = {"id": "270dbf472f7f", "name": "Маникюр Евпатория"}
        clean = pr._project_delivery_text(raw, proj, "vk")
        self.assertIn("Совет мастера про голубой френч.", clean)
        self.assertNotIn("нефт", clean.lower())
        self.assertNotIn("example.invalid", clean)
        self.assertNotIn("@master", clean)
        self.assertNotIn("111-22-33", clean)
        self.assertGreaterEqual(clean.count("#"), 5)

    def test_ocean_and_dushi_do_not_force_calendar_occasion(self):
        self.assertEqual(pr._project_occasion({"name": "Океан Клиентов и Борис"}), "")
        self.assertEqual(pr._project_occasion({"name": "От Души"}), "")

    def test_vk_pending_permanent_unavailable_is_terminal(self):
        rec = {
            "status": "published",
            "external_edit_pending": ["vk"],
            "external_edit_unavailable": {
                "vk": {"reason": "VK_EDIT_WINDOW_EXPIRED", "code": 15}
            },
        }
        self.assertFalse(pr._social_vk_pending_record_retryable(rec))
        rec.pop("external_edit_unavailable")
        self.assertTrue(pr._social_vk_pending_record_retryable(rec))

    def test_vk_pending_queue_is_newest_first_across_projects(self):
        older_ocean = ("u2", "old", -121, 7306, "old", "Океан Клиентов и Борис", "2026-08-29 06:01")
        newer_dushi = ("u2", "new", -239, 93, "new", "От Души", "2026-08-30 06:02")
        ranked = sorted(
            [older_ocean, newer_dushi],
            key=pr._social_vk_pending_sort_key,
            reverse=True,
        )
        self.assertEqual(ranked[0][3], 93)
        self.assertEqual(ranked[0][5], "От Души")

    def test_prompt_does_not_force_same_engagement_question(self):
        prompt = pr._build_text_prompt("практический совет")
        self.assertIn("ВОВЛЕЧЕНИЕ БЕЗ ШАБЛОНА", prompt)
        self.assertNotIn("вопрос в конце обязателен", prompt)
        self.assertNotIn("Заверши пост живым вопросом", prompt)

    def test_scheduled_plan_has_no_forced_ecosystem_slot(self):
        import inspect
        source = inspect.getsource(pr._scheduled_generation_plan)
        self.assertNotIn("коротко показать всю экосистему", source)
        self.assertNotIn("AI-Авитолог, AI-менеджер, AI-РОП", source)
        # In production the owner-policy guard may wrap the mutable runner plan.
        # The wrapper must normalize any non-configured/recent theme via _safe_theme.
        if "def guarded_plan" in source:
            self.assertIn("_safe_theme", source)
            self.assertNotIn("canonical_ocean_ecosystem", source)
        else:
            self.assertIn("_choose_rotating_project_theme", source)

    def test_dushi_contact_is_required_on_every_post(self):
        proj = {"name": "От Души", "contact_tg": "@kirill86oko"}
        texts = [
            "<b>История %d</b>\n\nТёплая личная деталь номер %d." % (i, i)
            for i in range(10)
        ]
        rendered = [pr._project_delivery_text(t, proj, "tg") for t in texts]
        self.assertTrue(all("@kirill86oko" in x for x in rendered))
        self.assertTrue(all("#отдуши" in x for x in rendered))
        self.assertTrue(all(5 <= x.count("#") <= 10 for x in rendered))

    def test_runtime_public_copy_boundary_is_green(self):
        health = pr.public_copy_runtime_boundary_health()
        self.assertTrue(health.get("ok"), health)
        self.assertTrue(health.get("ocean_ok"), health)
        self.assertTrue(health.get("dushi_ok"), health)


if __name__ == "__main__":
    unittest.main(verbosity=2)
