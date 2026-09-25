# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import telephony_core as T


class PhoneNativeReleaseEnvironmentTests(unittest.TestCase):
    def test_linux_has_native_release_channel_and_is_not_masqueraded_as_web(self):
        out=T.client_release_status('linux','0.0.0')
        self.assertEqual(out['platform'],'linux')
        self.assertEqual(out['current_version'],'0.1.0')
        self.assertEqual(out['install_mode'],'native')
        self.assertTrue(out['update_available'])
        self.assertFalse(out['download_ready'])
        self.assertEqual(out['blocked_reason'],'signed_artifact_not_verified')
        self.assertEqual(out['update_policy'],'signed_verified_only')
        self.assertTrue(out['pwa_available'])

    def test_native_release_block_reasons_are_explicit(self):
        windows=T.client_release_status('windows','0.0.0')
        self.assertEqual(windows['blocked_reason'],'signed_artifact_not_verified')
        with patch.object(T,'native_release_evidence',return_value={
            'status':'missing','platform':'android','signed_verified':False,
            'device_qa_verified':False,'release_verified':False,'artifact_path':None,'sha256':None
        }):
            android=T.client_release_status('android','0.0.0')
        self.assertEqual(android['blocked_reason'],'release_evidence_missing')
        self.assertFalse(android['download_ready'])
        unknown=T.client_release_status('plan9','0.0.0')
        self.assertEqual(unknown['blocked_reason'],'unsupported_platform')

    def test_desktop_release_manifest_refreshes_canonical_artifact_manifest(self):
        import json
        package=Path('/root/BORIS/clients/boris-phone/desktop/package.json')
        self.assertTrue(package.is_file())
        scripts=(json.loads(package.read_text(encoding='utf-8')).get('scripts') or {})
        manifest=str(scripts.get('manifest') or '')
        self.assertIn('release-manifest.js',manifest)
        self.assertIn('../scripts/artifact-manifest.py',manifest)
        for name in ('release:local','release:win','release:mac','release:linux','qa:win:unsigned'):
            self.assertIn('npm run manifest',str(scripts.get(name) or ''),name)

    def test_missing_release_inputs_are_detected_without_secret_values(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True), patch.object(T.shutil, 'which', return_value=None):
            root=Path(td)/'clients'/'boris-phone'
            (root/'android/app').mkdir(parents=True)
            out=T.phone_native_release_environment(root)
        android=out['android']; ios=out['ios']
        self.assertFalse(android['release_preflight_inputs_ready'])
        self.assertIn('firebase_google_services_json',android['missing'])
        self.assertIn('release_keystore',android['missing'])
        self.assertIn('adb_unavailable',android['missing'])
        self.assertIn('real_device_qa_not_proven',android['missing'])
        self.assertFalse(ios['archive_preflight_inputs_ready'])
        self.assertIn('xcodebuild',ios['missing'])
        self.assertIn('apple_team_id',ios['missing'])
        self.assertNotIn('password',repr(out).lower().replace('store_password','').replace('key_password',''))

    def test_present_build_inputs_never_fake_real_device_or_codesign_proof(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); root=base/'clients'/'boris-phone'; app=root/'android/app'; app.mkdir(parents=True)
            (app/'google-services.json').write_text('{}')
            keystore=base/'release.jks'; keystore.write_bytes(b'qa')
            sdk=base/'sdk'; (sdk/'platforms/android-35').mkdir(parents=True); (sdk/'build-tools/35.0.0').mkdir(parents=True); (sdk/'platform-tools').mkdir(parents=True); local_adb=sdk/'platform-tools/adb'; local_adb.write_text('#!/bin/sh\\n'); local_adb.chmod(0o755)
            env={
              'ANDROID_SDK_ROOT':str(sdk),'BORIS_ANDROID_KEYSTORE':str(keystore),
              'BORIS_ANDROID_STORE_PASSWORD':'secret-store','BORIS_ANDROID_KEY_ALIAS':'release',
              'BORIS_ANDROID_KEY_PASSWORD':'secret-key','BORIS_APPLE_TEAM_ID':'TEAM123',
            }
            def which(name):
                return f'/qa/{name}' if name in {'java','xcodebuild','xcodegen'} else None
            with patch.dict(os.environ,env,clear=True), patch.object(T.shutil,'which',side_effect=which):
                out=T.phone_native_release_environment(root)
        self.assertTrue(out['android']['release_preflight_inputs_ready'])
        self.assertTrue(out['android']['adb_available'])
        self.assertEqual(out['android']['adb_source'],'local_sdk')
        self.assertFalse(out['android']['real_device_qa_proven'])
        self.assertIn('real_device_qa_not_proven',out['android']['missing'])
        self.assertTrue(out['ios']['archive_preflight_inputs_ready'])
        self.assertFalse(out['ios']['codesigning_identity_proven'])
        self.assertIn('apple_codesigning_identity_not_proven',out['ios']['missing'])
        self.assertNotIn('secret-store',repr(out))
        self.assertNotIn('secret-key',repr(out))


    def test_media_diagnostics_exposes_missing_gateway_without_faking_readiness(self):
        with patch.object(T.shutil,'which',return_value=None), \
             patch('app.services.phone_media_gateway.gateway_health',return_value={'status':'not_configured','runtime_ready':False,'configured':False}), \
             patch('app.services.phone_media_gateway.turn_runtime_status',return_value={'status':'not_configured','configured':False,'runtime_ready':False}):
            out=T.phone_media_release_diagnostics(
                'uis', True,
                {'implemented':True,'capabilities':['api','call_control','recordings']},
                0,
            )
        self.assertEqual(out['status'],'blocked')
        self.assertIn('adapter_media_contract_unimplemented',out['blocker_codes'])
        self.assertIn('boris_media_gateway_not_ready',out['blocker_codes'])
        self.assertIn('real_media_transport_not_proven',out['blocker_codes'])
        self.assertFalse(out['media_session_proven'])

    def test_media_diagnostics_requires_real_validated_session_even_if_adapter_declares_media(self):
        with patch.object(T.shutil,'which',return_value='/qa/tool'), \
             patch('app.services.phone_media_gateway.gateway_health',return_value={'status':'ok','runtime_ready':True,'configured':True}), \
             patch('app.services.phone_media_gateway.turn_runtime_status',return_value={'status':'ok','configured':True,'runtime_ready':True,'stun_probe_ok':True}):
            blocked=T.phone_media_release_diagnostics(
                'uis', True,
                {'implemented':True,'capabilities':['api','media_session','webrtc']},
                0,
            )
            ready=T.phone_media_release_diagnostics(
                'uis', True,
                {'implemented':True,'capabilities':['api','media_session','webrtc']},
                1,
            )
        self.assertEqual(blocked['status'],'blocked')
        self.assertIn('real_media_transport_not_proven',blocked['blocker_codes'])
        self.assertEqual(ready['status'],'ready')
        self.assertTrue(ready['media_session_proven'])
        self.assertEqual(ready['blocker_codes'],[])

    def test_media_diagnostics_blocks_current_turn_failure_even_with_old_media_proof(self):
        with patch.object(T.shutil,'which',return_value='/qa/tool'), \
             patch('app.services.phone_media_gateway.gateway_health',return_value={'status':'ok','runtime_ready':True,'configured':True}), \
             patch('app.services.phone_media_gateway.turn_runtime_status',return_value={'status':'degraded','configured':True,'runtime_ready':False,'stun_probe_ok':False}):
            out=T.phone_media_release_diagnostics(
                'uis', True,
                {'implemented':True,'capabilities':['api','media_session','webrtc']},
                1,
            )
        self.assertEqual(out['status'],'blocked')
        self.assertIn('boris_turn_not_ready',out['blocker_codes'])
        self.assertTrue(out['media_session_proven'])
        self.assertFalse(out['turn_runtime']['runtime_ready'])

    def test_media_diagnostics_current_provider_failure_overrides_old_media_proof(self):
        with patch.object(T.shutil,'which',return_value='/qa/tool'), \
             patch('app.services.phone_media_gateway.gateway_health',return_value={'status':'ok','runtime_ready':True,'configured':True}), \
             patch('app.services.phone_media_gateway.turn_runtime_status',return_value={'status':'ok','configured':True,'runtime_ready':True,'stun_probe_ok':True}):
            out=T.phone_media_release_diagnostics(
                'uis', False,
                {'implemented':True,'capabilities':['api','media_session','webrtc']},
                1,
            )
        self.assertEqual(out['status'],'blocked')
        self.assertIn('provider_not_verified',out['blocker_codes'])


    def test_primary_next_action_keeps_internal_telphin_recovery_on_boris(self):
        gates={'real_inbound_seen':False}
        out=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'unregistered'},online_devices=1,
            gates=gates,recording_policy='manual',
        )
        self.assertEqual(out['code'],'telphin_sip_self_heal')
        self.assertEqual(out['actor'],'boris')
        self.assertFalse(out['owner_action_required'])

    def test_primary_next_action_escalates_only_rejected_telphin_credentials(self):
        out=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'rejected'},online_devices=1,
            gates={},recording_policy='manual',
        )
        self.assertEqual(out['code'],'telphin_sip_rejected')
        self.assertEqual(out['actor'],'owner')
        self.assertTrue(out['owner_action_required'])

    def test_primary_next_action_is_one_human_step_after_internal_contour_ready(self):
        gates={
            'real_inbound_seen':False,'real_answer_seen':False,'real_media_provider':False,
            'recording_received':False,'transcription_completed':False,'ai_analysis_completed':False,
            'real_outbound_seen':False,'real_outbound_lifecycle':False,'real_inbound_e2e_chain':False,
        }
        out=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'registered'},online_devices=1,
            gates=gates,recording_policy='not_required',
        )
        self.assertEqual(out['code'],'real_inbound_test')
        self.assertEqual(out['actor'],'manager')
        self.assertFalse(out['owner_action_required'])
        self.assertIn('один реальный входящий',out['text'])

    def test_primary_next_action_recording_policy_is_explicit_not_inferred(self):
        gates={
            'real_inbound_seen':True,'real_answer_seen':True,'real_media_provider':True,
            'real_hold_completed':True,'real_resume_completed':True,'real_call_completed':True,
            'recording_received':False,'transcription_completed':False,'ai_analysis_completed':False,
            'real_outbound_seen':False,'real_outbound_lifecycle':False,'real_inbound_e2e_chain':False,
        }
        manual=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'registered'},online_devices=1,
            gates=gates,recording_policy='manual',
        )
        automatic=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'registered'},online_devices=1,
            gates=gates,recording_policy='not_required',
        )
        self.assertEqual(manual['code'],'recording_policy')
        self.assertTrue(manual['owner_action_required'])
        self.assertEqual(automatic['code'],'recording_wait')
        self.assertEqual(automatic['actor'],'boris')


    def test_primary_next_action_escalates_when_rop_entitlement_is_inactive(self):
        gates={
            'real_inbound_seen':True,'real_answer_seen':True,'real_media_provider':True,
            'real_hold_completed':True,'real_resume_completed':True,'real_call_completed':True,
            'recording_received':True,'transcription_completed':False,'ai_analysis_completed':False,
            'real_outbound_seen':False,'real_outbound_lifecycle':False,'real_inbound_e2e_chain':False,
        }
        out=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'registered'},online_devices=1,
            gates=gates,recording_policy='not_required',
            recording_ai_entitlement={'active':False,'left':0,'expired':False},
        )
        self.assertEqual(out['code'],'rop_entitlement_required')
        self.assertEqual(out['actor'],'owner')
        self.assertTrue(out['owner_action_required'])

    def test_primary_next_action_keeps_active_rop_processing_on_boris(self):
        gates={
            'real_inbound_seen':True,'real_answer_seen':True,'real_media_provider':True,
            'real_hold_completed':True,'real_resume_completed':True,'real_call_completed':True,
            'recording_received':True,'transcription_completed':False,'ai_analysis_completed':False,
            'real_outbound_seen':False,'real_outbound_lifecycle':False,'real_inbound_e2e_chain':False,
        }
        out=T._phone_primary_next_action(
            provider_key='telphin',provider_connected=True,provider_has_credentials=True,
            telphin_sip={'enabled':True,'status':'registered'},online_devices=1,
            gates=gates,recording_policy='not_required',
            recording_ai_entitlement={'active':True,'left':120},
        )
        self.assertEqual(out['code'],'recording_ai_processing')
        self.assertEqual(out['actor'],'boris')
        self.assertFalse(out['owner_action_required'])


    def test_webrtc_media_contract_requires_boris_signaling_and_wss(self):
        base={'transport':'webrtc','session_id':'s1','ttl_seconds':60,'session_token':'short-lived-token-12345','ice_servers':[]}
        ok={**base,'signaling_protocol':'boris-webrtc-v1','signaling_url':'wss://media.boris-ai.pro/ws'}
        self.assertEqual(T._validate_media_session_payload(ok),(True,'ok'))
        bad_protocol={**ok,'signaling_protocol':'other'}
        self.assertEqual(T._validate_media_session_payload(bad_protocol)[1],'media_signaling_protocol_invalid')
        bad_url={**ok,'signaling_url':'ws://media.boris-ai.pro/ws'}
        self.assertEqual(T._validate_media_session_payload(bad_url)[1],'media_signaling_url_invalid')
        bad_token={**ok,'session_token':'short'}
        self.assertEqual(T._validate_media_session_payload(bad_token)[1],'media_session_token_invalid')

    def test_webrtc_media_contract_bounds_ice_servers(self):
        ok={'transport':'webrtc','signaling_protocol':'boris-webrtc-v1','signaling_url':'wss://media.boris-ai.pro/ws','session_id':'s1','session_token':'short-lived-token-12345','ttl_seconds':60,
            'ice_servers':[{'urls':['stun:stun.example.net:3478','turns:turn.example.net:5349'],'username':'u','credential':'c'}]}
        self.assertEqual(T._validate_media_session_payload(ok),(True,'ok'))
        bad={**ok,'ice_servers':[{'urls':'https://not-ice.example'}]}
        self.assertEqual(T._validate_media_session_payload(bad)[1],'media_ice_servers_invalid')

    def test_sip_ws_media_contract_is_temporary_and_identity_bound(self):
        ok={'transport':'sip_ws','signaling_protocol':'boris-sip-v1','signaling_url':'wss://boris-ai.pro/phone-media/ws',
            'session_id':'bp_call_abc123','session_token':'short-lived-browser-secret-12345','authorization_username':'bp_call_abc123',
            'sip_uri':'sip:bp_call_abc123@boris-ai.pro','register_expires_seconds':90,'ttl_seconds':90,'ice_servers':[]}
        self.assertEqual(T._validate_media_session_payload(ok),(True,'ok'))
        self.assertEqual(T._validate_media_session_payload({**ok,'authorization_username':'bp_other'})[1],'media_sip_username_invalid')
        self.assertEqual(T._validate_media_session_payload({**ok,'sip_uri':'sip:other@boris-ai.pro'})[1],'media_sip_uri_invalid')
        self.assertEqual(T._validate_media_session_payload({**ok,'register_expires_seconds':600})[1],'media_sip_registration_expiry_invalid')
        self.assertEqual(T._validate_media_session_payload({**ok,'signaling_url':'ws://boris-ai.pro/phone-media/ws'})[1],'media_signaling_url_invalid')


if __name__ == '__main__':
    unittest.main()
