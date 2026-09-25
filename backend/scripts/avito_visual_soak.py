#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, time, urllib.request, urllib.error, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from sqlalchemy import text
from app.db.session import SessionLocal

DURATION=int(os.environ.get('BORIS_VISUAL_SOAK_SECONDS','2700'))
INTERVAL=int(os.environ.get('BORIS_VISUAL_SOAK_INTERVAL','30'))
FORBIDDEN=(b'fullai_',b'gptimg_',b'boris_deterministic',b'boris_unique',b'campaign_openai_gpt_image_2')
ROOT='/root/BORIS/backend'

def now(): return datetime.now(timezone.utc).isoformat()
def emit(kind, **kw):
    print(json.dumps({'ts':now(),'kind':kind,**kw},ensure_ascii=False),flush=True)

def http(url,timeout=20):
    req=urllib.request.Request(url,headers={'User-Agent':'BORIS-visual-soak/1.0','Cache-Control':'no-cache','Pragma':'no-cache'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r: body=r.read(); code=r.status
        return {'ok':code==200,'code':code,'bytes':len(body),'body':body}
    except Exception as e:
        return {'ok':False,'error':type(e).__name__+':'+str(e)[:180],'body':b''}

def accounts():
    s=SessionLocal()
    try:return [r[0] for r in s.execute(text("select distinct account_id from storage where key='feed_items' order by account_id")).fetchall() if r[0]]
    finally:s.close()

def run_contract(path):
    p=subprocess.run([f'{ROOT}/venv/bin/python',f'{ROOT}/scripts/{path}'],cwd=ROOT,env={**os.environ,'PYTHONPATH':ROOT},text=True,capture_output=True,timeout=60)
    emit('contract',script=path,ok=p.returncode==0,rc=p.returncode,tail=(p.stdout+p.stderr)[-2500:])
    return p.returncode==0

def feed_sweep():
    ok=0; fail=[]
    for a in accounts():
        sides={}
        for label,base in [('local','http://127.0.0.1:8000'),('public','https://boris-ai.pro')]:
            url=f'{base}/api/avito/feed/{a}.xml'
            if label=='public': url += '?visual_guard=' + str(time.time_ns())
            r=http(url,30); body=r.pop('body',b'')
            if r.get('ok'):
                try: ET.fromstring(body); r['xml']=True
                except Exception as e: r['xml']=False; r['error']='XML:'+str(e)[:120]
                r['forbidden']=[x.decode() for x in FORBIDDEN if x in body.lower()]
                r['ok']=bool(r.get('ok') and r.get('xml') and not r['forbidden'])
            sides[label]=r
        good=all(x.get('ok') for x in sides.values())
        ok+=int(good)
        if not good: fail.append({'account':a,'sides':sides})
    emit('feed_sweep',ok=not fail,passed=ok,total=ok+len(fail),failures=fail[:20])
    return not fail

start=time.time(); tick=0; failures=0
emit('soak_start',duration_seconds=DURATION,interval_seconds=INTERVAL)
while time.time()-start < DURATION:
    tick+=1
    local=http('http://127.0.0.1:8000/health',8); local.pop('body',None)
    public=http('https://boris-ai.pro/health?visual_guard='+str(time.time_ns()),12); public.pop('body',None)
    service=subprocess.run(['systemctl','is-active','boris-backend'],capture_output=True,text=True).stdout.strip()
    health_ok=local.get('ok') and public.get('ok') and service=='active'
    if not health_ok: failures+=1
    emit('health',tick=tick,ok=health_ok,service=service,local=local,public=public,failures=failures)
    if tick==1 or tick%10==0: feed_sweep()
    if tick==1 or tick%20==0:
        run_contract('avito_visual_contract_regression.py')
        run_contract('avito_media_quality_audit.py')
        try:
            payload=json.dumps({'account_id':'qa_visual_contract','raw_description':'soak-no-paid','use_template_library':False}).encode('utf-8')
            req=urllib.request.Request('http://127.0.0.1:8000/api/banners/full_ai',data=payload,headers={'Content-Type':'application/json','Cache-Control':'no-cache','Pragma':'no-cache'},method='POST')
            with urllib.request.urlopen(req,timeout=10) as r:
                legacy_body=r.read().decode('utf-8','ignore')
            legacy_ok='legacy_text_in_image_forbidden' in legacy_body
            emit('legacy_endpoint_guard',ok=legacy_ok,body=legacy_body[:500])
            if not legacy_ok: failures+=1
        except Exception as exc:
            failures+=1; emit('legacy_endpoint_guard',ok=False,error=type(exc).__name__+':'+str(exc)[:220])
    time.sleep(INTERVAL)
emit('soak_end',ticks=tick,health_failures=failures,ok=failures==0)
