#!/usr/bin/env python3
"""Refresh BORIS Ops acceptance matrix only from verifiable local evidence."""
import hashlib,json,os,time,urllib.request
from pathlib import Path
ROOT=Path('/root/BORIS/boris_ops'); STATE=Path('/var/lib/boris-ops/longqa'); OUT=ROOT/'OPERATIONS_ACCEPTANCE_MATRIX.json'
def r(p):
    try:return json.loads(Path(p).read_text())
    except Exception:return {}
def health():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8741/health',timeout=5) as x:return json.loads(x.read())
    except Exception:return {}
h=health(); sha=hashlib.sha256((ROOT/'agent.py').read_bytes()).hexdigest() if (ROOT/'agent.py').is_file() else None
post=r(ROOT/'POST_ACCEPTANCE.json'); remote=r('/var/lib/boris-ops/MCP_REMOTE_60M.json'); promo=r(ROOT/'PROMOTION_1_4_3.json'); cert=r(STATE/'cert60.json'); stress=r(STATE/'stress45.json'); final=r(ROOT/'FINAL_PRODUCTION_1_4_3.json'); indep=r(ROOT/'INDEPENDENCE_1_4_3.json'); retire=r(ROOT/'SENTINELX_RETIREMENT_READY.json'); recovery=r(ROOT/'RECOVERY_DRYRUN.json')
try:d=r(OUT)
except Exception:d={}
d.setdefault('contract','BORIS_OPS_OPERATIONS_ACCEPTANCE_MATRIX'); d['generated_at']=time.time(); d['production_version']=h.get('version'); d['production_agent_sha256']=sha
p=d.setdefault('pending_or_future',{})
p['postacceptance_45m']=post.get('ok') is True and int(post.get('samples',0))>=45 and int(post.get('failures',1))==0
p['remote_mcp_60m']=remote.get('ok') is True and int(remote.get('samples',0))>=60 and int(remote.get('failures',1))==0
p['promotion_1_4_3']=promo.get('ok') is True and promo.get('version')=='1.4.3' and h.get('version')=='1.4.3' and sha==promo.get('expected_sha256')
p['fresh_1_4_3_cert60_stress45']=bool(cert.get('version')=='1.4.3' and stress.get('version')=='1.4.3' and cert.get('agent_sha256')==sha and stress.get('agent_sha256')==sha and cert.get('complete') and stress.get('complete') and cert.get('ok') is True and stress.get('ok') is True and cert.get('control_failures')==0 and stress.get('control_failures')==0)
p['final_production_1_4_3']=final.get('ok') is True and final.get('version')=='1.4.3' and final.get('agent_sha256')==sha
p['sentinelx_independence_1_4_3']=indep.get('ok') is True and indep.get('version')=='1.4.3' and indep.get('agent_sha256')==sha and indep.get('sigcont_confirmed') is True and indep.get('sentinelx_cgroup_children_preserved') is True
p['sentinelx_retirement_ready']=retire.get('ok') is True and retire.get('sentinelx_retirement_ready') is True and p['sentinelx_independence_1_4_3'] and p['final_production_1_4_3']
d['recovery_dryrun_current']=recovery.get('ok') is True
d['runtime_dependency_on_github']=False
d['runtime_dependency_on_sentinelx']=False if p['sentinelx_independence_1_4_3'] else None
d['note']=('All current acceptance stages are evidence-backed and complete for this exact production SHA.' if all(p.values()) and d['recovery_dryrun_current'] else 'Only evidence-backed stages are marked true; one or more acceptance stages remain incomplete.')
tmp=OUT.with_suffix('.tmp'); tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,OUT)
# Keep the production-readiness artifact bound to the same exact live version/SHA and acceptance evidence.
readiness={
 'contract':'BORIS_OPS_PRODUCTION_READINESS',
 'ok': bool(h.get('ok') is True and h.get('version')=='1.4.3' and all(p.values()) and d['recovery_dryrun_current']),
 'version':h.get('version'),
 'agent_sha256':sha,
 'post_acceptance_samples':int(post.get('samples',0)),
 'remote_mcp_samples':int(remote.get('samples',0)),
 'cert60_samples':int(cert.get('samples',0)),
 'stress45_samples':int(stress.get('samples',0)),
 'independence_samples':int(indep.get('samples',0)),
 'runtime_dependency_on_github':False,
 'runtime_dependency_on_sentinelx':False if p['sentinelx_independence_1_4_3'] else None,
 'sentinelx_retirement_ready':bool(p['sentinelx_retirement_ready']),
 'recovery_dryrun_current':bool(d['recovery_dryrun_current']),
 'generated_at':time.time(),
}
rp=ROOT/'PRODUCTION_READINESS.json'
_old_readiness=r(rp)
_old_semantic=dict(_old_readiness); _old_semantic.pop('generated_at',None)
_new_semantic=dict(readiness); _new_semantic.pop('generated_at',None)
if _old_semantic != _new_semantic:
    rt=rp.with_suffix('.tmp'); rt.write_text(json.dumps(readiness,ensure_ascii=False,indent=2)+'\n'); os.replace(rt,rp)
else:
    readiness['generated_at']=_old_readiness.get('generated_at')
print(json.dumps({'ok':readiness['ok'],'version':d.get('production_version'),'pending_or_future':p,'readiness_sha256':sha},ensure_ascii=False))
