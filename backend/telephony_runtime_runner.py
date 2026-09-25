#!/usr/bin/env python3
from __future__ import annotations
import time, traceback, os, socket
import psycopg2
from app.db.session import DATABASE_URL
from app.services.telephony_core import (
    ensure_schema, dispatch_pending_commands, callback_guardian, acquire_pending_recordings, process_pending_recordings,
    recording_storage_guard, telephony_operational_retention_guard, sweep_stale_devices, sweep_ring_targets, dispatch_mobile_pushes, quality_guardian, rop_report_guardian, coaching_guardian, coaching_followup_guardian, realtime_copilot_tick, voice_agent_post_call_guardian, minute_package_guardian, minute_alert_delivery_guardian, autoanswer_guardian, provider_health_guardian, media_session_guardian, turn_media_guardian, telephony_autonomy_guardian,
)
from app.services.calltracking_own import guardian as own_calltracking_guardian
from app.services.telphin_sip_trunk import telphin_sip_guardian
from app.services.asterisk_gateway import mcn_pjsip_guardian

ensure_schema()
# Cluster singleton: keep one authoritative telephony guardian/dispatcher active even if
# systemd/container orchestration accidentally starts a second runtime process.
_RUNTIME_LOCK_KEY=0x424F52495350  # stable BORIS Phone advisory lock key
_lock_conn=psycopg2.connect(
    DATABASE_URL,
    application_name='boris-telephony-runtime-lock',
    keepalives=1,
    keepalives_idle=30,
    keepalives_interval=10,
    keepalives_count=3,
)
_lock_conn.autocommit=True
_lock_cursor=_lock_conn.cursor()
_lock_cursor.execute('SELECT pg_try_advisory_lock(%s)',(_RUNTIME_LOCK_KEY,))
_lock_ok=bool(_lock_cursor.fetchone()[0])
if not _lock_ok:
    print('BORIS_TELEPHONY_RUNTIME=STANDBY singleton_lock_busy', flush=True)
    _lock_cursor.close(); _lock_conn.close()
    raise SystemExit(0)
print(f'BORIS_TELEPHONY_RUNTIME_LOCK=ACQUIRED pid={os.getpid()} host={socket.gethostname()}', flush=True)
last_lock_probe=time.time()
last_guard=0.0
last_storage_guard=0.0
last_retention_guard=0.0
last_presence_guard=0.0
last_quality_guard=0.0
last_report_guard=0.0
last_coaching_guard=0.0
last_copilot=0.0
last_voice_postcall=0.0
last_minute_guard=0.0
last_minute_alert_delivery=0.0
last_autoanswer=0.0
last_provider_health=0.0
last_media_guard=0.0
last_turn_media_guard=0.0
last_autonomy_guard=0.0
last_own_calltracking_guard=0.0
last_mcn_pjsip_guard=0.0
last_telphin_sip_guard=0.0
print('BORIS_TELEPHONY_RUNTIME=STARTED', flush=True)
while True:
    try:
        now=time.time()
        if now-last_lock_probe>=15:
            try:
                _lock_cursor.execute('SELECT 1')
                _lock_cursor.fetchone()
            except Exception as exc:
                print('BORIS_TELEPHONY_RUNTIME=EXIT singleton_lock_connection_lost '+repr(exc)[:240],flush=True)
                raise SystemExit(3)
            last_lock_probe=now
        result=dispatch_pending_commands(100)
        downloads=acquire_pending_recordings(2)
        if downloads.get('picked'):
            print('telephony_recording_downloads',downloads,flush=True)
        ai=process_pending_recordings(1)
        if ai.get('picked'):
            print('telephony_recordings',ai,flush=True)
        if result.get('processed'):
            print('telephony_commands',result,flush=True)
        now=time.time()
        if now-last_provider_health>=300:
            provider_health=provider_health_guardian(100,900)
            if provider_health.get('checked') or provider_health.get('degraded'):
                print('telephony_provider_health',provider_health,flush=True)
            last_provider_health=now
        if now-last_autoanswer>=2:
            auto=autoanswer_guardian(None,100)
            if auto.get('requested') or auto.get('failed'):
                print('telephony_autoanswer',auto,flush=True)
            last_autoanswer=now
        if now-last_voice_postcall>=10:
            post=voice_agent_post_call_guardian(None,50)
            if post.get('completed') or post.get('failed'):
                print('telephony_voice_postcall',post,flush=True)
            last_voice_postcall=now
        if now-last_copilot>=3:
            copilot=realtime_copilot_tick(None,20)
            if copilot.get('created'):
                print('telephony_copilot',copilot,flush=True)
            last_copilot=now
        if now-last_media_guard>=10:
            media_guard=media_session_guardian(100)
            if media_guard.get('cleaned') or media_guard.get('expired') or media_guard.get('retry') or media_guard.get('unresolved') or media_guard.get('activation_started') or media_guard.get('activation_errors'):
                print('telephony_media_guardian',media_guard,flush=True)
            last_media_guard=now
        if now-last_turn_media_guard>=60:
            turn_guard=turn_media_guardian(False)
            if turn_guard.get('action') not in {None,'none'} or turn_guard.get('owner_action_required') or turn_guard.get('status')=='degraded':
                print('telephony_turn_media_guardian',turn_guard,flush=True)
            last_turn_media_guard=now
        if now-last_presence_guard>=10:
            stale=sweep_stale_devices(None,90)
            rings=sweep_ring_targets(None)
            pushes=dispatch_mobile_pushes(100)
            if stale.get('offline_marked') or rings.get('expired') or pushes.get('failed'):
                print('telephony_presence',{'stale':stale,'rings':rings,'pushes':pushes},flush=True)
            last_presence_guard=now
        if now-last_minute_guard>=60:
            minute_guard=minute_package_guardian(1000)
            if minute_guard.get('cycles_reset') or minute_guard.get('alerts_created') or minute_guard.get('failed'):
                print('telephony_minute_guardian',minute_guard,flush=True)
            last_minute_guard=now
        if now-last_minute_alert_delivery>=60:
            alert_delivery=minute_alert_delivery_guardian(100)
            if alert_delivery.get('sent') or alert_delivery.get('failed'):
                print('telephony_minute_alert_delivery',alert_delivery,flush=True)
            last_minute_alert_delivery=now
        if now-last_coaching_guard>=60:
            coaching=coaching_guardian(None,14,100)
            followup=coaching_followup_guardian(None,30,100)
            if coaching.get('created') or followup.get('completed'):
                print('telephony_coaching',{'created':coaching,'followup':followup},flush=True)
            last_coaching_guard=now
        if now-last_report_guard>=60:
            reports=rop_report_guardian()
            if reports.get('sent') or reports.get('failed'):
                print('telephony_rop_reports',reports,flush=True)
            last_report_guard=now
        if now-last_quality_guard>=60:
            qguard=quality_guardian(None,7,100)
            if qguard.get('evaluated') or qguard.get('failed'):
                print('telephony_quality_guardian',qguard,flush=True)
            last_quality_guard=now
        if now-last_guard>=30:
            guard=callback_guardian(None)
            devices=sweep_stale_devices(None)
            ringing=sweep_ring_targets(None)
            if guard.get('overdue') or guard.get('tasks_created'):
                print('telephony_guardian',guard,flush=True)
            if devices.get('offline_marked') or ringing.get('expired') or ringing.get('missed'):
                print('telephony_multidevice',{'devices':devices,'ringing':ringing},flush=True)
            last_guard=now
        if now-last_autonomy_guard>=60:
            autonomy=telephony_autonomy_guardian(100)
            if autonomy.get('recovered') or autonomy.get('unresolved'):
                print('telephony_autonomy_guardian',autonomy,flush=True)
            last_autonomy_guard=now
        if now-last_own_calltracking_guard>=30:
            ctguard=own_calltracking_guardian(1000)
            if ctguard.get('expired_sessions') or ctguard.get('numbers_returned'):
                print('telephony_own_calltracking_guardian',ctguard,flush=True)
            last_own_calltracking_guard=now
        if now-last_mcn_pjsip_guard>=60:
            mcnguard=mcn_pjsip_guardian()
            if mcnguard.get('changed') or mcnguard.get('deferred') or mcnguard.get('error_code') or mcnguard.get('trunks_degraded'):
                print('telephony_mcn_pjsip_guardian',mcnguard,flush=True)
            last_mcn_pjsip_guard=now
        if now-last_telphin_sip_guard>=60:
            telguard=telphin_sip_guardian()
            if telguard.get('rendered',{}).get('configured') or telguard.get('unregistered') or telguard.get('rejected') or telguard.get('unknown') or telguard.get('apply',{}).get('status') not in {'not_needed','applied'}:
                print('telephony_telphin_sip_guardian',telguard,flush=True)
            last_telphin_sip_guard=now
        if now-last_storage_guard>=600:
            storage=recording_storage_guard()
            if storage.get('purged') or storage.get('emergency'):
                print('telephony_storage_guard',storage,flush=True)
            last_storage_guard=now
        if now-last_retention_guard>=3600:
            retention=telephony_operational_retention_guard()
            if any((retention.get('purged') or {}).values()):
                print('telephony_retention_guard',retention,flush=True)
            last_retention_guard=now
        time.sleep(0.75 if result.get('processed') else 1.5)
    except KeyboardInterrupt:
        break
    except Exception as exc:
        print('telephony_runtime_error',repr(exc)[:500],flush=True)
        traceback.print_exc()
        time.sleep(3)
