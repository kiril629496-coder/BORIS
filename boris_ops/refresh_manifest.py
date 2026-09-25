#!/usr/bin/env python3
import hashlib,json,re,time,os,subprocess
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops')
AGENT=ROOT/'agent.py'
version='unknown'
if AGENT.is_file():
    m=re.search(r'^VERSION\s*=\s*["\']([^"\']+)',AGENT.read_text(encoding='utf-8'),re.M)
    if m: version=m.group(1)
FILES=[
 'agent.py','opsctl.py','mcp_stdio.py','qa_e2e.py','hardening_qa.py','verify_install.py','state_cleanup.py',
 'rolling_backend_restart.py','reload_nginx_safe.py','longqa_tick.py','guardian.py','pipeline_guardian.py','acceptance_matrix_refresh.py','autonomous_90m.py','acceptance_orchestrator.py','postacceptance.py','promote_1_4_3.py','candidate_1_4_3_soak.py','candidate_1_4_3_full_preflight.py','candidate_1_4_3_safety_modes_qa.py','finalize_1_4_3.py','independence_1_4_3.py','retirement_readiness.py','recovery_dryrun.py','offline_restore.py','refresh_manifest.py','build_recovery_pack.py'
]
UNITS=[
 'boris-ops.service','boris-ops-watchdog.service','boris-ops-watchdog.timer','boris-ops-updater.service',
 'boris-ops-guardian.service','boris-ops-guardian.timer','boris-ops-longqa.service','boris-ops-longqa.timer',
 'boris-ops-qa.service','boris-ops-qa.timer','boris-ops-cleanup.service','boris-ops-cleanup.timer',
 'boris-ops-final-acceptance.service','boris-ops-candidate-1-4-3-soak.service','boris-ops-postacceptance.service','boris-ops-promote-1-4-3.service','boris-ops-finalize-1-4-3.service','boris-ops-pipeline-guardian.service','boris-ops-pipeline-guardian.timer','boris-ops-autonomous-90m.service','boris-ops-remote-mcp-60m.service','boris-ops-independence-1-4-3.service','boris-ops-retirement-readiness.service'
]
EXTRA=['/usr/local/sbin/boris-ops-watchdog','/usr/local/sbin/boris-ops-self-update','/etc/sudoers.d/boris-ops','/etc/logrotate.d/boris-ops','/var/lib/boris-ops/mcp_remote_60m.py']
def info(p):
    try: digest=hashlib.sha256(p.read_bytes()).hexdigest()
    except PermissionError:
        cp=subprocess.run(['sha256sum',str(p)],capture_output=True,text=True,timeout=10)
        if cp.returncode!=0 or not cp.stdout.strip(): raise
        digest=cp.stdout.split()[0]
    return {'sha256':digest,'size':p.stat().st_size}
out={'schema':3,'component':'BORIS Ops','version':version,'generated_at':time.time(),'runtime_dependency_on_sentinelx':False,'runtime_dependency_on_github':False,'secrets_included':False,'control_plane_url':'https://boris-ai.pro/_ops/','mcp_http_url':'https://boris-ai.pro/_ops/mcp','local_mcp_stdio':str(ROOT/'mcp_stdio.py'),'runtime_user':'borisops','files':{},'systemd_units':[]}
for name in FILES:
 p=ROOT/name
 if p.is_file(): out['files'][str(p)]=info(p)
for name in UNITS:
 p=Path('/etc/systemd/system')/name
 if p.is_file(): out['files'][str(p)]=info(p); out['systemd_units'].append(name)
for raw in EXTRA:
 p=Path(raw)
 if p.is_file(): out['files'][str(p)]=info(p)
ng=Path('/etc/nginx/sites-enabled/boris-ai')
if ng.is_file(): out['files'][str(ng)]=info(ng)
manifest=ROOT/'INSTALL_MANIFEST.json'; tmp=manifest.with_suffix('.tmp'); tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,manifest)
print(json.dumps({'ok':True,'version':version,'files':len(out['files']),'units':len(out['systemd_units']),'manifest':str(manifest)}))
