#!/usr/bin/env python3
import hashlib,json,os,re,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops')
# PIPELINE_RETRY_BACKOFF_V1: avoid hot restart loops after strict soak failures.
RETRY_COOLDOWN=300
def retry_ready(unit):
    try:
        cp=subprocess.run(['systemctl','show',unit,'-p','InactiveEnterTimestampMonotonic','--value'],capture_output=True,text=True,timeout=5)
        us=int((cp.stdout or '0').strip() or 0)
        if us<=0:return True
        now=float(Path('/proc/uptime').read_text().split()[0]); return (now-us/1_000_000)>=RETRY_COOLDOWN
    except Exception:return True
OUT=Path('/var/lib/boris-ops/pipeline-guardian.json')
CAND=ROOT/'releases/agent-1.4.3-candidate.py'

def readj(p):
    try:return json.loads(Path(p).read_text())
    except Exception:return {}
def active(u):
    return subprocess.run(['systemctl','is-active',u],capture_output=True,text=True,timeout=5).stdout.strip()
def start(u):
    cp=subprocess.run(['systemctl','reset-failed',u],capture_output=True,text=True,timeout=10)
    cp=subprocess.run(['systemctl','start','--no-block',u],capture_output=True,text=True,timeout=10)
    return {'unit':u,'rc':cp.returncode,'stderr':cp.stderr[-400:]}
def version():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=5) as r:return json.loads(r.read()).get('version')
    except Exception:return None

def promoter_expected():
    try:
        s=(ROOT/'promote_1_4_3.py').read_text(); m=re.search(r"EXPECTED='([0-9a-f]{64})'",s); return m.group(1) if m else None
    except Exception:return None

v=version(); actions=[]; checks={}
checks['version']=v
checks['final_acceptance']=readj(ROOT/'FINAL_ACCEPTANCE.json')
checks['post_acceptance']=readj(ROOT/'POST_ACCEPTANCE.json')
checks['candidate_soak']=readj(ROOT/'CANDIDATE_1_4_3_SOAK.json')
checks['promotion']=readj(ROOT/'PROMOTION_1_4_3.json')
checks['final_production']=readj(ROOT/'FINAL_PRODUCTION_1_4_3.json')
checks['independence']=readj(ROOT/'INDEPENDENCE_1_4_3.json')
checks['retirement_readiness']=readj(ROOT/'SENTINELX_RETIREMENT_READY.json')
checks['remote_mcp']=readj('/var/lib/boris-ops/MCP_REMOTE_60M.json')
checks['states']={u:active(u) for u in [
 'boris-ops-final-acceptance.service','boris-ops-postacceptance.service','boris-ops-candidate-1-4-3-soak.service',
 'boris-ops-promote-1-4-3.service','boris-ops-finalize-1-4-3.service','boris-ops-independence-1-4-3.service','boris-ops-retirement-readiness.service']}
exp=promoter_expected(); actual=hashlib.sha256(CAND.read_bytes()).hexdigest() if CAND.is_file() else None
checks['candidate_sha256']=actual; checks['promoter_expected_sha256']=exp; checks['candidate_sha_match']=bool(actual and exp and actual==exp)
# PIPELINE_RESTART_SAFETY_GATE_V1: never advance a release while the independent guardian reports unsafe backend restarts.
guardian=readj('/var/lib/boris-ops/guardian-state.json')
checks['guardian_ok']=guardian.get('ok') is True
checks['restart_safety']=((guardian.get('checks') or {}).get('restart_safety') or {})
release_gate_ok=checks['guardian_ok'] and checks['restart_safety'].get('ok') is True

# Never skip gates. Only recover a stage whose prerequisites are proven.
if v=='1.4.2':
    soak=checks['candidate_soak']; fs=checks['states']['boris-ops-candidate-1-4-3-soak.service']
    if not (soak.get('complete') and soak.get('ok') is True) and fs not in {'active','activating'} and checks['candidate_sha_match']:
        actions.append(start('boris-ops-candidate-1-4-3-soak.service'))
    fa=checks['final_acceptance']; fas=checks['states']['boris-ops-final-acceptance.service']
    if fa.get('ok') is not True and fas not in {'active','activating'}:
        actions.append(start('boris-ops-final-acceptance.service'))
    elif fa.get('ok') is True:
        pa=checks['post_acceptance']; pas=checks['states']['boris-ops-postacceptance.service']
        # PIPELINE_POSTACCEPTANCE_QUIET_GATE_V1: do not start a clean-window
        # certification while guardian still reports an unsafe backend restart.
        # Otherwise every retry is guaranteed to fail on its first sample.
        if pa.get('ok') is not True and pas not in {'active','activating'} and retry_ready('boris-ops-postacceptance.service') and release_gate_ok:
            actions.append(start('boris-ops-postacceptance.service'))
        elif pa.get('ok') is True and checks['remote_mcp'].get('ok') is True and int(checks['remote_mcp'].get('samples',0)) >= 60 and int(checks['remote_mcp'].get('failures',1)) == 0 and soak.get('complete') and soak.get('ok') is True and checks['candidate_sha_match'] and release_gate_ok:
            pr=checks['promotion']; prs=checks['states']['boris-ops-promote-1-4-3.service']
            if pr.get('ok') is not True and prs not in {'active','activating'}:
                actions.append(start('boris-ops-promote-1-4-3.service'))
elif v=='1.4.3':
    pr=checks['promotion']; fp=checks['final_production']; fps=checks['states']['boris-ops-finalize-1-4-3.service']
    current_agent_sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest() if (ROOT/'agent.py').is_file() else None
    recovery=readj(ROOT/'RECOVERY_DRYRUN.json')
    promoted_identity_ok = pr.get('ok') is True and pr.get('version')=='1.4.3' and current_agent_sha==exp
    if promoted_identity_ok and recovery.get('ok') is True and release_gate_ok and fp.get('ok') is not True and fps not in {'active','activating'}:
        # Bootstrap longQA immediately after identity change instead of waiting up to one timer interval.
        subprocess.run(['/usr/bin/python3',str(ROOT/'longqa_tick.py')],capture_output=True,text=True,timeout=45)
        actions.append(start('boris-ops-finalize-1-4-3.service'))
    elif fp.get('ok') is True:
        final_identity_ok = fp.get('version')=='1.4.3' and fp.get('agent_sha256')==current_agent_sha==exp
        indep=checks['independence']; inds=checks['states']['boris-ops-independence-1-4-3.service']
        if final_identity_ok and indep.get('ok') is not True and inds not in {'active','activating'} and retry_ready('boris-ops-independence-1-4-3.service') and release_gate_ok:
            actions.append(start('boris-ops-independence-1-4-3.service'))
        elif final_identity_ok and indep.get('ok') is True:
            independence_identity_ok = indep.get('version')=='1.4.3' and indep.get('agent_sha256')==current_agent_sha==exp and indep.get('sigcont_confirmed') is True and indep.get('sentinelx_cgroup_children_preserved') is True
            rr=checks['retirement_readiness']; rrs=checks['states']['boris-ops-retirement-readiness.service']
            if independence_identity_ok and rr.get('ok') is not True and rrs not in {'active','activating'} and retry_ready('boris-ops-retirement-readiness.service') and release_gate_ok:
                actions.append(start('boris-ops-retirement-readiness.service'))

subprocess.run(['/usr/bin/python3',str(ROOT/'acceptance_matrix_refresh.py')],capture_output=True,text=True,timeout=20)
report={'schema':1,'contract':'BORIS_OPS_PIPELINE_GUARDIAN','ts':time.time(),'ok':v in {'1.4.2','1.4.3'},'checks':checks,'actions':actions,'runtime_dependency_on_github':False,'runtime_dependency_on_sentinelx':False}
OUT.parent.mkdir(parents=True,exist_ok=True); tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT)
print(json.dumps({'ok':report['ok'],'version':v,'actions':actions,'candidate_sha_match':checks['candidate_sha_match']},ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
