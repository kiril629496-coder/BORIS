import unittest
from app.services.telephony_adapters.zadarma import ZadarmaAdapter
from app.services.telephony_adapters.novofon import NovofonAdapter
from app.services.telephony_adapters.uis import UISAdapter
from app.services.telephony_adapters.mango import MangoAdapter

class TelephonyAdapterNormalizationTests(unittest.TestCase):
    def test_zadarma_no_answer_is_missed(self):
        out=ZadarmaAdapter().normalize_webhook({'event':'NOTIFY_END','pbx_call_id':'z1','disposition':'no answer','caller_id':'7999','called_did':'7800','call_start':'1'}, {})
        self.assertEqual(out['type'],'call.missed'); self.assertEqual(out['provider_call_id'],'z1')
    def test_novofon_recording_requires_call_identity(self):
        with self.assertRaises(ValueError): NovofonAdapter().normalize_webhook({'event':'NOTIFY_RECORD','call_id_with_rec':'r1'}, {})
    def test_uis_lost_is_missed(self):
        out=UISAdapter().normalize_webhook({'call_session_id':'42','is_lost':True,'contact_phone_number':'7999','virtual_phone_number':'7800'}, {})
        self.assertEqual(out['type'],'call.missed'); self.assertEqual(out['payload']['direction'],'inbound')
    def test_mango_normalization_does_not_mutate_ingress(self):
        payload={'_event_kind':'call','entry_id':'m1','call_state':'connected','call_id':'c1','seq':'7','from':{'extension':'101'},'to':{'number':'7999'}}
        original=dict(payload); out=MangoAdapter().normalize_webhook(payload,{})
        self.assertEqual(payload,original); self.assertEqual(out['type'],'call.answered'); self.assertEqual(out['payload']['direction'],'outbound')

if __name__=='__main__': unittest.main()
