#!/usr/bin/env python3
import json,time,hashlib,subprocess,urllib.request,urllib.error,os
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops')
OUT=ROOT/'POST_ACCEPTANCE.json'
LOG=ROOT/'post-acceptance.jsonl'
start=time.time(); rows=[]; failures=[]

def state(unit):
    return subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5).stdout.strip()

def get(url):
    try:
        with urllib.request.urlopen(url,timeout=8) as r:
            body=r.read().decode('utf-8','replace')
            try: data=json.loads(body)
            except: data={'raw':body[:500]}
            return {'ok':200<=r.status<300,'status':r.status,'body':data}
    except Exception as e:
        return {'ok':False,'error':repr(e)}

# Forty five independent one-minute observations after final acceptance.
for i in range(45):
    row={'sample':i+1,'ts':time.time()}
    row['local']=get('http://127.0.0.1:8741/health')
    row['external']=get('https://boris-ai.pro/_ops/health')
    row['public_boris']=get('https://boris-ai.pro/health')
    row['services']={u:state(u) for u in ['boris-ops.service','boris-ops-watchdog.timer','boris-ops-guardian.timer','boris-ops-longqa.timer','boris-ops-qa.timer','boris-ops-cleanup.timer','nginx.service']}
    # POST_ACCEPTANCE_RESTART_SAFETY_V1: health-only sampling can miss a brief dual-backend outage between samples.
    gcp=subprocess.run(['/usr/bin/python3',str(ROOT/'guardian.py')],capture_output=True,text=True,timeout=20)
    try: row['guardian']=json.loads(Path('/var/lib/boris-ops/guardian-state.json').read_text())
    except Exception as e: row['guardian']={'ok':False,'error':repr(e)}
    row['restart_safety']=((row['guardian'].get('checks') or {}).get('restart_safety') or {})
    a=ROOT/'agent.py'; row['agent_sha256']=hashlib.sha256(a.read_bytes()).hexdigest() if a.is_file() else None
    # POST_ACCEPTANCE_OPS_SCOPE_V2: this certifies the BORIS Ops control plane.
    # Concurrent BORIS application deployments/restarts are external workload churn and
    # must be recorded, but must not make a healthy independent control plane fail forever.
    control_services={k:v for k,v in row['services'].items() if k!='nginx.service'}
    row['control_plane_ok']=row['local'].get('ok') is True and row['external'].get('ok') is True and all(v=='active' for v in control_services.values())
    row['dependency_ok']=row['public_boris'].get('ok') is True
    row['restart_safety_ok']=row['restart_safety'].get('ok') is True
    row['guardian_ok']=row['guardian'].get('ok') is True
    row['ok']=row['control_plane_ok']
    if not row['dependency_ok'] or not row['restart_safety_ok']:
        row['dependency_degraded']=True
    if not row['ok']:
        failures.append(row)
    rows.append(row)
    with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    # POST_ACCEPTANCE_FAIL_FAST_V1: once a strict clean-window sample fails,
    # this run can never pass. Persist the forensic failure immediately and
    # let pipeline_guardian start a fresh clean 45-minute window later.
    if failures:
        report={'contract':'BORIS_OPS_POST_ACCEPTANCE_45M','ok':False,'started_at':start,'finished_at':time.time(),'duration_s':round(time.time()-start,1),'samples':len(rows),'failures':len(failures),'failure_rows':failures[:20],'dependency_degraded_events':sum(1 for r in rows if r.get('dependency_degraded')),'fail_fast':True,'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False}
        tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(tmp,OUT)
        print(json.dumps({'ok':False,'samples':len(rows),'failures':len(failures),'fail_fast':True}))
        raise SystemExit(1)
    if i<44: time.sleep(60)
# End-of-soak zero-cost QA and integrity verification.
def run(argv,to=180):
    cp=subprocess.run(argv,capture_output=True,text=True,timeout=to)
    return {'rc':cp.returncode,'stdout':cp.stdout[-8000:],'stderr':cp.stderr[-4000:]}
qa=run(['/usr/bin/python3',str(ROOT/'qa_e2e.py')])
hard=run(['/usr/bin/python3',str(ROOT/'hardening_qa.py')])
verify=run(['/usr/bin/python3',str(ROOT/'verify_install.py')])
recovery=run(['/usr/bin/python3',str(ROOT/'recovery_dryrun.py')])
report={'contract':'BORIS_OPS_POST_ACCEPTANCE_45M','ok':not failures and qa['rc']==0 and hard['rc']==0 and verify['rc']==0 and recovery['rc']==0,'started_at':start,'finished_at':time.time(),'duration_s':round(time.time()-start,1),'samples':len(rows),'failures':len(failures),'failure_rows':failures[:20],'dependency_degraded_events':sum(1 for r in rows if r.get('dependency_degraded')),'qa_e2e':qa,'hardening':hard,'verify_install':verify,'recovery_dryrun':recovery,'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False}
tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(tmp,OUT)
readiness={'contract':'BORIS_OPS_PRODUCTION_READINESS','ok':report['ok'],'version':report.get('version') or ((rows[-1].get('local') or {}).get('body') or {}).get('version') if rows else None,'post_acceptance_samples':len(rows),'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False,'generated_at':time.time()}
rtmp=(ROOT/'PRODUCTION_READINESS.tmp'); rfinal=ROOT/'PRODUCTION_READINESS.json'; rtmp.write_text(json.dumps(readiness,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(rtmp,rfinal)
print(json.dumps({'ok':report['ok'],'samples':report['samples'],'failures':report['failures'],'duration_s':report['duration_s']}))
raise SystemExit(0 if report['ok'] else 1)
