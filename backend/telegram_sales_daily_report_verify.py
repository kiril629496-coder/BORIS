#!/usr/bin/env python3
from __future__ import annotations
import os, subprocess, json
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
TZ=os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')
engine=create_engine(DB,pool_pre_ping=True)

def receipt_message_id(value):
    try:
        data=json.loads(str(value or ''))
        mid=int((data or {}).get('message_id') or 0)
        return mid if mid>0 else None
    except Exception:
        return None

def main()->int:
    now=datetime.now(ZoneInfo(TZ)); day=now.date().isoformat()
    # This unit is scheduled for 19:43 Moscow. A manual/early run is read-only.
    if now.hour < 19 or (now.hour==19 and now.minute<43):
        print(f'TG_DAILY_REPORT_VERIFY=SKIP before_window now={now.strftime("%H:%M")} earliest=19:43 {TZ}')
        return 0
    key=f'tg_sales_daily_report:{day}'
    with engine.begin() as db:
        receipt=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':key}).scalar()
    confirmed_mid=receipt_message_id(receipt)
    if confirmed_mid:
        print(f'TG_DAILY_REPORT_VERIFY=PASS already_confirmed day={day} message_id={confirmed_mid}')
        return 0
    p=subprocess.run(['/root/BORIS/backend/venv/bin/python','/root/BORIS/backend/telegram_sales_daily_report.py','--send'],
                     cwd='/root/BORIS/backend',text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
    print((p.stdout or '').strip())
    with engine.begin() as db:
        receipt=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':key}).scalar()
    confirmed_mid=receipt_message_id(receipt)
    print(f'TG_DAILY_REPORT_VERIFY={"PASS" if confirmed_mid else "FAIL"} retry_rc={p.returncode} day={day} message_id={confirmed_mid or 0}')
    return 0 if confirmed_mid else 2
if __name__=='__main__': raise SystemExit(main())
