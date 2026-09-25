#!/usr/bin/env python3
import json,time,os,urllib.request,hashlib
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); OUT=ROOT/'SENTINELX_RETIREMENT_READY.json'
def r(name):
    try:return json.loads((ROOT/name).read_text())
    except Exception as e:return {'ok':False,'error':str(e)}
def health():
    try:
        with urllib.request.urlopen('https://boris-ai.pro/_ops/health',timeout=8) as x:return json.loads(x.read())
    except Exception as e:return {'ok':False,'error':repr(e)}
f=r('FINAL_PRODUCTION_1_4_3.json'); i=r('INDEPENDENCE_1_4_3.json'); rec=r('RECOVERY_DRYRUN.json'); manifest=r('INSTALL_MANIFEST.json'); h=health()
current_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest() if (ROOT/'agent.py').is_file() else None
recovery_current = rec.get('ok') is True and rec.get('pack')==manifest.get('recovery_pack') and rec.get('pack_sha256')==manifest.get('recovery_pack_sha256')
independence_strict = i.get('ok') is True and i.get('version')=='1.4.3' and i.get('agent_sha256')==current_sha and i.get('sigcont_confirmed') is True and i.get('sentinelx_cgroup_children_preserved') is True
ok=f.get('ok') is True and f.get('version')=='1.4.3' and f.get('agent_sha256')==current_sha and independence_strict and recovery_current and h.get('ok') is True and h.get('version')=='1.4.3'
report={'contract':'BORIS_OPS_SENTINELX_RETIREMENT_READINESS','ok':ok,'version':h.get('version'),'final_production_ok':f.get('ok'),'independence_ok':i.get('ok'),'recovery_dryrun_ok':rec.get('ok'),'recovery_current':recovery_current,'independence_strict':independence_strict,'agent_sha256':current_sha,'runtime_dependency_on_sentinelx':False if ok else None,'runtime_dependency_on_github':False,'sentinelx_retirement_ready':ok,'generated_at':time.time()}
tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT); print(json.dumps(report,ensure_ascii=False)); raise SystemExit(0 if ok else 1)
