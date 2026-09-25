#!/usr/bin/env python3
import json,os,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); OUT=ROOT/'AUTONOMOUS_90M.json'; LOG=ROOT/'autonomous-90m.jsonl'
start=time.time(); failures=[]; LOG.write_text('',encoding='utf-8')
def state(u): return subprocess.run(['systemctl','is-active',u],capture_output=True,text=True,timeout=5).stdout.strip()
def get(url):
    try:
        with urllib.request.urlopen(url,timeout=7) as r:return {'ok':r.status==200,'status':r.status,'body':json.loads(r.read())}
    except Exception as e:return {'ok':False,'error':repr(e)}
def readj(p):
    try:return json.loads(Path(p).read_text())
    except Exception:return {}
for i in range(90):
    local=get('http://127.0.0.1:8741/health'); ext=get('https://boris-ai.pro/_ops/health'); pub=get('https://boris-ai.pro/health')
    guardian=readj('/var/lib/boris-ops/guardian-state.json'); pipe=readj('/var/lib/boris-ops/pipeline-guardian.json'); cand=readj(ROOT/'CANDIDATE_1_4_3_SOAK.json')
    manifest=readj(ROOT/'INSTALL_MANIFEST.json')
    units={u:state(u) for u in ['boris-ops.service','boris-ops-watchdog.timer','boris-ops-guardian.timer','boris-ops-longqa.timer','boris-ops-pipeline-guardian.timer','nginx.service']}
    disk=((guardian.get('checks') or {}).get('disk_percent'))
    row={'sample':i+1,'ts':time.time(),'version':(local.get('body') or {}).get('version'),'local':local.get('ok'),'external_ops':ext.get('ok'),'public_boris':pub.get('ok'),'guardian_ok':guardian.get('ok'),'pipeline_guardian_ok':pipe.get('ok'),'candidate_soak_ok':cand.get('ok'),'candidate_soak_complete':cand.get('complete'),'units':units,'disk_percent':disk,'github_dependency':manifest.get('runtime_dependency_on_github'),'sentinelx_dependency':manifest.get('runtime_dependency_on_sentinelx')}
    row['ok']=bool(row['local'] and row['external_ops'] and row['public_boris'] and all(v=='active' for v in units.values()) and row['github_dependency'] is False and row['sentinelx_dependency'] is False and (disk is None or float(disk)<92))
    if not row['ok']: failures.append(row)
    with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    if i<89: time.sleep(60)
report={'contract':'BORIS_OPS_AUTONOMOUS_90M','ok':not failures,'samples':90,'failures':len(failures),'started_at':start,'finished_at':time.time(),'duration_s':round(time.time()-start,1),'failure_rows':failures[:20],'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False}
tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT)
print(json.dumps({'ok':report['ok'],'samples':90,'failures':len(failures),'duration_s':report['duration_s']}))
raise SystemExit(0 if report['ok'] else 1)
