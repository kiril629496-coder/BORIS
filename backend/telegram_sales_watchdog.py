#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT=Path('/root/BORIS/backend')
PYTHON=str(ROOT/'venv/bin/python')
load_dotenv(ROOT/'.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)
ALERT_KEY='tg_sales_watchdog_last_alert'

def run(args, timeout=30):
    # TG_WATCHDOG_NONBLOCKING_SYSTEMD_KICK_V1: systemctl start on a long-running
    # oneshot waits for the child to finish. The watchdog only needs to enqueue
    # the canonical cycle and then inspect its runtime/business evidence; waiting
    # 20s here caused the watchdog itself to crash while the cycle was healthy
    # and still running. Use --no-block only for service start requests.
    cmd=list(args)
    if len(cmd) >= 3 and cmd[0]=='systemctl' and cmd[1]=='start' and '--no-block' not in cmd:
        cmd=['systemctl','start','--no-block',*cmd[2:]]
    try:
        p=subprocess.run(cmd,cwd=str(ROOT),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        return p.returncode,(p.stdout or '').strip()
    except subprocess.TimeoutExpired as exc:
        out=exc.stdout or ''
        if isinstance(out,bytes): out=out.decode('utf-8','replace')
        return 124,str(out).strip()

def state(unit):
    try:
        _,out=run(['systemctl','is-active',unit],10)
        return out.strip()
    except Exception:
        return 'unknown'

def enabled(unit):
    try:
        _,out=run(['systemctl','is-enabled',unit],10)
        return out.strip()
    except Exception:
        return 'unknown'

def recent_cycle_age_minutes():
    with engine.connect() as db:
        row=db.execute(text("select ended_at from telegram_sales_cycle_runs where status='pass' and ended_at is not null order by ended_at desc limit 1")).first()
        if not row or not row[0]: return 10**9
        dt=row[0]
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return max(0,(datetime.now(timezone.utc)-dt).total_seconds()/60)

def daily_report_receipt_message_id(day: str) -> int | None:
    key=f'tg_sales_daily_report:{day}'
    with engine.connect() as db:
        value=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':key}).scalar()
    try:
        payload=json.loads(str(value or ''))
        mid=int((payload or {}).get('message_id') or 0)
        return mid if mid>0 else None
    except Exception:
        return None

def heal_runtime():
    actions=[]
    if enabled('boris-tg-sales-cycle.timer')!='enabled':
        rc,_=run(['systemctl','enable','boris-tg-sales-cycle.timer'],20)
        actions.append({'enable_cycle_timer':rc})
    if state('boris-tg-sales-cycle.timer')!='active':
        rc,_=run(['systemctl','start','boris-tg-sales-cycle.timer'],20)
        actions.append({'start_cycle_timer':rc})
    if enabled('boris-tg-sales-morning-preflight.timer')!='enabled':
        rc,_=run(['systemctl','enable','boris-tg-sales-morning-preflight.timer'],20); actions.append({'enable_morning_preflight_timer':rc})
    if state('boris-tg-sales-morning-preflight.timer')!='active':
        rc,_=run(['systemctl','start','boris-tg-sales-morning-preflight.timer'],20); actions.append({'start_morning_preflight_timer':rc})
    if enabled('boris-tg-sales-daily-selftest.timer')!='enabled':
        rc,_=run(['systemctl','enable','boris-tg-sales-daily-selftest.timer'],20)
        actions.append({'enable_daily_selftest_timer':rc})
    if state('boris-tg-sales-daily-selftest.timer')!='active':
        rc,_=run(['systemctl','start','boris-tg-sales-daily-selftest.timer'],20)
        actions.append({'start_daily_selftest_timer':rc})
    if state('boris-tg-sales-discovery.timer')=='active' or enabled('boris-tg-sales-discovery.timer')=='enabled':
        rc1,_=run(['systemctl','stop','boris-tg-sales-discovery.timer'],20)
        rc2,_=run(['systemctl','disable','boris-tg-sales-discovery.timer'],20)
        actions.append({'retire_legacy_discovery':[rc1,rc2]})
    age=recent_cycle_age_minutes()
    svc=state('boris-tg-sales-cycle.service')
    if age>45 and svc not in ('active','activating'):
        rc,_=run(['systemctl','start','boris-tg-sales-cycle.service'],20)
        actions.append({'kick_sales_cycle':rc,'age_min':round(age,1)})
    # The normal report runs at 19:38 Moscow and verifier at 19:43. If both
    # missed or a transient send failed, the watchdog becomes the third layer:
    # after 19:48 it retries the idempotent verifier until a Telegram message_id
    # receipt is persisted.
    local_now=datetime.now(ZoneInfo(os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')))
    if local_now.hour>9 or (local_now.hour==9 and local_now.minute>=5):
        day=local_now.date().isoformat(); key=f'tg_sales_morning_preflight:{day}'; ok=False
        try:
            with engine.connect() as db: raw=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':key}).scalar()
            pp=json.loads(str(raw or '')); ran_raw=str((pp or {}).get('ran_at') or ''); ran=datetime.fromisoformat(ran_raw.replace('Z','+00:00')) if ran_raw else None
            if ran is not None and ran.tzinfo is None: ran=ran.replace(tzinfo=local_now.tzinfo)
            floor=local_now.replace(hour=8,minute=50,second=0,microsecond=0); ok=bool((pp or {}).get('ok') is True and ran and ran.astimezone(local_now.tzinfo)>=floor)
        except Exception: ok=False
        if not ok:
            rc,_=run(['systemctl','start','boris-tg-sales-morning-preflight.service'],180); actions.append({'retry_morning_preflight':rc,'day':day})
    if local_now.hour>19 or (local_now.hour==19 and local_now.minute>=48):
        day=local_now.date().isoformat()
        if not daily_report_receipt_message_id(day):
            rc,_=run(['systemctl','start','boris-tg-sales-daily-report-verify.service'],30)
            actions.append({'retry_daily_report_verifier':rc,'day':day})
    if local_now.hour>19 or (local_now.hour==19 and local_now.minute>=30):
        day=local_now.date().isoformat(); key=f'tg_sales_daily_selftest:{day}'
        ok=False
        try:
            with engine.connect() as db:
                raw=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':key}).scalar()
            payload=json.loads(str(raw or ''))
            total=int((payload or {}).get('total') or 0); failed=int((payload or {}).get('failed') or 0)
            ran_raw=str((payload or {}).get('ran_at') or '')
            ran_at=datetime.fromisoformat(ran_raw.replace('Z','+00:00')) if ran_raw else None
            if ran_at is not None and ran_at.tzinfo is None: ran_at=ran_at.replace(tzinfo=local_now.tzinfo)
            scheduled_floor=local_now.replace(hour=19,minute=20,second=0,microsecond=0)
            ok=bool(total>0 and failed==0 and ran_at and ran_at.astimezone(local_now.tzinfo)>=scheduled_floor)
        except Exception:
            ok=False
        if not ok:
            rc,_=run(['systemctl','start','boris-tg-sales-daily-selftest.service'],40)
            actions.append({'retry_daily_selftest':rc,'day':day})
    return actions

def alert(signature, body):
    now=datetime.now(timezone.utc)
    should=False
    with engine.begin() as db:
        db.execute(text("create table if not exists monitor_runtime_state (key text primary key,value_text text,updated_at timestamptz not null default now())"))
        row=db.execute(text("select value_text,updated_at from monitor_runtime_state where key=:k"),{'k':ALERT_KEY}).first()
        if not row or str(row[0] or '')!=signature or not row[1] or row[1] < now-timedelta(hours=6):
            db.execute(text("insert into monitor_runtime_state(key,value_text,updated_at) values(:k,:v,now()) on conflict(key) do update set value_text=excluded.value_text,updated_at=now()"),{'k':ALERT_KEY,'v':signature})
            should=True
    if not should:
        print('TG_WATCHDOG_ALERT=SKIP duplicate')
        return
    try:
        from app.telegram_bot import send_telegram_message
        chat=os.getenv('HOT_LEAD_REPLY_CHAT_ID','-1003952038222')
        thread=int(os.getenv('HOT_LEAD_REPLY_THREAD_ID','126') or 126)
        r=send_telegram_message(chat,'🚨 <b>BORIS — контроль Telegram-продаж</b>\n\n'+body,thread)
        print('TG_WATCHDOG_ALERT=' + ('PASS' if isinstance(r,dict) and r.get('ok') else 'DEGRADED'))
    except Exception as e:
        print(f'TG_WATCHDOG_ALERT=DEGRADED type={type(e).__name__}')

def _owner_audit_body(out, rc, actions):
    """Translate machine audit JSON into a short owner-facing Russian report."""
    payload={}
    for raw in reversed((out or '').splitlines()):
        raw=raw.strip()
        if raw.startswith('{') and raw.endswith('}'):
            try:
                payload=json.loads(raw); break
            except Exception:
                pass
    hard=(payload.get('hard') or {}) if isinstance(payload,dict) else {}
    info=(payload.get('info') or {}) if isinstance(payload,dict) else {}
    labels={
        'sales_cycle_timer_active':'Планировщик Telegram-продаж не запущен',
        'legacy_discovery_timer_inactive':'Старый конфликтующий планировщик Telegram ещё включён',
        'watchdog_timer_active':'Контроль Telegram-продаж не запущен',
        'reactivation_timer_active':'Планировщик реактивации контактов не запущен',
        'daily_report_timer_active':'Ежедневный отчёт Telegram-продаж не запущен',
        'daily_report_service_sends':'Сервис ежедневного отчёта запущен без реальной отправки (--send)',
        'daily_report_verify_timer_active':'Проверка доставки ежедневного отчёта не запущена',
        'daily_report_confirmed_after_deadline':'Ежедневный отчёт не подтверждён Telegram после контрольного времени',
        'daily_selftest_timer_active':'Ежедневный self-test Telegram-продаж не запущен',
        'morning_preflight_timer_active':'Утренний preflight Telegram-продаж не запущен',
        'morning_preflight_pass_after_0905':'Утренний preflight Telegram-продаж не подтвердил PASS до начала рабочего дня',
        'daily_selftest_pass_after_deadline':'Ежедневный self-test Telegram-продаж не подтвердил PASS после контрольного времени',
        'hard_send_window_9_19':'Нарушено безопасное окно отправки 09:00–19:00',
        'no_outside_window_personal_outbound':'Зафиксирована фактическая отправка personal Telegram вне разрешённого окна',
        'source_telemetry_coverage':'Телеметрия источников не покрывает все активные Telegram-источники или содержит ошибки',
        'source_cadence_not_starved':'Адаптивный обход Telegram-источников пропустил источник дольше допустимого окна',
        'no_legacy_core_cron':'Найдены конфликтующие старые задания расписания',
        'active_sources_present':'Нет активных источников для поиска заявок',
        'no_stale_runs':'Один из циклов поиска/продаж завис и не завершился вовремя',
        'recent_cycle_healthy':'За последние 30 минут не было успешного цикла Telegram-продаж',
        'no_recent_degraded':'Недавний цикл Telegram-продаж завершился с ошибкой или таймаутом',
        'recent_scan_ok':'За последние 30 минут не было успешного поиска новых заявок',
        'queue_safety':'В очереди Telegram есть данные, которые нельзя безопасно отправлять автоматически',
        'reactivation_queue_ready':'Очередь реактивации не подготовлена полностью: нет приоритета, текста или контакта',
        'backfill_complete':'Исторические источники Telegram ещё не полностью обработаны',
    }
    failed=[labels.get(k,k) for k,v in hard.items() if v is not True]
    lines=['BORIS обнаружил проблему в Telegram-лидогенерации:']
    if failed:
        lines.extend('• '+x for x in failed[:8])
    else:
        lines.append('• Машинная проверка завершилась с ошибкой, причина уточняется автоматически.')
    if actions:
        lines.append('')
        lines.append('Что BORIS уже сделал: перезапустил или восстановил нужные планировщики.')
    sendable=int(info.get('fresh_ready_send') or 0)
    blocked=int(info.get('identity_blocked') or 0)
    lines.append('')
    lines.append('Готовых безопасных заявок к отправке сейчас: %d.' % sendable)
    if blocked:
        lines.append('Заявок без достаточных контактных данных: %d — они не будут отправлены.' % blocked)
    lines.append('От вас действий не требуется. Небезопасная отправка заблокирована, BORIS повторит проверку сам.')
    return '\n'.join(lines)


def main():
    actions=heal_runtime()
    if actions: time.sleep(2)
    reconcile_rc,reconcile_out=run([PYTHON,'telegram_delivery_reconcile.py'],45)
    print(reconcile_out[-4000:])
    rc,out=run([PYTHON,'telegram_sales_production_audit.py'],45)
    print(out[-12000:])
    if rc==0:
        print('TG_SALES_WATCHDOG=PASS actions='+json.dumps(actions,ensure_ascii=False))
        return 0
    signature=json.dumps({'audit_rc':rc,'actions':actions},sort_keys=True,ensure_ascii=False)
    alert(signature,_owner_audit_body(out,rc,actions))
    print('TG_SALES_WATCHDOG=FAIL')
    return 2

if __name__=='__main__':
    raise SystemExit(main())
