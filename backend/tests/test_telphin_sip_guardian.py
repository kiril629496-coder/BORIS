# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import telphin_sip_trunk as S


class TelphinSipGuardianTests(unittest.TestCase):
    def _row(self, status='configured_unverified'):
        return {
            'account_id':'qa-account',
            'enabled':True,
            'registrar':'sip.telphin.com',
            'registration_id':'telphin-reg-0123456789abcdef',
            'endpoint_id':'telphin-trunk-0123456789abcdef',
            'status':status,
            'rendered_sha256':'sha-v2',
            'applied_sha256':'sha-v1',
        }

    def test_render_is_fail_closed_and_protected(self):
        with tempfile.TemporaryDirectory() as td:
            out_path=Path(td)/'pjsip_telphin.conf'
            row={'account_id':'qa-account','credentials_enc':'fake','public_config_json':{'sip_enabled':True,'src_num':'+74951234567'}}
            with patch.object(S,'GENERATED_DIR',Path(td)), patch.object(S,'GENERATED_PJSIP',out_path),                  patch.object(S,'_provider_rows',return_value=[row]),                  patch.object(S,'_decrypt_credentials',return_value={'sip_line':'line101','sip_password':'qa-sip-secret'}),                  patch.object(S,'_state_upsert') as state, patch.object(S,'_mark_removed_accounts'):
                out=S.render_telphin_pjsip_config()
            self.assertTrue(out['external_transport'])
            self.assertEqual(out['configured'],1)
            content=out_path.read_text()
            self.assertIn('type=registration',content)
            self.assertIn('context=boris-telphin-inbound',content)
            self.assertIn('BORIS_TELPHIN_ENDPOINT=telphin-trunk-',content)
            self.assertEqual(oct(out_path.stat().st_mode & 0o777),'0o640')
            state.assert_called_once()
            self.assertFalse(state.call_args.kwargs['metadata']['secret_fields_persisted_here'])

    def test_bad_registrar_never_opens_external_transport(self):
        with tempfile.TemporaryDirectory() as td:
            row={'account_id':'qa-account','credentials_enc':'fake','public_config_json':{'sip_enabled':True,'sip_registrar':'evil.example'}}
            with patch.object(S,'GENERATED_DIR',Path(td)), patch.object(S,'GENERATED_PJSIP',Path(td)/'pjsip.conf'),                  patch.object(S,'_provider_rows',return_value=[row]),                  patch.object(S,'_decrypt_credentials',return_value={'sip_line':'line101','sip_password':'qa-sip-secret'}),                  patch.object(S,'_state_upsert'), patch.object(S,'_mark_removed_accounts'):
                out=S.render_telphin_pjsip_config()
            self.assertFalse(out['external_transport'])
            self.assertEqual(out['configured'],0)
            self.assertEqual(out['waiting_credentials'],1)

    def test_deferred_apply_is_retried_and_old_registration_is_not_trusted(self):
        row=self._row('registered')
        with patch.object(S,'render_telphin_pjsip_config',return_value={'status':'ok','changed':False,'sha256':'sha-v2','external_transport':True,'configured':1}),              patch.object(S,'_pending_apply',return_value=1),              patch.object(S,'_apply_config',return_value={'status':'deferred','reason':'active_calls_present','active_channels':2}) as apply,              patch.object(S,'_state_rows',return_value=[row]), patch.object(S,'_state_upsert') as upsert,              patch.object(S,'registration_health') as health:
            out=S.telphin_sip_guardian()
        apply.assert_called_once()
        health.assert_not_called()
        self.assertTrue(out['apply_pending'])
        self.assertEqual(out['registered'],0)
        self.assertFalse(out['owner_action_required'])
        self.assertEqual(upsert.call_args.kwargs['status'],'apply_deferred')

    def test_pending_apply_retries_next_tick_then_proves_registration(self):
        row=self._row('apply_deferred')
        with patch.object(S,'render_telphin_pjsip_config',return_value={'status':'ok','changed':False,'sha256':'sha-v2','external_transport':True,'configured':1}),              patch.object(S,'_pending_apply',return_value=1),              patch.object(S,'_apply_config',return_value={'status':'applied','active_channels':0}) as apply,              patch.object(S,'_mark_config_applied') as mark,              patch.object(S,'_state_rows',return_value=[row]), patch.object(S,'_state_upsert'),              patch.object(S,'registration_health',return_value={'status':'registered','ready':True}):
            out=S.telphin_sip_guardian()
        apply.assert_called_once()
        mark.assert_called_once_with('sha-v2')
        self.assertEqual(out['registered'],1)
        self.assertEqual(out['status'],'ok')

    def test_unregistered_self_heals_but_rejected_requires_owner(self):
        row=self._row()
        base={'status':'ok','changed':False,'sha256':'sha-v2','external_transport':True,'configured':1}
        with patch.object(S,'render_telphin_pjsip_config',return_value=base), patch.object(S,'_pending_apply',return_value=0),              patch.object(S,'_state_rows',return_value=[row]), patch.object(S,'_state_upsert'),              patch.object(S,'registration_health',return_value={'status':'unregistered','ready':False}),              patch.object(S,'_cli',return_value=(0,'sent')) as cli:
            unreg=S.telphin_sip_guardian()
        self.assertEqual(unreg['self_heal_requested'],1)
        self.assertFalse(unreg['owner_action_required'])
        cli.assert_called_once_with('pjsip send register telphin-reg-0123456789abcdef')

        with patch.object(S,'render_telphin_pjsip_config',return_value=base), patch.object(S,'_pending_apply',return_value=0),              patch.object(S,'_state_rows',return_value=[row]), patch.object(S,'_state_upsert'),              patch.object(S,'registration_health',return_value={'status':'rejected','ready':False}),              patch.object(S,'_cli') as cli:
            rejected=S.telphin_sip_guardian()
        self.assertEqual(rejected['rejected'],1)
        self.assertTrue(rejected['owner_action_required'])
        cli.assert_not_called()

    def test_registration_health_parses_real_states_without_echoing_cli(self):
        for raw,expected,ready in [
            ('Registration: telphin-reg-0123456789abcdef Registered', 'registered', True),
            ('Registration: telphin-reg-0123456789abcdef Rejected', 'rejected', False),
            ('Registration: telphin-reg-0123456789abcdef Unregistered', 'unregistered', False),
        ]:
            with self.subTest(expected=expected), patch.object(S,'_cli',return_value=(0,raw+' SECRET_SHOULD_NOT_ESCAPE')):
                out=S.registration_health('telphin-reg-0123456789abcdef')
            self.assertEqual(out['status'],expected)
            self.assertEqual(out['ready'],ready)
            self.assertNotIn('SECRET_SHOULD_NOT_ESCAPE',str(out))


if __name__=='__main__':
    unittest.main()
