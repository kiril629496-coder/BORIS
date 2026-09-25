#!/usr/bin/env python3
import hashlib,hmac,json,secrets,time,urllib.request,urllib.error,subprocess,fcntl,sys
from pathlib import Path
LOCK=Path("/var/lib/boris-ops/certify60.lock")
LOCK.parent.mkdir(parents=True, exist_ok=True)
CERT_LOCK_FD=LOCK.open("a+")
try:
    fcntl.flock(CERT_LOCK_FD.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(json.dumps({"ok":False,"error":"certification_already_running","lock":str(LOCK)}))
    raise SystemExit(75)
BASE='https://boris-ai.pro/_ops'
ENV={}
for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1); ENV[k]=v.strip().strip('"').strip("'")
T=ENV['BORIS_OPS_TOKEN']; S=ENV['BORIS_OPS_HMAC_SECRET']
OUT=Path('/root/BORIS/boris_ops/certification-60m.jsonl'); REPORT=Path('/root/BORIS/boris_ops/CERTIFICATION_60M.json'); AGENT=Path('/root/BORIS/boris_ops/agent.py')

def req(path,method='GET',obj=None):
    body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(12); sig=hmac.new(S.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
    r=urllib.request.Request(BASE+path,data=(body if method=='POST' else None),method=method,headers={'Authorization':'Bearer '+T,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(r,timeout=10) as x:return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e:
        try:return e.code,json.loads(e.read())
        except:return e.code,{'error':'http'}
start=time.time(); failures=[]; samples=0; dependency_fail_streak=0; degraded_events=[]; OUT.write_text(''); expected_agent_sha256=hashlib.sha256(AGENT.read_bytes()).hexdigest()
with urllib.request.urlopen(BASE+'/health',timeout=10) as _r:
    expected=json.loads(_r.read()).get('version')
if not expected:
    raise SystemExit('cannot_resolve_expected_version')
for i in range(120):
    row={'ts':time.time(),'sample':i+1}
    try:
        with urllib.request.urlopen(BASE+'/health',timeout=5) as x: hs=x.status; hb=json.loads(x.read())
        vs,v=req('/version'); bs,b=req('/boris/health')
        # A rolling restart is not a BORIS outage when one backend remains live and
        # the public health endpoint is still good. Preserve the first payload for
        # evidence and allow one bounded confirmation retry only for that exact case.
        if bs != 200 or b.get('ok') is not True:
            row['boris_health_initial']={'status':bs,'ok':b.get('ok'),'backend_pair_ok':b.get('backend_pair_ok'),'redundancy_degraded':b.get('redundancy_degraded'),'http':b.get('http'),'services':b.get('services')}
            public_ok=bool(((b.get('http') or {}).get('public') or {}).get('ok'))
            if b.get('backend_pair_ok') is True and public_ok:
                time.sleep(2)
                bs2,b2=req('/boris/health')
                row['boris_health_retry']={'status':bs2,'ok':b2.get('ok'),'backend_pair_ok':b2.get('backend_pair_ok'),'redundancy_degraded':b2.get('redundancy_degraded'),'http':b2.get('http'),'services':b2.get('services')}
                if bs2 == 200 and b2.get('ok') is True:
                    row['rolling_restart_transient_recovered']=True; bs,b=bs2,b2
        ms,m=req('/mcp','POST',{'jsonrpc':'2.0','id':i+1,'method':'tools/list','params':{}})
        svc=subprocess.run(['systemctl','is-active','boris-ops.service'],capture_output=True,text=True,timeout=5).stdout.strip()
        wd=subprocess.run(['systemctl','is-active','boris-ops-watchdog.timer'],capture_output=True,text=True,timeout=5).stdout.strip()
        current_agent_sha256=hashlib.sha256(AGENT.read_bytes()).hexdigest()
        row.update({'health_status':hs,'health_version':hb.get('version'),'version_status':vs,'version':v.get('version'),'boris_health_status':bs,'boris_ok':b.get('ok'),'mcp_status':ms,'tools':len((m.get('result') or {}).get('tools') or []),'service':svc,'watchdog':wd,'agent_sha256':current_agent_sha256,'agent_sha_stable':current_agent_sha256==expected_agent_sha256})
        control_plane_ok = hs==200 and vs==200 and ms==200 and svc=='active' and wd=='active' and v.get('version')==expected and current_agent_sha256==expected_agent_sha256
        dependency_ok = bs==200 and b.get('ok') is True
        if dependency_ok:
            dependency_fail_streak=0
        else:
            dependency_fail_streak+=1
            row['dependency_degraded']=True
            row['dependency_fail_streak']=dependency_fail_streak
            degraded_events.append({'sample':i+1,'ts':row['ts'],'status':bs,'payload':b})
        # One isolated BORIS dependency blip is recorded as degraded, not an Ops outage.
        # Two consecutive dependency failures do fail certification.
        row['ok']=control_plane_ok and (dependency_ok or dependency_fail_streak < 2)
    except Exception as e:
        row['ok']=False; row['error']=repr(e)
    if not row['ok']: failures.append(row)
    with OUT.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    samples+=1
    if i<119: time.sleep(30)
final_agent_sha256=hashlib.sha256(AGENT.read_bytes()).hexdigest()
report={'contract':'BORIS_OPS_60M_CERTIFICATION','ok':not failures and final_agent_sha256==expected_agent_sha256,'expected_version':expected,'expected_agent_sha256':expected_agent_sha256,'final_agent_sha256':final_agent_sha256,'samples':samples,'failures':len(failures)+(0 if final_agent_sha256==expected_agent_sha256 else 1),'duration_s':round(time.time()-start,1),'started_at':start,'finished_at':time.time(),'failure_rows':failures[:20]+([] if final_agent_sha256==expected_agent_sha256 else [{'error':'agent_sha_changed_after_last_sample','expected':expected_agent_sha256,'actual':final_agent_sha256}]),'degraded_events':degraded_events[:50]}
REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps(report,ensure_ascii=False)); raise SystemExit(0 if report['ok'] else 1)
