# -*- coding: utf-8 -*-
import inspect
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.services import asterisk_gateway as A
from app.services import mcn_network_guard as N


class MCNNetworkAutodiscoveryTests(unittest.TestCase):
    def test_firewall_desired_state_ignores_unpaid_or_expired_mcn_trunks(self):
        src=inspect.getsource(N._desired_mcn_network_state)
        self.assertIn('telephony_entitlements e',src)
        self.assertIn('e.account_id=t.account_id',src)
        self.assertIn('e.enabled=true',src)
        self.assertIn('e.paid_until>now()',src)

    def test_registrar_host_normalization(self):
        self.assertEqual(N._registrar_host('sip.mcn.ru:5060'),'sip.mcn.ru')
        self.assertEqual(N._registrar_host('sip:user@sip.mcn.ru:5060;transport=udp'),'sip.mcn.ru')
        self.assertEqual(N._registrar_host('SIP:SIP.MCN.RU.'),'sip.mcn.ru')
        self.assertEqual(N._registrar_host('85.94.32.92:5060'),'85.94.32.92')

    def test_explicit_ipv4_registrar_becomes_single_host_network(self):
        self.assertEqual(N._resolve_mcn_registrar_networks('85.94.32.92:5060'),['85.94.32.92/32'])

    def test_non_mcn_hostname_is_not_auto_trusted(self):
        with patch('app.services.mcn_network_guard.subprocess.run') as run:
            self.assertEqual(N._resolve_mcn_registrar_networks('sip.example.net:5060'),[])
        run.assert_not_called()

    def test_mcn_dns_result_is_canonicalized_and_deduplicated(self):
        fake=type('R',(),{
            'returncode':0,
            'stdout':'85.94.32.92 STREAM sip.mcn.ru\n85.94.32.92 DGRAM\n85.94.32.93 RAW\n'
        })()
        with patch('app.services.mcn_network_guard.subprocess.run',return_value=fake):
            out=N._resolve_mcn_registrar_networks('sip.mcn.ru:5060')
        self.assertEqual(out,['85.94.32.92/32','85.94.32.93/32'])

    def test_health_fails_closed_when_registrar_dns_is_unresolved(self):
        with patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':[],'unresolved_registrars':[{'account_id':'acct','registrar':'sip.mcn.ru'}]}), \
             patch.object(N,'_read_state',return_value={'networks':[]}), \
             patch.object(N,'_ufw_active',return_value=True), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)):
            out=N.mcn_firewall_health()
        self.assertFalse(out['ready'])
        self.assertEqual(out['status'],'registrar_resolution_failed')
        self.assertEqual(out['pending_actions'],0)

    def test_dry_run_fails_closed_when_registrar_dns_is_unresolved(self):
        with patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':[],'unresolved_registrars':[{'account_id':'acct','registrar':'sip.mcn.ru'}]}), \
             patch.object(N,'_read_state',return_value={'networks':[]}), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)):
            out=N.sync_mcn_firewall(dry_run=True)
        self.assertFalse(out['ready'])
        self.assertEqual(out['status'],'dry_run')
        self.assertTrue(out['unresolved_registrars'])

    def test_dns_failure_never_plans_delete_of_last_known_good_network(self):
        with patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':[],'unresolved_registrars':[{'account_id':'acct','registrar':'sip.mcn.ru'}]}), \
             patch.object(N,'_read_state',return_value={'networks':['85.94.32.92/32']}), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)):
            out=N.sync_mcn_firewall(dry_run=True)
        self.assertFalse(out['ready'])
        self.assertEqual(out['actions'],[])

    def test_dns_failure_preserves_last_known_good_state_until_resolution_recovers(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(N,'LOCK_PATH',Path(td)/'mcn_firewall.lock'), \
             patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':[],'unresolved_registrars':[{'account_id':'acct','registrar':'sip.mcn.ru'}]}), \
             patch.object(N,'_read_state',return_value={'networks':['85.94.32.92/32']}), \
             patch.object(N,'_ufw_active',return_value=True), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)), \
             patch.object(N,'_atomic_state_write') as write_state, \
             patch('app.services.mcn_network_guard.subprocess.run') as run, \
             patch.dict('os.environ',{'BORIS_MCN_FIREWALL_AUTOMATIC':'1'}):
            out=N.sync_mcn_firewall(dry_run=False)
        self.assertFalse(out['ready'])
        self.assertEqual(out['status'],'registrar_resolution_failed')
        self.assertTrue(out['preserved_last_known_good'])
        self.assertEqual(out['applied_networks'],['85.94.32.92/32'])
        run.assert_not_called()
        write_state.assert_called_once()
        self.assertEqual(write_state.call_args.args[0]['networks'],['85.94.32.92/32'])

    def test_first_real_trunk_plans_strict_rtp_any_rule(self):
        with patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':['85.94.32.92/32'],'unresolved_registrars':[],'active_real_trunks':1}), \
             patch.object(N,'_read_state',return_value={'version':2,'networks':['85.94.32.92/32'],'rtp_any':False}), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)):
            out=N.sync_mcn_firewall(dry_run=True)
        self.assertTrue(out['rtp_any_required'])
        self.assertFalse(out['rtp_any_applied'])
        self.assertEqual(out['actions'],[{'action':'allow','source':'0.0.0.0/0','kind':'rtp_any'}])

    def test_last_real_trunk_removed_plans_rtp_any_delete(self):
        with patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':[],'unresolved_registrars':[],'active_real_trunks':0}), \
             patch.object(N,'_read_state',return_value={'version':2,'networks':[],'rtp_any':True}), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)):
            out=N.sync_mcn_firewall(dry_run=True)
        self.assertFalse(out['rtp_any_required'])
        self.assertTrue(out['rtp_any_applied'])
        self.assertEqual(out['actions'],[{'action':'delete','source':'0.0.0.0/0','kind':'rtp_any'}])

    def test_legacy_source_rtp_migrates_to_strict_rtp_any(self):
        with patch.object(N,'_desired_mcn_network_state',return_value={
                'networks':['85.94.32.92/32'],'unresolved_registrars':[],'active_real_trunks':1}), \
             patch.object(N,'_read_state',return_value={'networks':['85.94.32.92/32']}), \
             patch.object(N,'_sip_port',return_value=5060), \
             patch.object(N,'_rtp_range',return_value=(10000,10999)):
            out=N.sync_mcn_firewall(dry_run=True)
        self.assertTrue(out['legacy_source_rtp'])
        self.assertFalse(out['legacy_source_rtp_migration_pending'])
        self.assertEqual(out['actions'],[
            {'action':'delete','source':'85.94.32.92/32','kind':'rtp_legacy'},
            {'action':'allow','source':'0.0.0.0/0','kind':'rtp_any'},
        ])

    def test_sip_stays_source_restricted_when_rtp_is_any(self):
        out=N.firewall_plan(
            ['85.94.32.92/32'],[],5060,10000,10999,
            rtp_any_required=True,rtp_any_applied=False,legacy_source_rtp=False,
        )
        visible=[{k:v for k,v in x.items() if k!='argv'} for x in out]
        self.assertIn({'action':'allow','source':'85.94.32.92/32','kind':'sip'},visible)
        self.assertIn({'action':'allow','source':'0.0.0.0/0','kind':'rtp_any'},visible)
        self.assertNotIn({'action':'allow','source':'0.0.0.0/0','kind':'sip'},visible)

    def test_real_firewall_apply_is_serialized_by_process_lock(self):
        active=0
        max_active=0
        guard=threading.Lock()

        def fake_apply(dry_run=False):
            nonlocal active,max_active
            with guard:
                active += 1
                max_active=max(max_active,active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return {'status':'ok','ready':True}

        with tempfile.TemporaryDirectory() as td, \
             patch.object(N,'LOCK_PATH',Path(td)/'mcn_firewall.lock'), \
             patch.object(N,'_sync_mcn_firewall_unlocked',side_effect=fake_apply):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:N.sync_mcn_firewall(False),range(2)))
        self.assertEqual(max_active,1)
        self.assertEqual([x['status'] for x in results],['ok','ok'])

    def test_firewall_port_matches_mcn_transport_bind(self):
        with patch.dict('os.environ',{},clear=True):
            bind=A._mcn_transport_bind()
            self.assertEqual(N._sip_port(),int(bind.rsplit(':',1)[1]))
            self.assertEqual(N._sip_port(),5064)
        with patch.dict('os.environ',{'BORIS_MCN_SIP_BIND':'0.0.0.0:5077'}):
            bind=A._mcn_transport_bind()
            self.assertEqual(N._sip_port(),int(bind.rsplit(':',1)[1]))
            self.assertEqual(N._sip_port(),5077)


if __name__=='__main__':
    unittest.main()
