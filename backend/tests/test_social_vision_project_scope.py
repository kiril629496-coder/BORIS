"""Project-scoped Vision contract; captured prompt and strict verdict, no IO."""
import copy
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.services import social_quality as sq
from app.services.social_editorial_contract import occasion_blockers


OCEAN = {"id": "a374ced25745", "name": "Океан Клиентов и Борис", "tz_offset": 3}
DUSHI = {"id": "f938caa3a944", "name": "От Души", "tz_offset": 3}
MANICURE = {"id": "270dbf472f7f", "name": "Маникюр Евпатория", "tz_offset": 3}
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


class VisionProjectScopeTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(sq, "SessionLocal", side_effect=AssertionError("DB access is forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        feedback = patch.object(sq, "_feedback_preferences", return_value={"likes": 0, "dislikes": {}, "recent": []})
        feedback.start()
        self.addCleanup(feedback.stop)

    def brief(self, project, theme):
        return sq.build_content_brief("u2", project, theme, recent_posts=[], now=NOW)

    def captured(self, project, theme, *, final_text="Один конкретный совет без календарного повода.",
                 verdict=None, reference=False, premium=False):
        brief = self.brief(project, theme)
        visual = sq.build_visual_brief(brief, "Одна конкретная мысль", project)
        if premium:
            visual["premium_contract"] = {"exact_title": visual["headline"]}
        raw = {key: True for key in ("pass", *sq.SOCIAL_VISION_REQUIRED_FLAGS, *sq.NATIVE_PREMIUM_REQUIRED_FLAGS)}
        raw["issues"] = []
        if verdict:
            raw.update(verdict)
        provider = Mock(return_value=(raw, {"cost_rub": 0.3949, "provider_request_id": "unit-vision-request"}))
        media = types.ModuleType("app.services.media_semantic_validator")
        media._vision_chat_json = provider
        ref = ({"required": True, "ok": True, "niche": "manicure", "path": "/unit/owner-reference.png", "sha256": "a" * 64}
               if reference else {"required": False, "ok": True})
        with patch.dict(sys.modules, {"app.services.media_semantic_validator": media}), \
             patch.object(sq, "vision_reference_context", return_value=ref), \
             patch.object(sq, "_image_data_uri", return_value="data:image/png;base64,AA=="):
            result = sq.validate_banner_vision("u2", "/unit/candidate.png", brief, visual, final_text, project)
        provider.assert_called_once()
        args = provider.call_args.args
        self.assertEqual(args[1], "social_banner_vision_qa_v1")
        return result, args[2][0]["text"], args[2], brief, visual

    def test_ocean_has_no_implicit_nail_palette_or_prop_ban(self):
        result, prompt, _, _, _ = self.captured(OCEAN, "Авито: первый кадр объявления")
        self.assertNotIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)
        self.assertNotIn("muddy brown/burgundy/beige salon palette", prompt)
        self.assertNotIn("wine-like props", prompt)
        self.assertIn("this project's own mood/style", prompt)
        self.assertEqual(result["vision_qa_contract"]["profile"], "ocean_business")

    def test_dushi_keeps_its_own_emotional_style(self):
        result, prompt, _, _, visual = self.captured(DUSHI, "Поздравление коллеге")
        self.assertNotIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)
        self.assertNotIn("muddy brown/burgundy/beige salon palette", prompt)
        self.assertIn("эмоциональная", visual["mood"])
        self.assertEqual(result["vision_qa_contract"]["profile"], "dushi_personal")

    def test_nail_reference_keeps_all_premium_and_anatomy_requirements(self):
        result, prompt, images, _, _ = self.captured(MANICURE, "Педикюр осенью", reference=True, premium=True)
        self.assertIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)
        self.assertIn("muddy brown/burgundy/beige salon palette", prompt)
        self.assertIn("hands vs feet", prompt)
        self.assertIn("partially hidden toes are acceptable", prompt)
        self.assertEqual(len([item for item in images if item["type"] == "image_url"]), 2)
        self.assertTrue(result["reference_style_match"])
        self.assertTrue(result["premium_verdict"]["passed"])

    def test_explicit_reference_or_native_premium_contract_keeps_strict_rules(self):
        for kwargs in ({"reference": True}, {"premium": True}):
            with self.subTest(kwargs=kwargs):
                _, prompt, _, _, _ = self.captured(OCEAN, "Авито: первый кадр", **kwargs)
                self.assertIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)

    def test_theme_alone_cannot_switch_ocean_to_nail_profile(self):
        result, prompt, _, _, _ = self.captured(OCEAN, "Как рекламировать маникюр через Авито")
        self.assertEqual(result["vision_qa_contract"]["profile"], "ocean_business")
        self.assertNotIn("NAIL/REFERENCE PREMIUM STYLE RULES", prompt)

    def test_phone_number_caption_is_separated_from_image_disclosure_rule(self):
        _, prompt, _, _, _ = self.captured(OCEAN, "Аналитика звонков", final_text="Совет о звонках. Контакт: 8 000 000-00-00.")
        self.assertIn("forbidden_text applies only to text rendered inside the image", prompt)
        self.assertIn("do not extend image-only text prohibitions to a phone number/contact in the post caption", prompt)
        self.assertIn("existing project public-copy policy", prompt)
        self.assertIn("does not grant a new permission", prompt)
        self.assertIn("8 000 000-00-00", prompt)

    def test_topic_relevant_smartphone_is_not_a_forbidden_phone_object(self):
        _, prompt, _, _, visual = self.captured(OCEAN, "Почему входящий звонок не должен теряться")
        self.assertIn("смартфон", visual["main_object"])
        self.assertNotIn("телефоны", visual["forbidden_objects"])
        self.assertNotIn("цены", visual["forbidden_objects"])
        self.assertIn("телефон", visual["forbidden_text"])
        self.assertIn("цена", visual["forbidden_text"])
        self.assertIn("explicitly required by the exact topic and main_object", prompt)
        self.assertIn("unrelated device still fails semantic/object checks", prompt)
        self.assertIn("not a ban on a topic-relevant headline about calls or pricing", prompt)

    def test_actual_image_phone_number_or_wrong_scene_still_fails_strictly(self):
        for fields in ({"pass": False, "forbidden_ok": False},
                       {"pass": False, "semantic_match": False, "service_ok": False, "object_ok": False}):
            with self.subTest(fields=fields):
                result, _, _, _, _ = self.captured(OCEAN, "Аналитика звонков", verdict=fields)
                self.assertFalse(result["pass"])
                self.assertIn("vision_qa_failed:pass", result["issues"])
                self.assertEqual(result["qa_cost_rub"], 0.3949)

    def test_evergreen_business_has_no_invented_holiday_requirement(self):
        _, prompt, _, brief, _ = self.captured(OCEAN, "Как выбрать первый кадр объявления")
        self.assertEqual(brief["occasion"], {"kind": "evergreen"})
        self.assertIn("evergreen is valid without any holiday or calendar event", prompt)
        self.assertIn("Evergreen does not assert that an event happens today", prompt)
        self.assertIn("never present a future event as today", prompt)
        self.assertEqual(occasion_blockers({"kind": "dated", "date": "2026-10-08", "source": "approved"}, "2026-10-09"), ["occasion_expired"])

    def test_marketing_footer_does_not_replace_core_semantic_check(self):
        _, prompt, _, _, _ = self.captured(OCEAN, "Авито: первый кадр объявления")
        self.assertIn("selected_theme/main_thesis", prompt)
        self.assertIn("does not require illustrating every listed service", prompt)
        self.assertIn("main scene must still match the core post idea", prompt)

    def test_same_required_boolean_flags_fail_closed_without_coercion(self):
        for values in ({"pass": "true"}, {"occasion_ok": "true"}, {"service_ok": None}):
            with self.subTest(values=values):
                result, _, _, _, _ = self.captured(OCEAN, "Аналитика звонков", verdict=values)
                self.assertFalse(result["pass"])
                self.assertTrue(any(issue.startswith("vision_qa_invalid_boolean:") for issue in result["issues"]))

    def test_current_project_contract_replaces_stale_profile_only_in_refresh_copy(self):
        original = self.brief(OCEAN, "Авито: первый кадр")
        original["vision_qa_contract"] = {"version": "old", "profile": "nail_premium"}
        before = copy.deepcopy(original)
        refreshed = sq.refresh_temporal_brief(original, OCEAN, now=NOW)
        self.assertEqual(refreshed["vision_qa_contract"], {"version": "SOCIAL_VISION_PROJECT_SCOPE_V2", "profile": "ocean_business"})
        self.assertEqual(original, before)
        self.assertEqual(refreshed["selected_theme"], original["selected_theme"])
        self.assertEqual(refreshed["occasion"], original["occasion"])

    def test_prompt_and_verdict_carry_stable_contract_revision_not_release_or_time(self):
        result, prompt, _, brief, _ = self.captured(OCEAN, "Аналитика звонков")
        contract = {"version": "SOCIAL_VISION_PROJECT_SCOPE_V2", "profile": "ocean_business"}
        self.assertEqual(result["vision_qa_contract"], contract)
        self.assertEqual(brief["vision_qa_contract"], contract)
        self.assertIn("VISION QA CONTRACT", prompt)
        self.assertIn("SOCIAL_VISION_PROJECT_SCOPE_V2", prompt)
        self.assertEqual(set(contract), {"version", "profile"})


if __name__ == "__main__":
    unittest.main()
