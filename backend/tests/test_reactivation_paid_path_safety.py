import inspect
import unittest
from unittest.mock import patch
from pathlib import Path

from app import reactivation_draft as R


class ReactivationPaidPathSafetyTests(unittest.TestCase):
    def test_reactivation_has_no_second_direct_openai_purchase_path(self):
        source = inspect.getsource(R)
        self.assertNotIn("api.openai.com", source)
        self.assertNotIn("_direct_openai", source)
        generate_source = inspect.getsource(R.generate)
        self.assertIn("generate_ai_draft_reply", generate_source)
        self.assertIn("contextual_no_ai", generate_source)

    def test_generation_fail_closes_known_avito_subscription_block(self):
        src = Path('/root/BORIS/backend/app/reactivation_runtime.py').read_text(encoding='utf-8')
        start = src.index('def generate_for_candidate')
        end = src.index('\ndef preflight_send', start)
        body = src[start:end]
        gate = body.index('avito_messenger_subscription_required')
        paid = body.index('from app.reactivation_draft import generate')
        self.assertLess(gate, paid)
        self.assertIn('return {"ok": False, "reason": "transport_unavailable"}', body)


if __name__ == "__main__":
    unittest.main()

class ReactivationPartialChatPaidGuardTests(unittest.TestCase):
    def test_partial_402_target_chat_blocks_before_paid_generate(self):
        import app.reactivation_runtime as rr
        class R:
            def mappings(self): return self
            def first(self): return {'id':7,'account_id':'acc','avito_chat_id':'chat402','status':'candidate','attempts':0,'primary_reason':'no_reply','summary':'x'}
            def scalar(self): return '{"classification":"avito_messenger_history_partial_402"}'
        class DB:
            def execute(self,*a,**k): return R()
        with patch.object(rr,'_candidate_message',return_value=None), patch.object(rr,'_mop_active',return_value=True), patch.object(rr,'_settings',return_value={'enabled':True,'max_attempts_per_dialog':2}), patch('app.reactivation_avito_health.effective_capability',return_value={'classification':'avito_messenger_history_partial_402'}), patch('app.reactivation_avito_health.chat_transport_blocked',return_value=False), patch('app.api.messenger.sync_chat_messages',return_value={'status':'error','message':'Avito API error 402: messenger subscription'}), patch('app.reactivation_draft.generate') as paid:
            out=rr.generate_for_candidate(DB(),7)
        self.assertEqual(out.get('reason'),'transport_unavailable')
        self.assertEqual(out.get('scope'),'chat')
        paid.assert_not_called()

    def test_partial_402_readable_target_chat_allows_paid_path(self):
        import app.reactivation_runtime as rr
        class R:
            def mappings(self): return self
            def first(self): return {'id':7,'account_id':'acc','avito_chat_id':'chatOK','status':'candidate','attempts':0,'primary_reason':'no_reply','summary':'x'}
            def scalar(self): return '{"classification":"avito_messenger_history_partial_402"}'
        class DB:
            def execute(self,*a,**k): return R()
            def commit(self): pass
        with patch.object(rr,'_candidate_message',return_value=None), patch.object(rr,'_mop_active',return_value=True), patch.object(rr,'_settings',return_value={'enabled':True,'max_attempts_per_dialog':2}), patch('app.reactivation_avito_health.effective_capability',return_value={'classification':'avito_messenger_history_partial_402'}), patch('app.reactivation_avito_health.chat_transport_blocked',return_value=False), patch('app.api.messenger.sync_chat_messages',return_value={'status':'ok','messages':2}), patch('app.reactivation_draft.generate',return_value={'ok':False,'error':'sentinel_after_preflight'}) as paid:
            out=rr.generate_for_candidate(DB(),7)
        self.assertEqual(out.get('reason'),'sentinel_after_preflight')
        paid.assert_called_once()
