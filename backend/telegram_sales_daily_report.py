#!/usr/bin/env python3
from __future__ import annotations
import os
import argparse
import subprocess
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
from app.telegram_bot import send_telegram_message
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
TZ=os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')
MAX_AGE_HOURS=int(os.getenv('HOT_LEAD_AUTO_MAX_AGE_HOURS','120') or 120)
DAILY_CAP=int(os.getenv('HOT_LEAD_AUTO_DAILY_CAP','10') or 10)
REACT_DAILY_CAP=int(os.getenv('TG_CONTACT_REACT_DAILY_CAP','4') or 4)
FOLLOWUP_DAILY_CAP=int(os.getenv('HOT_LEAD_FOLLOWUP_DAILY_CAP','3') or 3)
CHAT=os.getenv('HOT_LEAD_REPLY_CHAT_ID','-1003952038222')
THREAD=int(os.getenv('HOT_LEAD_REPLY_THREAD_ID','126') or 126)
engine=create_engine(DB,pool_pre_ping=True)

def scalar(db,q,**kw): return int(db.execute(text(q),kw).scalar() or 0)

def _receipt_message_id(value: str | None) -> int | None:
    try:
        data=json.loads(str(value or ''))
        mid=int((data or {}).get('message_id') or 0)
        return mid if mid>0 else None
    except Exception:
        return None

def main(send: bool=False, force: bool=False)->int:
    now_local=datetime.now(ZoneInfo(TZ))
    day=now_local.date().isoformat()
    report_hour=int(os.getenv('TG_SALES_DAILY_REPORT_HOUR','19') or 19)
    report_minute=int(os.getenv('TG_SALES_DAILY_REPORT_MINUTE','38') or 35)
    before_window=(now_local.hour<report_hour or (now_local.hour==report_hour and now_local.minute<report_minute))
    if send and not force and before_window:
        print(f'TG_DAILY_REPORT=SKIP before_window now={now_local.strftime("%H:%M")} earliest={report_hour:02d}:{report_minute:02d} {TZ}')
        return 0
    with engine.begin() as db:
        db.execute(text("CREATE TABLE IF NOT EXISTS monitor_runtime_state (key TEXT PRIMARY KEY,value_text TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
        key=f'tg_sales_daily_report:{day}'
        existing_receipt=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':key}).scalar()
        if send and _receipt_message_id(existing_receipt):
            print(f'TG_DAILY_REPORT=SKIP already_confirmed day={day} message_id={_receipt_message_id(existing_receipt)}'); return 0
        vals={
          'sources':scalar(db,"select count(*) from monitor_sources where platform='telegram' and is_active=true"),
          'scans':scalar(db,"select count(*) from monitor_runs where platform='telegram' and status<>'running' and timezone(:tz,started_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'scan_ok':scalar(db,"select count(*) from monitor_runs where platform='telegram' and status='ok' and timezone(:tz,started_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'scan_running':scalar(db,"select count(*) from monitor_runs where platform='telegram' and status='running' and timezone(:tz,started_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'fresh':scalar(db,"select count(*) from boris_sales_hot_leads where monitor_intent='buy_service' and status not in ('skipped','declined','lost','ignored') and timezone(:tz,COALESCE(source_posted_at,discovered_at))::date=CAST(:d AS date)",tz=TZ,d=day),
          'fresh_ready':scalar(db,"""select count(*) from boris_sales_hot_leads where monitor_intent='buy_service'
            and status='new' and boris_fit>=75 and coalesce(approval_required,false)=false
            and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours => :max_age)
            and (coalesce(contact_username,'')<>'' or (contact_peer_id is not null and contact_access_hash is not null))""",max_age=MAX_AGE_HOURS),
          'identity_blocked':scalar(db,"""select count(*) from boris_sales_hot_leads where monitor_intent='buy_service'
            and status='new' and boris_fit>=75 and coalesce(source_posted_at,discovered_at)>=now()-make_interval(hours => :max_age)
            and coalesce(contact_username,'')='' and not (contact_peer_id is not null and contact_access_hash is not null)""",max_age=MAX_AGE_HOURS),
          'sent':scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and timezone(:tz,contacted_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'react_sent':scalar(db,"select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and monitor_intent='existing_contact_reactivation' and timezone(:tz,contacted_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'followups_sent':scalar(db,"select count(*) from boris_sales_hot_leads where timezone(:tz,followup_sent_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'invalidated_followups':scalar(db,"""select count(*) from boris_sales_hot_leads l
            where timezone(:tz,l.followup_sent_at)::date=CAST(:d AS date)
              and coalesce(l.approval_note,'') in ('reactivation_existing_avitologist_guard','reactivation_followup_quality_guard','telegram_publisher_redirect_suppressed','telegram_vendor_reply','suppressed_identity')""",tz=TZ,d=day),
          'verified_initial':scalar(db,"""select count(*) from boris_sales_hot_leads l where l.outbound_transport='telegram_personal'
            and timezone(:tz,l.contacted_at)::date=CAST(:d AS date) and l.outbound_verified_at is not null
            and not (coalesce(l.approval_note,'') in ('telegram_publisher_redirect_suppressed','telegram_vendor_reply','suppressed_identity')
              or exists(select 1 from boris_sales_tg_replies r where r.hot_lead_id=l.id and r.auto_reply_policy='vendor_handoff'))""",tz=TZ,d=day),
          'verified_followups':scalar(db,"""select count(*) from boris_sales_hot_leads l where timezone(:tz,l.followup_sent_at)::date=CAST(:d AS date)
            and l.followup_verified_at is not null
            and coalesce(l.approval_note,'') not in ('reactivation_existing_avitologist_guard','reactivation_followup_quality_guard','telegram_publisher_redirect_suppressed','telegram_vendor_reply','suppressed_identity')""",tz=TZ,d=day),
          'auto_replies_sent':scalar(db,"select count(*) from boris_sales_tg_replies where timezone(:tz,auto_replied_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'auto_replies_verified':scalar(db,"select count(*) from boris_sales_tg_replies where timezone(:tz,auto_replied_at)::date=CAST(:d AS date) and auto_reply_verified_at is not null",tz=TZ,d=day),
          'invalidated_sent':scalar(db,"""select count(*) from boris_sales_hot_leads l where l.outbound_transport='telegram_personal'
            and timezone(:tz,l.contacted_at)::date=CAST(:d AS date)
            and (coalesce(l.approval_note,'') in ('telegram_publisher_redirect_suppressed','telegram_vendor_reply','suppressed_identity')
              or exists(select 1 from boris_sales_tg_replies r where r.hot_lead_id=l.id and r.auto_reply_policy='vendor_handoff'))""",tz=TZ,d=day),
          'replies':scalar(db,"select count(*) from boris_sales_tg_replies where timezone(:tz,received_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'vendor_replies':scalar(db,"select count(*) from boris_sales_tg_replies where timezone(:tz,received_at)::date=CAST(:d AS date) and auto_reply_policy='vendor_handoff'",tz=TZ,d=day),
          'buyer_reply_messages':scalar(db,"select count(*) from boris_sales_tg_replies where timezone(:tz,received_at)::date=CAST(:d AS date) and coalesce(auto_reply_policy,'')<>'vendor_handoff'",tz=TZ,d=day),
          'buyer_replies':scalar(db,"select count(distinct hot_lead_id) from boris_sales_tg_replies where timezone(:tz,received_at)::date=CAST(:d AS date) and coalesce(auto_reply_policy,'')<>'vendor_handoff'",tz=TZ,d=day),
          'initial_reply_leads':scalar(db,"""select count(distinct l.id) from boris_sales_hot_leads l join boris_sales_tg_replies r on r.hot_lead_id=l.id
            where l.outbound_transport='telegram_personal' and timezone(:tz,l.contacted_at)::date=CAST(:d AS date)
              and r.received_at>=l.contacted_at and timezone(:tz,r.received_at)::date=CAST(:d AS date)
              and coalesce(r.auto_reply_policy,'')<>'vendor_handoff'
              and not (coalesce(l.approval_note,'') in ('telegram_publisher_redirect_suppressed','telegram_vendor_reply','suppressed_identity') or exists(select 1 from boris_sales_tg_replies rv where rv.hot_lead_id=l.id and rv.auto_reply_policy='vendor_handoff'))""",tz=TZ,d=day),
          'followup_reply_leads':scalar(db,"""select count(distinct l.id) from boris_sales_hot_leads l join boris_sales_tg_replies r on r.hot_lead_id=l.id
            where l.followup_sent_at is not null and timezone(:tz,l.followup_sent_at)::date=CAST(:d AS date)
              and r.received_at>=l.followup_sent_at and timezone(:tz,r.received_at)::date=CAST(:d AS date)
              and coalesce(r.auto_reply_policy,'')<>'vendor_handoff'
              and coalesce(l.approval_note,'') not in ('reactivation_followup_quality_guard','reactivation_canonical_quality_guard')""",tz=TZ,d=day),
          'qualified':scalar(db,"select count(distinct ml.id) from boris_sales_tg_replies r join manager_leads ml on ml.id=r.manager_lead_id where ml.status='квалифицирован' and timezone(:tz,r.received_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'target_action':scalar(db,"select count(distinct ml.id) from boris_sales_tg_replies r join manager_leads ml on ml.id=r.manager_lead_id where ml.status='целевое действие' and timezone(:tz,r.received_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'won':scalar(db,"select count(distinct ml.id) from boris_sales_tg_replies r join manager_leads ml on ml.id=r.manager_lead_id where ml.status='сделка' and timezone(:tz,r.received_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'declined':scalar(db,"select count(distinct l.id) from boris_sales_hot_leads l join boris_sales_tg_replies r on r.hot_lead_id=l.id where l.status='declined' and timezone(:tz,r.received_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'crm_total':scalar(db,"select count(*) from manager_leads where lead_source in ('telegram_prospecting','telegram_reactivation') and timezone(:tz,created_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'dialog':scalar(db,"select count(*) from manager_leads where lead_source in ('telegram_prospecting','telegram_reactivation') and status='в диалоге' and timezone(:tz,updated_at)::date=CAST(:d AS date)",tz=TZ,d=day),
          'overdue_active_tasks':scalar(db,"select count(distinct ml.id) from manager_leads ml join manager_notes n on n.lead_id=ml.id where ml.lead_source in ('telegram_prospecting','telegram_reactivation') and ml.status in ('квалифицирован','целевое действие') and n.kind='reminder' and coalesce(n.done,false)=false and n.remind_at<now()-interval '24 hours'"),
          'crm':scalar(db,"""select count(*) from manager_leads where lead_source in ('telegram_prospecting','telegram_reactivation')
            and timezone(:tz,created_at)::date=CAST(:d AS date)
            and lower(coalesce(status,'')) not in ('отказ','закрыт','закрыто','нецелевой','lost')""",tz=TZ,d=day),
          'ready':scalar(db,"select count(*) from boris_sales_hot_leads where status='new' and monitor_intent='existing_contact_reactivation' and boris_fit>=75"),
          'backfill_remaining':scalar(db,"select count(*) from monitor_sources s where s.platform='telegram' and s.is_active=true and coalesce(s.payload->>'telegram_access_hash','')<>'' and lower(coalesce(s.title,'')) ~ '(авито|avito|авитолог|маркет|бизнес|предприним|продаж|лид|трафик|эксперт)' and lower(coalesce(s.title,'')) !~ '(ваканс|работа для|фриланс ваканс)' and not exists (select 1 from telegram_marketing_backfill_sources b where b.source_id=s.id and b.status='ok')"),
          'dup':scalar(db,"select count(*) from (select coalesce(contact_peer_id::text,lower(contact_username)),count(*) from boris_sales_hot_leads where contacted_at is not null group by 1 having count(*)>1)x"),
        }
        try:
            cp=subprocess.run(['crontab','-l'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
            sigs=('monitor_runner.py --scan','monitor_source_discovery.py --apply','hot_lead_queue.py','telegram_sales_cycle.py')
            vals['scheduler_conflicts']=sum(1 for raw in (cp.stdout or '').splitlines() if raw.strip() and not raw.strip().startswith('#') and any(x in raw for x in sigs))
            dp=subprocess.run(['systemctl','is-active','boris-tg-sales-discovery.timer'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
            vals['legacy_discovery_timer_active']=1 if (dp.stdout or '').strip()=='active' else 0
        except Exception:
            vals['scheduler_conflicts']=1
            vals['legacy_discovery_timer_active']=1
        rr=db.execute(text("select status,duration_seconds,details from telegram_sales_cycle_runs where started_at>=now()-interval '24 hours' and status<>'running' order by started_at desc limit 1")).mappings().first()
        loss_rows=db.execute(text("""select replace(n.text,'Причина отказа: ','') reason,count(*) n
          from manager_notes n join manager_leads ml on ml.id=n.lead_id
          where ml.lead_source in ('telegram_prospecting','telegram_reactivation')
            and n.kind='note' and n.text like 'Причина отказа: %'
            and timezone(:tz,n.created_at)::date=CAST(:d AS date)
          group by 1 order by n desc,reason limit 3"""),{'tz':TZ,'d':day}).all()
        qrows=db.execute(text("""select query,qualified_hits,attempts,messages_saved from telegram_discovery_query_stats
          where attempts>0 order by qualified_hits desc,(qualified_hits::numeric/attempts) desc,updated_at desc limit 3""")).mappings().all()
    valid_sent=max(0,vals['sent']-vals['invalidated_sent'])
    valid_followups=max(0,vals['followups_sent']-vals['invalidated_followups'])
    outbound_total=vals['sent']+vals['followups_sent']
    outbound_remaining=max(0,DAILY_CAP-outbound_total)
    initial_reply_rate=(vals['initial_reply_leads']/valid_sent*100) if valid_sent else 0.0
    followup_reply_rate=(vals['followup_reply_leads']/valid_followups*100) if valid_followups else 0.0
    advanced=vals['qualified']+vals['target_action']+vals['won']
    reply_to_advanced=(advanced/vals['buyer_replies']*100) if vals['buyer_replies'] else 0.0
    cycle_ok = bool(rr and str(rr.get('status') or '').lower()=='pass')
    schedule_issues = vals['scheduler_conflicts'] + vals['legacy_discovery_timer_active']
    safety_ok = (schedule_issues==0 and vals['dup']==0)
    if vals['fresh']>0:
        verdict='✅ Сегодня найдены новые целевые заявки.'
    elif vals['fresh_ready']>0:
        verdict='⏳ Новых заявок сегодня пока нет, BORIS продолжает работу с уже найденной безопасной очередью.'
    else:
        verdict='⚠️ Новых целевых заявок сегодня пока нет. BORIS продолжает поиск автоматически.'
    msg=(
      f"📊 <b>Telegram-продажи BORIS · {day}</b>\n\n"
      f"<b>Результат за сегодня</b>\n"
      f"• Найдено новых целевых заявок: {vals['fresh']}\n"
      f"• Валидных первых отправок: {valid_sent} (доставка подтверждена {vals['verified_initial']}/{valid_sent})\n"
      f"• Валидных повторных сообщений: {valid_followups} (доставка подтверждена {vals['verified_followups']}/{valid_followups})\n"
      f"• Отсеяно после первой отправки: {vals['invalidated_sent']}\n"
      f"• Отсеяно после follow-up: {vals['invalidated_followups']}\n"
      f"• Автоответов в диалогах: {vals['auto_replies_sent']} (доставка подтверждена {vals['auto_replies_verified']}/{vals['auto_replies_sent']})\n"
      f"• Ответило уникальных потенциальных клиентов: {vals['buyer_replies']} (сообщений: {vals['buyer_reply_messages']})\n"
      f"• Initial → ответ: {vals['initial_reply_leads']}/{valid_sent} ({initial_reply_rate:.1f}%)\n"
      f"• Follow-up → ответ: {vals['followup_reply_leads']}/{valid_followups} ({followup_reply_rate:.1f}%)\n"
      f"• Publisher/vendor redirects: {vals['vendor_replies']}\n"
      f"• В активном диалоге: {vals['dialog']}\n"
      f"• Просроченных активных задач: {vals['overdue_active_tasks']}\n"
      f"• Квалифицировано: {vals['qualified']}\n"
      f"• Дошло до целевого действия / демонстрации: {vals['target_action']}\n"
      f"• Сделок: {vals['won']}\n"
      f"• Ответ → следующий коммерческий этап: {reply_to_advanced:.1f}%\n"
      f"• Явных отказов: {vals['declined']}\n"
      + ("• Причины отказов: " + "; ".join(f"{reason} — {n}" for reason,n in loss_rows) + "\n" if loss_rows else "")
      + f"• Активных карточек CRM сегодня: {vals['crm']} из {vals['crm_total']} созданных\n\n"
      f"<b>Что BORIS может сделать дальше</b>\n"
      f"• Готово к безопасной автоотправке: {vals['fresh_ready']}\n"
      f"• Не хватает контактных данных: {vals['identity_blocked']} — им сообщения не отправляются\n"
      f"• Контактов для реактивации: {vals['ready']}\n\n"
      f"<b>Контроль безопасности</b>\n"
      f"• Дубли отправок: {vals['dup']}\n"
      f"• Лимит инициативных исходящих (первичные + follow-up): {outbound_total}/{DAILY_CAP}, осталось {outbound_remaining}\n"
      f"• Реактивация: {vals['react_sent']}/{REACT_DAILY_CAP}\n"
      f"• Follow-up: {vals['followups_sent']}/{FOLLOWUP_DAILY_CAP}\n"
      f"• Конфликты планировщиков: {schedule_issues}\n"
      f"• Поиск заявок сегодня: {vals['scan_ok']} успешных завершённых циклов из {vals['scans']}\n"
      f"• Выполняется прямо сейчас: {vals['scan_running']}\n"
      f"• Последний цикл продаж: {'работает нормально' if cycle_ok else 'требует повторной проверки'}\n\n"
      f"{verdict}\n"
      + ("От вас действий не требуется." if safety_ok else "BORIS уже исправляет техническое отклонение автоматически. От вас действий не требуется.")
    )
    if not send:
        print(msg)
        print(f'TG_DAILY_REPORT=PREVIEW day={day} valid_sent={valid_sent} invalidated={vals["invalidated_sent"]} buyer_replies={vals["buyer_replies"]} vendor_replies={vals["vendor_replies"]} fresh={vals["fresh"]}')
        return 0
    res=send_telegram_message(CHAT,msg,THREAD)
    result=(res or {}).get('result') if isinstance(res,dict) else None
    try: message_id=int((result or {}).get('message_id') or 0)
    except Exception: message_id=0
    if not (isinstance(res,dict) and res.get('ok') and message_id>0):
        print(f'TG_DAILY_REPORT=FAIL send receipt_missing ok={bool(isinstance(res,dict) and res.get("ok"))}'); return 2
    receipt=json.dumps({
        'message_id':message_id,
        'chat_id':str(CHAT),
        'thread_id':int(THREAD) if THREAD else None,
        'sent_at':datetime.now(ZoneInfo(TZ)).isoformat(),
        'telegram_ok':True,
    },ensure_ascii=False,sort_keys=True)
    with engine.begin() as db:
        db.execute(text("insert into monitor_runtime_state(key,value_text,updated_at) values(:k,:v,now()) on conflict(key) do update set value_text=excluded.value_text,updated_at=now()"),{'k':key,'v':receipt})
    print(f'TG_DAILY_REPORT=PASS day={day} message_id={message_id} valid_sent={valid_sent} invalidated={vals["invalidated_sent"]} buyer_replies={vals["buyer_replies"]} vendor_replies={vals["vendor_replies"]} fresh={vals["fresh"]}')
    return 0
if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--send',action='store_true',help='Send only in the configured evening report window')
    ap.add_argument('--force-send',action='store_true',help='Explicit operator override for emergency resend')
    args=ap.parse_args()
    raise SystemExit(main(send=(args.send or args.force_send),force=args.force_send))
