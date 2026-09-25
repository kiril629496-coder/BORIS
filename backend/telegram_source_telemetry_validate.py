#!/usr/bin/env python3
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
from dotenv import load_dotenv
ROOT=Path('/root/BORIS/backend'); load_dotenv(ROOT/'.env'); DB=os.environ['DATABASE_URL']
START=datetime.now(timezone.utc); DEADLINE=time.time()+1500
LOG=ROOT/'logs'/f'telegram_source_telemetry_validate_{START.strftime("%Y%m%dT%H%M%SZ")}.jsonl'
def emit(x):
 x={'at':datetime.now(timezone.utc).isoformat(),**x}; line=json.dumps(x,ensure_ascii=False,default=str,sort_keys=True); print(line,flush=True); LOG.parent.mkdir(parents=True,exist_ok=True); LOG.open('a').write(line+'\n')
emit({'event':'start','started_after':START.isoformat(),'log':str(LOG)})
while time.time()<DEADLINE:
 c=psycopg2.connect(DB); q=c.cursor()
 q.execute("""select id,status,started_at,finished_at,scanned_sources,scanned_messages,matched_messages,new_messages,errors
 from monitor_runs where platform='telegram' and started_at>=%s and status<>'running' order by started_at asc limit 1""",(START,)); run=q.fetchone()
 if not run:
  c.close(); time.sleep(15); continue
 rid,status,started,finished,ss,sm,mm,nm,errors=run
 q.execute("""select count(*),coalesce(sum(processed_messages),0),coalesce(sum(matched_messages),0),coalesce(sum(new_messages),0),coalesce(sum(errors),0),coalesce(sum(raw_messages),0)
 from telegram_source_scan_stats where last_scan_at>=%s and last_scan_at<=%s+interval '2 minutes'""",(started,finished)); agg=q.fetchone(); c.close()
 rows,processed,matched,new,err_count,raw=map(int,agg)
 ok=(status=='ok' and rows==int(ss or 0) and processed==int(sm or 0) and matched==int(mm or 0) and new==int(nm or 0))
 emit({'event':'validate','ok':ok,'run_id':str(rid),'run_status':status,'run_started':started,'run_finished':finished,
       'run':{'sources':ss,'messages':sm,'matched':mm,'new':nm},'telemetry':{'rows':rows,'raw':raw,'processed':processed,'matched':matched,'new':new,'errors':err_count}})
 emit({'event':'end','status':'PASS' if ok else 'FAIL'})
 raise SystemExit(0 if ok else 2)
emit({'event':'end','status':'FAIL','reason':'timeout_waiting_for_completed_scan'}); raise SystemExit(2)
