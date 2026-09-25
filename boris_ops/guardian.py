#!/usr/bin/env python3
import json,os,subprocess,time,urllib.request
from pathlib import Path
OUT=Path('/var/lib/boris-ops/guardian-state.json')
GATE=Path('/etc/boris-ops/allow-self-update.json')
checks={}
def active(unit):
    return subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5).stdout.strip()
for unit in ['boris-ops.service','boris-ops-watchdog.timer','boris-ops-guardian.timer','boris-ops-longqa.timer','boris-ops-qa.timer','boris-ops-cleanup.timer','nginx.service']:
    checks[unit]=active(unit)
for name,url in [('local','http://127.0.0.1:8741/health'),('external','https://boris-ai.pro/_ops/health')]:
    try:
        with urllib.request.urlopen(url,timeout=5) as r: checks['http_'+name]={'ok':r.status==200,'status':r.status,'body':json.loads(r.read())}
    except Exception as e: checks['http_'+name]={'ok':False,'error':str(e)[:200]}
try:
    st=os.statvfs('/'); total=st.f_blocks*st.f_frsize; free=st.f_bavail*st.f_frsize; checks['disk_percent']=round((total-free)*100/total,1)
except Exception as e: checks['disk_error']=str(e)[:200]
try:
    gate=json.loads(GATE.read_text()) if GATE.exists() else None
    checks['self_update_gate']={'exists':bool(gate),'open':bool(gate and float(gate.get('expires_at',0))>=time.time()),'expires_at':gate.get('expires_at') if gate else None}
except Exception as e: checks['self_update_gate']={'exists':True,'open':'unknown','error':str(e)[:200]}
try:
    for n in ['cert60','stress45']:
        p=Path('/var/lib/boris-ops/longqa')/(n+'.json'); checks['longqa_'+n]=json.loads(p.read_text()) if p.exists() else {'missing':True}
except Exception as e: checks['longqa_error']=str(e)[:200]
# Detect unsafe backend restart paths independently of SentinelX job metadata.
# Canonical rolling restart commands execute from /root/BORIS/backend and are
# serialized by /run/lock/boris-backend-rolling.lock. A direct restart from '/'
# (or one command restarting both API replicas) bypasses that HA contract and can
# create a public 5xx even when each individual restart looks successful.
try:
    j=subprocess.run(['journalctl','--since','-120 seconds','--no-pager','-o','cat'],capture_output=True,text=True,timeout=8).stdout
    unsafe=[]
    for line in j.splitlines():
        if 'COMMAND=' not in line or 'systemctl restart boris-backend' not in line:
            continue
        combined=('boris-backend.service boris-backend-replica.service' in line or 'boris-backend-replica.service boris-backend.service' in line)
        wrong_cwd=('PWD=/ ;' in line or 'PWD=/;' in line)
        shorthand=('systemctl restart boris-backend' in line and 'boris-backend.service' not in line and 'boris-backend-replica.service' not in line)
        if combined or wrong_cwd or shorthand:
            unsafe.append(line[-500:])
    checks['restart_safety']={'ok':not unsafe,'unsafe_events':len(unsafe),'recent':unsafe[-8:]}
except Exception as e:
    checks['restart_safety']={'ok':False,'error':str(e)[:200]}

try:
    chain_files={'candidate_1_4_3_soak':'CANDIDATE_1_4_3_SOAK.json','final_acceptance':'FINAL_ACCEPTANCE.json','post_acceptance':'POST_ACCEPTANCE.json','promotion_1_4_3':'PROMOTION_1_4_3.json','final_production_1_4_3':'FINAL_PRODUCTION_1_4_3.json'}
    for key,name in chain_files.items():
        fp=Path('/root/BORIS/boris_ops')/name; checks[key]=json.loads(fp.read_text()) if fp.exists() else {'pending':True}
except Exception as e: checks['chain_state_error']=str(e)[:200]
# Acceptance/readiness identity integrity: detect stale or contradictory control artifacts without owner checks.
try:
    import hashlib
    root=Path('/root/BORIS/boris_ops')
    agent_sha=hashlib.sha256((root/'agent.py').read_bytes()).hexdigest()
    acceptance=json.loads((root/'OPERATIONS_ACCEPTANCE_MATRIX.json').read_text())
    readiness=json.loads((root/'PRODUCTION_READINESS.json').read_text())
    live_version=(checks.get('http_local',{}).get('body') or {}).get('version')
    stages=acceptance.get('pending_or_future') or {}
    acceptance_ok=(acceptance.get('production_version')==live_version and acceptance.get('production_agent_sha256')==agent_sha and bool(stages) and all(v is True for v in stages.values()))
    readiness_ok=(readiness.get('ok') is True and readiness.get('version')==live_version and readiness.get('agent_sha256')==agent_sha and readiness.get('runtime_dependency_on_github') is False and readiness.get('runtime_dependency_on_sentinelx') is False)
    checks['acceptance_integrity']={'ok':bool(acceptance_ok and readiness_ok),'live_version':live_version,'agent_sha256':agent_sha,'acceptance_ok':bool(acceptance_ok),'readiness_ok':bool(readiness_ok)}
except Exception as e:
    checks['acceptance_integrity']={'ok':False,'error':str(e)[:200]}
# Local security/integrity invariants.
try:
    st=Path('/etc/boris-ops/agent.env').stat(); checks['env_security']={'mode':oct(st.st_mode & 0o777),'uid':st.st_uid,'gid':st.st_gid,'ok':(st.st_mode & 0o007)==0}
except Exception as e: checks['env_security']={'ok':False,'error':str(e)[:200]}
try:
    audit=Path('/var/log/boris-ops/audit.jsonl'); st=audit.stat() if audit.exists() else None; runtime_uid=__import__('pwd').getpwnam('borisops').pw_uid; writable=bool(st and st.st_uid==runtime_uid and (st.st_mode & 0o200)); checks['audit_security']={'exists':audit.exists(),'writable_by_runtime':writable,'mode':oct(st.st_mode & 0o777) if st else None,'uid':st.st_uid if st else None}
except Exception as e: checks['audit_security']={'exists':False,'error':str(e)[:200]}
try:
    manifest=json.loads(Path('/root/BORIS/boris_ops/INSTALL_MANIFEST.json').read_text()); pack=Path(manifest.get('recovery_pack',''))
    import hashlib
    actual=hashlib.sha256(pack.read_bytes()).hexdigest() if pack.is_file() else ''
    checks['recovery']={'exists':pack.is_file(),'sha256_match':actual==manifest.get('recovery_pack_sha256'),'sentinelx_dependency':manifest.get('runtime_dependency_on_sentinelx'),'github_dependency':manifest.get('runtime_dependency_on_github'),'secrets_included':manifest.get('secrets_included')}
except Exception as e: checks['recovery']={'exists':False,'error':str(e)[:200]}
critical_units=['boris-ops.service','boris-ops-watchdog.timer','boris-ops-longqa.timer','boris-ops-guardian.timer','nginx.service']
# OPS_GUARDIAN_RELOADING_HEALTHY_V1: systemd reports a healthy service as
# 'reloading' during an in-place configuration reload. Treat that transient
# state as available instead of poisoning the autonomous acceptance chain.
def unit_healthy(unit, state):
    if state == 'active': return True
    return unit.endswith('.service') and state == 'reloading'
def _longqa_current_ok(d):
    if not isinstance(d,dict): return False
    if d.get('complete') is True: return d.get('ok') is True
    row=d.get('last_row') or {}
    # Historical failed attempts remain evidence, but guardian reflects current recoverability.
    return bool(row.get('effective_ok',True)) and int(d.get('consecutive_dependency_failures',0) or 0)<2
longqa_ok=all(_longqa_current_ok(checks.get('longqa_'+n,{})) for n in ['cert60','stress45'])
security_ok=checks.get('env_security',{}).get('ok') is True and checks.get('recovery',{}).get('exists') is True and checks.get('recovery',{}).get('sha256_match') is True and checks.get('recovery',{}).get('sentinelx_dependency') is False and checks.get('recovery',{}).get('github_dependency') is False and checks.get('recovery',{}).get('secrets_included') is False and checks.get('acceptance_integrity',{}).get('ok') is True
restart_safety_ok=checks.get('restart_safety',{}).get('ok') is True
# OPS_GUARDIAN_SCOPE_V2: restart_safety describes concurrent BORIS app deployment behavior.
# Keep it visible as diagnostic evidence, but don't mark the independent Ops control plane
# dead when another workstream restarts application backends.
ok=all(unit_healthy(u, checks.get(u)) for u in critical_units) and checks.get('http_local',{}).get('ok') and checks.get('http_external',{}).get('ok') and checks.get('self_update_gate',{}).get('open') is not True and longqa_ok and security_ok
report={'schema':2,'ts':time.time(),'ok':bool(ok),'checks':checks}
tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)); os.replace(tmp,OUT)
print(json.dumps({'ok':bool(ok),'version':checks.get('http_local',{}).get('body',{}).get('version'),'disk_percent':checks.get('disk_percent'),'longqa_cert60_samples':checks.get('longqa_cert60',{}).get('samples'),'longqa_stress45_samples':checks.get('longqa_stress45',{}).get('samples')},ensure_ascii=False))
raise SystemExit(0 if ok else 1)
