#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
engine=create_engine(os.environ['DATABASE_URL'],pool_pre_ping=True)
DURATION=5400; INTERVAL=300
log=Path('/root/BORIS/backend/logs')/f"telegram_sales_integrity_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
def run(cmd):
 p=subprocess.run(cmd,cwd='/root/BORIS/backend',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
 return p.returncode,(p.stdout or '').strip()
def scalar(db,q,**kw): return int(db.execute(text(q),kw).scalar() or 0)
def emit(x):
 line=json.dumps(x,ensure_ascii=False,default=str,sort_keys=True); print(line,flush=True)
 with log.open('a') as f: f.write(line+'\n')
start=time.monotonic(); tick=0; failures=0
emit({'event':'start','at':datetime.now(timezone.utc),'duration_sec':DURATION,'interval_sec':INTERVAL,'log':str(log)})
while True:
 tick+=1; now=datetime.now(timezone.utc)
 arc,aout=run(['./venv/bin/python','telegram_sales_production_audit.py'])
 hrc,hout=run(['./venv/bin/python','telegram_sales_health.py'])
 with engine.connect() as db:
  lead=db.execute(text("select id,status,contact_username,contacted_at,outbound_message_id,outbound_error,approval_note,updated_at from boris_sales_hot_leads where id=4184")).mappings().first()
  replies=[dict(r) for r in db.execute(text("select id,body,received_at,auto_reply_policy,manager_lead_id,auto_replied_at from boris_sales_tg_replies where hot_lead_id=4184 order by received_at,id" )).mappings().all()]
  latest=db.execute(text("select status,started_at,ended_at,duration_seconds,details from telegram_sales_cycle_runs order by started_at desc limit 1")).mappings().first()
  bad_min=scalar(db,"select count(*) from boris_sales_hot_leads where status='new' and monitor_intent='buy_service' and coalesce(contact_username,'')='' and contact_peer_id is not null and contact_access_hash is not null and approval_note='permanent_telegram_outbound_error'")
  suppressed_new=scalar(db,"select count(*) from boris_sales_hot_leads l where l.status='new' and exists(select 1 from telegram_sales_identity_suppressions s where s.peer_id=l.contact_peer_id or (coalesce(s.username,'')<>'' and lower(s.username)=lower(coalesce(l.contact_username,''))))")
 prc,pout=run(['bash','-lc',"pgrep -af 'monitor_runner.py --scan --platform telegram' | grep -v pgrep | wc -l"])
 try: scanners=int((pout.splitlines() or ['0'])[-1].strip())
 except: scanners=99
 ok=(arc==0 and hrc==0 and scanners<=1 and bad_min==0 and suppressed_new==0)
 if not ok: failures+=1
 emit({'event':'tick','tick':tick,'at':now,'ok':ok,'audit_rc':arc,'health_rc':hrc,'scanner_processes':scanners,'bad_min':bad_min,'suppressed_new':suppressed_new,'lead4184':dict(lead) if lead else None,'replies4184':replies,'latest_cycle':dict(latest) if latest else None,'audit_tail':'\n'.join(aout.splitlines()[-2:]),'health_tail':'\n'.join(hout.splitlines()[-4:])})
 elapsed=time.monotonic()-start
 if elapsed>=DURATION: break
 time.sleep(min(INTERVAL,max(1,DURATION-elapsed)))
status='PASS' if failures==0 else 'FAIL'
emit({'event':'end','at':datetime.now(timezone.utc),'ticks':tick,'failures':failures,'status':status,'log':str(log)})
raise SystemExit(0 if failures==0 else 2)
