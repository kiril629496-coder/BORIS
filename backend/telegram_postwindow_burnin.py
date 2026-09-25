#!/usr/bin/env python3
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import psycopg2
from dotenv import load_dotenv
ROOT=Path('/root/BORIS/backend'); load_dotenv(ROOT/'.env')
DB=os.environ['DATABASE_URL']; TZ=ZoneInfo(os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow'))
INTERVAL=300; MAX_SEC=10800
START=datetime.now(timezone.utc); LOG=ROOT/'logs'/f'telegram_postwindow_burnin_{START.strftime("%Y%m%dT%H%M%SZ")}.jsonl'

def emit(x):
 x={'at':datetime.now(timezone.utc).isoformat(),**x}; line=json.dumps(x,ensure_ascii=False,default=str,sort_keys=True); print(line,flush=True); LOG.parent.mkdir(parents=True,exist_ok=True); LOG.open('a').write(line+'\n')
def counts():
 c=psycopg2.connect(DB); q=c.cursor();
 q.execute("""select
 count(*) filter(where outbound_transport='telegram_personal' and (contacted_at at time zone 'Europe/Moscow')::date=(now() at time zone 'Europe/Moscow')::date and extract(hour from timezone('Europe/Moscow',contacted_at))>=19),
 count(*) filter(where followup_sent_at is not null and (followup_sent_at at time zone 'Europe/Moscow')::date=(now() at time zone 'Europe/Moscow')::date and extract(hour from timezone('Europe/Moscow',followup_sent_at))>=19)
 from boris_sales_hot_leads"""); a,b=q.fetchone();
 q.execute("""select count(*) from boris_sales_tg_replies where auto_replied_at is not null and (auto_replied_at at time zone 'Europe/Moscow')::date=(now() at time zone 'Europe/Moscow')::date and extract(hour from timezone('Europe/Moscow',auto_replied_at))>=19"""); d=q.fetchone()[0]
 q.execute("select status,started_at,ended_at,duration_seconds from telegram_sales_cycle_runs order by started_at desc limit 1"); cyc=q.fetchone(); c.close(); return int(a),int(b),int(d),cyc
emit({'event':'start','interval_sec':INTERVAL,'max_sec':MAX_SEC,'log':str(LOG)})
fails=0;ticks=0;saw_postwindow=False
while (datetime.now(timezone.utc)-START).total_seconds()<MAX_SEC:
 ticks+=1; local=datetime.now(TZ); ini,fu,ar,cyc=counts(); after=local.hour>=19; saw_postwindow|=after
 ok=(not after) or (ini==0 and fu==0 and ar==0)
 if not ok: fails+=1
 emit({'event':'tick','tick':ticks,'local':local.isoformat(),'after_19':after,'ok':ok,'outside_initial_today':ini,'outside_followup_today':fu,'outside_auto_reply_today':ar,'latest_cycle':cyc})
 if after and local.hour>=21:
  status='PASS' if fails==0 and saw_postwindow else 'FAIL'; emit({'event':'end','status':status,'ticks':ticks,'failures':fails}); raise SystemExit(0 if status=='PASS' else 2)
 time.sleep(INTERVAL)
emit({'event':'end','status':'PASS' if fails==0 and saw_postwindow else 'FAIL','ticks':ticks,'failures':fails}); raise SystemExit(0 if fails==0 and saw_postwindow else 2)
