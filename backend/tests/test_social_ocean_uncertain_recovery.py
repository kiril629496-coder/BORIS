from datetime import datetime
import unittest
from unittest.mock import patch
import posting_runner as pr


class OceanUncertainRecoveryTest(unittest.TestCase):
    def test_ocean_fallback_is_clean_and_no_url_hashtag(self):
        text=pr._ocean_uncertain_text_fallback(
            "анализ конкурентов: на что смотреть в чужих объявлениях"
        )
        self.assertIn("Конкурентов",text)
        self.assertIn("BORIS",text)
        self.assertNotIn("http",text.lower())
        self.assertNotIn("www.",text.lower())
        self.assertNotIn("#",text)

    def test_scheduled_deadline_uses_slot_not_recovery_created_at(self):
        rec={
            "generation_key":"social-slot:u2:a374ced25745:20260905_09",
            "created_at":"2026-09-05 12:55",
        }
        proj={"mode":"moderate","auto_publish_hours":3}
        got=pr._scheduled_record_delivery_due_local(rec,proj)
        self.assertEqual(got,datetime(2026,9,5,12,0))

    def test_manual_draft_has_no_scheduled_deadline(self):
        self.assertIsNone(pr._scheduled_record_delivery_due_local(
            {"generation_key":"","created_at":"2026-09-05 12:55"},
            {"mode":"moderate","auto_publish_hours":3},
        ))

    def test_generate_post_uses_fallback_only_for_ocean_uncertain(self):
        with patch.object(
            pr,"_openai_text",
            side_effect=RuntimeError(
                "CAMPAIGN_AI_OUTCOME_UNCERTAIN:campaign_transport_outcome_unknown"
            ),
        ), patch.object(
            pr,"_reuse_existing_ocean_social_banner",
            return_value="/root/BORIS/backend/banners/qa.png",
        ), patch("os.path.getsize",return_value=100000):
            out=pr.generate_post(
                "u2","анализ конкурентов: на что смотреть в чужих объявлениях",
                image_source="reuse",project_id="a374ced25745",
            )
        self.assertEqual(out["text_engine"],"deterministic_uncertain_fallback")
        self.assertTrue(out["text"])
        self.assertEqual(out["banner_engine"],"reuse_existing_social")

    def test_dushi_uncertain_uses_provider_free_text_and_existing_banner(self):
        with patch.object(
            pr,"_openai_text",
            side_effect=RuntimeError(
                "CAMPAIGN_AI_OUTCOME_UNCERTAIN:campaign_transport_outcome_unknown"
            ),
        ), patch.object(
            pr,"_social_reuse_published_banner",
            return_value={"path":"/tmp/dushi-existing.png","source_record_id":"published-1"},
        ):
            out=pr.generate_post(
                "u2","слова для прощания, когда трудно говорить",
                image_source="ai",project_id="f938caa3a944",
            )
        self.assertEqual(out["text_engine"],"deterministic_uncertain_fallback")
        self.assertEqual(out["banner_engine"],"reuse_published_project_asset")
        self.assertEqual(out["banner_recovery_source_record_id"],"published-1")

    def test_manicure_uncertain_uses_provider_free_text_and_existing_banner(self):
        with patch.object(
            pr,"_openai_text",
            side_effect=RuntimeError(
                "CAMPAIGN_AI_OUTCOME_UNCERTAIN:campaign_transport_outcome_unknown"
            ),
        ), patch.object(
            pr,"_social_reuse_published_banner",
            return_value={"path":"/tmp/manicure-existing.png","source_record_id":"published-2"},
        ):
            out=pr.generate_post(
                "u2","кошачий глаз. Услуга: уход за стопами.",
                image_source="ai",project_id="270dbf472f7f",
            )
        self.assertEqual(out["text_engine"],"deterministic_uncertain_fallback")
        self.assertEqual(out["banner_engine"],"reuse_published_project_asset")

    def test_unknown_project_uncertain_remains_fail_closed(self):
        with patch.object(
            pr,"_openai_text",
            side_effect=RuntimeError(
                "CAMPAIGN_AI_OUTCOME_UNCERTAIN:campaign_transport_outcome_unknown"
            ),
        ):
            with self.assertRaises(RuntimeError):
                pr.generate_post("u2","тема",image_source="ai",project_id="other")


if __name__=="__main__":
    unittest.main()
