#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import psycopg2
from dotenv import load_dotenv

ROOT=Path('/root/BORIS/backend')
load_dotenv(ROOT/'.env')
DB=os.environ['DATABASE_URL']
TZ=os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')
PY=str(ROOT/'venv/bin/python')
INTERVAL=120
MAX_SEC=4500
START=datetime.now(timezone.utc)
LOG=ROOT/'logs'/f'telegram_report_window_watch_{START.strftime("%Y%m%dT%H%M%SZ")}.jsonl'

def emit(obj):
    obj={'at':datetime.now(timezone.utc).isoformat(),**obj}
    line=json.dumps(obj,ensure_ascii=False,default=str,sort_keys=True)
    print(line,flush=True)
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a') as f: f.write(line+'\n')

def run(args,timeout=45):
    try:
        p=subprocess.run(args,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        return p.returncode,(p.stdout or '').strip()
    except Exception as e:
        return 99,f'{type(e).__name__}: {e}'

def receipt():
    day=datetime.now(ZoneInfo(TZ)).date().isoformat(); key=f'tg_sales_daily_report:{day}'
    c=psycopg2.connect(DB); q=c.cursor(); q.execute('select value_text,updated_at from monitor_runtime_state where key=%s',(key,)); row=q.fetchone(); c.close()
    value=row[0] if row else None
    try:
        payload=json.loads(str(value or '')); mid=int((payload or {}).get('message_id') or 0)
    except Exception: payload={}; mid=0
    return day,(mid if mid>0 else None),payload,(row[1] if row else None)

def selftest_status():
    day=datetime.now(ZoneInfo(TZ)).date().isoformat(); key=f'tg_sales_daily_selftest:{day}'
    c=psycopg2.connect(DB); q=c.cursor(); q.execute('select value_text,updated_at from monitor_runtime_state where key=%s',(key,)); row=q.fetchone(); c.close()
    try:
        payload=json.loads(str((row[0] if row else '') or ''))
        ok=int((payload or {}).get('total') or 0)>0 and int((payload or {}).get('failed') or 0)==0
    except Exception: payload={}; ok=False
    return ok,payload,(row[1] if row else None)

def latest_cycle():
    c=psycopg2.connect(DB); q=c.cursor(); q.execute("select status,started_at,ended_at,duration_seconds,details from telegram_sales_cycle_runs order by started_at desc limit 1"); row=q.fetchone(); c.close()
    if not row: return None
    return {'status':row[0],'started_at':row[1],'ended_at':row[2],'duration_seconds':row[3],'details':row[4]}

def active(unit):
    _,out=run(['systemctl','is-active',unit],10); return out.strip()

emit({'event':'start','max_sec':MAX_SEC,'interval_sec':INTERVAL,'log':str(LOG)})
failures=0; ticks=0; confirmed_ticks=0
while (datetime.now(timezone.utc)-START).total_seconds() < MAX_SEC:
    ticks+=1
    local=datetime.now(ZoneInfo(TZ))
    day,mid,payload,updated=receipt()
    audit_rc,audit_out=run([PY,'telegram_sales_production_audit.py'],45)
    timers={u:active(u) for u in ('boris-tg-sales-cycle.timer','boris-tg-sales-watchdog.timer','boris-tg-sales-daily-report.timer','boris-tg-sales-daily-report-verify.timer')}
    healed=None
    after_selfheal=(local.hour>19 or (local.hour==19 and local.minute>=15))
    if after_selfheal and not mid:
        rc,out=run(['systemctl','start','boris-tg-sales-daily-report-verify.service'],30)
        healed={'verify_start_rc':rc,'tail':out[-500:]}
        time.sleep(2)
        day,mid,payload,updated=receipt()
    selftest_ok,selftest_payload,selftest_updated=selftest_status()
    selftest_due=(local.hour>19 or (local.hour==19 and local.minute>=30))
    ok=(audit_rc==0 and all(v=='active' for v in timers.values()) and ((not after_selfheal) or bool(mid)) and ((not selftest_due) or selftest_ok))
    if not ok: failures+=1
    if mid and local.hour==19 and local.minute>=15: confirmed_ticks+=1
    else: confirmed_ticks=0
    emit({'event':'tick','tick':ticks,'local':local.isoformat(),'ok':ok,'audit_rc':audit_rc,
          'audit_tail':'\n'.join(audit_out.splitlines()[-2:]),'timers':timers,'report_message_id':mid,
          'receipt':payload,'receipt_updated_at':updated,'selftest_ok':selftest_ok,'selftest':selftest_payload,'selftest_updated_at':selftest_updated,'healed':healed,'latest_cycle':latest_cycle()})
    # Keep watching until report + verifier/self-heal window has passed. Two
    # confirmed ticks ensure the receipt remains stable across a full interval.
    if local.hour==19 and local.minute>=32 and confirmed_ticks>=2 and audit_rc==0 and selftest_ok:
        emit({'event':'end','status':'PASS','ticks':ticks,'failures':failures,'message_id':mid})
        raise SystemExit(0)
    time.sleep(INTERVAL)
local=datetime.now(ZoneInfo(TZ)); day,mid,payload,updated=receipt(); audit_rc,audit_out=run([PY,'telegram_sales_production_audit.py'],45)
status='PASS' if mid and audit_rc==0 else 'FAIL'
emit({'event':'end','status':status,'ticks':ticks,'failures':failures,'message_id':mid,'audit_rc':audit_rc,'local':local.isoformat()})
raise SystemExit(0 if status=='PASS' else 2)
