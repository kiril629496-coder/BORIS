#!/usr/bin/env python3
import hashlib,hmac,json,secrets,subprocess,time,urllib.request,urllib.error,sys
from pathlib import Path
BASE='https://boris-ai.pro/_ops'
CAND=Path('/root/BORIS/boris_ops/releases/agent-1.4.0-candidate.py')
EXPECTED_SHA='6926ecada136a1be2d3dd3fc8f5a7d3d641f4523c7200692e798fbf485f77785'
REPORT=Path('/root/BORIS/boris_ops/PROMOTION_1_4.json')
def save(obj): REPORT.write_text(json.dumps(obj,ensure_ascii=False,indent=2))
def active(unit): return subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()
start=time.time(); state={'contract':'BORIS_OPS_PROMOTE_1_4','started_at':start,'candidate':str(CAND),'candidate_sha256':EXPECTED_SHA,'steps':[]}
try:
    # Wait for the already-running 60m certification; never interrupt it.
    deadline=time.time()+4300
    while active('boris-ops-certify60.service') in {'activating','active'}:
        if time.time()>deadline: raise RuntimeError('pre_promotion_cert_timeout')
        time.sleep(20)
    certp=Path('/root/BORIS/boris_ops/CERTIFICATION_60M.json')
    if not certp.is_file(): raise RuntimeError('pre_promotion_cert_missing')
    cert=json.loads(certp.read_text()); state['pre_promotion_cert']=cert
    if not cert.get('ok'): raise RuntimeError('pre_promotion_cert_failed')
    actual=hashlib.sha256(CAND.read_bytes()).hexdigest()
    if actual!=EXPECTED_SHA: raise RuntimeError('candidate_sha_changed:'+actual)
    # Load credentials locally; never write them to report/output.
    env={}
    for line in Path('/etc/boris-ops/agent.env').read_text().splitlines():
        if line.strip() and '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
    T=env['BORIS_OPS_TOKEN']; S=env['BORIS_OPS_HMAC_SECRET']
    def req(path,method='GET',obj=None):
        body=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode(); ts=str(int(time.time())); nonce=secrets.token_hex(16); sig=hmac.new(S.encode(),(ts+'\n'+nonce+'\n').encode()+body,hashlib.sha256).hexdigest()
        r=urllib.request.Request(BASE+path,data=(body if method=='POST' else None),method=method,headers={'Authorization':'Bearer '+T,'X-Boris-Timestamp':ts,'X-Boris-Nonce':nonce,'X-Boris-Signature':sig,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(r,timeout=20) as x:return x.status,json.loads(x.read())
        except urllib.error.HTTPError as e:
            try:return e.code,json.loads(e.read())
            except:return e.code,{'error':'http'}
    code,resp=req('/self/update','POST',{'candidate':str(CAND),'sha256':EXPECTED_SHA}); state['steps'].append({'self_update_request':code,'response':{k:v for k,v in resp.items() if k not in ['token','secret']}})
    if code!=202 or not resp.get('ok'): raise RuntimeError('self_update_request_failed')
    # Observe v1.4 recovery after updater restart.
    recovered=False
    for i in range(180):
        time.sleep(.5)
        try:
            with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=2) as r:
                d=json.loads(r.read())
                if r.status==200 and d.get('version')=='1.4.0': recovered=True; state['steps'].append({'v1_4_health_after_s':round((i+1)*.5,1)}); break
        except Exception: pass
    if not recovered: raise RuntimeError('v1_4_health_not_recovered')
    time.sleep(2)
    upd=json.loads(Path('/var/lib/boris-ops/update-status.json').read_text()); state['self_update_status']=upd
    if not upd.get('ok'): raise RuntimeError('self_update_report_failed')
    # Fast post-promotion acceptance.
    qa=subprocess.run(['/usr/bin/python3','/root/BORIS/boris_ops/qa_e2e.py'],capture_output=True,text=True,timeout=120)
    state['steps'].append({'qa_e2e_rc':qa.returncode,'stdout':qa.stdout[-4000:],'stderr':qa.stderr[-2000:]})
    if qa.returncode!=0: raise RuntimeError('qa_e2e_failed')
    hard=subprocess.run(['/usr/bin/python3','/root/BORIS/boris_ops/hardening_qa.py'],capture_output=True,text=True,timeout=120)
    state['steps'].append({'hardening_rc':hard.returncode,'stdout':hard.stdout[-4000:],'stderr':hard.stderr[-2000:]})
    if hard.returncode!=0: raise RuntimeError('hardening_qa_failed')
    # Launch fresh long certifications on the promoted version; dynamic expected-version scripts resolve 1.4.0.
    subprocess.run(['systemctl','reset-failed','boris-ops-certify60.service'],check=False)
    subprocess.run(['systemctl','reset-failed','boris-ops-stress45.service'],check=False)
    subprocess.run(['systemctl','start','--no-block','boris-ops-certify60.service'],check=True)
    subprocess.run(['systemctl','start','--no-block','boris-ops-stress45.service'],check=True)
    state['post_promotion_long_qa_started']={'certify60':active('boris-ops-certify60.service'),'stress45':active('boris-ops-stress45.service')}
    state['ok']=True; state['finished_at']=time.time(); save(state); print(json.dumps({'ok':True,'contract':'BORIS_OPS_PROMOTE_1_4','long_qa_started':state['post_promotion_long_qa_started']}))
except Exception as e:
    state['ok']=False; state['error']=str(e)[:500]; state['finished_at']=time.time(); save(state); print(json.dumps({'ok':False,'contract':'BORIS_OPS_PROMOTE_1_4','error':str(e)[:500]})); sys.exit(1)
