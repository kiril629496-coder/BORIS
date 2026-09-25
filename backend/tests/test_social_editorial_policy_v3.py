import importlib.machinery
import re
import types
import unittest
from unittest.mock import patch


POLICY_PATH = "/root/BORIS/backend/app/services/social_owner_policy_v3.py"


def load_policy():
    mod = types.ModuleType("social_owner_policy_snapshot_test")
    importlib.machinery.SourceFileLoader(mod.__name__, POLICY_PATH).exec_module(mod)
    return mod


class SocialEditorialPolicyV3Tests(unittest.TestCase):
    def test_ocean_keeps_model_tags_and_strips_video(self):
        m = load_policy()
        raw = (
            "Практический совет про объявления и лиды.\n"
            "Видео BORIS: https://example.invalid/video\n"
            "#авито #лиды #продажи #авитолог #объявления #бизнес"
        )
        signature = "Клиентская подпись Ocean для теста."
        out = m.render_public_text(
            raw,
            {"name": "Океан Клиентов и Борис", "footer_signature": signature},
            "tg",
        )
        tags = re.findall(r"#[A-Za-zА-Яа-яЁё0-9_]+", out)
        self.assertEqual(
            tags[-6:],
            ["#авито", "#лиды", "#продажи", "#авитолог", "#объявления", "#бизнес"],
        )
        self.assertIn(signature, out)
        self.assertNotIn("Напишите в MAX или WhatsApp:", out)
        self.assertIn("🤖 BORIS: https://boris-ai.pro/?utm_source=tg&utm_medium=ocean&utm_campaign=boris", out)
        self.assertIn("💻 Разработка ПО: https://boris-ai.pro/software-dev/?utm_source=tg&utm_medium=ocean&utm_campaign=dev", out)
        self.assertEqual(out.count("utm_campaign=boris"), 1)
        self.assertEqual(out.count("utm_campaign=dev"), 1)
        self.assertNotIn("example.invalid", out)
        self.assertNotIn("Видео BORIS", out)

    def test_dushi_has_contact_and_model_tags(self):
        m = load_policy()
        raw = (
            "Тёплый свадебный тост.\n"
            "#свадьба #тост #молодожены #подарок #поздравление #отдуши"
        )
        out = m.render_public_text(
            raw,
            {"name": "От Души", "contact_tg": "@owner"},
            "tg",
        )
        self.assertIn("@owner", out)
        self.assertIn("#молодожены", out)
        self.assertGreaterEqual(
            len(re.findall(r"#[A-Za-zА-Яа-яЁё0-9_]+", out)),
            5,
        )

    def test_manicure_preserves_useful_text_but_removes_contact_and_forced_holiday(self):
        m = load_policy()
        raw = (
            "Совет мастера про голубой френч. "
            "Ко Дню нефтяника сделаем дизайн. "
            "Записаться по телефону: +7 000 000-00-00\n"
            "https://example.invalid @master\n"
            "#маникюр #евпатория #ногти #френч #голубой #крым"
        )
        out = m.render_public_text(
            raw,
            {"id": "270dbf472f7f", "name": "Маникюр Евпатория"},
            "vk",
        )
        self.assertIn("Совет мастера про голубой френч.", out)
        self.assertNotIn("нефт", out.lower())
        self.assertNotIn("example.invalid", out)
        self.assertNotIn("@master", out)
        self.assertNotIn("000-00-00", out)
        self.assertEqual(
            len(re.findall(r"#[A-Za-zА-Яа-яЁё0-9_]+", out)),
            6,
        )

    def test_ocean_last_mile_is_idempotent(self):
        m = load_policy()
        signature = (
            "Если хотите проверить BORIS на своей задаче — настроим тест бесплатно. "
            "MAX / WhatsApp: 8 981 967-37-87."
        )
        project = {
            "name": "Океан Клиентов и Борис",
            "footer_signature": signature,
        }
        raw = (
            "Полезный пост про аналитику.\n"
            "#аналитика #бизнес #продажи #лиды #авито #борис"
        )
        once = m.render_public_text(raw, project, "vk")
        twice = m.render_public_text(once, project, "vk")
        self.assertEqual(once, twice)
        self.assertEqual(once.count(signature), 1)
        self.assertEqual(
            len(re.findall(r"#[A-Za-zА-Яа-яЁё0-9_]+", once)),
            6,
        )

    def test_dushi_last_mile_is_idempotent_per_platform(self):
        m = load_policy()
        raw = (
            "Тёплый текст для годовщины.\n"
            "#годовщина #поздравление #отдуши #подарок #стихиназаказ #трогательно"
        )
        cases = (
            ("tg", {"name": "От Души", "contact_tg": "@kirill86oko"}, "@kirill86oko"),
            (
                "vk",
                {
                    "name": "От Души",
                    "contact_vk": "https://vk.ru/goldenjack007",
                },
                "goldenjack007",
            ),
        )
        for platform, project, marker in cases:
            once = m.render_public_text(raw, project, platform)
            twice = m.render_public_text(once, project, platform)
            self.assertEqual(once, twice)
            self.assertEqual(once.count(marker), 1)
            self.assertEqual(
                len(re.findall(r"#[A-Za-zА-Яа-яЁё0-9_]+", once)),
                6,
            )

    def test_manicure_last_mile_is_idempotent(self):
        m = load_policy()
        project = {
            "id": "270dbf472f7f",
            "name": "Маникюр Евпатория",
        }
        raw = (
            "Совет мастера про френч. Ко Дню нефтяника сделаем дизайн. "
            "Телефон: +7 000 000-00-00\n"
            "https://example.invalid @master\n"
            "#маникюр #евпатория #ногти #френч #крым #гельлак"
        )
        once = m.render_public_text(raw, project, "vk")
        twice = m.render_public_text(once, project, "vk")
        self.assertEqual(once, twice)
        self.assertNotIn("день нефт", once.lower())
        self.assertNotIn("example.invalid", once)
        self.assertNotIn("@master", once)
        self.assertNotIn("000-00-00", once)
        self.assertEqual(
            len(re.findall(r"#[A-Za-zА-Яа-яЁё0-9_]+", once)),
            6,
        )

    def test_guard_refreshes_stale_prompt_snapshot(self):
        m = load_policy()

        class Runner:
            pass

        runner = Runner()
        runner._project_delivery_text = (
            lambda text, project, platform="tg": text
        )
        runner._project_occasion = lambda project: "День нефтяника"
        runner._posting_generation_exists = (
            lambda account_id, generation_key: False
        )

        def original_plan(account_id, project, slot_hour=None):
            return "social-slot:test", {
                "slot": "20260905_09",
                "theme": "френч-педикюр",
                "occasion": "День нефтяника",
                "text_prompt_snapshot": "STALE",
            }

        runner._scheduled_generation_plan = original_plan

        def build_prompt(
            theme,
            client_prompt="",
            occasion="",
            examples=None,
            allow_hashtags=False,
        ):
            return (
                "FRESH|"
                + theme
                + "|"
                + client_prompt
                + "|"
                + occasion
                + "|tags="
                + str(bool(allow_hashtags))
            )

        runner._build_text_prompt = build_prompt
        persisted = []

        project = {
            "id": "270dbf472f7f",
            "name": "Маникюр Евпатория",
            "text_prompt": "Новый редакционный промт без календаря",
            "allow_hashtags": True,
            "themes": [],
        }

        with patch.object(
            m,
            "_recent_plan_themes",
            return_value=[],
        ), patch.object(
            m,
            "_persist_generation_plan",
            side_effect=lambda runner, account_id, project_id, plan: (
                persisted.append(dict(plan)) or True
            ),
        ):
            installed = m.install_posting_runner_guard(runner)
            key, plan = runner._scheduled_generation_plan(
                "u2",
                project,
                slot_hour=9,
            )

        self.assertTrue(installed["plan_guard"])
        self.assertEqual(key, "social-slot:test")
        self.assertEqual(plan["occasion"], "")
        self.assertTrue(
            plan["text_prompt_snapshot"].startswith(
                "FRESH|френч-педикюр|Новый редакционный промт"
            )
        )
        self.assertTrue(plan["prompt_snapshot_refreshed"])
        self.assertEqual(len(persisted), 1)
        self.assertEqual(
            persisted[0]["text_prompt_snapshot"],
            plan["text_prompt_snapshot"],
        )


if __name__ == "__main__":
    unittest.main()
