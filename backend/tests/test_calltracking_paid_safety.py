# -*- coding: utf-8 -*-
import pathlib
import unittest
from unittest.mock import patch

from app.api import calltracking as C


class CalltrackingReportProviderSafetyTests(unittest.TestCase):
    def test_report_recommendations_reuse_same_business_intent(self):
        calls=[]
        def fake(**kwargs):
            calls.append(kwargs)
            return {
                "text":'{"overall":["ok"],"by_manager":[]}',
                "json":{"overall":["ok"],"by_manager":[]},
                "provider":"gemini",
                "model":"gemini-test",
                "usage":{},
                "cost_rub":0.0,
            }
        managers=[{"phone":"masked","total":3,"answered":2,"missed":1,"minutes":4.5}]
        totals={"total":3,"answered":2,"missed":1,"minutes":4.5}
        with patch("app.services.sales_ai_router.generate_text",side_effect=fake):
            a=C._report_recommendations("acc-safe",managers,totals)
            b=C._report_recommendations("acc-safe",managers,totals)
        self.assertEqual(a["overall"],["ok"])
        self.assertEqual(b["overall"],["ok"])
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]["idempotency_key"],calls[1]["idempotency_key"])
        self.assertTrue(calls[0]["idempotency_key"].startswith("call-report-recs:"))
        self.assertEqual(calls[0]["account_id"],"acc-safe")
        self.assertEqual(calls[0]["operation"],"calltracking_report_recommendations")
        self.assertEqual(calls[0]["module"],"rop")

    def test_changed_stats_change_business_intent(self):
        intents=[]
        def fake(**kwargs):
            intents.append(kwargs["idempotency_key"])
            return {
                "text":'{"overall":[],"by_manager":[]}',
                "json":{"overall":[],"by_manager":[]},
                "provider":"ollama",
                "usage":{},
                "cost_rub":0.0,
            }
        with patch("app.services.sales_ai_router.generate_text",side_effect=fake):
            C._report_recommendations("acc-safe",[],{"total":1,"answered":1,"missed":0,"minutes":1})
            C._report_recommendations("acc-safe",[],{"total":2,"answered":1,"missed":1,"minutes":1})
        self.assertEqual(len(set(intents)),2)

    def test_provider_unavailable_fails_closed(self):
        from app.services import sales_ai_router as R
        with patch(
            "app.services.sales_ai_router.generate_text",
            side_effect=R.SalesAIUnavailable("offline"),
        ):
            out=C._report_recommendations(
                "acc-safe",[],{"total":1,"answered":1,"missed":0,"minutes":1}
            )
        self.assertEqual(out,{"overall":[],"by_manager":[]})

class CalltrackingPaidPathStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = pathlib.Path('/root/BORIS/backend/app/api/calltracking.py').read_text(encoding='utf-8')

    def test_no_direct_openai_http_remains(self):
        self.assertNotIn('https://api.openai.com/v1/chat/completions', self.src)

    def test_call_analysis_uses_exactly_once_guard(self):
        self.assertIn('operation="calltracking_call_analysis"', self.src)
        self.assertIn('"call-analysis:"', self.src)

    def test_chat_analysis_uses_shared_sales_ai_router(self):
        self.assertIn('operation="rop_chat_analysis"', self.src)
        self.assertIn('"chat-analysis:"', self.src)
        self.assertIn("sales_ai_router", self.src)
        self.assertIn("expect_json=True", self.src)

    def test_training_preview_uses_exactly_once_guard(self):
        self.assertIn('operation="calltracking_rop_training_preview"', self.src)
        self.assertIn('"rop-training-preview:"', self.src)


class CalltrackingCrmRefreshContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = pathlib.Path('/root/BORIS/backend/app/crm/calltracking_sync.py').read_text(encoding='utf-8')

    def test_existing_call_refreshes_delayed_avito_snapshot(self):
        block = self.src.split('if existing:', 1)[1].split('contact_id, contact_created', 1)[0]
        self.assertIn('UPDATE boris_crm_activities', block)
        self.assertIn('"talk_duration": talk', block)
        self.assertIn('"missed": missed', block)

    def test_analysis_is_merged_after_snapshot_and_not_overwritten_again(self):
        block = self.src.split('if existing:', 1)[1].split('contact_id, contact_created', 1)[0]
        refresh_pos = block.find('SET title=:t,body=:b,metadata_json=CAST(:m AS jsonb),created_at=:at')
        analysis_pos = block.find('_sync_analysis_into_crm')
        self.assertGreater(refresh_pos, -1)
        self.assertGreater(analysis_pos, refresh_pos)
        tail = block[analysis_pos:]
        self.assertNotIn('metadata_json=CAST(:m AS jsonb)', tail)


if __name__=='__main__': unittest.main()
