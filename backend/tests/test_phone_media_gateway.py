# -*- coding: utf-8 -*-
import json
import os
import unittest
from unittest.mock import patch

from app.services import phone_media_gateway as G


class PhoneMediaGatewayTests(unittest.TestCase):
    def env(self):
        return {
            'BORIS_MEDIA_ARI_URL':'http://127.0.0.1:18088/ari',
            'BORIS_MEDIA_ARI_USER':'boris',
            'BORIS_MEDIA_ARI_PASSWORD':'ari-secret-value',
            'BORIS_MEDIA_WSS_URL':'wss://boris-ai.pro/phone-media/ws',
            'BORIS_MEDIA_SIP_DOMAIN':'boris-ai.pro',
        }

    def test_installed_binary_never_means_runtime_ready(self):
        with patch.dict(os.environ,{},clear=True), patch.object(G.shutil,'which',return_value='/usr/sbin/asterisk'):
            out=G.gateway_health()
        self.assertTrue(out['binary_installed'])
        self.assertFalse(out['configured'])
        self.assertFalse(out['runtime_ready'])
        self.assertEqual(out['status'],'not_configured')

    def test_health_requires_real_ari_pong(self):
        with patch.dict(os.environ,self.env(),clear=True), patch.object(G,'_request',return_value=(200,{'ping':'pong'})):
            out=G.gateway_health()
        self.assertTrue(out['runtime_ready'])
        self.assertEqual(out['status'],'ok')

    def test_provision_returns_only_short_lived_browser_credentials(self):
        calls=[]
        def req(method,path,body=None,timeout=4.0):
            calls.append((method,path,body))
            if path.endswith('/asterisk/ping'): return 200,{'ping':'pong'}
            return 200,{}
        with patch.dict(os.environ,self.env(),clear=True), patch.object(G,'_request',side_effect=req):
            out=G.provision_webrtc_endpoint('bp_qa_1234',90)
        self.assertEqual(out['status'],'ok')
        media=out['media']
        self.assertEqual(media['transport'],'sip_ws')
        self.assertEqual(media['signaling_protocol'],'boris-sip-v1')
        self.assertEqual(media['signaling_url'],'wss://boris-ai.pro/phone-media/ws')
        self.assertEqual(media['authorization_username'],'bp_qa_1234')
        self.assertGreaterEqual(len(media['session_token']),20)
        self.assertNotIn(self.env()['BORIS_MEDIA_ARI_PASSWORD'],json.dumps(out))
        created=[x[1] for x in calls if x[0]=='PUT']
        self.assertEqual(created,[
            '/asterisk/config/dynamic/res_pjsip/auth/bp_qa_1234',
            '/asterisk/config/dynamic/res_pjsip/aor/bp_qa_1234',
            '/asterisk/config/dynamic/res_pjsip/endpoint/bp_qa_1234',
        ])

    def test_partial_provision_is_cleaned_in_reverse_order(self):
        calls=[]
        def req(method,path,body=None,timeout=4.0):
            calls.append((method,path))
            if path.endswith('/asterisk/ping'): return 200,{'ping':'pong'}
            if method=='PUT' and '/endpoint/' in path: return 500,{}
            return 200,{}
        with patch.dict(os.environ,self.env(),clear=True), patch.object(G,'_request',side_effect=req):
            out=G.provision_webrtc_endpoint('bp_qa_fail',90)
        self.assertEqual(out['status'],'media_gateway_provision_failed')
        deletes=[x[1] for x in calls if x[0]=='DELETE']
        self.assertEqual(deletes,[
            '/asterisk/config/dynamic/res_pjsip/aor/bp_qa_fail',
            '/asterisk/config/dynamic/res_pjsip/auth/bp_qa_fail',
        ])

    def test_turn_secret_mints_ephemeral_credential_without_echoing_secret(self):
        env={**self.env(),'BORIS_MEDIA_TURN_URLS':'["turn:boris-ai.pro:3478?transport=udp","turns:boris-ai.pro:5349?transport=tcp"]','BORIS_MEDIA_TURN_SECRET':'never-return-this-secret'}
        with patch.dict(os.environ,env,clear=True), patch.object(G.time,'time',return_value=1000):
            ice=G._turn_ice_servers(90,'bp_call_1234')
            other=G._turn_ice_servers(90,'bp_call_other')
        self.assertEqual(len(ice),1)
        self.assertEqual(len(ice[0]['urls']),2)
        self.assertEqual(int(ice[0]['username'].split(':',1)[0]),4600)
        self.assertIn(':boris-phone-',ice[0]['username'])
        self.assertNotEqual(ice[0]['username'],other[0]['username'])
        self.assertNotIn(env['BORIS_MEDIA_TURN_SECRET'],json.dumps(ice))

    def test_turn_target_parses_standard_turn_uri(self):
        env={**self.env(),'BORIS_MEDIA_TURN_URLS':'["turn:boris-ai.pro:3478?transport=udp","turns:boris-ai.pro:5349?transport=tcp"]','BORIS_MEDIA_TURN_SECRET':'x'*40}
        with patch.dict(os.environ,env,clear=True):
            self.assertEqual(G._turn_target(),('boris-ai.pro',3478))


    def test_turn_runtime_status_uses_live_tls_fallback_when_root_cert_files_are_unreadable(self):
        env={
            **self.env(),
            'BORIS_MEDIA_TURN_URLS':'["turn:boris-ai.pro:3478?transport=udp","turns:boris-ai.pro:5349?transport=tcp"]',
            'BORIS_MEDIA_TURN_SECRET':'x'*40,
        }
        with patch.dict(os.environ,env,clear=True), \
             patch.object(G,'_turn_service_active',return_value=True), \
             patch.object(G,'_file_sha256',return_value=None), \
             patch.object(G,'_turn_tls_probe',return_value=True) as tls, \
             patch.object(G,'_turn_stun_probe',return_value=True):
            out=G.turn_runtime_status(probe=True)
        self.assertTrue(out['runtime_ready'])
        self.assertTrue(out['certificate_present'])
        self.assertTrue(out['certificate_current'])
        self.assertEqual(out['certificate_evidence'],'live_tls')
        self.assertTrue(out['tls_probe_ok'])
        tls.assert_called_once()

    def test_turn_runtime_status_keeps_strict_hash_mismatch_when_files_are_readable(self):
        env={
            **self.env(),
            'BORIS_MEDIA_TURN_URLS':'["turn:boris-ai.pro:3478?transport=udp","turns:boris-ai.pro:5349?transport=tcp"]',
            'BORIS_MEDIA_TURN_SECRET':'x'*40,
        }
        with patch.dict(os.environ,env,clear=True), \
             patch.object(G,'_turn_service_active',return_value=True), \
             patch.object(G,'_file_sha256',side_effect=['runtime-hash','source-hash']), \
             patch.object(G,'_turn_tls_probe') as tls, \
             patch.object(G,'_turn_stun_probe',return_value=True):
            out=G.turn_runtime_status(probe=True)
        self.assertFalse(out['runtime_ready'])
        self.assertTrue(out['certificate_present'])
        self.assertFalse(out['certificate_current'])
        self.assertEqual(out['certificate_evidence'],'file_hash')
        tls.assert_not_called()

    def test_turn_guardian_unconfigured_is_noop(self):
        with patch.object(G,'turn_runtime_status',return_value={
            'configured':False,'service_active':False,'runtime_ready':False,'status':'not_configured'
        }), patch.object(G.subprocess,'run') as run:
            out=G.turn_runtime_guardian(0)
        self.assertEqual(out['action'],'none')
        self.assertFalse(out['owner_action_required'])
        run.assert_not_called()

    def test_turn_guardian_restarts_broken_service_and_verifies(self):
        before={'configured':True,'service_active':False,'runtime_ready':False,'status':'degraded',
                'certificate_present':True,'certificate_current':True,'stun_probe_ok':False}
        after={'configured':True,'service_active':True,'runtime_ready':True,'status':'ok',
               'certificate_present':True,'certificate_current':True,'stun_probe_ok':True}
        cp=type('CP',(),{'returncode':0})()
        with patch.object(G,'turn_runtime_status',side_effect=[before,after]), patch.object(G.subprocess,'run',return_value=cp) as run:
            out=G.turn_runtime_guardian(3)
        self.assertEqual(out['action'],'restart')
        self.assertEqual(out['reason'],'service_inactive')
        self.assertTrue(out['recovered'])
        self.assertFalse(out['owner_action_required'])
        run.assert_called_once()

    def test_turn_guardian_defers_certificate_refresh_during_live_media(self):
        before={'configured':True,'service_active':True,'runtime_ready':False,'status':'degraded',
                'certificate_present':True,'certificate_current':False,'stun_probe_ok':True}
        with patch.object(G,'turn_runtime_status',return_value=before), patch.object(G.subprocess,'run') as run:
            out=G.turn_runtime_guardian(2)
        self.assertEqual(out['action'],'deferred')
        self.assertEqual(out['reason'],'certificate_refresh_required')
        self.assertTrue(out['deferred'])
        self.assertFalse(out['owner_action_required'])
        run.assert_not_called()

    def test_turn_guardian_refreshes_certificate_at_zero_media(self):
        before={'configured':True,'service_active':True,'runtime_ready':False,'status':'degraded',
                'certificate_present':True,'certificate_current':False,'stun_probe_ok':True}
        after={'configured':True,'service_active':True,'runtime_ready':True,'status':'ok',
               'certificate_present':True,'certificate_current':True,'stun_probe_ok':True}
        cp=type('CP',(),{'returncode':0})()
        with patch.object(G,'turn_runtime_status',side_effect=[before,after]), patch.object(G.subprocess,'run',return_value=cp) as run:
            out=G.turn_runtime_guardian(0)
        self.assertTrue(out['recovered'])
        self.assertEqual(out['reason'],'certificate_refresh_required')
        run.assert_called_once()


    def test_delete_is_idempotent_for_missing_objects(self):
        seen=[]
        def req(method,path,body=None,timeout=4.0):
            seen.append((method,path))
            return 404,{}
        with patch.dict(os.environ,self.env(),clear=True), patch.object(G,'_request',side_effect=req):
            out=G.delete_webrtc_endpoint('bp_missing_1')
        self.assertEqual(out['status'],'ok')
        self.assertEqual([p for _,p in seen],[
            '/asterisk/config/dynamic/res_pjsip/endpoint/bp_missing_1',
            '/asterisk/config/dynamic/res_pjsip/aor/bp_missing_1',
            '/asterisk/config/dynamic/res_pjsip/auth/bp_missing_1',
        ])


if __name__=='__main__':
    unittest.main()
