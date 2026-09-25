#!/usr/bin/env python3
import hashlib,hmac,json,secrets,time,urllib.request,subprocess
from pathlib import Path
E={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); E[k]=v.strip().strip('"').strip("'")
B='https://boris-ai.pro/_ops'; T=E['BORIS_OPS_TOKEN']; S=E['BORIS_OPS_HMAC_SECRET']
RUN=Path('/var/lib/boris-ops/certification'); RUN.mkdir(parents=True,exist_ok=True)
out=RUN/'latest.jsonl'; summary=RUN/'latest-summary.json'; out.write_text('')
def req(path,method='GET',obj=None):
    body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(12); sig=hmac.new(S.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
    r=urllib.request.Request(B+path,data=body if method=='POST' else None,method=method,headers={'Authorization':'Bearer '+T,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
    with urllib.request.urlopen(r,timeout=10) as x:return x.status,json.loads(x.read())
start=time.time(); failures=[]; samples=0
for _ in range(90):
    row={'ts':time.time()}
    try:
        with urllib.request.urlopen(B+'/health',timeout=5) as x: row['health_status']=x.status; row['health']=json.loads(x.read())
        row['version']=req('/version'); row['boris_health']=req('/boris/health'); row['mcp']=req('/mcp','POST',{'jsonrpc':'2.0','id':samples+1,'method':'tools/list','params':{}})
        row['service']=subprocess.run(['systemctl','is-active','boris-ops.service'],capture_output=True,text=True,timeout=5).stdout.strip(); row['watchdog']=subprocess.run(['systemctl','is-active','boris-ops-watchdog.timer'],capture_output=True,text=True,timeout=5).stdout.strip()
        row['ok']=row['health_status']==200 and row['version'][0]==200 and row['mcp'][0]==200 and row['service']=='active' and row['watchdog']=='active'
    except Exception as e: row['ok']=False; row['error']=repr(e)
    if not row['ok']: failures.append(row)
    with out.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    samples+=1; time.sleep(30)
report={'ok':not failures,'samples':samples,'failures':len(failures),'duration_s':round(time.time()-start,1),'finished_at':time.time()}
summary.write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps(report,ensure_ascii=False),flush=True)
raise SystemExit(0 if report['ok'] else 1)
