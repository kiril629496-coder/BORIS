#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine,text

load_dotenv('/root/BORIS/backend/.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)
DURATION=int(os.getenv('TG_OUTBOUND_WATCH_DURATION_SEC','3600') or 3600)
INTERVAL=int(os.getenv('TG_OUTBOUND_WATCH_INTERVAL_SEC','60') or 60)
LOGDIR=Path('/root/BORIS/backend/logs'); LOGDIR.mkdir(parents=True,exist_ok=True)
log=LOGDIR/f"telegram_outbound_integrity_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"

def emit(d):
    d={'at':datetime.now(timezone.utc).isoformat(),**d}
    line=json.dumps(d,ensure_ascii=False,default=str)
    print(line,flush=True)
    with log.open('a') as f: f.write(line+'\n')

def scalar(db,q,**kw): return int(db.execute(text(q),kw).scalar() or 0)

def worker_count():
    out=subprocess.run(['ps','-eo','cmd'],text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL).stdout
    pats=('hot_lead_auto_sender.py','telegram_sales_replies.py')
    return sum(1 for line in out.splitlines() if any(p in line for p in pats) and 'telegram_outbound_integrity_watch.py' not in line)

def tick(n):
    with engine.begin() as db:
        bad={}
        bad['auto_reply_without_message']=scalar(db,"select count(*) from boris_sales_tg_replies where auto_replied_at is not null and auto_reply_message_id is null")
        bad['auto_reply_without_timestamp']=scalar(db,"select count(*) from boris_sales_tg_replies where auto_reply_message_id is not null and auto_replied_at is null")
        bad['duplicate_auto_reply_message']=scalar(db,"""select count(*) from (select hot_lead_id,auto_reply_message_id,count(*) n from boris_sales_tg_replies where auto_reply_message_id is not null group by hot_lead_id,auto_reply_message_id having count(*)>1)q""")
        bad['approved_stuck']=scalar(db,"select count(*) from boris_sales_hot_leads where status='approved' and approved_at<now()-interval '20 minutes' and contacted_at is null")
        bad['contacted_without_message']=scalar(db,"select count(*) from boris_sales_hot_leads where status='contacted' and outbound_transport='telegram_personal' and outbound_message_id is null")
        bad['outside_window_today']=scalar(db,"""select count(*) from boris_sales_hot_leads where outbound_transport='telegram_personal' and contacted_at is not null and timezone('Europe/Moscow',contacted_at)::date=timezone('Europe/Moscow',now())::date and (extract(hour from timezone('Europe/Moscow',contacted_at))<9 or extract(hour from timezone('Europe/Moscow',contacted_at))>=19)""")
        bad['followup_outside_window_today']=scalar(db,"""select count(*) from boris_sales_hot_leads where followup_sent_at is not null and timezone('Europe/Moscow',followup_sent_at)::date=timezone('Europe/Moscow',now())::date and (extract(hour from timezone('Europe/Moscow',followup_sent_at))<9 or extract(hour from timezone('Europe/Moscow',followup_sent_at))>=19)""")
        bad['auto_reply_outside_window_today']=scalar(db,"""select count(*) from boris_sales_tg_replies where auto_replied_at is not null and timezone('Europe/Moscow',auto_replied_at)::date=timezone('Europe/Moscow',now())::date and (extract(hour from timezone('Europe/Moscow',auto_replied_at))<9 or extract(hour from timezone('Europe/Moscow',auto_replied_at))>=19)""")
        bad['outbound_lock_holders_over_one']=max(0,scalar(db,"select count(*) from pg_locks where locktype='advisory' and granted and objid=884422901")-1)
        last_cycle=db.execute(text("select status,started_at,ended_at,duration_seconds from telegram_sales_cycle_runs order by started_at desc limit 1")).mappings().first()
        last_scan=db.execute(text("select status,started_at,finished_at,scanned_sources,scanned_messages from monitor_runs where platform='telegram' order by started_at desc limit 1")).mappings().first()
    workers=worker_count()
    bad['parallel_personal_workers']=max(0,workers-1)
    cycle_ok=bool(last_cycle and (last_cycle['status']=='pass' or (last_cycle['status']=='running' and (datetime.now(timezone.utc)-last_cycle['started_at']).total_seconds()<1200)))
    scan_ok=bool(last_scan and (last_scan['status']=='ok' or (last_scan['status']=='running' and (datetime.now(timezone.utc)-last_scan['started_at']).total_seconds()<1200)))
    active={k:v for k,v in bad.items() if v}
    ok=not active and cycle_ok and scan_ok
    emit({'event':'tick','tick':n,'ok':ok,'bad':active,'workers':workers,'cycle':dict(last_cycle or {}),'scan':dict(last_scan or {})})
    return ok

def main():
    emit({'event':'start','duration_sec':DURATION,'interval_sec':INTERVAL,'log':str(log)})
    end=time.monotonic()+DURATION; n=0; failures=0
    while True:
        n+=1
        if not tick(n): failures+=1
        remain=end-time.monotonic()
        if remain<=0: break
        time.sleep(min(INTERVAL,remain))
    emit({'event':'finish','ticks':n,'failures':failures,'status':'PASS' if failures==0 else 'FAIL'})
    return 0 if failures==0 else 2
if __name__=='__main__': raise SystemExit(main())
