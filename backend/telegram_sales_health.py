#!/usr/bin/env python3
from __future__ import annotations
import os
import json
import subprocess
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
from app.monitoring.quality import classify as quality_classify
load_dotenv('/root/BORIS/backend/.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
TZ=os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')
MAX_AGE_HOURS=int(os.getenv('HOT_LEAD_AUTO_MAX_AGE_HOURS','120') or 120)
DAILY_CAP=int(os.getenv('HOT_LEAD_AUTO_DAILY_CAP','10') or 10)
REACT_DAILY_CAP=int(os.getenv('TG_CONTACT_REACT_DAILY_CAP','4') or 4)
FOLLOWUP_DAILY_CAP=int(os.getenv('HOT_LEAD_FOLLOWUP_DAILY_CAP','3') or 3)
engine=create_engine(DB,pool_pre_ping=True)

def scalar(db,q,**params):
    return int(db.execute(text(q),params).scalar() or 0)

def main()->int:
    # When health runs as the final stage of telegram_sales_cycle, requiring a
    # previously completed PASS creates a circular failure: after 30 minutes
    # without PASS the current healthy cycle can never become PASS. Standalone
    # watchdog/audit runs keep the historical recent-pass requirement.
    in_cycle = os.getenv('TG_SALES_HEALTH_IN_CYCLE','0') == '1'
    with engine.connect() as db:
        m={}
        guard_since=db.execute(text("select nullif(value_text,'')::timestamptz from monitor_runtime_state where key='tg_outbound_guard_enforced_at'" )).scalar()
        m['active_sources']=scalar(db,"select count(*) from monitor_sources where platform='telegram' and is_active=true")
        m['fresh_buy_24h']=scalar(db,"select count(*) from boris_sales_hot_leads where monitor_intent='buy_service' and status not in ('skipped','declined','lost','ignored') and COALESCE(source_posted_at,discovered_at)>=now()-interval '24 hours'")
        m['fresh_buy_5d']=scalar(db,"select count(*) from boris_sales_hot_leads where monitor_intent='buy_service' and status not in ('skipped','declined','lost','ignored') and COALESCE(source_posted_at,discovered_at)>=now()-interval '5 days'")
        m['fresh_ready_send']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where monitor_intent='buy_service' and status='new' and boris_fit>=75
            and coalesce(approval_required,false)=false
            and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours => :max_age)
            and ((coalesce(contact_username,'')<>'') or (contact_peer_id is not null and contact_access_hash is not null))""",max_age=MAX_AGE_HOURS)
        m['fresh_identity_blocked']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where monitor_intent='buy_service' and status='new' and boris_fit>=75
            and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours => :max_age)
            and coalesce(contact_username,'')='' and not (contact_peer_id is not null and contact_access_hash is not null)""",max_age=MAX_AGE_HOURS)
        m['reactivation_ready']=scalar(db,"select count(*) from boris_sales_hot_leads where status='new' and monitor_intent='existing_contact_reactivation' and boris_fit>=75")
        m['sent_today']=scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and contacted_at is not null and timezone(:tz,contacted_at)::date=timezone(:tz,now())::date",tz=TZ)
        m['reactivation_sent_today']=scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and monitor_intent='existing_contact_reactivation' and contacted_at is not null and timezone(:tz,contacted_at)::date=timezone(:tz,now())::date",tz=TZ)
        m['followup_sent_today']=scalar(db,"select count(*) from boris_sales_hot_leads where followup_sent_at is not null and timezone(:tz,followup_sent_at)::date=timezone(:tz,now())::date",tz=TZ)
        m['outbound_total_today']=m['sent_today']+m['followup_sent_today']
        m['outbound_remaining_today']=max(0,DAILY_CAP-m['outbound_total_today'])
        m['daily_cap']=DAILY_CAP
        m['reactivation_daily_cap']=REACT_DAILY_CAP
        m['followup_daily_cap']=FOLLOWUP_DAILY_CAP
        m['outbound_cap_violation']=int(m['outbound_total_today']>DAILY_CAP or m['reactivation_sent_today']>REACT_DAILY_CAP or m['followup_sent_today']>FOLLOWUP_DAILY_CAP)
        m['replies_today']=scalar(db,"select count(*) from boris_sales_tg_replies where received_at is not null and timezone(:tz,received_at)::date=timezone(:tz,now())::date",tz=TZ)
        m['invalidated_sent_today']=scalar(db,"""select count(*) from boris_sales_hot_leads l where l.outbound_transport='telegram_personal' and timezone(:tz,l.contacted_at)::date=timezone(:tz,now())::date and (coalesce(l.approval_note,'') in ('telegram_publisher_redirect_suppressed','telegram_vendor_reply','suppressed_identity') or exists(select 1 from boris_sales_tg_replies r where r.hot_lead_id=l.id and r.auto_reply_policy='vendor_handoff'))""",tz=TZ)
        m['valid_sent_today']=max(0,m['sent_today']-m['invalidated_sent_today'])
        m['vendor_replies_today']=scalar(db,"select count(*) from boris_sales_tg_replies where received_at is not null and timezone(:tz,received_at)::date=timezone(:tz,now())::date and auto_reply_policy='vendor_handoff'",tz=TZ)
        m['buyer_reply_messages_today']=scalar(db,"select count(*) from boris_sales_tg_replies where received_at is not null and timezone(:tz,received_at)::date=timezone(:tz,now())::date and coalesce(auto_reply_policy,'')<>'vendor_handoff'",tz=TZ)
        m['buyer_replies_today']=scalar(db,"select count(distinct hot_lead_id) from boris_sales_tg_replies where received_at is not null and timezone(:tz,received_at)::date=timezone(:tz,now())::date and coalesce(auto_reply_policy,'')<>'vendor_handoff'",tz=TZ)
        m['buyer_reply_unalerted']=scalar(db,"select count(*) from boris_sales_tg_replies r where coalesce(r.auto_reply_policy,'')<>'vendor_handoff' and timezone(:tz,r.received_at)::date=timezone(:tz,now())::date and r.received_at<now()-interval '5 minutes' and r.alerted_at is null",tz=TZ)
        m['qualified_total']=scalar(db,"select count(*) from boris_sales_hot_leads where status='qualified'")
        m['declined_total']=scalar(db,"select count(*) from boris_sales_hot_leads where status='declined'")
        m['crm_tg_total']=scalar(db,"select count(*) from manager_leads where lead_source in ('telegram_prospecting','telegram_reactivation')")
        m['overdue_tg_crm_reminder_24h']=scalar(db,"select count(distinct ml.id) from manager_leads ml join manager_notes n on n.lead_id=ml.id where ml.lead_source in ('telegram_prospecting','telegram_reactivation') and ml.status in ('квалифицирован','целевое действие') and n.kind='reminder' and coalesce(n.done,false)=false and n.remind_at < now()-interval '24 hours'")
        m['missing_tg_crm_next_action']=scalar(db,"select count(*) from manager_leads ml where ml.lead_source in ('telegram_prospecting','telegram_reactivation') and ml.status in ('квалифицирован','целевое действие') and not exists (select 1 from manager_notes n where n.lead_id=ml.id and n.kind='reminder' and coalesce(n.done,false)=false)")
        m['dialog_stuck_36h']=scalar(db,"""select count(*) from manager_leads ml
          where ml.lead_source in ('telegram_prospecting','telegram_reactivation') and ml.status='в диалоге'
            and ml.updated_at < now()-interval '36 hours'
            and not exists (
              select 1 from boris_sales_tg_replies r join boris_sales_hot_leads l on l.id=r.hot_lead_id
              where r.manager_lead_id=ml.id and (l.followup_sent_at is not null or r.auto_replied_at>=now()-interval '36 hours')
            )""")
        m['duplicate_contacted']=scalar(db,"""select count(*) from (
          select coalesce(contact_peer_id::text,lower(contact_username)) k,count(*) n
          from boris_sales_hot_leads where contacted_at is not null
          group by 1 having count(*)>1)q""")
        m['historical_outside_window_auto_reply']=scalar(db,"""select count(*) from boris_sales_tg_replies
          where auto_replied_at is not null
            and (extract(hour from timezone(:tz,auto_replied_at))<9 or extract(hour from timezone(:tz,auto_replied_at))>=19)""",tz=TZ)
        m['outside_window_initial']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where outbound_transport='telegram_personal' and contacted_at is not null and contacted_at>=:since
            and (extract(hour from timezone(:tz,contacted_at))<9 or extract(hour from timezone(:tz,contacted_at))>=19)""",tz=TZ,since=guard_since)
        m['outside_window_followup']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where followup_sent_at is not null and followup_sent_at>=:since
            and (extract(hour from timezone(:tz,followup_sent_at))<9 or extract(hour from timezone(:tz,followup_sent_at))>=19)""",tz=TZ,since=guard_since)
        m['outside_window_auto_reply']=scalar(db,"""select count(*) from boris_sales_tg_replies
          where auto_replied_at is not null and auto_replied_at>=:since
            and (extract(hour from timezone(:tz,auto_replied_at))<9 or extract(hour from timezone(:tz,auto_replied_at))>=19)""",tz=TZ,since=guard_since)
        m['vendor_reply_in_crm']=scalar(db,"""select count(*) from boris_sales_tg_replies r join manager_leads ml on ml.id=r.manager_lead_id where r.auto_reply_policy='vendor_handoff' and r.manager_lead_id is not null and lower(coalesce(ml.status,'')) not in ('отказ','закрыт','закрыто','нецелевой','lost')""")
        m['unsafe_recruitment_active']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where status not in ('skipped','declined') and lower(coalesce(original_text,'')) ~ '(служб[ау] по контракту|набор.{0,20}служб|военная служба|военнослужащ)'""")
        m['later_without_reminder']=scalar(db,"""select count(*) from boris_sales_hot_leads l
          where l.status='later' and l.later_at is not null and not exists (
            select 1 from manager_leads ml join manager_notes n on n.lead_id=ml.id
            where n.kind='reminder' and coalesce(n.done,false)=false
              and ((l.contact_peer_id is not null and ml.telegram_peer_id=l.contact_peer_id)
                or (coalesce(l.contact_username,'')<>'' and lower(coalesce(ml.telegram_username,''))=lower(l.contact_username))))""")
        m['reply_without_crm']=scalar(db,"""select count(*) from boris_sales_tg_replies r
          join boris_sales_hot_leads l on l.id=r.hot_lead_id
          where coalesce(r.auto_reply_policy,'')<>'vendor_handoff' and l.status<>'declined' and not exists (
            select 1 from manager_leads ml
            where ml.lead_source in ('telegram_prospecting','telegram_reactivation')
              and ((l.contact_peer_id is not null and ml.telegram_peer_id=l.contact_peer_id)
                or (coalesce(l.contact_username,'')<>'' and lower(coalesce(ml.telegram_username,''))=lower(l.contact_username))))""")
        m['stale_monitor_runs']=scalar(db,"select count(*) from monitor_runs where status='running' and started_at<now()-interval '20 minutes'")
        m['stale_cycle_runs']=scalar(db,"select count(*) from telegram_sales_cycle_runs where status='running' and started_at<now()-interval '20 minutes'")
        m['recent_scanner_ok']=scalar(db,"select count(*) from monitor_runs where status='ok' and finished_at>=now()-interval '30 minutes'")
        try:
            cp=subprocess.run(['crontab','-l'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
            sigs=('monitor_runner.py --scan','monitor_source_discovery.py --apply','hot_lead_queue.py','telegram_sales_cycle.py')
            m['scheduler_conflicts']=sum(1 for raw in (cp.stdout or '').splitlines() if raw.strip() and not raw.strip().startswith('#') and any(x in raw for x in sigs))
            dp=subprocess.run(['systemctl','is-active','boris-tg-sales-discovery.timer'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
            m['legacy_discovery_timer_active']=1 if (dp.stdout or '').strip()=='active' else 0
        except Exception:
            m['scheduler_conflicts']=1
            m['legacy_discovery_timer_active']=1
        m['recent_cycle_pass']=scalar(db,"select count(*) from telegram_sales_cycle_runs where status='pass' and ended_at>=now()-interval '30 minutes'")
        m['recent_cycle_degraded']=scalar(db,"select count(*) from telegram_sales_cycle_runs where status in ('degraded','timeout') and started_at>=now()-interval '30 minutes'")
        rows=db.execute(text("""select original_text,monitor_score from boris_sales_hot_leads
          where status='new' and monitor_intent='buy_service' and boris_fit>=75
            and COALESCE(source_posted_at,discovered_at)>=now()-interval '5 days'""")).fetchall()
        bad_quality=0
        for body,score in rows:
            q=quality_classify(body or '','buy_service',int(score or 0))
            if q.get('reject') or not q.get('qualified'):
                bad_quality+=1
        m['fresh_new_bad_quality']=bad_quality
        m['auto_ready_without_contact']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where status='new' and coalesce(approval_required,false)=false
            and coalesce(contact_username,'')='' and contact_peer_id is null""")
        m['contacted_without_message']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where status='contacted' and outbound_transport='telegram_personal' and outbound_message_id is null""")
        m['auto_reply_without_message']=scalar(db,"""select count(*) from boris_sales_tg_replies
          where auto_replied_at is not null and auto_reply_message_id is null""")
        m['auto_reply_without_timestamp']=scalar(db,"""select count(*) from boris_sales_tg_replies
          where auto_reply_message_id is not null and auto_replied_at is null""")
        m['duplicate_auto_reply_message']=scalar(db,"""select count(*) from (
          select hot_lead_id,auto_reply_message_id,count(*) n from boris_sales_tg_replies
          where auto_reply_message_id is not null group by hot_lead_id,auto_reply_message_id having count(*)>1
        )q""")
        delivery_baseline=db.execute(text("select nullif(value_text,'')::timestamptz from monitor_runtime_state where key='tg_delivery_verification_baseline'")).scalar()
        m['unverified_initial_delivery']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where :b is not null and outbound_transport='telegram_personal' and contacted_at>=:b
            and outbound_message_id is not null and outbound_verified_at is null""",b=delivery_baseline)
        m['unverified_followup_delivery']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where :b is not null and followup_sent_at>=:b and followup_message_id is not null and followup_verified_at is null""",b=delivery_baseline)
        m['unverified_auto_reply_delivery']=scalar(db,"""select count(*) from boris_sales_tg_replies
          where :b is not null and auto_replied_at>=:b and auto_reply_message_id is not null and auto_reply_verified_at is null""",b=delivery_baseline)
        m['approved_stuck']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where status='approved' and approved_at<now()-interval '20 minutes' and contacted_at is null""")
        m['suppressed_new_candidates']=scalar(db,"""select count(*) from boris_sales_hot_leads l where l.status='new' and exists (select 1 from telegram_sales_identity_suppressions s where (l.contact_peer_id is not null and s.peer_id=l.contact_peer_id) or (coalesce(l.contact_username,'')<>'' and coalesce(s.username,'')<>'' and lower(s.username)=lower(l.contact_username)))""")
        m['fresh_peer_without_hash']=scalar(db,"""select count(*) from boris_sales_hot_leads
          where status='new' and monitor_intent='buy_service' and contact_peer_id is not null
            and contact_access_hash is null and coalesce(contact_username,'')=''
            and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours=>:max_age)""",max_age=MAX_AGE_HOURS)
    print('TG_SALES_HEALTH ' + ' '.join(f'{k}={v}' for k,v in m.items()))
    # TG_HEALTH_CURRENT_CYCLE_SEMANTICS_V1: while the canonical cycle is
    # executing, a fresh successful scanner plus zero hard safety violations is
    # valid current evidence. Do not require a historical PASS from a previous
    # cycle to allow the current one to become the first recovered PASS.
    guards=(m['active_sources']>0 and m['duplicate_contacted']==0 and m['outside_window_initial']==0 and m['outside_window_followup']==0 and m['outside_window_auto_reply']==0 and m['reply_without_crm']==0 and m['buyer_reply_unalerted']==0 and m['vendor_reply_in_crm']==0 and m['later_without_reminder']==0 and m['unsafe_recruitment_active']==0 and m['stale_monitor_runs']==0 and m['stale_cycle_runs']==0 and m['fresh_new_bad_quality']==0 and m['recent_scanner_ok']>0 and (in_cycle or m['recent_cycle_pass']>0) and m['scheduler_conflicts']==0 and m['auto_ready_without_contact']==0 and m['contacted_without_message']==0 and m['auto_reply_without_message']==0 and m['auto_reply_without_timestamp']==0 and m['duplicate_auto_reply_message']==0 and m['unverified_initial_delivery']==0 and m['unverified_followup_delivery']==0 and m['unverified_auto_reply_delivery']==0 and m['approved_stuck']==0 and m['fresh_peer_without_hash']==0 and m['legacy_discovery_timer_active']==0 and m['outbound_cap_violation']==0 and m['missing_tg_crm_next_action']==0)
    print('TG_SALES_GUARDS=' + ('PASS' if guards else 'FAIL'))
    if not guards:
        bad={k:v for k,v in m.items() if k in {
            'duplicate_contacted','outside_window_initial','outside_window_followup','outside_window_auto_reply',
            'vendor_reply_in_crm','later_without_reminder','reply_without_crm','buyer_reply_unalerted','unsafe_recruitment_active','stale_monitor_runs','stale_cycle_runs','fresh_new_bad_quality','recent_cycle_degraded','scheduler_conflicts','auto_ready_without_contact','contacted_without_message','auto_reply_without_message','auto_reply_without_timestamp','duplicate_auto_reply_message','unverified_initial_delivery','unverified_followup_delivery','unverified_auto_reply_delivery','approved_stuck','fresh_peer_without_hash','legacy_discovery_timer_active','outbound_cap_violation','missing_tg_crm_next_action'} and v}
        if m.get('recent_scanner_ok',0)==0: bad['recent_scanner_ok']=0
        if m.get('recent_cycle_pass',0)==0: bad['recent_cycle_pass']=0
        signature=json.dumps(bad,sort_keys=True,ensure_ascii=False)
        should_alert=False
        with engine.begin() as db:
            prev=db.execute(text("select value_text from monitor_runtime_state where key='tg_sales_health_last_alert'" )).scalar()
            if prev!=signature:
                db.execute(text("""insert into monitor_runtime_state(key,value_text,updated_at) values('tg_sales_health_last_alert',:v,now())
                  on conflict(key) do update set value_text=excluded.value_text,updated_at=now()"""),{'v':signature})
                should_alert=True
        if should_alert:
            try:
                from app.telegram_bot import send_telegram_message
                labels={
                    'duplicate_contacted':'Есть повторное сообщение одному и тому же контакту',
                    'outside_window_initial':'Первичное сообщение отправлено вне разрешённого времени',
                    'outside_window_followup':'Повторное сообщение отправлено вне разрешённого времени',
                    'outside_window_auto_reply':'Автоответ отправлен вне разрешённого времени',
                    'vendor_reply_in_crm':'Служебный/нецелевой ответ ошибочно остался активным в CRM',
                    'later_without_reminder':'Контакт отложен «на потом», но напоминание не создано',
                    'reply_without_crm':'Получен ответ, но карточка лида в CRM не создана',
                    'unsafe_recruitment_active':'Обнаружена запрещённая для автообработки тематика',
                    'stale_monitor_runs':'Поиск заявок завис и не завершился вовремя',
                    'stale_cycle_runs':'Цикл Telegram-продаж завис и не завершился вовремя',
                    'fresh_new_bad_quality':'В очередь попали заявки, не прошедшие проверку качества',
                    'recent_cycle_degraded':'Последний цикл Telegram-продаж завершился с ошибкой/таймаутом',
                    'scheduler_conflicts':'Одновременно работают конфликтующие планировщики',
                    'auto_ready_without_contact':'Заявка готова к отправке, но контакт получателя не определён',
                    'contacted_without_message':'Лид отмечен как обработанный, но ID отправленного сообщения отсутствует',
                    'auto_reply_without_message':'Автоответ отмечен отправленным, но Telegram message ID отсутствует',
                    'auto_reply_without_timestamp':'Есть Telegram message ID автоответа, но нет времени подтверждённой отправки',
                    'duplicate_auto_reply_message':'Один Telegram auto-reply message ID записан повторно для одного лида',
                    'unverified_initial_delivery':'Первичное сообщение не подтверждено чтением из целевого Telegram-диалога',
                    'unverified_followup_delivery':'Повторное сообщение не подтверждено чтением из целевого Telegram-диалога',
                    'unverified_auto_reply_delivery':'Автоответ не подтвержден чтением из целевого Telegram-диалога',
                    'approved_stuck':'Одобренная заявка слишком долго не отправляется',
                    'fresh_peer_without_hash':'Есть Telegram-контакт, но не хватает данных для безопасной отправки',
                    'suppressed_new_candidates':'В новой очереди остались контакты, которые должны быть исключены',
                    'legacy_discovery_timer_active':'Работает старый конфликтующий Telegram-планировщик',
                    'outbound_cap_violation':'Превышен дневной лимит Telegram-исходящих, реактиваций или follow-up',
                    'missing_tg_crm_next_action':'У активного квалифицированного Telegram-лида отсутствует следующая задача',
                    'recent_scanner_ok':'За последние 30 минут не было успешного поиска новых заявок',
                    'recent_cycle_pass':'За последние 30 минут не было успешного цикла Telegram-продаж',
                }
                lines=[]
                for k,v in bad.items():
                    label=labels.get(k,k)
                    lines.append(f'• {label}: {v}')
                send_telegram_message(os.getenv('HOT_LEAD_REPLY_CHAT_ID','-1003952038222'),
                    '🚨 <b>BORIS — Telegram-лидогенерация требует автопочинки</b>\n\n'+
                    '\n'.join(lines)+
                    '\n\nBORIS блокирует небезопасные действия и сам повторяет проверку. От вас действий не требуется.',
                    int(os.getenv('HOT_LEAD_REPLY_THREAD_ID','126') or 126))
                print('TG_SALES_HEALTH_ALERT=PASS')
            except Exception as e:
                print(f'TG_SALES_HEALTH_ALERT=DEGRADED type={type(e).__name__}')
    if guards:
        with engine.begin() as db:
            db.execute(text("insert into monitor_runtime_state(key,value_text,updated_at) values('tg_sales_health_last_alert','',now()) on conflict(key) do update set value_text='',updated_at=now()"))
    # Lack of fresh public demand is a commercial warning, not a runtime failure.
    print('TG_FRESH_DEMAND=' + ('PASS' if m['fresh_buy_24h']>0 else 'WARN_NO_NEW_24H'))
    print('TG_FRESH_SENDABLE=' + ('PASS' if m.get('fresh_ready_send',0)>0 else 'WARN_NO_SENDABLE_FRESH'))
    return 0 if guards else 2

if __name__=='__main__':
    raise SystemExit(main())
