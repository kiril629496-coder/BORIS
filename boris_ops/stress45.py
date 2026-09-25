#!/usr/bin/env python3
import hashlib,hmac,json,secrets,time,urllib.request,urllib.error,fcntl,sys
from pathlib import Path
LOCK=Path("/var/lib/boris-ops/stress45.lock")
LOCK.parent.mkdir(parents=True, exist_ok=True)
CERT_LOCK_FD=LOCK.open("a+")
try:
    fcntl.flock(CERT_LOCK_FD.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(json.dumps({"ok":False,"error":"certification_already_running","lock":str(LOCK)}))
    raise SystemExit(75)
BASE='https://boris-ai.pro/_ops'
env={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
TOKEN=env['BORIS_OPS_TOKEN']; SECRET=env['BORIS_OPS_HMAC_SECRET']
LOG=Path('/root/BORIS/boris_ops/stress45.jsonl'); REPORT=Path('/root/BORIS/boris_ops/STRESS45.json'); AGENT=Path('/root/BORIS/boris_ops/agent.py')
def req(path,method='GET',obj=None):
    body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(16)
    sig=hmac.new(SECRET.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
    r=urllib.request.Request(BASE+path,data=body if method=='POST' else None,method=method,headers={'Authorization':'Bearer '+TOKEN,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(r,timeout=10) as x:return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e:
        try:return e.code,json.loads(e.read())
        except:return e.code,{'error':'http'}
fail=[]; degraded=[]; dependency_fail_streak=0; start=time.time(); samples=0; expected_agent_sha256=hashlib.sha256(AGENT.read_bytes()).hexdigest()
with urllib.request.urlopen(BASE+'/health',timeout=10) as _r:
    expected=json.loads(_r.read()).get('version')
if not expected: raise SystemExit('cannot_resolve_expected_version')
LOG.write_text('')
for i in range(60):
    row={'sample':i+1,'ts':time.time()}
    try:
        with urllib.request.urlopen(BASE+'/health',timeout=8) as r: row['health']=r.status==200
        vs,v=req('/version'); row['version']=vs==200 and v.get('version')==expected
        bs,b=req('/boris/health'); row['boris_health']=bs==200 and b.get('ok') is True
        if row['boris_health']:
            dependency_fail_streak=0
        else:
            dependency_fail_streak+=1; row['dependency_degraded']=True; row['dependency_fail_streak']=dependency_fail_streak; degraded.append({'sample':i+1,'ts':row['ts'],'status':bs,'payload':b})
        ms,m=req('/mcp','POST',{'jsonrpc':'2.0','id':i+1,'method':'tools/list','params':{}}); row['mcp']=ms==200 and len((m.get('result') or {}).get('tools') or [])>=20
        es,e=req('/exec','POST',{'command':'uptime'}); row['safe_exec']=es==200 and e.get('ok') is True
        current_agent_sha256=hashlib.sha256(AGENT.read_bytes()).hexdigest(); row['agent_sha256']=current_agent_sha256; row['agent_sha_stable']=current_agent_sha256==expected_agent_sha256
        row['ok']=all(row[k] for k in ['health','version','mcp','safe_exec','agent_sha_stable']) and (row['boris_health'] or dependency_fail_streak < 2)
    except Exception as e:
        row['ok']=False; row['error']=repr(e)
    if not row['ok']: fail.append(row)
    with LOG.open('a') as f:f.write(json.dumps(row,separators=(',',':'))+'\n')
    samples+=1
    if i<59: time.sleep(45)
final_agent_sha256=hashlib.sha256(AGENT.read_bytes()).hexdigest()
rep={'contract':'BORIS_OPS_STRESS45','ok':not fail and final_agent_sha256==expected_agent_sha256,'expected_version':expected,'expected_agent_sha256':expected_agent_sha256,'final_agent_sha256':final_agent_sha256,'samples':samples,'failures':len(fail)+(0 if final_agent_sha256==expected_agent_sha256 else 1),'duration_s':round(time.time()-start,1),'failure_rows':fail[:20]+([] if final_agent_sha256==expected_agent_sha256 else [{'error':'agent_sha_changed_after_last_sample','expected':expected_agent_sha256,'actual':final_agent_sha256}]),'degraded_events':degraded[:50]}
REPORT.write_text(json.dumps(rep,indent=2)); print(json.dumps(rep))
raise SystemExit(0 if rep['ok'] else 1)
