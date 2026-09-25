# -*- coding: utf-8 -*-
import json
import unittest
from unittest.mock import patch

from app.services.telephony_adapters.telphin import TelphinAdapter
from app.services.telephony_adapters.registry import adapter_status, get_adapter


class TelphinAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter=TelphinAdapter()
        self.creds={'client_id':'cid-secretish','client_secret':'super-secret-value'}
        self.public={'extension_id':'12345','src_num':'101'}

    def test_registry_advertises_only_proven_contracts(self):
        st=adapter_status('telphin')
        self.assertTrue(st['implemented'])
        self.assertEqual(set(st['capabilities']),{'api','outbound_callback','call_control','media_session','sip_media','webrtc'})
        self.assertEqual(get_adapter('telphin').supported_commands,{'answer','hold','resume','hangup','transfer'})
        self.assertIn('media_session',st['capabilities'])
        self.assertNotIn('native_webhooks',st['capabilities'])

    def test_health_is_read_only_and_secret_free(self):
        with patch.object(TelphinAdapter,'_api',return_value=(200,{'id':20,'client_id':12,'extension_id':12345,'login':'private-login'})) as api:
            out=self.adapter.health(self.creds,self.public)
        self.assertTrue(out.ok); self.assertEqual(out.status,'ok')
        api.assert_called_once_with('GET','/api/ver1.0/user/',self.creds,read_only=True)
        blob=json.dumps(out.payload,ensure_ascii=False)
        self.assertNotIn('private-login',blob); self.assertNotIn(self.creds['client_secret'],blob)

    def test_callback_uses_call_api_id_as_control_identity(self):
        with patch.object(TelphinAdapter,'_api',return_value=(200,{'call_api_id':'api-call-42','call_id':'billing-call-77'})) as api:
            out=self.adapter.make_call(to_number='+79991234567',from_number=None,credentials=self.creds,public_config=self.public,metadata={})
        self.assertTrue(out.ok); self.assertEqual(out.provider_call_id,'api-call-42')
        api.assert_called_once_with('POST','/api/ver1.0/extension/12345/callback/',self.creds,{'dst_num':'+79991234567','src_num':'101'})
        self.assertNotIn('billing-call-77',json.dumps(out.payload))

    def test_callback_requires_extension_and_source(self):
        self.assertEqual(self.adapter.make_call(to_number='+7999',from_number=None,credentials=self.creds,public_config={},metadata={}).status,'configuration_required')
        self.assertEqual(self.adapter.make_call(to_number='+7999',from_number=None,credentials=self.creds,public_config={'extension_id':'12'},metadata={}).status,'configuration_required')

    def test_control_mapping_matches_telphin_current_calls_contract(self):
        cases=[
          ('hold',{},'PUT',{'action':'hold'}),
          ('resume',{},'PUT',{'action':'offhold'}),
          ('transfer',{'to_number':'105'},'PUT',{'action':'transfer','send_dst':'105'}),
          ('hangup',{},'DELETE',None),
        ]
        for command,payload,method,body in cases:
            with self.subTest(command=command), patch.object(TelphinAdapter,'_api',return_value=(200,{})) as api:
                out=self.adapter.command(provider_call_id='api-call-42',command=command,payload=payload,credentials=self.creds,public_config=self.public)
                self.assertTrue(out.ok)
                api.assert_called_once_with(method,'/api/ver1.0/extension/12345/current_calls/api-call-42',self.creds,body)

    def test_sip_configured_outbound_uses_canonical_asterisk_not_rest_callback(self):
        creds={**self.creds,'sip_line':'line-101','sip_password':'sip-secret-pass'}
        public={**self.public,'__boris_account_id':'acct-qa','sip_enabled':True}
        with patch('app.services.telphin_sip_trunk.trunk_status',return_value={'ready':True,'endpoint_id':'telphin-trunk-0123456789abcdef'}), \
             patch('app.services.asterisk_gateway.originate_telphin',return_value={'status':'accepted','provider_call_id':'telphin-channel-1','payload':{'transport':'asterisk_sip'}}) as originate, \
             patch.object(TelphinAdapter,'_api') as api:
            out=self.adapter.make_call(to_number='+79991234567',from_number='+74951234567',credentials=creds,public_config=public,metadata={'account_id':'acct-qa','idempotency_key':'idem-12345678'})
        self.assertTrue(out.ok); self.assertEqual(out.provider_call_id,'telphin-channel-1')
        originate.assert_called_once()
        api.assert_not_called()

    def test_sip_control_uses_same_asterisk_channel_and_supports_answer(self):
        creds={**self.creds,'sip_line':'line-101','sip_password':'sip-secret-pass'}
        public={**self.public,'__boris_account_id':'acct-qa','sip_enabled':True}
        with patch('app.services.asterisk_gateway.command_telphin',return_value={'status':'ok','http_status':204}) as ctl, \
             patch.object(TelphinAdapter,'_api') as api:
            out=self.adapter.command(provider_call_id='telphin-channel-1',command='answer',payload={},credentials=creds,public_config=public)
        self.assertTrue(out.ok); self.assertEqual(out.payload['transport'],'asterisk_sip')
        ctl.assert_called_once_with('acct-qa','telphin-channel-1','answer',{})
        api.assert_not_called()

    def test_media_requires_registered_sip_and_real_internal_bridge(self):
        creds={**self.creds,'sip_line':'line-101','sip_password':'sip-secret-pass'}
        public={**self.public,'__boris_account_id':'acct-qa','sip_enabled':True}
        with patch('app.services.telphin_sip_trunk.trunk_status',return_value={'ready':False}):
            blocked=self.adapter.media_session(device_id='dev',call_id='call',credentials=creds,public_config=public)
        self.assertFalse(blocked.ok); self.assertEqual(blocked.status,'telphin_sip_not_registered')
        media={'transport':'sip_ws','managed_by':'boris_gateway','endpoint_id':'bp_qa1234','signaling_protocol':'boris-sip-v1','signaling_url':'wss://boris-ai.pro/phone-media/ws','session_id':'bp_qa1234','session_token':'temporary-browser-secret-123456','authorization_username':'bp_qa1234','sip_uri':'sip:bp_qa1234@boris-ai.pro','register_expires_seconds':90,'ttl_seconds':90,'ice_servers':[]}
        with patch('app.services.telphin_sip_trunk.trunk_status',return_value={'ready':True}), \
             patch('app.services.asterisk_gateway.media_interconnect_health',return_value={'ready':True}), \
             patch('app.services.phone_media_gateway.provision_webrtc_endpoint',return_value={'status':'ok','media':media}) as provision:
            out=self.adapter.media_session(device_id='dev',call_id='call',credentials=creds,public_config=public)
        self.assertTrue(out.ok); self.assertEqual(out.payload['transport'],'sip_ws')
        self.assertTrue(provision.called)

    def test_side_effect_transport_timeout_is_not_replayed(self):
        from urllib.error import URLError
        from urllib.request import Request
        with patch('app.services.telephony_adapters.telphin.urlopen',side_effect=URLError('timeout')) as opener:
            with self.assertRaises(RuntimeError):
                TelphinAdapter._open(Request('https://apiproxy.telphin.ru/api/ver1.0/extension/1/callback/',data=b'{}',method='POST'),read_only=False)
        self.assertEqual(opener.call_count,1)

    def test_read_only_transport_can_use_documented_fallback(self):
        from urllib.error import URLError
        from urllib.request import Request
        class Resp:
            status=200
            def read(self,n): return b'{}'
            def __enter__(self): return self
            def __exit__(self,*a): return False
        with patch('app.services.telephony_adapters.telphin.urlopen',side_effect=[URLError('primary timeout'),Resp()]) as opener:
            status,raw=TelphinAdapter._open(Request('https://apiproxy.telphin.ru/api/ver1.0/user/',method='GET'),read_only=True)
        self.assertEqual(status,200); self.assertEqual(raw,b'{}'); self.assertEqual(opener.call_count,2)
        self.assertIn('altapi.telphin.ru',opener.call_args_list[1].args[0].full_url)

    def test_recording_link_uses_extension_record_storage_url(self):
        with patch.object(TelphinAdapter,'_api',return_value=(200,{'storage_url':'https://records.telphin.ru/r/abc'})) as api:
            out=self.adapter.recording_link(provider_recording_id='rec-42',credentials=self.creds,public_config=self.public)
        self.assertTrue(out.ok); self.assertEqual(out.payload['url'],'https://records.telphin.ru/r/abc')
        api.assert_called_once_with('GET','/api/ver1.0/extension/12345/record/rec-42/storage_url/',self.creds,read_only=True)

    def test_recording_link_rejects_non_https_or_missing_identity(self):
        with patch.object(TelphinAdapter,'_api',return_value=(200,{'storage_url':'http://unsafe.local/a'})):
            out=self.adapter.recording_link(provider_recording_id='rec-42',credentials=self.creds,public_config=self.public)
        self.assertFalse(out.ok); self.assertEqual(out.status,'recording_unavailable')
        self.assertEqual(self.adapter.recording_link(provider_recording_id='',credentials=self.creds,public_config=self.public).status,'recording_unavailable')
        self.assertEqual(self.adapter.recording_link(provider_recording_id='rec',credentials=self.creds,public_config={}).status,'configuration_required')

    def test_normalizes_inbound_answered_and_missed_lifecycle(self):
        dial={'EventType':'dial-in','CallAPIID':'api-1','CallID':'call-1','CallFlow':'in','CallStatus':'CALLING','CallerIDNum':'+79990001122','CalledDID':'+74951234567','EventTime':'1'}
        answer={**dial,'EventType':'answer','CallStatus':'ANSWER','EventTime':'2'}
        ended={**dial,'EventType':'hangup','CallStatus':'ANSWER','Bridged':'yes','RecID':'rec-1','Duration':'5000000','EventTime':'3'}
        missed={**dial,'EventType':'hangup','CallStatus':'NOANSWER','Bridged':'no','EventTime':'4'}
        r1=self.adapter.normalize_webhook(dial,{})
        r2=self.adapter.normalize_webhook(answer,{})
        r3=self.adapter.normalize_webhook(ended,{})
        r4=self.adapter.normalize_webhook(missed,{})
        self.assertEqual((r1['type'],r2['type'],r3['type'],r4['type']),('call.ringing','call.answered','call.ended','call.missed'))
        self.assertEqual(r1['provider_call_id'],'api-1')
        self.assertEqual(r1['payload']['direction'],'inbound')
        self.assertEqual(r1['payload']['from'],'+79990001122')
        self.assertEqual(r1['payload']['to'],'+74951234567')
        self.assertEqual(r3['provider_recording_id'],'rec-1')
        self.assertNotEqual(r1['event_id'],r2['event_id'])

    def test_normalizes_outbound_failure_and_requires_unambiguous_identity(self):
        out=self.adapter.normalize_webhook({'EventType':'hangup','CallAPIID':'api-2','CallFlow':'out','CallStatus':'FAILED','CallerExtension':'101','CalledNumber':'+7999','EventTime':'7'}, {})
        self.assertEqual(out['type'],'call.failed'); self.assertEqual(out['payload']['direction'],'outbound')
        with self.assertRaises(ValueError): self.adapter.normalize_webhook({'EventType':'answer','CallFlow':'in'}, {})
        with self.assertRaises(ValueError): self.adapter.normalize_webhook({'EventType':'answer','CallAPIID':'api-x'}, {})
        with self.assertRaises(ValueError): self.adapter.normalize_webhook({'EventType':'voicemail','CallAPIID':'api-x','CallFlow':'in'}, {})

    def test_media_without_sip_and_unauthenticated_ingress_remain_fail_closed(self):
        media=self.adapter.media_session(device_id='dev',call_id='call',credentials=self.creds,public_config=self.public)
        self.assertFalse(media.ok); self.assertEqual(media.status,'media_pending')
        with self.assertRaises(ValueError): self.adapter.normalize_webhook({}, {})


if __name__=='__main__':
    unittest.main()
