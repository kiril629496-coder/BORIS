#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, inspect, json, os, traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine,text

ROOT=Path('/root/BORIS/backend')
load_dotenv(ROOT/'.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
TZ=os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow')
engine=create_engine(DB,pool_pre_ping=True)

def main()->int:
    path=ROOT/'tests/test_telegram_sales_regression.py'
    spec=importlib.util.spec_from_file_location('tg_sales_regression',path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    passed=[]; failed=[]
    for name,fn in sorted(inspect.getmembers(mod,inspect.isfunction)):
        if not name.startswith('test_'): continue
        try:
            fn(); passed.append(name)
        except Exception as e:
            failed.append({'name':name,'type':type(e).__name__,'error':str(e)[:400]})
            traceback.print_exc()
    now=datetime.now(ZoneInfo(TZ)); day=now.date().isoformat()
    payload={'day':day,'passed':len(passed),'failed':len(failed),'total':len(passed)+len(failed),'failures':failed,'ran_at':now.isoformat()}
    key=f'tg_sales_daily_selftest:{day}'
    with engine.begin() as db:
        db.execute(text("insert into monitor_runtime_state(key,value_text,updated_at) values(:k,:v,now()) on conflict(key) do update set value_text=excluded.value_text,updated_at=now()"),{'k':key,'v':json.dumps(payload,ensure_ascii=False,sort_keys=True)})
    status='PASS' if not failed else 'FAIL'
    print(f'TG_SALES_REGRESSION={status} passed={len(passed)} failed={len(failed)} total={len(passed)+len(failed)}')
    print(json.dumps(payload,ensure_ascii=False,sort_keys=True))
    if failed:
        try:
            from app.telegram_bot import send_telegram_message
            chat=os.getenv('HOT_LEAD_REPLY_CHAT_ID','-1003952038222'); thread=int(os.getenv('HOT_LEAD_REPLY_THREAD_ID','126') or 126)
            names=', '.join(x['name'] for x in failed[:8])
            send_telegram_message(chat,f'🚨 <b>BORIS Telegram self-test FAIL</b>\n\nНе прошли: {names}\nБезопасные автоматические контуры остаются под watchdog. Ручных действий от владельца не требуется.',thread)
        except Exception as e:
            print('TG_SALES_REGRESSION_ALERT=DEGRADED',type(e).__name__)
    return 0 if not failed else 2
if __name__=='__main__': raise SystemExit(main())
