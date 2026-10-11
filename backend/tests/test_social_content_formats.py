"""Project-specific content formats; no model, image, database or transport calls."""
import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services import social_quality as sq


OCEAN = {"id": "a374ced25745", "name": "Океан Клиентов и Борис", "tz_offset": 3}
DUSHI = {"id": "f938caa3a944", "name": "От Души", "tz_offset": 3}
MANICURE = {"id": "270dbf472f7f", "name": "Маникюр Евпатория", "tz_offset": 3}
UNKNOWN = {"id": "other", "name": "Новый проект", "tz_offset": 3}
BEAUTY = {"разбор конкретного дизайна", "практический уход"}
NEUTRAL = {
    "полезный совет", "разбор ошибки", "сравнение двух вариантов", "миф и факт",
    "мини-гайд", "вопрос клиента", "экспертное наблюдение", "лёгкий юмор",
    "образовательный пост", "продающий пост",
}


def brief(project, theme, day=9, recent=None):
    with patch.object(sq, "_feedback_preferences", return_value={"likes": 0, "dislikes": {}, "recent": []}):
        return sq.build_content_brief(
            "u2", project, theme, recent_posts=[] if recent is None else recent,
            now=datetime(2026, 10, day, 12, tzinfo=timezone.utc),
        )


class ContentFormatTests(unittest.TestCase):
    def test_ocean_real_old_care_seed_is_now_business_format_in_prompt(self):
        result = brief(OCEAN, "CRM: как не потерять обращение", day=2)
        self.assertEqual(result["format"], "разбор рабочего процесса")
        prompt = sq.content_brief_prompt(result)
        self.assertIn("Формат: разбор рабочего процесса", prompt)
        self.assertNotIn("практический уход", prompt)

    def test_dushi_real_old_care_seed_is_now_personal_story_format_in_prompt(self):
        result = brief(DUSHI, "Шаблонное поздравление видно с первой строчки", day=6)
        self.assertEqual(result["format"], "подбор слов по личной истории")
        prompt = sq.content_brief_prompt(result)
        self.assertIn("Формат: подбор слов по личной истории", prompt)
        self.assertNotIn("практический уход", prompt)

    def test_project_pools_have_only_their_own_specialized_formats(self):
        self.assertEqual(set(sq._content_formats(OCEAN)), NEUTRAL | {"разбор бизнес-задачи", "разбор рабочего процесса"})
        self.assertEqual(set(sq._content_formats(DUSHI)), NEUTRAL | {"разбор поздравления", "подбор слов по личной истории"})
        self.assertEqual(set(sq._content_formats(UNKNOWN)), NEUTRAL)

    def test_unknown_topics_and_prompts_cannot_switch_to_manicure_pool(self):
        for project in ({}, UNKNOWN, {**UNKNOWN, "text_prompt": "маникюр", "niche": "педикюр", "themes": ["уход за ногтями"]}):
            with self.subTest(project=project):
                self.assertEqual(set(sq._content_formats(project)), NEUTRAL)
                self.assertNotIn(brief(project, "практический уход и разбор дизайна")["format"], BEAUTY)

    def test_manicure_keeps_exact_original_order_and_identity_classifier(self):
        original = [
            "полезный совет", "разбор ошибки", "сравнение двух вариантов", "миф и факт",
            "разбор конкретного дизайна", "мини-гайд", "вопрос клиента", "практический уход",
            "экспертное наблюдение", "лёгкий юмор", "образовательный пост", "продающий пост",
        ]
        self.assertEqual(sq._content_formats(MANICURE), original)
        self.assertEqual(sq._content_formats({"id": "270dbf472f7f", "name": "Новое название"}), original)
        self.assertEqual(sq._content_formats({"id": "new", "name": "Маникюр Евпатория"}), original)

    def test_rotation_avoids_two_latest_nonempty_formats_for_every_pool(self):
        for project in (OCEAN, DUSHI, MANICURE, UNKNOWN):
            with self.subTest(project=project["id"]):
                first = brief(project, "Одна конкретная тема")["format"]
                history = [{"content_brief": {"format": first}}]
                second = brief(project, "Одна конкретная тема", recent=history)["format"]
                self.assertNotEqual(first, second)
                history = [{"content_brief": {"format": ""}}, {"content_brief": {"format": second}}] + history
                third = brief(project, "Одна конкретная тема", recent=history)["format"]
                self.assertNotIn(third, (first, second))
                self.assertEqual(third, brief(project, "Одна конкретная тема", recent=history)["format"])

    def test_legacy_care_history_is_read_only_and_does_not_restore_foreign_pool(self):
        history = [
            {"id": "legacy", "text": "Сохранённый текст", "ids": {"tg": {"message_id": 174}},
             "content_brief": {"format": "практический уход", "current_date": "2026-10-08"}},
            {"id": "legacy2", "content_brief": {"format": "разбор конкретного дизайна"}},
        ]
        before = copy.deepcopy(history)
        for project in (OCEAN, DUSHI, UNKNOWN):
            self.assertNotIn(brief(project, "Одна конкретная тема", recent=history)["format"], BEAUTY)
        self.assertEqual(history, before)

    def test_rotation_only_excludes_latest_two_formats_not_all_history(self):
        history = [{"content_brief": {"format": value}} for value in sq._content_formats(MANICURE)]
        shortened = history[:2]
        for seed in ("same-day-a", "same-day-b", "next-day"):
            self.assertEqual(
                sq._least_recent_format(history, seed, project=MANICURE),
                sq._least_recent_format(shortened, seed, project=MANICURE),
            )

    def test_unclassified_boris_or_development_uses_safe_neutral_fallback(self):
        for name in ("BORIS", "Разработка программного обеспечения"):
            project = {**UNKNOWN, "name": name}
            self.assertEqual(set(sq._content_formats(project)), NEUTRAL)
            self.assertNotIn(brief(project, "Настройка автоматизации")["format"], BEAUTY)


if __name__ == "__main__":
    unittest.main()
