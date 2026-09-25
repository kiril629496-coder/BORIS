# -*- coding: utf-8 -*-
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import posting_runner as pr
import boris_background_worker as bw
from app.api import posting as posting_api
from app.services import social_owner_policy_v3 as owner
from app.services import social_public_contract as legacy_contract
from app.services import social_public_copy_guard as legacy_guard


class StableSocialRuntimeGuardTest(unittest.TestCase):
    def test_owner_boundary_is_clean(self):
        health = owner.public_copy_runtime_boundary_health()
        self.assertTrue(health.get("ok"), health)
        self.assertTrue(health.get("ocean_ok"), health)
        self.assertTrue(health.get("dushi_ok"), health)
        self.assertIn("Проверочная клиентская подпись BORIS.", health.get("ocean_preview") or "")
        full=owner.render_public_text(
            "<b>Проверка</b>\n\nАвито и продажи.",
            {"name":"Океан Клиентов и Борис"},"tg"
        )
        self.assertIn("#BORIS", full)

    def test_legacy_modules_delegate_to_owner_v3(self):
        self.assertTrue(getattr(legacy_contract, "OWNER_POLICY_COMPAT_SHIM", False))
        self.assertTrue(getattr(legacy_guard, "OWNER_POLICY_COMPAT_SHIM", False))
        raw = (
            "<b>Полезная мысль</b>\n\nАвито надо измерять по заявкам.\n"
            "ТОН: разговорный\n"
            "🤖 BORIS: AI-Авитолог — объявления; AI-РОП — контроль продаж.\n"
            "🎥 BORIS в работе: https://youtu.be/example\n"
            "www.example.ru\n"
            "#BORIS #авито #автоматизация"
        )
        project = {"name": "Океан Клиентов и Борис", "footer_signature": "Если хотите проверить BORIS на своей задаче — настроим тест бесплатно. MAX / WhatsApp: 8 981 967-37-87."}
        expected = owner.render_public_text(raw, project, "tg")
        self.assertEqual(legacy_contract.render_public_text(raw, project, "tg"), expected)
        self.assertEqual(legacy_guard.render_public_text(raw, project, "tg"), expected)
        self.assertIn("#BORIS", expected)
        self.assertIn("Если хотите проверить BORIS на своей задаче — настроим тест бесплатно. MAX / WhatsApp: 8 981 967-37-87.", expected)
        self.assertNotIn("AI-РОП", expected)
        self.assertNotIn("youtu", expected.lower())
        self.assertNotIn("www.", expected.lower())

    def test_guard_removes_forced_ecosystem_and_occasion(self):
        source = inspect.getsource(owner.install_posting_runner_guard)
        self.assertNotIn("canonical_ocean_ecosystem", source)
        self.assertNotIn("виртуальная команда маркетинга и продаж", source)
        state = owner.install_posting_runner_guard(pr)
        self.assertTrue(state.get("delivery_guard"), state)
        self.assertTrue(state.get("occasion_guard"), state)
        self.assertTrue(state.get("plan_guard"), state)
        self.assertEqual(pr._project_occasion({"name": "От Души"}), "")

    def test_vk_cleanup_is_newest_first_across_projects(self):
        older_ocean = (
            "2026-08-29 06:01", 7306, "u2", "old", -121, "old",
            "Океан Клиентов и Борис",
        )
        newer_dushi = (
            "2026-08-30 06:02", 93, "u2", "new", -239, "new",
            "От Души",
        )
        ranked = sorted(
            [older_ocean, newer_dushi],
            key=owner._vk_pending_sort_key,
            reverse=True,
        )
        self.assertEqual(ranked[0][1], 93)
        self.assertEqual(ranked[0][6], "От Души")

    def test_terminal_vk_edit_uses_canonical_runner_self_heal(self):
        health_source = inspect.getsource(owner.stable_social_vk_external_unavailable_health)
        cleanup_source = inspect.getsource(owner.stable_social_vk_pending_cleanup_once)
        self.assertIn("return runner.social_vk_external_unavailable_health()", health_source)
        self.assertIn("return runner.social_vk_pending_cleanup_once()", cleanup_source)

    def test_production_consumers_use_owner_v3(self):
        api_source = inspect.getsource(posting_api)
        worker_source = inspect.getsource(bw)
        runner_source = inspect.getsource(pr)
        for source in (api_source, worker_source, runner_source):
            self.assertIn("social_owner_policy_v3", source)
        self.assertNotIn("from app.services.social_public_contract import", api_source)
        self.assertNotIn("from app.services.social_public_contract import", worker_source)

    def test_source_and_consumer_watchdogs_are_green(self):
        # Use the canonical preflight module directly. The worker module may be
        # imported earlier in this test process and therefore legitimately hold
        # a pre-repair function object even after preflight rewrites its source.
        from app.services import social_owner_preflight as preflight
        source = preflight.ensure_owner_policy_source()
        consumers = preflight.ensure_social_consumers()
        overall = preflight.run()
        self.assertTrue(source.get("ok"), source)
        self.assertTrue(consumers.get("ok"), consumers)
        self.assertTrue(overall.get("ok"), overall)
        self.assertEqual(consumers.get("drift"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
