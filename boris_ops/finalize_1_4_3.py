#!/usr/bin/env python3
import json,subprocess,time,urllib.request,hashlib,os
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); STATE=Path('/var/lib/boris-ops/longqa'); OUT=ROOT/'FINAL_PRODUCTION_1_4_3.json'; WAIT_LOG=ROOT/'finalize-1.4.3-wait.jsonl'
TARGET='1.4.3'; start=time.time(); WAIT_LOG.write_text('',encoding='utf-8')
def readj(p):
    try:return json.loads(Path(p).read_text())
    except Exception as e:return {'error':str(e)}
def health():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=8) as r:return json.loads(r.read())
    except Exception as e:return {'ok':False,'error':repr(e)}
def run(argv,timeout=240):
    cp=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    return {'rc':cp.returncode,'stdout':cp.stdout[-8000:],'stderr':cp.stderr[-4000:]}
def save(obj):
    tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(tmp,OUT)
# Wait until v1.4.3 is active first.
deadline=time.time()+900
while time.time()<deadline:
    h=health()
    if h.get('ok') is True and h.get('version')==TARGET: break
    time.sleep(5)
else:
    result={'contract':'BORIS_OPS_FINAL_PRODUCTION_1_4_3','ok':False,'error':'target_version_not_active','health':health(),'started_at':start,'finished_at':time.time()}; save(result); raise SystemExit(1)
# Wait for resumable long-QA of the NEW identity. It survives chat/process restarts.
deadline=time.time()+4600
last={}
while time.time()<deadline:
    c=readj(STATE/'cert60.json'); s=readj(STATE/'stress45.json'); h=health()
    row={'ts':time.time(),'health_version':h.get('version'),'cert60_version':c.get('version'),'cert60_samples':c.get('samples'),'cert60_complete':c.get('complete'),'cert60_ok':c.get('ok'),'stress45_version':s.get('version'),'stress45_samples':s.get('samples'),'stress45_complete':s.get('complete'),'stress45_ok':s.get('ok')}
    with WAIT_LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    last=row
    current_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest()
    if c.get('version')==TARGET and s.get('version')==TARGET and c.get('agent_sha256')==current_sha and s.get('agent_sha256')==current_sha and c.get('complete') and s.get('complete'): break
    time.sleep(60)
else:
    result={'contract':'BORIS_OPS_FINAL_PRODUCTION_1_4_3','ok':False,'error':'post_promotion_longqa_timeout','last':last,'started_at':start,'finished_at':time.time()}; save(result); raise SystemExit(1)
c=readj(STATE/'cert60.json'); s=readj(STATE/'stress45.json')
current_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest()
long_ok=c.get('version')==TARGET and s.get('version')==TARGET and c.get('agent_sha256')==current_sha and s.get('agent_sha256')==current_sha and c.get('ok') is True and s.get('ok') is True and c.get('control_failures')==0 and s.get('control_failures')==0
# QA uses the public authenticated control plane and therefore shares the production token bucket.
# Do not weaken rate limiting for certification; give it bounded refill windows instead.
def qa_retry(argv, attempts=3):
    last=None
    for i in range(attempts):
        last=run(argv)
        if last['rc']==0: return last
        if i+1<attempts: time.sleep(15)
    return last
qa=qa_retry(['/usr/bin/python3',str(ROOT/'qa_e2e.py')],3)
time.sleep(10)
hard=qa_retry(['/usr/bin/python3',str(ROOT/'hardening_qa.py')],3)
recovery=run(['/usr/bin/python3',str(ROOT/'build_recovery_pack.py')]); verify=run(['/usr/bin/python3',str(ROOT/'verify_install.py')]) if recovery['rc']==0 else {'rc':-1}; dry=run(['/usr/bin/python3',str(ROOT/'recovery_dryrun.py')]) if recovery['rc']==0 and verify['rc']==0 else {'rc':-1}
agent_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest(); h=health()
ok=long_ok and qa['rc']==0 and hard['rc']==0 and verify['rc']==0 and recovery['rc']==0 and dry['rc']==0 and h.get('version')==TARGET
result={'contract':'BORIS_OPS_FINAL_PRODUCTION_1_4_3','ok':ok,'version':h.get('version'),'agent_sha256':agent_sha,'started_at':start,'finished_at':time.time(),'duration_s':round(time.time()-start,1),'cert60':{k:c.get(k) for k in ['version','agent_sha256','samples','control_failures','dependency_degraded_events','complete','ok']},'stress45':{k:s.get(k) for k in ['version','agent_sha256','samples','control_failures','dependency_degraded_events','complete','ok']},'qa_e2e':qa,'hardening':hard,'verify_install':verify,'recovery_pack':recovery,'recovery_dryrun':dry,'runtime_dependency_on_github':False,'declared_runtime_dependency_on_sentinelx':False,'runtime_dependency_on_sentinelx':None,'sentinelx_retirement_ready':False,'sentinelx_independence_test':'pending'}
save(result); print(json.dumps({'ok':ok,'contract':result['contract'],'version':result['version'],'duration_s':result['duration_s'],'sentinelx_retirement_ready':False,'sentinelx_independence_test':'pending'},ensure_ascii=False)); raise SystemExit(0 if ok else 1)
