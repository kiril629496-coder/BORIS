"""Current-post banner headings: no DB, provider, image, QA or transport calls."""
import ast
import copy
import hashlib
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import social_quality as sq


DUSHI = {"id": "f938caa3a944", "name": "От Души", "tz_offset": 3}
OCEAN = {"id": "a374ced25745", "name": "Океан Клиентов и Борис", "tz_offset": 3}
MANICURE = {"id": "270dbf472f7f", "name": "Маникюр Евпатория", "tz_offset": 3}
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
RUNNER_SOURCE = Path(sq.__file__).resolve().parents[2] / "posting_runner.py"
GENERATE_POST_SHA256 = "d3b06acec77aaeac94d9012d1a6099581ec7175f7f306f2f9634394c21be0b09"


class ProjectVisualHeadlineTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(sq, "SessionLocal", side_effect=AssertionError("DB access is forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        feedback = patch.object(sq, "_feedback_preferences", return_value={"likes": 0, "dislikes": {}, "recent": []})
        feedback.start()
        self.addCleanup(feedback.stop)

    def brief(self, project, theme, recent=None):
        return sq.build_content_brief("u2", project, theme, recent_posts=recent or [], now=NOW)

    def visual(self, project, theme, text, recent=None):
        brief = self.brief(project, theme, recent)
        return sq.build_visual_brief(brief, text, project)

    def test_dushi_distinct_real_subjects_keep_their_actual_complete_headings(self):
        cases = (
            ("Поздравление начальнику без лести", "Поздравить начальника без лести — это тоже можно 🌿", "Поздравить начальника без лести — это тоже можно"),
            ("Тост на свадьбе без штампов", "Свадебный тост из одной общей истории", "Свадебный тост из одной общей истории"),
            ("Поздравление маме с юбилеем", "Один эпизод из детства для мамы", "Один эпизод из детства для мамы"),
        )
        headings = []
        for theme, text, expected in cases:
            with self.subTest(theme=theme):
                visual = self.visual(DUSHI, theme, text)
                self.assertEqual(visual["headline"], expected)
                headings.append(visual["headline"])
        self.assertEqual(len(set(headings)), 3)

    def test_ocean_avito_posts_do_not_share_the_old_generic_heading(self):
        one = self.visual(OCEAN, "Авито: как выбрать первый кадр объявления", "Первый кадр объясняет товар за секунду")
        two = self.visual(OCEAN, "Объявления: проверка заявки после ответа", "После ответа у клиента должен быть следующий шаг")
        self.assertEqual(one["headline"], "Первый кадр объясняет товар за секунду")
        self.assertEqual(two["headline"], "После ответа у клиента должен быть следующий шаг")
        self.assertNotEqual(one["headline"], two["headline"])

    def test_long_first_line_uses_complete_current_theme_without_clipping(self):
        theme = "Поздравление коллеге без дежурных фраз"
        text = "Как подобрать искренние слова для коллеги, чтобы поздравление напоминало о совместной истории и не звучало формально"
        self.assertGreater(len(text), 80)
        self.assertEqual(self.visual(DUSHI, theme, text)["headline"], theme)

    def test_two_long_candidates_block_before_an_image_can_be_bought(self):
        theme = "Как подготовить поздравление для коллеги с учётом общей истории, его характера и отношений внутри большой команды"
        text = "Одна конкретная история из совместной работы часто помогает сказать больше, чем длинный список общих добрых пожеланий"
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_COPY_QUALITY_BLOCKED:headline_requires_unique_complete_short_rewrite"):
            self.visual(DUSHI, theme, text)

    def test_recent_visual_heading_is_not_reused_even_when_text_heading_is_new(self):
        history = [{"id": "old", "project_id": DUSHI["id"], "created_at": "2026-10-08T15:00:00+00:00",
                    "text": "Другой заголовок старого поста", "visual_brief": {"headline": "Шаблонное поздравление видно сразу"}}]
        theme = "Поздравление по личной истории"
        visual = self.visual(DUSHI, theme, "Шаблонное поздравление видно сразу", history)
        self.assertEqual(visual["headline"], theme)

    def test_both_repeated_headings_block_instead_of_falling_back_to_a_slogan(self):
        theme = "Поздравление по личной истории"
        heading = "Шаблонное поздравление видно сразу"
        history = [{"project_id": DUSHI["id"], "created_at": "2026-10-08", "visual_brief": {"headline": h}}
                   for h in (theme, heading)]
        with self.assertRaisesRegex(RuntimeError, "headline_requires_unique_complete_short_rewrite"):
            self.visual(DUSHI, theme, heading, history)

    def test_headline_history_uses_project_local_date_and_excludes_foreign_project(self):
        history = [
            {"project_id": DUSHI["id"], "created_at": "2026-09-24T22:30:00+00:00", "visual_brief": {"headline": "В границе четырнадцати дней"}},
            {"project_id": DUSHI["id"], "created_at": "2026-09-24T19:00:00+00:00", "visual_brief": {"headline": "За границей четырнадцати дней"}},
            {"project_id": OCEAN["id"], "created_at": "2026-10-08", "visual_brief": {"headline": "Заголовок другого проекта"}},
        ]
        self.assertEqual(self.brief(DUSHI, "Поздравление коллеге", history)["recent_visual_headlines"], ["В границе четырнадцати дней"])

    def test_unknown_date_and_pending_post_still_protect_their_visible_heading(self):
        history = [
            {"project_id": DUSHI["id"], "status": "partial", "visual_brief": {"headline": "Уже показанный заголовок"}},
            {"project_id": DUSHI["id"], "status": "draft", "created_at": "2026-10-10", "visual_brief": {"headline": "Заголовок ожидающего поста"}},
        ]
        brief = self.brief(DUSHI, "Поздравление коллеге", history)
        self.assertEqual(brief["recent_visual_headlines"], ["Уже показанный заголовок", "Заголовок ожидающего поста"])

    def test_history_is_bounded_and_does_not_mutate_historical_records(self):
        history = [{"project_id": DUSHI["id"], "created_at": "2026-10-08", "visual_brief": {"headline": "История номер " + str(i)},
                    "ids": {"tg": {"message_id": 173 + i}}, "banner_cost_rub": 1.587} for i in range(40)]
        before = copy.deepcopy(history)
        brief = self.brief(DUSHI, "Поздравление коллеге", history)
        self.assertEqual(len(brief["recent_visual_headlines"]), 30)
        self.assertEqual(history, before)

    def test_editorial_prompt_prepares_short_unique_heading_before_banner_stage(self):
        history = [{"project_id": DUSHI["id"], "visual_brief": {"headline": "Слова по вашей истории"}}]
        prompt = sq.content_brief_prompt(self.brief(DUSHI, "Поздравление коллеге", history))
        self.assertIn("не длиннее 80 знаков", prompt)
        self.assertIn("Слова по вашей истории", prompt)
        self.assertIn("не повторять", prompt)

    def test_open_ending_or_ellipsis_does_not_become_a_paid_banner_heading(self):
        for heading in ("Свадебный тост — это когда", "Слова для мамы и", "Поздравление начинается с…", "Поздравление начинается с...",
                        "Поздравление начинается с воспоминания...", "Свадебный тост помогает сказать главное...",
                        "<b>Поздравление начинается с воспоминания...</b>", "Поздравление начинается с воспоминания... 🎈"):
            with self.subTest(heading=heading):
                theme = "Свадебный тост из общей истории"
                visual = self.visual(DUSHI, theme, heading)
                self.assertEqual(visual["headline"], theme)

    def test_only_current_wording_is_used_without_inventing_quotes_or_event_dates(self):
        theme = "Тост для семейного праздника"
        text = "Один тёплый эпизод вместо общих слов"
        brief = self.brief(DUSHI, theme)
        visual = sq.build_visual_brief(brief, text, DUSHI)
        self.assertEqual(visual["headline"], text)
        self.assertEqual(brief["occasion"], {"kind": "evergreen"})
        self.assertEqual(brief["current_date"], "2026-10-09")

    def test_project_art_direction_palette_and_composition_are_preserved(self):
        for project, theme, text, mood in (
            (DUSHI, "Поздравление маме", "Один эпизод из детства", "тёплая премиальная эмоциональная фотография, без дешёвой открытки"),
            (OCEAN, "Авито: первый кадр", "Первый кадр показывает товар", "премиальный технологичный B2B, без случайных роботов и рукопожатий"),
        ):
            with self.subTest(project=project["id"]):
                visual = self.visual(project, theme, text)
                self.assertEqual(visual["mood"], mood)
                self.assertEqual(visual["primary_color"], "глубокий акцентный цвет по теме")
                self.assertEqual(visual["secondary_color"], "нейтральный поддерживающий фон")
                self.assertEqual(visual["composition"], "один главный объект, крупный план, много воздуха")
                self.assertNotIn("premium_contract", visual)

    def test_long_pedicure_heading_never_changes_the_service_to_manicure(self):
        theme = "Зачем нужен педикюр в закрытой обуви"
        text = "Почему регулярный педикюр и аккуратная обработка стоп помогают сохранять комфорт в закрытой обуви осенью"
        visual = self.visual(MANICURE, theme, text)
        self.assertEqual(visual["headline"], "Педикюр для реальной жизни")
        self.assertIn("стопы", visual["main_object"])
        self.assertTrue(sq.headline_before_image(visual, text)["pass"])

    def test_manicure_formats_headline_and_october_season_rules_are_unchanged(self):
        brief = self.brief(MANICURE, "Маникюр для повседневного образа")
        self.assertNotIn("recent_visual_headlines", brief)
        self.assertEqual(len(sq._content_formats(MANICURE)), 12)
        self.assertEqual(brief["season"], "осень-зима")
        self.assertTrue({"море", "пляж", "купание"}.issubset(brief["forbidden_mentions"]))
        text = "Почему привычный маникюр может помогать поддерживать аккуратный повседневный образ без сложных декоративных решений"
        visual = sq.build_visual_brief(brief, text, MANICURE)
        self.assertEqual(visual["headline"], "Маникюр для реальной жизни")
        self.assertEqual(sq.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB, 4.0)

    def test_unknown_project_does_not_inherit_editorial_specific_rules(self):
        project = {"id": "unknown", "name": "Новый проект", "tz_offset": 3}
        brief = self.brief(project, "Обычная услуга")
        self.assertNotIn("recent_visual_headlines", brief)

    def _repair_fixture(self):
        theme = "Поздравление по личной истории"
        heading = "Шаблонное поздравление видно сразу"
        history = [{"project_id": DUSHI["id"], "created_at": "2026-10-08", "visual_brief": {"headline": h}}
                   for h in (theme, heading)]
        body = (
            "\n\nНачните с одного эпизода, который вы пережили вместе. Вспомните конкретный поступок, "
            "привычку или фразу человека: такая деталь помогает объяснить, за что вы его цените."
            "\n\nЗатем свяжите эту историю с пожеланием. Достаточно нескольких спокойных предложений: "
            "их легче произнести вслух, чем длинный список одинаковых красивых слов."
        )
        return theme, history, heading + body, "Один совместный эпизод вместо дежурных пожеланий" + body

    def _actual_generate_post(self, text_callback, image_stage, recovery_text):
        # Execute the accepted runner's actual function, not a reimplementation
        # of its repair loop. IO boundaries are injected and no module top-level
        # bootstrap / DB / provider / transport code is executed.
        source = RUNNER_SOURCE.read_text()
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_post")
        self.assertEqual(hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest(), GENERATE_POST_SHA256)
        namespace = {
            "_openai_text": text_callback,
            "_project_occasion_context": lambda *a, **k: {"kind": "evergreen"},
            "_airify": lambda value: value,
            "_sanitize_public_post_text": lambda value: value,
            "_generate_social_quality_banner": image_stage,
            "_social_provider_text_recovery": lambda *a, **k: recovery_text,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(RUNNER_SOURCE), "exec"), namespace)
        return namespace["generate_post"]

    def _run_actual_generation(self, callback, image_stage):
        theme, history, bad, good = self._repair_fixture()
        brief = self.brief(DUSHI, theme, history)
        generate = self._actual_generate_post(callback, image_stage, bad)
        with patch.object(sq, "recent_project_posts", return_value=history):
            return generate("u2", theme, project_id=DUSHI["id"], project=DUSHI,
                            text_prompt_snapshot="Пиши один конкретный полезный пост.", content_brief=brief)

    def test_existing_writer_repair_loop_fixes_heading_before_image_stage(self):
        _, _, bad, good = self._repair_fixture()
        callback = Mock(side_effect=[bad, good])
        image_stage = Mock(side_effect=lambda **kwargs: kwargs["result"])
        result = self._run_actual_generation(callback, image_stage)
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(callback.call_args_list[1].kwargs["operation"], "social_copy_quality_repair_1")
        self.assertEqual(result["copy_attempts"], 2)
        self.assertEqual(result["text"], good)
        self.assertEqual(result["visual_brief"]["headline"], good.splitlines()[0])
        image_stage.assert_called_once()

    def test_heading_repair_exhaustion_keeps_two_retry_bound_and_buys_no_image(self):
        _, _, bad, _ = self._repair_fixture()
        callback = Mock(side_effect=[bad, bad, bad])
        image_stage = Mock(side_effect=AssertionError("Image stage must not be reached"))
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_COPY_QUALITY_BLOCKED:banner_headline_requires_unique_complete_short_rewrite"):
            self._run_actual_generation(callback, image_stage)
        self.assertEqual(sq.COPY_REGEN_MAX, 2)
        self.assertEqual(callback.call_count, 3)
        self.assertEqual([call.kwargs.get("operation") for call in callback.call_args_list],
                         [None, "social_copy_quality_repair_1", "social_copy_quality_repair_2"])
        image_stage.assert_not_called()

    def test_uncertain_text_outcome_cannot_create_a_paid_heading_repair(self):
        callback = Mock(side_effect=RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:offline-fixture"))
        image_stage = Mock(side_effect=AssertionError("Image stage must not be reached"))
        with self.assertRaisesRegex(RuntimeError, "SOCIAL_COPY_QUALITY_BLOCKED:banner_headline_requires_unique_complete_short_rewrite"):
            self._run_actual_generation(callback, image_stage)
        callback.assert_called_once()
        image_stage.assert_not_called()

    def test_frozen_legacy_brief_without_new_marker_keeps_existing_copy_contract(self):
        theme, history, bad, _ = self._repair_fixture()
        brief = self.brief(DUSHI, theme, history)
        brief.pop("recent_visual_headlines", None)
        original = copy.deepcopy(brief)
        verdict = sq.evaluate_copy(bad, brief, DUSHI, recent_posts=history, now=NOW)
        self.assertTrue(verdict["pass"])
        self.assertEqual(brief, original)


if __name__ == "__main__":
    unittest.main()
