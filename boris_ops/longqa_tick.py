#!/usr/bin/env python3
import hashlib,hmac,json,os,secrets,subprocess,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
BASE='https://boris-ai.pro/_ops'
STATE_DIR=Path('/var/lib/boris-ops/longqa'); STATE_DIR.mkdir(parents=True,exist_ok=True)
OUT_DIR=Path('/root/BORIS/boris_ops')
AGENT=OUT_DIR/'agent.py'
PROFILES={
 'cert60': {'target':60,'report':OUT_DIR/'CERTIFICATION_RESUMABLE_60M.json','log':OUT_DIR/'certification-resumable-60m.jsonl','stress':False},
 'stress45': {'target':45,'report':OUT_DIR/'STRESS_RESUMABLE_45M.json','log':OUT_DIR/'stress-resumable-45m.jsonl','stress':True},
}
# A real public BORIS 5xx is a certification failure even when the Ops control
# plane itself is healthy. Policy changes reset resumable evidence so an older
# permissive sample can never be carried into a strict acceptance result.
POLICY='strict-public-v3-autoretry'
env={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
TOKEN=env['BORIS_OPS_TOKEN']; SECRET=env['BORIS_OPS_HMAC_SECRET']

def req(path,method='GET',obj=None,timeout=10):
    body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(16)
    sig=hmac.new(SECRET.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
    r=urllib.request.Request(BASE+path,data=body if method=='POST' else None,method=method,headers={'Authorization':'Bearer '+TOKEN,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x:return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e:
        try:return e.code,json.loads(e.read())
        except Exception:return e.code,{'error':'http'}

def atom(path,obj):
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8'); os.replace(tmp,path)

def fresh_identity():
    with urllib.request.urlopen(BASE+'/health',timeout=8) as r: d=json.loads(r.read())
    return str(d.get('version') or ''),hashlib.sha256(AGENT.read_bytes()).hexdigest()

def sample(profile,version,sha):
    row={'ts':time.time(),'version_expected':version,'sha_expected':sha}
    try:
        with urllib.request.urlopen(BASE+'/health',timeout=8) as r:
            h=json.loads(r.read()); row['health_status']=r.status; row['health_version']=h.get('version')
        vs,v=req('/version'); row['version_status']=vs; row['version']=v.get('version')
        bs,b=req('/boris/health'); row['boris_health_status']=bs; row['boris_ok']=b.get('ok'); row['boris_health']=b
        ms,m=req('/mcp','POST',{'jsonrpc':'2.0','id':int(time.time()),'method':'tools/list','params':{}}); row['mcp_status']=ms; row['tools']=len((m.get('result') or {}).get('tools') or [])
        row['service']=subprocess.run(['systemctl','is-active','boris-ops.service'],capture_output=True,text=True,timeout=5).stdout.strip()
        row['watchdog']=subprocess.run(['systemctl','is-active','boris-ops-watchdog.timer'],capture_output=True,text=True,timeout=5).stdout.strip()
        row['sha_current']=hashlib.sha256(AGENT.read_bytes()).hexdigest()
        if profile['stress']:
            es,e=req('/exec','POST',{'command':'uptime'}); row['safe_exec']=es==200 and e.get('ok') is True
        else: row['safe_exec']=True
        row['control_plane_ok']=row['health_status']==200 and row['health_version']==version and vs==200 and row['version']==version and ms==200 and row['tools']>=20 and row['service']=='active' and row['watchdog']=='active' and row['sha_current']==sha and row['safe_exec']
        row['dependency_ok']=bs==200 and b.get('ok') is True
        row['ok']=row['control_plane_ok']
    except Exception as e:
        row['control_plane_ok']=False; row['dependency_ok']=False; row['ok']=False; row['error']=repr(e)
    return row

def archive_attempt(name,state):
    try:
        hist=STATE_DIR/'history'; hist.mkdir(parents=True,exist_ok=True)
        ts=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
        atom(hist/f"{name}-{ts}-{secrets.token_hex(3)}.json",state)
    except Exception:
        pass

def tick(name,p):
    version,sha=fresh_identity(); sf=STATE_DIR/(name+'.json')
    try: state=json.loads(sf.read_text())
    except Exception: state={}
    identity=f'{version}:{sha}'
    reset_reason=None
    if state.get('identity')!=identity: reset_reason='identity_changed'
    elif state.get('policy')!=POLICY: reset_reason='policy_changed'
    elif state.get('restart_after_failure') is True: reset_reason='retry_after_failure'
    if reset_reason:
        if state: archive_attempt(name,state)
        attempt=int(state.get('attempt',0))+1 if state.get('identity')==identity else 1
        state={'schema':3,'policy':POLICY,'profile':name,'identity':identity,'version':version,'agent_sha256':sha,'attempt':attempt,'reset_reason':reset_reason,'started_at':time.time(),'samples':0,'control_failures':0,'dependency_degraded_events':0,'consecutive_dependency_failures':0,'failure_rows':[],'degraded_rows':[],'complete':False,'restart_after_failure':False}
        p['log'].write_text('',encoding='utf-8')
    elif state.get('complete') is True and state.get('ok') is True:
        # Freeze only a completed PASS until runtime identity or policy changes.
        return {'profile':name,'sample':state.get('samples',0),'target':p['target'],'effective_ok':True,'complete':True,'version':version,'frozen':True,'attempt':state.get('attempt',1)}
    elif state.get('complete') is True:
        archive_attempt(name,state)
        state={'schema':3,'policy':POLICY,'profile':name,'identity':identity,'version':version,'agent_sha256':sha,'attempt':int(state.get('attempt',0))+1,'reset_reason':'completed_failure_retry','started_at':time.time(),'samples':0,'control_failures':0,'dependency_degraded_events':0,'consecutive_dependency_failures':0,'failure_rows':[],'degraded_rows':[],'complete':False,'restart_after_failure':False}
        p['log'].write_text('',encoding='utf-8')
    row=sample(p,version,sha); row['sample']=state['samples']+1
    if row.get('dependency_ok'): state['consecutive_dependency_failures']=0
    else:
        state['consecutive_dependency_failures']+=1; state['dependency_degraded_events']+=1; state['degraded_rows']=(state['degraded_rows']+[row])[-50:]
    # Control-plane failure always fails. A real public BORIS dependency failure
    # also fails immediately; do not hide a 5xx behind a one-sample grace window.
    effective_ok=bool(row.get('control_plane_ok')) and bool(row.get('dependency_ok'))
    row['effective_ok']=effective_ok; row['dependency_fail_streak']=state['consecutive_dependency_failures']
    if not effective_ok:
        state['control_failures']+=1; state['failure_rows']=(state['failure_rows']+[row])[-20:]; state['restart_after_failure']=True; state['failed_at']=time.time()
    with p['log'].open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    state['samples']+=1; state['last_sample_at']=time.time(); state['last_row']=row
    if state['samples']>=p['target']:
        state['complete']=True; state['finished_at']=time.time(); state['duration_s']=round(state['finished_at']-state['started_at'],1)
        state['ok']=state['control_failures']==0 and hashlib.sha256(AGENT.read_bytes()).hexdigest()==sha
        report={'contract':'BORIS_OPS_RESUMABLE_'+name.upper(),'ok':state['ok'],'policy':POLICY,'attempt':state.get('attempt',1),'version':version,'agent_sha256':sha,'samples':state['samples'],'target_samples':p['target'],'failures':state['control_failures'],'dependency_degraded_events':state['dependency_degraded_events'],'started_at':state['started_at'],'finished_at':state['finished_at'],'duration_s':state['duration_s'],'failure_rows':state['failure_rows'],'degraded_rows':state['degraded_rows']}
        atom(p['report'],report)
    atom(sf,state)
    return {'profile':name,'sample':state['samples'],'target':p['target'],'effective_ok':effective_ok,'complete':state['complete'],'version':version}

results=[]
for name,p in PROFILES.items():
    try: results.append(tick(name,p))
    except Exception as e: results.append({'profile':name,'ok':False,'error':repr(e)})
print(json.dumps({'ok':all(r.get('effective_ok',r.get('complete',False)) or r.get('complete',False) for r in results if 'error' not in r) and not any('error' in r for r in results),'results':results},ensure_ascii=False))
