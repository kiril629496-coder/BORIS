#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
load_dotenv('/root/BORIS/backend/.env')
ROOT='/root/BORIS/backend'; PY=ROOT+'/venv/bin/python'
TZ=ZoneInfo(os.getenv('HOT_LEAD_AUTO_TIMEZONE','Europe/Moscow'))
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)

def run(script, timeout=90):
    env=os.environ.copy()
    if script=='telegram_sales_production_audit.py': env['TG_PREFLIGHT_CONTEXT']='1'
    p=subprocess.run([PY,ROOT+'/'+script],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,env=env)
    return p.returncode,(p.stdout or '').strip()

def telegram_session_ok():
    from app.crypto_utils import decrypt_secret
    from app.monitoring.telegram import build_client
    session=decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC'])
    client=build_client(session,int(os.environ['MONITOR_TG_API_ID']),os.environ['MONITOR_TG_API_HASH'])
    try:
        client.connect(); me=client.get_me()
        return bool(me and getattr(me,'id',None)), int(getattr(me,'id',0) or 0)
    finally:
        try: client.disconnect()
        except Exception: pass

def build_reactivation_plan(limit: int = 4):
    from telegram_contact_reactivation import reactivation_quality_ok, reactivation_priority, draft, score_text
    out=[]
    with engine.begin() as db:
        rows=db.execute(text("""select id,contact_name,contact_username,original_text,boris_fit,reactivation_priority,reactivation_priority_reason
          from boris_sales_hot_leads
          where status='new' and monitor_intent='existing_contact_reactivation' and boris_fit>=75
          order by coalesce(reactivation_priority,0) desc,boris_fit desc,coalesce(source_posted_at,discovered_at) desc,id""")).mappings().all()
        rejected=0
        for r in rows:
            name=r.get('contact_name') or ''; ctx=r.get('original_text') or ''
            if not reactivation_quality_ok(name,ctx):
                db.execute(text("update boris_sales_hot_leads set status='skipped',skipped_at=coalesce(skipped_at,now()),approval_note='morning_preflight_reactivation_quality_guard',updated_at=now() where id=:i and status='new'"),{'i':r['id']})
                rejected+=1
                continue
            pr,reason=reactivation_priority(name,ctx,r.get('contact_username'))
            _,hits=score_text(ctx); msg=draft(name,ctx,hits)
            db.execute(text("update boris_sales_hot_leads set reactivation_priority=:p,reactivation_priority_reason=:r,first_message_draft=:d,updated_at=now() where id=:i"),{'p':pr,'r':reason,'d':msg,'i':r['id']})
            out.append({'lead_id':int(r['id']),'name':str(name),'username':str(r.get('contact_username') or ''),'priority':pr,'reason':reason})
        out.sort(key=lambda x:(-x['priority'],x['lead_id']))
        out=out[:max(0,int(limit))]
    return out,rejected


def previous_report_late_events(now):
    """Collect buyer replies received after the previous day's confirmed evening report."""
    from datetime import timedelta
    prev=(now.date()-timedelta(days=1)).isoformat()
    report_key=f'tg_sales_daily_report:{prev}'
    with engine.connect() as db:
        raw=db.execute(text('select value_text from monitor_runtime_state where key=:k'),{'k':report_key}).scalar()
        sent_at=None
        try:
            payload=json.loads(str(raw or ''))
            sent_raw=str((payload or {}).get('sent_at') or '')
            sent_at=datetime.fromisoformat(sent_raw.replace('Z','+00:00')) if sent_raw else None
        except Exception:
            sent_at=None
        if sent_at is None:
            return {'previous_day':prev,'report_found':False,'count':0,'items':[]}
        rows=db.execute(text("""
          with late as (
            select r.*,
                   count(*) over(partition by r.hot_lead_id) as reply_count,
                   row_number() over(partition by r.hot_lead_id order by r.received_at desc,r.id desc) as rn
            from boris_sales_tg_replies r
            where r.received_at>:sent_at and r.received_at<:now
              and coalesce(r.auto_reply_policy,'')<>'vendor_handoff'
          )
          select r.id,r.hot_lead_id,r.received_at,r.body,r.alerted_at,r.reply_count,
                 ml.id as manager_lead_id,ml.status as manager_status,
                 l.contact_username,
                 exists(select 1 from manager_notes n where n.lead_id=ml.id and n.kind='reminder' and coalesce(n.done,false)=false) as has_next_action
          from late r
          join boris_sales_hot_leads l on l.id=r.hot_lead_id
          left join manager_leads ml on ml.id=r.manager_lead_id
          where r.rn=1
          order by r.received_at
        """),{'sent_at':sent_at,'now':now}).mappings().all()
    items=[]
    for r in rows:
        items.append({
          'reply_id':int(r['id']),'hot_lead_id':int(r['hot_lead_id']),
          'received_at':r['received_at'].isoformat() if r.get('received_at') else None,
          'username':str(r.get('contact_username') or ''),
          'body':str(r.get('body') or '')[:220],
          'reply_count':int(r.get('reply_count') or 1),
          'alerted':bool(r.get('alerted_at')),
          'manager_lead_id':int(r['manager_lead_id']) if r.get('manager_lead_id') else None,
          'manager_status':str(r.get('manager_status') or ''),
          'has_next_action':bool(r.get('has_next_action')),
        })
    return {'previous_day':prev,'report_found':True,'report_sent_at':sent_at.isoformat(),'count':len(items),'items':items}

def main():
    now=datetime.now(TZ); steps={}
    try:
        late=previous_report_late_events(now); steps['late_events']={'ok':True,**late}
    except Exception as e:
        steps['late_events']={'ok':False,'error':type(e).__name__}
    try:
        ok,uid=telegram_session_ok(); steps['telegram_session']={'ok':ok,'user_id':uid}
    except Exception as e:
        steps['telegram_session']={'ok':False,'error':type(e).__name__}
    try:
        from telegram_sales_replies import ensure_active_crm_next_actions
        repaired=ensure_active_crm_next_actions(); steps['crm_next_actions']={'ok':True,'repaired':int(repaired)}
        plan,rejected=build_reactivation_plan(4); steps['reactivation_plan']={'ok':True,'count':len(plan),'rejected':int(rejected),'items':plan}
    except Exception as e:
        steps['crm_next_actions']={'ok':False,'error':type(e).__name__}
    for name,script,timeout in (
        ('delivery_reconcile','telegram_delivery_reconcile.py',120),
        ('regression','telegram_sales_regression_runner.py',120),
        ('audit','telegram_sales_production_audit.py',120),
    ):
        try:
            rc,out=run(script,timeout); steps[name]={'ok':rc==0,'rc':rc,'tail':out[-700:]}
        except Exception as e:
            steps[name]={'ok':False,'error':type(e).__name__}
    ok=all(bool(v.get('ok')) for v in steps.values())
    payload={'day':now.date().isoformat(),'ran_at':now.isoformat(),'ok':ok,'steps':steps}
    with engine.begin() as db:
        db.execute(text("""insert into monitor_runtime_state(key,value_text,updated_at)
          values(:k,:v,now()) on conflict(key) do update set value_text=excluded.value_text,updated_at=now()"""),
          {'k':f'tg_sales_morning_preflight:{now.date().isoformat()}','v':json.dumps(payload,ensure_ascii=False)})
        db.execute(text("""insert into monitor_runtime_state(key,value_text,updated_at) values(:k,:v,now()) on conflict(key) do update set value_text=excluded.value_text,updated_at=now()"""),{'k':f'tg_sales_reactivation_plan:{now.date().isoformat()}','v':json.dumps({'day':now.date().isoformat(),'generated_at':now.isoformat(),'items':steps.get('reactivation_plan',{}).get('items',[])},ensure_ascii=False)})
    print('TG_MORNING_PREFLIGHT='+('PASS' if ok else 'FAIL'))
    print(json.dumps(payload,ensure_ascii=False))
    return 0 if ok else 2
if __name__=='__main__': raise SystemExit(main())
