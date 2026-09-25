# -*- coding: utf-8 -*-
import unittest
import posting_runner as pr
from app.services import social_owner_policy_v3 as owner


class SocialContentQualityContractTest(unittest.TestCase):
    def test_public_sanitizer_removes_meta_markdown_and_hash_tail(self):
        raw=(
            "**Живой заголовок**\n\nПолезная мысль про Авито.\n"
            "ТОН: разговорный\nДЛЯ ИЗОБРАЖЕНИЯ: синий фон\n"
            "#BORIS #авито #автоматизация"
        )
        clean=pr._sanitize_public_post_text(raw)
        self.assertNotIn("**",clean)
        self.assertNotIn("ТОН:",clean)
        self.assertNotIn("ДЛЯ ИЗОБРАЖЕНИЯ:",clean)
        self.assertNotIn("#BORIS",clean)

    def test_ocean_delivery_uses_current_signature_without_video_or_meta(self):
        raw=(
            "<b>Одна конкретная мысль</b>\n\n"
            "Авито и продажи должны быть связаны.\n"
            "ТОН: разговорный\n"
            "🎥 BORIS в работе: https://youtu.be/example\n"
            "#старый #мусор"
        )
        signature="Клиентская подпись Ocean для теста."
        clean=owner.render_public_text(
            raw,
            {"name":"Океан Клиентов и Борис","footer_signature":signature},
            "tg",
        )
        self.assertIn(signature,clean)
        self.assertIn("🤖 BORIS: https://boris-ai.pro/?utm_source=tg&utm_medium=ocean&utm_campaign=boris",clean)
        self.assertIn("💻 Разработка ПО: https://boris-ai.pro/software-dev/?utm_source=tg&utm_medium=ocean&utm_campaign=dev",clean)
        self.assertEqual(clean.count("utm_campaign=boris"),1)
        self.assertEqual(clean.count("utm_campaign=dev"),1)
        self.assertNotIn("Настройка BORIS и сопровождение первого месяца — бесплатно.",clean)
        self.assertIn("#BORIS",clean)
        self.assertNotIn("🎥",clean)
        self.assertNotIn("youtu",clean.lower())
        self.assertNotIn("ТОН:",clean)
        self.assertLessEqual(len(clean),1000)

    def test_dushi_delivery_requires_contact_and_topical_hashtags(self):
        raw="<b>Тёплые слова</b>\n\nИстория важнее шаблона."
        clean=owner.render_public_text(
            raw,{"name":"От Души","contact_tg":"@example"},"tg"
        )
        self.assertIn("@example",clean)
        self.assertIn("#отдуши",clean)
        self.assertGreaterEqual(clean.count("#"),5)
        self.assertLessEqual(clean.count("#"),10)

    def test_no_forced_calendar_hook_for_ocean_and_dushi(self):
        owner.install_posting_runner_guard(pr)
        self.assertEqual(pr._project_occasion({"name":"Океан Клиентов и Борис"}),"")
        self.assertEqual(pr._project_occasion({"name":"От Души"}),"")


if __name__=="__main__":
    unittest.main(verbosity=2)
