# -*- coding: utf-8 -*-
"""BORIS Phone autonomy/control-plane contracts.

No provider, push, STT or paid network action is executed here.
"""
import inspect
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import telephony_core as T
from app.services import control_plane_adapters as CPA
import telephony_guardian_runner as GR


class TelephonyAutonomyGuardianTests(unittest.TestCase):
    def test_runtime_health_snapshot_ignores_unpaid_or_expired_phone_transport(self):
        src=inspect.getsource(T.telephony_autonomy_snapshot)
        self.assertGreaterEqual(src.count('telephony_entitlements e'),4)
        self.assertIn('e.account_id=p.account_id',src)
        self.assertIn('e.account_id=t.account_id',src)
        self.assertIn('e.account_id=s.account_id',src)
        self.assertGreaterEqual(src.count('e.enabled=true'),4)
        self.assertGreaterEqual(src.count('e.paid_until>now()'),4)

    def _zero(self):
        return {
            'stale_command_processing': 0,
            'overdue_command_queue': 0,
            'stale_recording_download': 0,
            'stale_recording_transcript': 0,
            'stale_online_devices': 0,
            'expired_ring_targets': 0,
            'callback_without_task': 0,
            'stale_provider_health': 0,
            'stale_media_sessions': 0,
            'deferred_mcn_asterisk_events': 0,
            'stale_mcn_crm_sync': 0,
            'stale_mcn_terminal_cleanup': 0,
            'unreconciled_mcn_cdr': 0,
            'degraded_mcn_transport': 0,
            'degraded_telphin_sip': 0,
            'degraded_turn_media': 0,
        }

    def test_guardian_noop_when_runtime_clean(self):
        clean = self._zero()
        with patch.object(T, 'telephony_autonomy_snapshot', side_effect=[clean, clean]),              patch.object(T, 'dispatch_pending_commands') as commands,              patch.object(T, 'acquire_pending_recordings') as downloads,              patch.object(T, 'process_pending_recordings') as transcripts,              patch.object(T, 'sweep_stale_devices') as devices,              patch.object(T, 'sweep_ring_targets') as rings,              patch.object(T, 'callback_guardian') as callbacks,              patch.object(T, 'provider_health_guardian') as providers,              patch.object(T, 'media_session_guardian') as media:
            out = T.telephony_autonomy_guardian(100)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['actions'], {})
        self.assertEqual(out['unresolved'], {})
        for mocked in (commands, downloads, transcripts, devices, rings, callbacks, providers, media):
            mocked.assert_not_called()

    def test_guardian_routes_each_stale_class_to_existing_safe_recovery(self):
        before = self._zero()
        before.update({
            'stale_command_processing': 1,
            'overdue_command_queue': 2,
            'stale_recording_download': 1,
            'stale_recording_transcript': 1,
            'stale_online_devices': 1,
            'expired_ring_targets': 1,
            'callback_without_task': 1,
            'stale_provider_health': 1,
            'stale_media_sessions': 1,
            'deferred_mcn_asterisk_events': 1,
            'stale_mcn_crm_sync': 1,
            'stale_mcn_terminal_cleanup': 1,
            'unreconciled_mcn_cdr': 1,
            'degraded_mcn_transport': 1,
            'degraded_telphin_sip': 1,
        })
        after = self._zero()
        with patch.object(T, 'telephony_autonomy_snapshot', side_effect=[before, after]), \
             patch.object(T, 'dispatch_pending_commands', return_value={'status':'ok'}) as commands, \
             patch.object(T, 'acquire_pending_recordings', return_value={'status':'ok'}) as downloads, \
             patch.object(T, 'process_pending_recordings', return_value={'status':'ok'}) as transcripts, \
             patch.object(T, 'sweep_stale_devices', return_value={'status':'ok'}) as devices, \
             patch.object(T, 'sweep_ring_targets', return_value={'status':'ok'}) as rings, \
             patch.object(T, 'callback_guardian', return_value={'status':'ok'}) as callbacks, \
             patch.object(T, 'provider_health_guardian', return_value={'status':'ok'}) as providers, \
             patch.object(T, 'media_session_guardian', return_value={'status':'ok'}) as media, \
             patch('app.services.asterisk_gateway.mcn_deferred_asterisk_event_guardian',
                   return_value={'status':'ok','processed':1}) as mcn_deferred, \
             patch.object(T, 'mcn_crm_sync_guardian', return_value={'status':'ok','completed':1}) as mcn_crm, \
             patch.object(T, 'mcn_terminal_cleanup_guardian', return_value={'status':'ok','calls_cleaned':1}) as mcn_cleanup, \
             patch('app.services.mcn_core.reconcile_carrier_cdr', return_value={'status':'ok','reconciled':1}) as mcn_cdr, \
             patch('app.services.asterisk_gateway.mcn_pjsip_guardian',
                   return_value={'status':'ok','owner_action_required':False}) as mcn, \
             patch('app.services.telphin_sip_trunk.telphin_sip_guardian',
                   return_value={'status':'ok','owner_action_required':False}) as telphin:
            out = T.telephony_autonomy_guardian(100)
        self.assertEqual(out['status'], 'ok')
        self.assertFalse(out['owner_action_required'])
        self.assertEqual(out['owner_action_codes'], [])
        self.assertEqual(out['unresolved'], {})
        self.assertEqual(out['recovered']['stale_command_processing'], 1)
        self.assertEqual(out['recovered']['overdue_command_queue'], 2)
        commands.assert_called_once_with(100, include_synthetic=False)
        downloads.assert_called_once_with(10, include_synthetic=False)
        transcripts.assert_called_once_with(5, include_synthetic=False)
        devices.assert_called_once_with(None, 90)
        rings.assert_called_once_with(None)
        callbacks.assert_called_once_with(None)
        providers.assert_called_once_with(100, 900, include_synthetic=False)
        media.assert_called_once_with(100, include_synthetic=False)
        mcn_deferred.assert_called_once_with(100, include_synthetic=False)
        mcn_crm.assert_called_once_with(100, include_synthetic=False)
        mcn_cleanup.assert_called_once_with(100, include_synthetic=False)
        mcn_cdr.assert_called_once_with(None,100,include_synthetic=False)
        mcn.assert_called_once_with(include_synthetic=False)
        telphin.assert_called_once_with()

    def test_mcn_rejected_registration_escalates_only_after_self_heal_path(self):
        before = self._zero()
        before['degraded_mcn_transport'] = 1
        after = self._zero()
        after['degraded_mcn_transport'] = 1
        with patch.object(T, 'telephony_autonomy_snapshot', side_effect=[before, after]), \
             patch('app.services.asterisk_gateway.mcn_pjsip_guardian', return_value={
                 'status':'degraded',
                 'owner_action_required':True,
                 'owner_action_code':'check_mcn_sip_credentials',
                 'trunks_degraded':1,
             }) as mcn:
            out = T.telephony_autonomy_guardian(100)
        self.assertEqual(out['status'], 'degraded')
        self.assertTrue(out['owner_action_required'])
        self.assertEqual(out['owner_action_codes'], ['check_mcn_sip_credentials'])
        self.assertEqual(out['unresolved'], {'degraded_mcn_transport': 1})
        mcn.assert_called_once_with(include_synthetic=False)

    def test_guardian_reports_unresolved_without_claiming_owner_action(self):
        before = self._zero()
        before['stale_provider_health'] = 1
        after = self._zero()
        after['stale_provider_health'] = 1
        with patch.object(T, 'telephony_autonomy_snapshot', side_effect=[before, after]),              patch.object(T, 'provider_health_guardian', return_value={'status':'ok','degraded':1}):
            out = T.telephony_autonomy_guardian(100)
        self.assertEqual(out['status'], 'degraded')
        self.assertEqual(out['unresolved'], {'stale_provider_health': 1})
        self.assertFalse(out['owner_action_required'])


class TelephonyControlPlaneCallbackTests(unittest.TestCase):
    def test_control_plane_ignores_unpaid_provider_but_flags_active_paid_provider(self):
        T.ensure_schema()
        account='test-phone-cp-'+uuid.uuid4().hex[:12]
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO telephony_provider_configs(
                account_id,provider,credentials_enc,public_config_json,status,last_health_at,last_health_status
            ) VALUES(:a,'mcn','qa-placeholder','{}'::jsonb,'configured_unverified',NULL,NULL)"""),{'a':account})
            db.commit()

            unpaid=CPA._telephony_state(db,account)
            self.assertEqual(unpaid['unhealthy_provider_configs'],0)
            self.assertEqual(CPA._telephony_diagnose(db,account,None,unpaid)['classification'],'TELEPHONY_NOT_CONFIGURED')

            db.execute(text("""INSERT INTO telephony_entitlements(
                account_id,enabled,period_start,paid_until,price_rub,commercial_ref,source
            ) VALUES(:a,true,now()-interval '2 days',now()-interval '1 minute',1,'qa-cp-expired','qa')"""),{'a':account})
            db.commit()
            expired=CPA._telephony_state(db,account)
            self.assertEqual(expired['unhealthy_provider_configs'],0)

            db.execute(text("UPDATE telephony_entitlements SET paid_until=now()+interval '1 day' WHERE account_id=:a"),{'a':account})
            db.commit()
            active=CPA._telephony_state(db,account)
            self.assertEqual(active['unhealthy_provider_configs'],1)
            self.assertEqual(CPA._telephony_diagnose(db,account,None,active)['classification'],'TELEPHONY_PROVIDER_UNHEALTHY')

            db.execute(text("UPDATE telephony_entitlements SET enabled=false WHERE account_id=:a"),{'a':account})
            db.commit()
            disabled=CPA._telephony_state(db,account)
            self.assertEqual(disabled['unhealthy_provider_configs'],0)
            self.assertEqual(CPA._telephony_diagnose(db,account,None,disabled)['classification'],'TELEPHONY_NOT_CONFIGURED')
        finally:
            db.execute(text("DELETE FROM telephony_provider_configs WHERE account_id=:a"),{'a':account})
            db.execute(text("DELETE FROM telephony_entitlements WHERE account_id=:a"),{'a':account})
            db.commit()
            db.close()

    def test_missing_callback_task_is_degraded_and_recoverable(self):
        actual = {
            'provider_configs': 1,
            'active_entitlements': 1,
            'calls_without_crm_deal_24h': 0,
            'overdue_callbacks_without_task': 2,
            'overdue_callbacks_waiting_human': 0,
            'transcription_pending_24h': 0,
            'analysis_pending_24h': 0,
        }
        out = CPA._telephony_diagnose(None, None, None, actual)
        self.assertEqual(out['classification'], 'MISSED_CALLBACK_TASK_MISSING')
        self.assertEqual(out['health'], 'degraded')
        self.assertEqual(out['safe_action'], 'ensure_callback_task')

    def test_existing_callback_task_is_waiting_manager_not_system_failure(self):
        actual = {
            'provider_configs': 1,
            'active_entitlements': 1,
            'calls_without_crm_deal_24h': 0,
            'overdue_callbacks_without_task': 0,
            'overdue_callbacks_waiting_human': 3,
            'transcription_pending_24h': 0,
            'analysis_pending_24h': 0,
        }
        out = CPA._telephony_diagnose(None, None, None, actual)
        self.assertEqual(out['classification'], 'MISSED_CALLBACK_WAITING_MANAGER')
        self.assertEqual(out['health'], 'waiting_human')
        self.assertIsNone(out['safe_action'])
        self.assertFalse(out['owner_action_required'])

    def test_runtime_residue_has_priority_and_routes_to_autonomy_guardian(self):
        actual = {
            'provider_configs': 1,
            'active_entitlements': 1,
            'stale_runtime_commands': 1,
            'stale_recording_leases': 0,
            'stale_online_devices': 0,
            'expired_ring_targets': 0,
            'unhealthy_provider_configs': 0,
            'calls_without_crm_deal_24h': 5,
        }
        out = CPA._telephony_diagnose(None, None, None, actual)
        self.assertEqual(out['classification'], 'TELEPHONY_RUNTIME_STALLED')
        self.assertEqual(out['safe_action'], 'telephony_autonomy_guardian')

    def test_unhealthy_provider_is_detected_before_business_lag(self):
        actual = {
            'provider_configs': 1,
            'active_entitlements': 1,
            'stale_runtime_commands': 0,
            'stale_recording_leases': 0,
            'stale_online_devices': 0,
            'expired_ring_targets': 0,
            'unhealthy_provider_configs': 1,
            'calls_without_crm_deal_24h': 0,
        }
        out = CPA._telephony_diagnose(None, None, None, actual)
        self.assertEqual(out['classification'], 'TELEPHONY_PROVIDER_UNHEALTHY')
        self.assertEqual(out['safe_action'], 'verify_provider_connection')


class TelephonyStandaloneGuardianRunnerTests(unittest.TestCase):
    def test_timer_fallback_runs_full_autonomy_guardian(self):
        expected = {'status': 'ok', 'recovered': {'expired_ring_targets': 2}}
        with patch.object(GR, 'ensure_schema') as schema, \
             patch.object(GR, 'telephony_autonomy_guardian', return_value=expected) as guardian:
            out = GR.run_guardian_once()
        schema.assert_called_once_with()
        guardian.assert_called_once_with(100)
        self.assertEqual(out, expected)


if __name__ == '__main__':
    unittest.main()
