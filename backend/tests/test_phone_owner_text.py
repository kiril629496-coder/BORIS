# -*- coding: utf-8 -*-
import unittest

from app.services.telephony_core import _phone_primary_next_action


class PhoneOwnerTextTests(unittest.TestCase):
    def _assert_neutral(self, out):
        text=str(out.get('text') or '')
        self.assertNotIn('MCN', text)
        self.assertNotIn('Телфин', text)
        self.assertNotIn('Telphin', text)

    def test_no_provider_next_action_is_neutral(self):
        out=_phone_primary_next_action(
            provider_key='',provider_connected=False,provider_has_credentials=False,
            telphin_sip=None,online_devices=0,gates={}
        )
        self.assertEqual(out['code'],'connect_telephony')
        self.assertTrue(out['owner_action_required'])
        self._assert_neutral(out)

    def test_provider_specific_internal_path_still_has_neutral_owner_text(self):
        out=_phone_primary_next_action(
            provider_key='mcn',provider_connected=False,provider_has_credentials=True,
            telphin_sip=None,online_devices=0,gates={},
            mcn_readiness={'ready':False,'blockers':['mcn_active_did_missing']},
            mcn_transport={'ready':True,'owner_action_required':False},
            mcn_network={'ready':True},
        )
        self.assertEqual(out['code'],'mcn_did_required')
        self._assert_neutral(out)

    def test_telphin_internal_path_still_has_neutral_owner_text(self):
        out=_phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'status':'waiting_credentials','enabled':False},
            online_devices=0,gates={}
        )
        self.assertEqual(out['code'],'telphin_sip_credentials')
        self._assert_neutral(out)


if __name__ == '__main__':
    unittest.main()
