#!/usr/bin/env python3
import os,time,json,subprocess
from datetime import datetime,timezone
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
eng=create_engine(os.environ['DATABASE_URL'],pool_pre_ping=True)
LEAD=4184; end=time.time()+2700; tick=0; failures=0
log='/root/BORIS/backend/logs/telegram_fresh_4184_watch_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.jsonl'
def emit(d):
 d={'at':datetime.now(timezone.utc).isoformat(),**d}
 with open(log,'a') as f: f.write(json.dumps(d,ensure_ascii=False,default=str)+'\n')
 print(json.dumps(d,ensure_ascii=False,default=str),flush=True)
emit({'event':'start','lead':LEAD,'log':log})
while time.time()<end:
 tick+=1
 try:
  with eng.connect() as db:
   l=db.execute(text("select id,status,approval_note,contacted_at,outbound_message_id,outbound_error,contact_username,contact_peer_id,updated_at from boris_sales_hot_leads where id=:i"),{'i':LEAD}).mappings().first()
   replies=[dict(x) for x in db.execute(text("select id,body,received_at,auto_reply_policy,manager_lead_id from boris_sales_tg_replies where hot_lead_id=:i order by received_at"),{'i':LEAD}).mappings()]
   cycle=db.execute(text("select status,started_at,ended_at,duration_seconds,details from telegram_sales_cycle_runs order by started_at desc limit 1")).mappings().first()
  audit=subprocess.run(['/root/BORIS/backend/venv/bin/python','/root/BORIS/backend/telegram_sales_production_audit.py'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
  ok=audit.returncode==0
  if not ok: failures+=1
  emit({'event':'tick','tick':tick,'ok':ok,'lead':dict(l) if l else None,'replies':replies,'cycle':dict(cycle) if cycle else None,'audit_rc':audit.returncode})
 except Exception as e:
  failures+=1; emit({'event':'error','tick':tick,'error':f'{type(e).__name__}: {e}'})
 time.sleep(min(300,max(0,end-time.time())))
emit({'event':'end','ticks':tick,'failures':failures,'status':'PASS' if failures==0 else 'FAIL'})
raise SystemExit(0 if failures==0 else 2)
