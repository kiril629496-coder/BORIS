#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path('/root/BORIS/backend')
PY=str(ROOT/'venv/bin/python')
DURATION=int(os.getenv('TG_SOAK_DURATION_SECONDS','3600') or 3600)
INTERVAL=int(os.getenv('TG_SOAK_INTERVAL_SECONDS','300') or 300)
LOGDIR=ROOT/'logs'; LOGDIR.mkdir(exist_ok=True)
LOG=LOGDIR/f"telegram_sales_soak_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"

def run(script, timeout=90):
    p=subprocess.run([PY,str(ROOT/script)],cwd=str(ROOT),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
    return p.returncode,(p.stdout or '').strip()

def emit(row):
    with LOG.open('a') as f: f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
    print(json.dumps(row,ensure_ascii=False,default=str),flush=True)

def main():
    start=time.monotonic(); end=start+DURATION; tick=0; failures=0
    emit({'event':'start','at':datetime.now(timezone.utc).isoformat(),'duration_sec':DURATION,'interval_sec':INTERVAL,'log':str(LOG)})
    while True:
        tick+=1
        at=datetime.now(timezone.utc).isoformat()
        try:
            arc,aout=run('telegram_sales_production_audit.py',60)
        except Exception as e:
            arc,aout=99,f'{type(e).__name__}: {e}'
        try:
            wrc,wout=run('telegram_sales_watchdog.py',90)
        except Exception as e:
            wrc,wout=99,f'{type(e).__name__}: {e}'
        ok=(arc==0 and wrc==0)
        if not ok: failures+=1
        emit({'event':'tick','tick':tick,'at':at,'ok':ok,'audit_rc':arc,'watchdog_rc':wrc,'audit_tail':aout[-1200:],'watchdog_tail':wout[-1200:]})
        now=time.monotonic()
        if now>=end: break
        time.sleep(min(INTERVAL,max(1,end-now)))
    emit({'event':'end','at':datetime.now(timezone.utc).isoformat(),'ticks':tick,'failures':failures,'status':'PASS' if failures==0 else 'FAIL','log':str(LOG)})
    return 0 if failures==0 else 2

if __name__=='__main__': raise SystemExit(main())
