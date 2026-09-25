import json
import unittest
import uuid
from unittest.mock import patch

from app.monitoring import llm, store


class MonitoringPaidIntentTests(unittest.TestCase):
    def setUp(self):
        self.key='qa-'+uuid.uuid4().hex
        self.state_key='monitor_llm_paid:v1:'+self.key
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        conn=store.connect()
        try:
            with conn.cursor() as cur:
                store.ensure_runtime_state(cur)
                cur.execute('DELETE FROM monitor_runtime_state WHERE key=%s',(getattr(self,'state_key',''),))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _raw():
        return json.dumps({'summary':'нужна услуга','intent':'buy_service','urgency':'medium','city':None,'service':'ремонт','reasons':['ищет исполнителя'],'reply':None},ensure_ascii=False)

    def test_same_paid_intent_uses_durable_result_without_second_provider_call(self):
        with patch.object(llm,'call_llm',return_value=self._raw()) as provider:
            first=llm.analyze('Нужен ремонт',['ремонт'],{'keyword':10},'lead',paid_intent_key=self.key)
        with patch.object(llm,'call_llm',side_effect=AssertionError('provider must not run')) as provider2:
            second=llm.analyze('Нужен ремонт',['ремонт'],{'keyword':10},'lead',paid_intent_key=self.key)
        self.assertEqual(first.get('source'),'llm')
        self.assertEqual(second.get('source'),'llm')
        self.assertEqual(provider.call_count,1)
        self.assertEqual(provider2.call_count,0)

    def test_claim_without_result_is_fail_closed_and_never_repurchased(self):
        state,_=llm._paid_intent_claim(self.key)
        self.assertEqual(state,'owner')
        with patch.object(llm,'call_llm',side_effect=AssertionError('ambiguous intent must not retry')) as provider:
            out=llm.analyze('Нужен ремонт',['ремонт'],{'keyword':10},'lead',paid_intent_key=self.key)
        self.assertEqual(out.get('source'),'rules')
        self.assertEqual(provider.call_count,0)


if __name__=='__main__':
    unittest.main()
