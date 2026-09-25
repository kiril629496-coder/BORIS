import json
import uuid
import unittest
from unittest.mock import patch
from pathlib import Path

import posting_runner as pr
from app.models.storage import Storage


class SocialScheduledPromptSnapshotTest(unittest.TestCase):
    def _project(self,pid="p1",name="QA"):
        return {
            "id":pid,"name":name,
            "themes":["тема один"],
            "text_prompt":"СТИЛЬ QA",
            "times":["9","18"],"tz_offset":3,
            "mode":"moderate","auto_publish_hours":3,
        }

    def test_existing_plan_without_snapshot_is_upgraded_deterministically(self):
        acc="qa_plan_"+uuid.uuid4().hex[:10]
        p=self._project()
        key="posting_generation_plan:p1:20260905_09"
        old={
            "generation_key":"social-slot:%s:p1:20260905_09"%acc,
            "project_id":"p1","slot":"20260905_09",
            "theme":"тема один","occasion":"",
            "created_at":"2026-09-05T06:00:00",
        }
        s=pr.SessionLocal()
        try:
            s.add(Storage(account_id=acc,key=key,value=json.dumps(old,ensure_ascii=False)))
            s.commit()
        finally:s.close()
        try:
            with patch.object(pr,"datetime") as dt:
                dt.now.return_value=__import__("datetime").datetime(2026,9,5,9,5)
                dt.side_effect=lambda *a,**k: __import__("datetime").datetime(*a,**k)
                g,plan=pr._scheduled_generation_plan(acc,p,slot_hour=9)
            self.assertTrue(plan.get("text_prompt_snapshot"))
            self.assertIn("СТИЛЬ QA",plan["text_prompt_snapshot"])
            self.assertNotIn("Вот примеры уже опубликованных",plan["text_prompt_snapshot"])
            s=pr.SessionLocal()
            try:
                row=s.query(Storage).filter(Storage.account_id==acc,Storage.key==key).first()
                saved=json.loads(row.value)
            finally:s.close()
            self.assertEqual(saved["text_prompt_snapshot"],plan["text_prompt_snapshot"])
        finally:
            s=pr.SessionLocal()
            try:
                s.query(Storage).filter(Storage.account_id==acc).delete(synchronize_session=False)
                s.commit()
            finally:s.close()

    def test_generate_post_snapshot_skips_live_channel_examples(self):
        snapshot=pr._build_text_prompt("тема","СТИЛЬ","",examples=None)
        with patch.object(pr,"_fetch_channel_posts") as fetch,              patch.object(pr,"_best_examples") as best,              patch.object(pr,"_openai_text",return_value="<b>Тест</b>\n\nТекст"),              patch.object(pr,"_reuse_existing_ocean_social_banner",return_value="/tmp/x.png"),              patch("os.path.getsize",return_value=100000):
            out=pr.generate_post(
                "u2","тема",channel="@x",project_id="a374ced25745",
                image_source="reuse",text_prompt_snapshot=snapshot,
            )
        fetch.assert_not_called()
        best.assert_not_called()
        self.assertEqual(out["text"],"<b>Тест</b>\n\nТекст")


class U42UncertainRecoveryTest(unittest.TestCase):
    def test_u42_fallback_copy_has_no_prices_dates_urls_or_hashtags(self):
        text=pr._u42_uncertain_text_fallback(
            "раздевалка и комната отдыха для рабочих"
        )
        low=text.lower()
        self.assertIn("бригады",low)
        for bad in ("http","www.","#","₽","руб"):
            self.assertNotIn(bad,low)

    def test_live_u42_recovery_banner_is_valid_and_related(self):
        got=pr._u42_uncertain_reuse_banner(
            "u42","688556520514",
            "раздевалка и комната отдыха для рабочих",
        )
        self.assertTrue(Path(got["path"]).is_file())
        self.assertGreater(Path(got["path"]).stat().st_size,50000)
        self.assertTrue(got["source_record_id"])

    def test_u42_uncertain_uses_no_provider_banner_reuse(self):
        with patch.object(
            pr,"_openai_text",
            side_effect=RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:provider_outcome_unknown"),
        ), patch.object(
            pr,"_u42_uncertain_reuse_banner",
            return_value={"path":"/tmp/u42.png","source_record_id":"r1","score":6},
        ), patch("os.path.isfile",return_value=True), patch("os.path.getsize",return_value=100000):
            out=pr.generate_post(
                "u42","раздевалка и комната отдыха для рабочих",
                project_id="688556520514",image_source="ai",
                text_prompt_snapshot="frozen prompt",
            )
        self.assertEqual(out["text_engine"],"deterministic_uncertain_fallback")
        self.assertEqual(out["banner_engine"],"reuse_published_project_asset")
        self.assertEqual(out["banner_recovery_source_record_id"],"r1")


if __name__=="__main__":
    unittest.main()
