# -*- coding: utf-8 -*-
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services import social_quality as sq
import posting_runner as pr


class SocialQualityBriefTest(unittest.TestCase):
    def _manicure(self):
        return {
            "id": "270dbf472f7f",
            "name": "Маникюр Евпатория",
            "platforms": "vk",
            "tz_offset": 3,
            "text_prompt": "АУДИТОРИЯ\nЖенщины Евпатории.\nЭкспертный тон.",
            "seasonal_exclusions": ["море", "пляж"],
        }

    def test_october_brief_forbids_sea_and_has_single_theme(self):
        with patch.object(sq, "_feedback_preferences", return_value={"likes": 0, "dislikes": {}, "recent": []}):
            brief = sq.build_content_brief(
                "u2",
                self._manicure(),
                "педикюр осенью: зачем он нужен. Услуга: педикюр.",
                recent_posts=[],
                now=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(brief["season"], "осень-зима")
        self.assertIn("море", brief["forbidden_mentions"])
        self.assertIn("пляж", brief["forbidden_mentions"])
        self.assertEqual(
            brief["selected_theme"],
            "педикюр осенью: зачем он нужен",
        )

    def test_visual_brief_pedicure_is_feet_not_hands(self):
        brief = {
            "selected_theme": "педикюр осенью: бордовый оттенок",
            "forbidden_mentions": ["море", "пляж"],
        }
        visual = sq.build_visual_brief(
            brief,
            "Осенний педикюр на коротких ногтях.",
            self._manicure(),
        )
        self.assertIn("стоп", visual["main_object"].lower())
        self.assertIn("бордов", visual["primary_color"].lower())
        self.assertIn("руки вместо стоп", visual["forbidden_objects"])
        self.assertIn("море", visual["forbidden_objects"])


class SocialCopyQualityTest(unittest.TestCase):
    def _brief(self):
        return {
            "selected_theme": "педикюр осенью",
            "forbidden_mentions": ["море", "пляж", "купание"],
        }

    def _project(self):
        return {"id": "270dbf472f7f", "name": "Маникюр Евпатория"}

    def test_metadata_leak_is_blocked(self):
        text = (
            "<b>Педикюр осенью</b>\n\n"
            "Услуга: педикюр. Формат подачи: совет мастера. "
            "Осенью закрытая обувь меняет привычки ухода. "
            "Короткая длина удобна каждый день и выглядит аккуратно. "
            "Сохраните идею для следующего визита."
        )
        out = sq.evaluate_copy(text, self._brief(), self._project(), recent_posts=[])
        self.assertFalse(out["pass"])
        self.assertIn("prompt_metadata_leak", out["blockers"])

    def test_sea_in_october_is_blocked(self):
        text = (
            "<b>Педикюр осенью</b>\n\n"
            "Перед поездкой к морю хочется подобрать покрытие под пляж. "
            "Соль и купание меняют привычный уход за стопами. "
            "Выберите спокойный оттенок и сохраните эту идею."
        )
        out = sq.evaluate_copy(text, self._brief(), self._project(), recent_posts=[])
        self.assertFalse(out["pass"])
        self.assertTrue(any(x.startswith("season_forbidden:") for x in out["blockers"]))

    def test_near_duplicate_inside_7_days_is_blocked(self):
        base = (
            "<b>Педикюр осенью нужен не только ради цвета</b>\n\n"
            "В закрытой обуви важнее аккуратная короткая длина и регулярный уход. "
            "Спокойный нюд выглядит собранно и не требует сложного дизайна. "
            "Сохраните идею на следующий визит."
        )
        recent = [{
            "id": "old",
            "created_at": "2026-10-03 12:00",
            "text": base,
        }]
        out = sq.evaluate_copy(base, self._brief(), self._project(), recent_posts=recent)
        self.assertFalse(out["pass"])
        self.assertIn("semantic_repeat_7d", out["blockers"])

    def test_distinct_useful_copy_passes(self):
        text = (
            "<b>Закрытая обувь — не повод забывать о педикюре</b>\n\n"
            "Осенью меняется не необходимость ухода, а его задача. "
            "Короткая естественная длина меньше мешает в повседневной обуви, "
            "а спокойное покрытие можно выбирать уже не под босоножки, а под собственный вкус.\n\n"
            "Если яркий цвет сейчас не нужен, оставьте акцент на аккуратности и удобстве."
        )
        out = sq.evaluate_copy(text, self._brief(), self._project(), recent_posts=[])
        self.assertTrue(out["pass"], out)


class SocialVisionQualityTest(unittest.TestCase):
    def test_all_required_vision_flags_must_pass(self):
        fake = {
            "pass": True,
            "semantic_match": True,
            "service_ok": True,
            "object_ok": True,
            "anatomy_ok": True,
            "headline_ok": True,
            "headline_complete_ok": True,
            "spelling_ok": True,
            "forbidden_ok": True,
            "season_ok": True,
            "professional_ok": True,
            "mobile_ok": True,
            "creative_distinctiveness_ok": True,
            "premium_palette_ok": True,
            "premium_typography_ok": True,
            "issues": [],
        }
        with patch.object(sq, "_image_data_uri", return_value="data:image/jpeg;base64,AA=="), \
             patch("app.services.media_semantic_validator._vision_chat_json", return_value=(fake, {"cost_rub": 0.01})):
            out = sq.validate_banner_vision(
                "u2", "/tmp/x.png",
                {"selected_theme": "педикюр осенью", "forbidden_mentions": ["море"]},
                {"main_object": "стопы", "headline": "Педикюр осенью"},
                "Полезный текст про педикюр.",
                {"name": "Маникюр Евпатория"},
            )
        self.assertTrue(out["pass"])

    def test_one_failed_flag_fails_closed(self):
        fake = {
            "pass": True,
            "semantic_match": True,
            "service_ok": True,
            "object_ok": True,
            "anatomy_ok": False,
            "headline_ok": True,
            "headline_complete_ok": True,
            "spelling_ok": True,
            "forbidden_ok": True,
            "season_ok": True,
            "professional_ok": True,
            "mobile_ok": True,
            "creative_distinctiveness_ok": True,
            "premium_palette_ok": True,
            "premium_typography_ok": True,
            "issues": ["лишний палец"],
        }
        with patch.object(sq, "_image_data_uri", return_value="data:image/jpeg;base64,AA=="), \
             patch("app.services.media_semantic_validator._vision_chat_json", return_value=(fake, {"cost_rub": 0.01})):
            out = sq.validate_banner_vision(
                "u2", "/tmp/x.png", {}, {}, "Текст", {}
            )
        self.assertFalse(out["pass"])
        self.assertIn("лишний палец", out["issues"])


class SocialGenerateQualityIntegrationTest(unittest.TestCase):
    def test_generate_post_builds_one_brief_and_quality_gate(self):
        project = {
            "id": "p1",
            "name": "Тестовый проект",
            "platforms": "vk",
            "tz_offset": 3,
            "text_prompt": "",
            "banner_prompt": "",
            "image_source": "ai",
        }
        brief = {
            "policy_version": sq.POLICY_VERSION,
            "business": "Тестовый проект",
            "audience": "клиенты",
            "platform": "vk",
            "current_date": "2026-10-04",
            "season": "осень-зима",
            "selected_theme": "как не терять заявки",
            "client_problem": "как не терять заявки",
            "main_thesis": "каждому обращению нужен следующий шаг",
            "format": "полезный совет",
            "emotional_tone": "живой",
            "cta": "спокойный вывод без призыва",
            "visual_idea": "следующий шаг",
            "seasonal_priorities": [],
            "forbidden_mentions": [],
            "feedback_preferences": {"avoid": [], "positive_count": 0},
        }
        strong = (
            "<b>Заявка теряется не в CRM, а между действиями</b>\n\n"
            "Когда после первого ответа не зафиксирован следующий шаг, диалог легко остаётся без продолжения. "
            "Полезнее сразу определить, что должно произойти дальше: уточнение, расчёт или звонок.\n\n"
            "Так менеджеру не приходится вспоминать о клиенте случайно, а владелец видит реальное движение по обращению."
        )
        with patch.object(sq, "recent_project_posts", return_value=[]), \
             patch.object(sq, "build_content_brief", return_value=brief), \
             patch.object(pr, "_openai_text", return_value=strong), \
             patch.object(pr, "_generate_social_quality_banner") as banner:
            def _banner(**kwargs):
                result = kwargs["result"]
                result.update({
                    "banner": "/tmp/ok.png",
                    "banner_engine": "test",
                    "banner_model": "test",
                    "vision_qa": {"pass": True},
                    "banner_cost_rub": 1.0,
                    "quality_status": "pass",
                })
                return result
            banner.side_effect = _banner
            out = pr.generate_post(
                "u2", "как не терять заявки",
                image_source="ai",
                project_id="p1",
                project=project,
                content_brief=brief,
            )
        self.assertEqual(out["content_brief"]["selected_theme"], "как не терять заявки")
        self.assertTrue(out["copy_quality"]["pass"], out["copy_quality"])
        self.assertEqual(out["visual_brief"]["policy_version"], sq.POLICY_VERSION)
        self.assertEqual(banner.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class SocialProjectThemeBucketTest(unittest.TestCase):
    def test_ocean_topics_split_into_business_modules(self):
        proj={"name":"Океан Клиентов и Борис"}
        self.assertEqual(pr._social_theme_bucket(proj,"как выбрать категорию на Авито"),"avito")
        self.assertEqual(pr._social_theme_bucket(proj,"как вести соцсети, когда некогда"),"social_content")
        self.assertEqual(pr._social_theme_bucket(proj,"анализ конкурентов и цены"),"market_growth")

    def test_dushi_story_buckets_are_distinct(self):
        proj={"name":"От Души"}
        self.assertEqual(pr._social_theme_bucket(proj,"тост на свадьбу"),"wedding")
        self.assertEqual(pr._social_theme_bucket(proj,"как поздравить маму"),"parents")
        self.assertEqual(pr._social_theme_bucket(proj,"песня в подарок коллективу"),"team")


class SocialPostPublishVerifyRuntimeTest(unittest.TestCase):
    def test_verifier_has_timezone_support_for_durable_timestamps(self):
        self.assertEqual(pr.timezone.utc.utcoffset(None).total_seconds(), 0)

class SocialBannerFullCostTest(unittest.TestCase):
    def test_banner_total_cost_includes_image_and_vision_qa(self):
        class DummyDB:
            def close(self): pass
        result = {"text": "Полезный пост", "quality_status": "copy_pass"}
        with patch("app.db.session.SessionLocal", return_value=DummyDB()),              patch("app.services.image_service.generate_banner_financially_guarded", return_value={"ok": True, "cost_rub": 1.40}),              patch.object(sq, "validate_banner_vision", return_value={"pass": True, "qa_cost_rub": 0.60, "issues": []}),              patch("os.path.exists", return_value=True),              patch("os.path.getsize", return_value=1024):
            out = pr._generate_social_quality_banner(
                account_id="u2",
                project_id="p1",
                theme="полезная тема",
                banner_prompt="",
                result=result,
                project={"id": "p1", "name": "Тест"},
                content_brief={"selected_theme": "полезная тема"},
                visual_brief={"headline": "Полезная тема"},
                quality_module=sq,
            )
        self.assertEqual(out["banner_attempt_costs"], [1.4])
        self.assertEqual(out["banner_vision_qa_costs"], [0.6])
        self.assertEqual(out["banner_cost_rub"], 2.0)
        self.assertEqual(out["quality_status"], "pass")


class SocialManicureQualityV2Test(unittest.TestCase):
    def _project(self):
        return {
            "id": "270dbf472f7f",
            "name": "Маникюр Евпатория",
            "platforms": "both",
            "tz_offset": 3,
            "text_prompt": "АУДИТОРИЯ\nЖенщины Евпатории.",
            "seasonal_exclusions": ["море", "пляж"],
        }

    def test_incomplete_headline_flag_fails_closed(self):
        fake = {
            "pass": True,
            "semantic_match": True,
            "service_ok": True,
            "object_ok": True,
            "anatomy_ok": True,
            "headline_ok": True,
            "headline_complete_ok": False,
            "spelling_ok": True,
            "forbidden_ok": True,
            "season_ok": True,
            "professional_ok": True,
            "mobile_ok": True,
            "creative_distinctiveness_ok": True,
            "premium_palette_ok": True,
            "premium_typography_ok": True,
            "issues": ["unfinished headline"],
        }
        with patch.object(sq, "_image_data_uri", return_value="data:image/jpeg;base64,AA=="),              patch("app.services.media_semantic_validator._vision_chat_json", return_value=(fake, {"cost_rub": 0.01})):
            out = sq.validate_banner_vision(
                "u2", "/tmp/x.png",
                {"selected_theme": "длина ногтей под повседневный ритм", "forbidden_mentions": []},
                {"main_object": "ногти", "headline": "Удобная длина ногтей"},
                "Текст про удобную длину ногтей.",
                self._project(),
            )
        self.assertFalse(out["pass"])

    def test_creative_distinctiveness_required(self):
        fake = {
            "pass": True,
            "semantic_match": True,
            "service_ok": True,
            "object_ok": True,
            "anatomy_ok": True,
            "headline_ok": True,
            "headline_complete_ok": True,
            "spelling_ok": True,
            "forbidden_ok": True,
            "season_ok": True,
            "professional_ok": True,
            "mobile_ok": True,
            "creative_distinctiveness_ok": False,
            "premium_palette_ok": True,
            "premium_typography_ok": True,
            "issues": ["generic beige beauty-template look"],
        }
        with patch.object(sq, "_image_data_uri", return_value="data:image/jpeg;base64,AA=="),              patch("app.services.media_semantic_validator._vision_chat_json", return_value=(fake, {"cost_rub": 0.01})):
            out = sq.validate_banner_vision(
                "u2", "/tmp/x.png",
                {"selected_theme": "педикюр осенью", "forbidden_mentions": ["море"]},
                {"main_object": "стопы", "headline": "Педикюр нужен и осенью"},
                "Текст про осенний педикюр.",
                self._project(),
            )
        self.assertFalse(out["pass"])

    def test_manicure_visual_brief_forbids_beige_empty_template(self):
        brief = {
            "selected_theme": "педикюр осенью",
            "forbidden_mentions": ["море", "пляж"],
            "visual_variant": "premium_product_beauty",
            "business": "Маникюр Евпатория",
            "project_id": "270dbf472f7f",
        }
        visual = sq.build_visual_brief(brief, "Текст про педикюр.", self._project())
        forbidden = " | ".join(visual["forbidden_objects"]).lower()
        self.assertIn("однотонный бежевый фон", forbidden)
        self.assertIn("только стопы на пустом фоне", forbidden)
        self.assertEqual(visual["composition_variant"], "premium_product_beauty")

    def test_duplicate_hashtags_are_removed_before_single_append(self):
        text = "Полезный текст #маникюр #евпатория"
        out = pr._append_public_hashtags(text, ["#маникюр", "#евпатория"])
        self.assertEqual(out.count("#маникюр"), 1)
        self.assertEqual(out.count("#евпатория"), 1)

    def test_manicure_titles_are_three_to_five_words(self):
        themes = [
            "длина ногтей под повседневный ритм",
            "педикюр осенью",
            "осенние оттенки бордо",
        ]
        for theme in themes:
            title = pr._manicure_banner_title(theme)
            words = title.split()
            self.assertGreaterEqual(len(words), 3, title)
            self.assertLessEqual(len(words), 5, title)

    def test_manicure_title_is_complete_not_truncated(self):
        title = pr._manicure_banner_title("длина ногтей должна жить в вашем ритме")
        self.assertEqual(title, "Удобная длина ногтей")
        self.assertNotEqual(title, "Длина ногтей должна жить в вашем")


class SocialPremiumArtDirectionV3Test(unittest.TestCase):
    def _project(self):
        return {"id":"270dbf472f7f","name":"Маникюр Евпатория"}

    def _base_fake(self):
        return {
            "pass": True,
            "semantic_match": True,
            "service_ok": True,
            "object_ok": True,
            "anatomy_ok": True,
            "headline_ok": True,
            "headline_complete_ok": True,
            "spelling_ok": True,
            "forbidden_ok": True,
            "season_ok": True,
            "professional_ok": True,
            "mobile_ok": True,
            "creative_distinctiveness_ok": True,
            "premium_palette_ok": True,
            "premium_typography_ok": True,
            "issues": [],
        }

    def test_cheap_palette_fails_closed(self):
        fake=self._base_fake(); fake["premium_palette_ok"]=False; fake["issues"]=["muddy brown salon palette"]
        with patch.object(sq,"_image_data_uri",return_value="data:image/jpeg;base64,AA=="),              patch("app.services.media_semantic_validator._vision_chat_json",return_value=(fake,{"cost_rub":0.01})):
            out=sq.validate_banner_vision("u2","/tmp/x.png",{},{},"text",self._project())
        self.assertFalse(out["pass"])

    def test_dated_typography_fails_closed(self):
        fake=self._base_fake(); fake["premium_typography_ok"]=False; fake["issues"]=["oversized classical serif"]
        with patch.object(sq,"_image_data_uri",return_value="data:image/jpeg;base64,AA=="),              patch("app.services.media_semantic_validator._vision_chat_json",return_value=(fake,{"cost_rub":0.01})):
            out=sq.validate_banner_vision("u2","/tmp/x.png",{},{},"text",self._project())
        self.assertFalse(out["pass"])


class SocialManicureLocalArtDirectionTest(unittest.TestCase):
    def test_local_art_direction_outputs_valid_square_png(self):
        import tempfile, os
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            src=os.path.join(td,"src.png")
            out=os.path.join(td,"out.png")
            Image.new("RGB",(1024,1024),(230,230,235)).save(src)
            result=sq.apply_manicure_local_art_direction(
                src,
                "Удобная длина ногтей",
                {"composition_variant":"macro_beauty_closeup"},
                output_path=out,
            )
            self.assertEqual(result,out)
            with Image.open(out) as im:
                self.assertEqual(im.size,(1024,1024))
                self.assertEqual(im.format,"PNG")

    def test_local_art_direction_rejects_wrong_headline_length(self):
        import tempfile, os
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            src=os.path.join(td,"src.png")
            Image.new("RGB",(1024,1024),(230,230,235)).save(src)
            with self.assertRaises(ValueError):
                sq.apply_manicure_local_art_direction(
                    src,
                    "Слишком коротко",
                    {"composition_variant":"macro_beauty_closeup"},
                )
