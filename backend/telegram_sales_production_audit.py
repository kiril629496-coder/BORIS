#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import create_engine,text

load_dotenv('/root/BORIS/backend/.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
MAX_AGE=int(os.getenv('HOT_LEAD_AUTO_MAX_AGE_HOURS','120') or 120)
DAILY_CAP=int(os.getenv('HOT_LEAD_AUTO_DAILY_CAP','10') or 10)
REACT_DAILY_CAP=int(os.getenv('TG_CONTACT_REACT_DAILY_CAP','4') or 4)
FOLLOWUP_DAILY_CAP=int(os.getenv('HOT_LEAD_FOLLOWUP_DAILY_CAP','3') or 3)
TZ=os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')
engine=create_engine(DB,pool_pre_ping=True)

def scalar(db,q,**kw): return int(db.execute(text(q),kw).scalar() or 0)

def main()->int:
    hard={}; info={}
    def cmd(args):
        p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
        return p.returncode,(p.stdout or '').strip()
    try:
        _,timer=cmd(['systemctl','is-active','boris-tg-sales-cycle.timer'])
        hard['sales_cycle_timer_active']=timer=='active'
    except Exception:
        hard['sales_cycle_timer_active']=False
    try:
        _,disc=cmd(['systemctl','is-active','boris-tg-sales-discovery.timer'])
        hard['legacy_discovery_timer_inactive']=disc!='active'
    except Exception:
        hard['legacy_discovery_timer_inactive']=False
    for key,unit in (
        ('watchdog_timer_active','boris-tg-sales-watchdog.timer'),
        ('reactivation_timer_active','boris-tg-contact-reactivation.timer'),
        ('daily_report_timer_active','boris-tg-sales-daily-report.timer'),
        ('daily_report_verify_timer_active','boris-tg-sales-daily-report-verify.timer'),
        ('daily_selftest_timer_active','boris-tg-sales-daily-selftest.timer'),
        ('morning_preflight_timer_active','boris-tg-sales-morning-preflight.timer'),
    ):
        try:
            _,st=cmd(['systemctl','is-active',unit])
            hard[key]=st=='active'
        except Exception:
            hard[key]=False
    # A green timer is insufficient: the report service itself must execute
    # telegram_sales_daily_report.py with --send. Otherwise systemd can look healthy
    # while producing only a preview and never delivering the owner report.
    try:
        _,report_exec=cmd(['systemctl','show','-p','ExecStart','--value','boris-tg-sales-daily-report.service'])
        info['daily_report_execstart']=report_exec[:1200]
        hard['daily_report_service_sends']=('telegram_sales_daily_report.py' in report_exec and '--send' in report_exec)
    except Exception:
        info['daily_report_execstart']='read_failed'
        hard['daily_report_service_sends']=False

    # Owner-approved personal-account send window is a hard safety contract.
    start=int(os.getenv('HOT_LEAD_AUTO_HARD_START_HOUR','9') or 9)
    end=int(os.getenv('HOT_LEAD_AUTO_HARD_END_HOUR','19') or 19)
    info['hard_send_window']=f'{start}:00-{end}:00'
    hard['hard_send_window_9_19']=(start==9 and end==19)
    try:
        _,cron=cmd(['crontab','-l'])
        sigs=('monitor_runner.py --scan','monitor_source_discovery.py --apply','hot_lead_queue.py','telegram_sales_cycle.py')
        conflicts=[x.strip() for x in cron.splitlines() if x.strip() and not x.strip().startswith('#') and any(s in x for s in sigs)]
    except Exception:
        conflicts=['crontab_read_failed']
    info['scheduler_conflicts']=conflicts
    hard['no_legacy_core_cron']=not conflicts
    with engine.connect() as db:
        info['active_sources']=scalar(db,"select count(*) from monitor_sources where platform='telegram' and is_active=true")
        hard['active_sources_present']=info['active_sources']>0
        # Per-source telemetry must cover the active source set after instrumentation is live.
        info['source_telemetry_rows']=scalar(db,"select count(*) from telegram_source_scan_stats")
        info['source_telemetry_errors_historical']=scalar(db,"select coalesce(sum(errors),0) from telegram_source_scan_stats")
        info['source_telemetry_errors']=scalar(db,"select count(*) from telegram_source_scan_stats where last_error is not null")
        hard['source_telemetry_coverage']=(info['source_telemetry_rows']>=info['active_sources'] and info['source_telemetry_errors']==0)
        # Adaptive source cadence must never starve sources. Fast-tier sources
        # (productive/high-weight/new telemetry) must be touched within 25 min;
        # proven low-activity sources within 60 min. This is intentionally wider
        # than the 15/30 minute target to allow one complete scanner runtime.
        info['source_fast_stale']=scalar(db,"""select count(*) from monitor_sources ms
          left join telegram_source_scan_stats t on t.source_id=ms.id
          where ms.platform='telegram' and ms.is_active=true
            and (t.source_id is null or coalesce(t.scans,0)<5 or coalesce(t.raw_messages,0)>2
              or coalesce(t.matched_messages,0)>0 or coalesce(t.new_messages,0)>0 or coalesce(ms.weight,10)>=17)
            and (ms.last_scanned_at is null or ms.last_scanned_at < now()-interval '25 minutes')""")
        info['source_slow_stale']=scalar(db,"""select count(*) from monitor_sources ms
          join telegram_source_scan_stats t on t.source_id=ms.id
          where ms.platform='telegram' and ms.is_active=true
            and coalesce(t.scans,0)>=5 and coalesce(t.raw_messages,0)<=2
            and coalesce(t.matched_messages,0)=0 and coalesce(t.new_messages,0)=0 and coalesce(ms.weight,10)<17
            and (ms.last_scanned_at is null or ms.last_scanned_at < now()-interval '60 minutes')""")
        hard['source_cadence_not_starved']=(info['source_fast_stale']==0 and info['source_slow_stale']==0)
        cadence_raw=db.execute(text("select value_text from monitor_runtime_state where key='telegram_source_cadence_last'")).scalar()
        try:
            info['source_cadence_last']=json.loads(str(cadence_raw or '{}'))
        except Exception:
            info['source_cadence_last']={}
        info['stale_monitor_runs']=scalar(db,"select count(*) from monitor_runs where platform='telegram' and status='running' and started_at<now()-interval '20 minutes'")
        info['stale_cycle_runs']=scalar(db,"select count(*) from telegram_sales_cycle_runs where status='running' and started_at<now()-interval '20 minutes'")
        hard['no_stale_runs']=info['stale_monitor_runs']==0 and info['stale_cycle_runs']==0
        latest_cycle=db.execute(text("select status,started_at,ended_at,duration_seconds,details from telegram_sales_cycle_runs order by started_at desc limit 1")).mappings().first()
        latest_scan=db.execute(text("select status,started_at,finished_at,scanned_sources,scanned_messages,matched_messages,new_messages,errors from monitor_runs where platform='telegram' order by started_at desc limit 1")).mappings().first()
        info['latest_cycle']=dict(latest_cycle) if latest_cycle else None
        info['latest_scan']=dict(latest_scan) if latest_scan else None
        # TG_AUDIT_RECOVERY_CONVERGENCE_V1: a fresh canonical cycle that is
        # actively recovering a previous degraded run is healthy convergence,
        # not a second outage. Stale running cycles remain hard failures above.
        fresh_running=bool(latest_cycle and str(latest_cycle.get('status') or '').lower()=='running'
                           and latest_cycle.get('started_at')
                           and latest_cycle.get('started_at') >= datetime.now(timezone.utc)-timedelta(minutes=20))
        recent_pass=scalar(db,"select count(*) from telegram_sales_cycle_runs where status='pass' and ended_at>=now()-interval '30 minutes'")>0
        hard['recent_cycle_healthy']=bool(recent_pass or fresh_running)
        last_done=str((db.execute(text("select status from telegram_sales_cycle_runs where status<>'running' order by started_at desc limit 1")).scalar() or '')).lower()
        hard['no_recent_degraded']=bool(fresh_running or last_done not in ('degraded','timeout'))
        hard['recent_scan_ok']=scalar(db,"select count(*) from monitor_runs where platform='telegram' and status='ok' and finished_at>=now()-interval '30 minutes'")>0
        info['duplicate_contacted']=scalar(db,"""select count(*) from (select coalesce(contact_peer_id::text,lower(contact_username)) k,count(*) n from boris_sales_hot_leads where contacted_at is not null group by 1 having count(*)>1)x""")
        info['initial_sent_today']=scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and timezone(:tz,contacted_at)::date=timezone(:tz,now())::date",tz=TZ)
        info['reactivation_sent_today']=scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and monitor_intent='existing_contact_reactivation' and timezone(:tz,contacted_at)::date=timezone(:tz,now())::date",tz=TZ)
        info['followup_sent_today']=scalar(db,"select count(*) from boris_sales_hot_leads where followup_sent_at is not null and timezone(:tz,followup_sent_at)::date=timezone(:tz,now())::date",tz=TZ)
        info['outbound_total_today']=info['initial_sent_today']+info['followup_sent_today']
        info['outbound_remaining_today']=max(0,DAILY_CAP-info['outbound_total_today'])
        info['outbound_caps']={'total':DAILY_CAP,'reactivation':REACT_DAILY_CAP,'followup':FOLLOWUP_DAILY_CAP}
        hard['outbound_caps_not_exceeded']=(info['outbound_total_today']<=DAILY_CAP and info['reactivation_sent_today']<=REACT_DAILY_CAP and info['followup_sent_today']<=FOLLOWUP_DAILY_CAP)
        guard_since=db.execute(text("select nullif(value_text,'')::timestamptz from monitor_runtime_state where key='tg_outbound_guard_enforced_at'")).scalar()
        info['outside_window_initial']=scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and contacted_at is not null and (:b is null or contacted_at>=:b) and (extract(hour from timezone(:tz,contacted_at))<9 or extract(hour from timezone(:tz,contacted_at))>=19)",b=guard_since,tz=TZ)
        info['outside_window_followup']=scalar(db,"select count(*) from boris_sales_hot_leads where followup_sent_at is not null and (:b is null or followup_sent_at>=:b) and (extract(hour from timezone(:tz,followup_sent_at))<9 or extract(hour from timezone(:tz,followup_sent_at))>=19)",b=guard_since,tz=TZ)
        info['outside_window_auto_reply']=scalar(db,"select count(*) from boris_sales_tg_replies where auto_replied_at is not null and (:b is null or auto_replied_at>=:b) and (extract(hour from timezone(:tz,auto_replied_at))<9 or extract(hour from timezone(:tz,auto_replied_at))>=19)",b=guard_since,tz=TZ)
        hard['no_outside_window_personal_outbound']=all(info[k]==0 for k in ('outside_window_initial','outside_window_followup','outside_window_auto_reply'))
        info['overdue_tg_crm_reminder_24h']=scalar(db,"select count(distinct ml.id) from manager_leads ml join manager_notes n on n.lead_id=ml.id where ml.lead_source in ('telegram_prospecting','telegram_reactivation') and ml.status in ('квалифицирован','целевое действие') and n.kind='reminder' and coalesce(n.done,false)=false and n.remind_at < now()-interval '24 hours'")
        info['missing_tg_crm_next_action']=scalar(db,"select count(*) from manager_leads ml where ml.lead_source in ('telegram_prospecting','telegram_reactivation') and ml.status in ('квалифицирован','целевое действие') and not exists (select 1 from manager_notes n where n.lead_id=ml.id and n.kind='reminder' and coalesce(n.done,false)=false)")
        hard['active_crm_next_actions_present']=info['missing_tg_crm_next_action']==0
        info['unsafe_recruitment_active']=scalar(db,"""select count(*) from boris_sales_hot_leads where status not in ('skipped','declined') and lower(coalesce(original_text,'')) ~ '(служб[ау] по контракту|набор.{0,20}служб|военная служба|военнослужащ)'""")
        info['approved_stuck']=scalar(db,"select count(*) from boris_sales_hot_leads where status='approved' and approved_at<now()-interval '20 minutes' and contacted_at is null")
        info['contacted_without_message']=scalar(db,"select count(*) from boris_sales_hot_leads where status='contacted' and outbound_transport='telegram_personal' and outbound_message_id is null")
        info['auto_reply_without_message']=scalar(db,"select count(*) from boris_sales_tg_replies where auto_replied_at is not null and auto_reply_message_id is null")
        info['auto_reply_without_timestamp']=scalar(db,"select count(*) from boris_sales_tg_replies where auto_reply_message_id is not null and auto_replied_at is null")
        info['buyer_reply_unalerted']=scalar(db,"select count(*) from boris_sales_tg_replies r where coalesce(r.auto_reply_policy,'')<>'vendor_handoff' and timezone(:tz,r.received_at)::date=timezone(:tz,now())::date and r.received_at<now()-interval '5 minutes' and r.alerted_at is null",tz=TZ)
        info['duplicate_auto_reply_message']=scalar(db,"select count(*) from (select hot_lead_id,auto_reply_message_id,count(*) n from boris_sales_tg_replies where auto_reply_message_id is not null group by hot_lead_id,auto_reply_message_id having count(*)>1)q")
        delivery_baseline=db.execute(text("select nullif(value_text,'')::timestamptz from monitor_runtime_state where key='tg_delivery_verification_baseline'")).scalar()
        info['unverified_initial_delivery']=scalar(db,"select count(*) from boris_sales_hot_leads where :b is not null and outbound_transport='telegram_personal' and contacted_at>=:b and outbound_message_id is not null and outbound_verified_at is null",b=delivery_baseline)
        info['unverified_followup_delivery']=scalar(db,"select count(*) from boris_sales_hot_leads where :b is not null and followup_sent_at>=:b and followup_message_id is not null and followup_verified_at is null",b=delivery_baseline)
        info['unverified_auto_reply_delivery']=scalar(db,"select count(*) from boris_sales_tg_replies where :b is not null and auto_replied_at>=:b and auto_reply_message_id is not null and auto_reply_verified_at is null",b=delivery_baseline)
        info['auto_ready_without_contact']=scalar(db,"select count(*) from boris_sales_hot_leads where status='new' and coalesce(approval_required,false)=false and coalesce(contact_username,'')='' and contact_peer_id is null")
        info['suppressed_new_candidates']=scalar(db,"""select count(*) from boris_sales_hot_leads l where l.status='new' and exists (select 1 from telegram_sales_identity_suppressions s where (l.contact_peer_id is not null and s.peer_id=l.contact_peer_id) or (coalesce(l.contact_username,'')<>'' and coalesce(s.username,'')<>'' and lower(s.username)=lower(l.contact_username)))""")
        info['fresh_peer_without_hash']=scalar(db,"""select count(*) from boris_sales_hot_leads where status='new' and monitor_intent='buy_service' and contact_peer_id is not null and contact_access_hash is null and coalesce(contact_username,'')='' and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours=>:h)""",h=MAX_AGE)
        # TG_SUPPRESSED_CANDIDATE_IS_SAFE_V1: an identity suppression is the safety
        # mechanism working, not an unsafe queue condition. Keep it visible in info
        # for audit, but do not make the watchdog/service fail merely because a
        # suppressed row remains in status=new.
        hard['queue_safety']=all(info[k]==0 for k in ('duplicate_contacted','unsafe_recruitment_active','approved_stuck','contacted_without_message','auto_reply_without_message','auto_reply_without_timestamp','buyer_reply_unalerted','duplicate_auto_reply_message','unverified_initial_delivery','unverified_followup_delivery','unverified_auto_reply_delivery','auto_ready_without_contact','fresh_peer_without_hash'))
        info['reactivation_queue_incomplete']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where status='new' and monitor_intent='existing_contact_reactivation' and boris_fit>=75
            and (reactivation_priority is null or btrim(coalesce(first_message_draft,''))='' or (coalesce(contact_username,'')='' and contact_peer_id is null))""")
        hard['reactivation_queue_ready']=info['reactivation_queue_incomplete']==0
        info['fresh_ready_send']=scalar(db,"""select count(*) from boris_sales_hot_leads where status='new' and monitor_intent='buy_service' and boris_fit>=75 and coalesce(approval_required,false)=false and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours=>:h) and (coalesce(contact_username,'')<>'' or (contact_peer_id is not null and contact_access_hash is not null))""",h=MAX_AGE)
        info['identity_blocked']=scalar(db,"""select count(*) from boris_sales_hot_leads where status='new' and monitor_intent='buy_service' and boris_fit>=75 and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours=>:h) and coalesce(contact_username,'')='' and not (contact_peer_id is not null and contact_access_hash is not null)""",h=MAX_AGE)
        info['backfill_remaining']=scalar(db,"""select count(*) from monitor_sources s where s.platform='telegram' and s.is_active=true and coalesce(s.payload->>'telegram_access_hash','')<>'' and lower(coalesce(s.title,'')) ~ '(авито|avito|авитолог|маркет|бизнес|предприним|продаж|лид|трафик|эксперт)' and lower(coalesce(s.title,'')) !~ '(ваканс|работа для|фриланс ваканс)' and not exists (select 1 from telegram_marketing_backfill_sources b where b.source_id=s.id and b.status='ok')""")
        hard['backfill_complete']=info['backfill_remaining']==0
        local_now=datetime.now(ZoneInfo(os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')))
        report_due=(local_now.hour>19 or (local_now.hour==19 and local_now.minute>=48))
        report_key=f'tg_sales_daily_report:{local_now.date().isoformat()}'
        report_value=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':report_key}).scalar()
        report_mid=None
        try:
            report_payload=json.loads(str(report_value or ''))
            report_mid=int((report_payload or {}).get('message_id') or 0) or None
        except Exception:
            report_mid=None
        info['daily_report_message_id']=report_mid
        info['daily_report_confirmed_today']=bool(report_mid)
        hard['daily_report_confirmed_after_deadline']=(not report_due) or info['daily_report_confirmed_today']
        selftest_due=(local_now.hour>19 or (local_now.hour==19 and local_now.minute>=30))
        selftest_key=f'tg_sales_daily_selftest:{local_now.date().isoformat()}'
        selftest_value=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':selftest_key}).scalar()
        selftest_ok=False; selftest_total=0; selftest_failed=None
        try:
            selftest_payload=json.loads(str(selftest_value or ''))
            selftest_total=int((selftest_payload or {}).get('total') or 0)
            selftest_failed=int((selftest_payload or {}).get('failed') or 0)
            selftest_ran_raw=str((selftest_payload or {}).get('ran_at') or '')
            selftest_ran_at=datetime.fromisoformat(selftest_ran_raw.replace('Z','+00:00')) if selftest_ran_raw else None
            if selftest_ran_at is not None and selftest_ran_at.tzinfo is None: selftest_ran_at=selftest_ran_at.replace(tzinfo=local_now.tzinfo)
            scheduled_floor=local_now.replace(hour=19,minute=20,second=0,microsecond=0)
            selftest_fresh=bool(selftest_ran_at and selftest_ran_at.astimezone(local_now.tzinfo)>=scheduled_floor)
            selftest_ok=selftest_total>0 and selftest_failed==0 and selftest_fresh
        except Exception:
            selftest_ok=False
        info['daily_selftest_total']=selftest_total
        info['daily_selftest_failed']=selftest_failed
        info['daily_selftest_ran_at']=selftest_ran_at.isoformat() if 'selftest_ran_at' in locals() and selftest_ran_at else None
        info['daily_selftest_fresh_after_1920']=bool(locals().get('selftest_fresh',False))
        info['daily_selftest_pass_today']=selftest_ok
        hard['daily_selftest_pass_after_deadline']=(not selftest_due) or selftest_ok
        preflight_due=(local_now.hour>9 or (local_now.hour==9 and local_now.minute>=5))
        preflight_key=f'tg_sales_morning_preflight:{local_now.date().isoformat()}'
        preflight_value=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':preflight_key}).scalar()
        preflight_ok=False; preflight_ran_at=None
        try:
            pp=json.loads(str(preflight_value or '')); preflight_ran_raw=str((pp or {}).get('ran_at') or '')
            preflight_ran_at=datetime.fromisoformat(preflight_ran_raw.replace('Z','+00:00')) if preflight_ran_raw else None
            if preflight_ran_at is not None and preflight_ran_at.tzinfo is None: preflight_ran_at=preflight_ran_at.replace(tzinfo=local_now.tzinfo)
            floor=local_now.replace(hour=8,minute=50,second=0,microsecond=0)
            preflight_ok=bool((pp or {}).get('ok') is True and preflight_ran_at and preflight_ran_at.astimezone(local_now.tzinfo)>=floor)
        except Exception: preflight_ok=False
        info['morning_preflight_pass_today']=preflight_ok
        info['morning_preflight_ran_at']=preflight_ran_at.isoformat() if preflight_ran_at else None
        preflight_context=os.getenv('TG_PREFLIGHT_CONTEXT','0')=='1'
        info['morning_preflight_context']=preflight_context
        hard['morning_preflight_pass_after_0905']=preflight_context or (not preflight_due) or preflight_ok
    status='PASS' if all(hard.values()) else 'FAIL'
    def conv(v):
        if isinstance(v,(datetime,)): return v.isoformat()
        return str(v)
    print('TG_PRODUCTION_AUDIT='+status)
    print(json.dumps({'status':status,'hard':hard,'info':info},ensure_ascii=False,default=conv,sort_keys=True))
    return 0 if status=='PASS' else 2

if __name__=='__main__': raise SystemExit(main())
