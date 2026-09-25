#!/usr/bin/env python3
import hashlib,hmac,json,secrets,subprocess,time,urllib.request,urllib.error,sys
from pathlib import Path
BASE='https://boris-ai.pro/_ops'; CAND=Path('/root/BORIS/boris_ops/releases/agent-1.5.0-candidate.py'); EXPECTED_SHA='17bbcf962a34843adac86a0d93799e1dec0d84ef35f7c15964eb6227cc7f581b'; REPORT=Path('/root/BORIS/boris_ops/PROMOTION_1_5.json')
def save(o): REPORT.write_text(json.dumps(o,ensure_ascii=False,indent=2))
def state(unit): return subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()
start=time.time(); out={'contract':'BORIS_OPS_PROMOTE_1_5','started_at':start,'candidate':str(CAND),'candidate_sha256':EXPECTED_SHA,'steps':[]}
try:
    # Guarded wait: promote only after the fresh single-writer 60m 1.3 certification passes.
    deadline=time.time()+4500
    while state('boris-ops-certify60.service') in {'activating','active'}:
        if time.time()>deadline: raise RuntimeError('pre_promotion_cert_timeout')
        time.sleep(20)
    cp=Path('/root/BORIS/boris_ops/CERTIFICATION_60M.json')
    if not cp.is_file(): raise RuntimeError('pre_promotion_cert_missing')
    cert=json.loads(cp.read_text()); out['pre_promotion_cert']=cert
    if not cert.get('ok') or cert.get('expected_version')!='1.3.0': raise RuntimeError('pre_promotion_cert_not_clean_1_3')
    actual=hashlib.sha256(CAND.read_bytes()).hexdigest()
    if actual!=EXPECTED_SHA: raise RuntimeError('candidate_sha_changed:'+actual)
    env={}
    for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
        if line.strip() and '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
    T,S=env['BORIS_OPS_TOKEN'],env['BORIS_OPS_HMAC_SECRET']
    def req(path,method='GET',obj=None):
        body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); n=secrets.token_hex(16); sig=hmac.new(S.encode(),(ts+'\n'+n+'\n').encode()+body,hashlib.sha256).hexdigest()
        r=urllib.request.Request(BASE+path,data=body if method=='POST' else None,method=method,headers={'Authorization':'Bearer '+T,'X-Boris-Timestamp':ts,'X-Boris-Nonce':n,'X-Boris-Signature':sig,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
        try:
            with urllib.request.urlopen(r,timeout=20) as x:return x.status,json.loads(x.read())
        except urllib.error.HTTPError as e:
            try:return e.code,json.loads(e.read())
            except:return e.code,{'ok':False,'error':'http'}
    gate=Path('/etc/boris-ops/allow-self-update.json')
    gate.write_text(json.dumps({'sha256':EXPECTED_SHA,'expires_at':time.time()+120,'purpose':'guarded_promotion_1_5'})); gate.chmod(0o600)
    code,r=req('/self/update','POST',{'candidate':str(CAND),'sha256':EXPECTED_SHA}); out['steps'].append({'self_update_request':code,'response':r})
    if code!=202 or not r.get('ok'):
        try: gate.unlink(missing_ok=True)
        except Exception: pass
        raise RuntimeError('self_update_request_failed')
    recovered=False
    for i in range(180):
        time.sleep(.5)
        try:
            with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=2) as x:
                d=json.loads(x.read())
                if x.status==200 and d.get('version')=='1.5.0': recovered=True; out['steps'].append({'v1_5_health_after_s':round((i+1)*.5,1)}); break
        except Exception: pass
    if not recovered: raise RuntimeError('v1_5_health_not_recovered')
    time.sleep(2)
    us=json.loads(Path('/var/lib/boris-ops/update-status.json').read_text()); out['self_update_status']=us
    if not us.get('ok'): raise RuntimeError('self_update_report_failed')
    # Fast deterministic acceptance.
    for name,argv in [
        ('qa_e2e',['/usr/bin/python3','/root/BORIS/boris_ops/qa_e2e.py']),
        ('hardening',['/usr/bin/python3','/root/BORIS/boris_ops/hardening_qa.py'])]:
        q=subprocess.run(argv,capture_output=True,text=True,timeout=180); out['steps'].append({name+'_rc':q.returncode,'stdout':q.stdout[-5000:],'stderr':q.stderr[-2000:]})
        if q.returncode!=0: raise RuntimeError(name+'_failed')
    pack=subprocess.run(['/usr/bin/python3','/root/BORIS/boris_ops/build_recovery_pack.py'],capture_output=True,text=True,timeout=180)
    out['steps'].append({'recovery_pack_rc':pack.returncode,'stdout':pack.stdout[-3000:],'stderr':pack.stderr[-1000:]})
    if pack.returncode!=0: raise RuntimeError('recovery_pack_failed')
    # Ensure MCP ping and bounded-job features are visible before long soak.
    c,v=req('/version');
    if c!=200 or v.get('version')!='1.5.0' or not {'bounded_jobs','bounded_search','mcp_ping'}.issubset(set(v.get('capabilities') or [])): raise RuntimeError('v1_5_capabilities_missing')
    c,m=req('/mcp','POST',{'jsonrpc':'2.0','id':991,'method':'ping','params':{}})
    if c!=200 or (m.get('result') is None): raise RuntimeError('mcp_ping_failed')
    # Fresh long certification on promoted runtime. Scripts resolve expected version dynamically.
    subprocess.run(['systemctl','stop','boris-ops-stress45.service'],check=False)
    subprocess.run(['systemctl','reset-failed','boris-ops-certify60.service'],check=False)
    subprocess.run(['systemctl','reset-failed','boris-ops-stress45.service'],check=False)
    for f in ['/root/BORIS/boris_ops/CERTIFICATION_60M.json','/root/BORIS/boris_ops/certification-60m.jsonl','/root/BORIS/boris_ops/STRESS45.json','/root/BORIS/boris_ops/stress45.jsonl']:
        try: Path(f).unlink()
        except FileNotFoundError: pass
    subprocess.run(['systemctl','start','--no-block','boris-ops-certify60.service'],check=True)
    subprocess.run(['systemctl','start','--no-block','boris-ops-stress45.service'],check=True)
    out['post_promotion_long_qa_started']={'certify60':state('boris-ops-certify60.service'),'stress45':state('boris-ops-stress45.service')}
    out['ok']=True; out['finished_at']=time.time(); save(out); print(json.dumps({'ok':True,'contract':'BORIS_OPS_PROMOTE_1_5','long_qa_started':out['post_promotion_long_qa_started']}))
except Exception as e:
    out['ok']=False; out['error']=str(e)[:500]; out['finished_at']=time.time(); save(out); print(json.dumps({'ok':False,'contract':'BORIS_OPS_PROMOTE_1_5','error':str(e)[:500]})); sys.exit(1)
