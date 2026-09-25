# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from app.services.asterisk_gateway import MCN_REGISTRATION_EXPIRATION_SECONDS, _asterisk_conf_escape, _mcn_codec_allow, _mcn_pjsip_auth_lines, _mcn_transport_bind, _mcn_trunk_prerequisites
from app.services.mcn_network_guard import _sip_port


class MCNPJSIPAuthModeTests(unittest.TestCase):
    def test_default_mcn_bind_avoids_existing_udp_5060_listener(self):
        with patch.dict(os.environ, {"BORIS_MCN_SIP_BIND":""}, clear=False):
            self.assertEqual(_mcn_transport_bind(),"0.0.0.0:5064")
            self.assertEqual(_sip_port(),5064)

    def test_explicit_mcn_bind_override_is_still_supported(self):
        with patch.dict(os.environ, {"BORIS_MCN_SIP_BIND":"127.0.0.1:15061"}, clear=False):
            self.assertEqual(_mcn_transport_bind(),"127.0.0.1:15061")
            self.assertEqual(_sip_port(),15061)

    def test_mcn_registration_expiration_matches_carrier_guidance(self):
        self.assertEqual(MCN_REGISTRATION_EXPIRATION_SECONDS,120)

    def test_default_mcn_codec_is_alaw_only(self):
        self.assertEqual(_mcn_codec_allow(None),'alaw')
        self.assertEqual(_mcn_codec_allow([]),'alaw')

    def test_explicit_codec_list_is_preserved_when_safe(self):
        self.assertEqual(_mcn_codec_allow(['alaw','ulaw']),'alaw,ulaw')

    def test_unsafe_codec_tokens_are_ignored(self):
        self.assertEqual(_mcn_codec_allow(['alaw','bad;codec']),'alaw')

    def test_ip_mode_never_renders_stale_registration_credentials(self):
        lines=_mcn_pjsip_auth_lines('ip','mcn-auth-1','old-login','old-password')
        self.assertEqual(lines,[])

    def test_registration_mode_renders_auth_only_when_complete(self):
        lines=_mcn_pjsip_auth_lines('registration','mcn-auth-1','login','secret')
        body='\n'.join(lines)
        self.assertIn('outbound_auth=mcn-auth-1',body)
        self.assertIn('from_user=login',body)
        self.assertIn('type=auth',body)
        self.assertIn('username=login',body)
        self.assertIn('password=secret',body)

    def test_registration_mode_with_incomplete_credentials_fails_closed(self):
        self.assertEqual(_mcn_pjsip_auth_lines('registration','mcn-auth-1','login',''),[])
        self.assertEqual(_mcn_pjsip_auth_lines('registration','mcn-auth-1','','secret'),[])

    def test_semicolon_in_password_is_escaped_for_asterisk_conf(self):
        self.assertEqual(_asterisk_conf_escape('qa;secret'), r'qa\;secret')
        body='\n'.join(_mcn_pjsip_auth_lines('registration','mcn-auth-1','login','qa;secret'))
        self.assertIn(r'password=qa\;secret',body)
        self.assertNotIn('password=qa;secret',body)

    def test_existing_backslash_before_semicolon_is_preserved(self):
        self.assertEqual(_asterisk_conf_escape(r'qa\;secret'), r'qa\\;secret')

    def test_unsafe_registration_username_fails_closed(self):
        base={'auth_mode':'registration','registrar':'sip.mcn.ru','password':'secret'}
        for username in ('bad;user','bad user','bad@user'):
            out=_mcn_trunk_prerequisites({**base,'username':username})
            self.assertFalse(out['ready'])
            self.assertEqual(out['reason'],'mcn_registration_username_unsafe')


if __name__=='__main__':
    unittest.main()
