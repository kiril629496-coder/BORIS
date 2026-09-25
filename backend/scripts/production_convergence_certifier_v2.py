#!/usr/bin/env python3
"""Read-only cross-contour BORIS generation convergence certificate.

This is deliberately NOT a second functional QA/Guardian/long-soak system.
Specialized contours own their own behavior. This process only certifies the
shared release boundary after those writers become quiet: one source generation,
canonical deploy idle, runtime generations aligned, basic HA/public health and
queue invariants stable repeatedly. It never restarts or edits production.
"""
import fcntl, hashlib, json, os, subprocess, sys, time, urllib.request
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/root/BORIS/backend')
FRONTEND=Path('/root/BORIS/frontend')
LOG=ROOT/'logs/production_convergence_certifier_v2.jsonl'
LATEST=ROOT/'data/production_convergence_certificate_latest.json'
LOCK=Path('/run/lock/boris-production-convergence-certifier.lock')
MAX_CYCLES=int(os.getenv('BORIS_CERT_MAX_CYCLES','0'))  # 0 = keep observing until a real stable window exists
INTERVAL=int(os.getenv('BORIS_CERT_INTERVAL','60'))
REQUIRED_STREAK=int(os.getenv('BORIS_CERT_REQUIRED_STREAK','3'))
SOURCE_QUIET_SEC=int(os.getenv('BORIS_CERT_SOURCE_QUIET_SEC','120'))
CORE=['boris-deploy.service','boris-backend.service','boris-backend-replica.service','boris-background-worker.service','boris-dispatcher.service','boris-frontend.service']
RUNTIME_EXT={'.py','.js','.jsx','.ts','.tsx','.css','.json'}

LOCK.parent.mkdir(parents=True,exist_ok=True)
_lf=LOCK.open('a+')
try: fcntl.flock(_lf.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    print(json.dumps({'ok':False,'error':'convergence_certifier_already_running','lock':str(LOCK)})); raise SystemExit(75)

def now(): return datetime.now(timezone.utc).isoformat()
def run(cmd,timeout=180,env=None): return subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout,env=env)
def active(unit): return run(['systemctl','is-active',unit],20).stdout.strip()=='active'
def public(url):
    try:
        r=urllib.request.urlopen(url,timeout=12); return int(r.status)
    except Exception: return 0

def contract():
    env=dict(os.environ); env['BORIS_POLICY_SELF_HEAL']='0'; env['PYTHONPATH']=str(ROOT)
    p=run([str(ROOT/'venv/bin/python'),str(ROOT/'production_contract_runner.py')],240,env)
    obj={}
    for line in reversed([x.strip() for x in p.stdout.splitlines() if x.strip()]):
        try: obj=json.loads(line); break
        except Exception: pass
    return p.returncode,obj,p.stderr[-2000:]

def queue_state():
    code='from app.ext_api import queuefix,deploy; import json; print(json.dumps({"queue":queuefix.invariants(),"dispatcher":deploy._dispatcher_generation_state()},default=str))'
    env=dict(os.environ); env['PYTHONPATH']=str(ROOT)
    p=run([str(ROOT/'venv/bin/python'),'-c',code],60,env)
    try: return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception: return {'error':p.stderr[-1000:] or p.stdout[-1000:]}

def deploy_busy():
    # DEPLOY_BUSY_PROCESS_MATCH_V2: builders can be launched as absolute paths,
    # repo-relative ./scripts paths, through npm/sh, and `next build`.  The old
    # regex only saw absolute paths and could certify while a real build ran.
    p=run(['ps','-eo','pid=,args='],20)
    # DEPLOY_BUSY_EXACT_EXEC_V1: do not treat diagnostic shells/grep commands
    # that merely *mention* a deploy script as an active writer.
    import re
    patterns=(
        re.compile(r'^\s*\d+\s+(?:/bin/)?bash\s+/root/BORIS/backend/rolling_restart_backend\.sh(?:\s|$)'),
        re.compile(r'^\s*\d+\s+(?:/bin/)?bash\s+/root/BORIS/frontend/scripts/boris-frontend-(?:build|deploy)\.sh(?:\s|$)'),
        re.compile(r'^\s*\d+\s+.*node_modules/\.bin/next\s+build(?:\s|$)'),
    )
    rows=[]
    for line in p.stdout.splitlines():
        if any(rx.search(line) for rx in patterns): rows.append(line.strip())
    return bool(rows),rows[:10]

def source_quiet_state():
    newest=0.0; newest_path=''
    # Backend runtime code only; tests/logs/backups are intentionally excluded.
    for base in (ROOT/'app',):
        if not base.exists(): continue
        for p in base.rglob('*'):
            if p.is_file() and p.suffix in RUNTIME_EXT and '__pycache__' not in p.parts:
                try: m=p.stat().st_mtime
                except OSError: continue
                if m>newest: newest,newest_path=m,str(p)
    # Root-level QA/test/tmp probes are written by independent workstreams and
    # are not production runtime inputs.  Counting them here made an unrelated
    # QA edit reset the release quiet window forever.  Keep runtime/root runners
    # in scope, but exclude only the unambiguous non-runtime prefixes.
    for p in ROOT.glob('*.py'):
        if p.name.startswith(('qa_', 'test_', 'tests_', 'tmp_')):
            continue
        try: m=p.stat().st_mtime
        except OSError: continue
        if m>newest: newest,newest_path=m,str(p)
    # Match the real Next.js tree used by frontend-source-hash.py.  This repo
    # has no /src directory; watching FRONTEND/src silently ignored every UI
    # edit.  Only source/config inputs are included, never build artifacts or
    # backups, so unrelated QA/build churn cannot reset this boundary.
    frontend_root_files=('package.json','package-lock.json','next.config.ts','tsconfig.json','postcss.config.mjs','eslint.config.mjs','deploy.sh')
    frontend_dirs=('app','components','lib','public','scripts')
    for name in frontend_root_files:
        p=FRONTEND/name
        if p.is_file():
            try: m=p.stat().st_mtime
            except OSError: continue
            if m>newest: newest,newest_path=m,str(p)
    for dirname in frontend_dirs:
        base=FRONTEND/dirname
        if not base.exists(): continue
        for p in base.rglob('*'):
            if not p.is_file() or any(part.startswith('.next') or part in {'node_modules','__pycache__'} for part in p.parts):
                continue
            # FRONTEND_QUIET_SCOPE_PARITY_V1: mirror frontend-source-hash.py
            # exactly enough that the certifier never waits on bytes that cannot
            # change the immutable runtime generation. Generated Browser Gateway
            # ZIP, QA probes and editor/rollback copies are not release inputs.
            rel=p.relative_to(FRONTEND)
            if rel.as_posix()=='public/boris-browser-gateway.zip':
                continue
            if rel.parts and rel.parts[0]=='scripts' and p.name.startswith('qa-'):
                continue
            if any(marker in p.name for marker in ('.bak','.before','.backup','~','.pyc')):
                continue
            if re.search(r'(?:\.broken_[^/]*|\.step\d+|\.old(?:\.\d+)?|\.orig|\.tmp(?:\.\d+)?)$',p.name,re.I):
                continue
            try: m=p.stat().st_mtime
            except OSError: continue
            if m>newest: newest,newest_path=m,str(p)
    age=max(0.0,time.time()-newest) if newest else 10**9
    return {'quiet':age>=SOURCE_QUIET_SEC,'age_sec':round(age,1),'required_sec':SOURCE_QUIET_SEC,'newest_source':newest_path}

def write_latest(obj):
    LATEST.parent.mkdir(parents=True,exist_ok=True); tmp=LATEST.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)); os.replace(tmp,LATEST)
def append(obj):
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a') as f: f.write(json.dumps(obj,ensure_ascii=False)+'\n')

streak=0; streak_fp=None; started=time.time(); last={}; cycle=0
# SOURCE_CONTENT_QUIET_FENCE_V1: mtime can be preserved by atomic editors/restores.
# A release candidate is quiet only when the exact backend runtime byte fingerprint
# is unchanged for SOURCE_QUIET_SEC. This prevents a transient PASS from being
# counted after source bytes changed without a newer mtime.
source_content_fp=None; source_content_since=0.0
def backend_content_fp():
    env=dict(os.environ); env['PYTHONPATH']=str(ROOT)
    p=run([str(ROOT/'venv/bin/python'),'-c','import production_contract_runner as p; print(p._backend_runtime_content_fingerprint())'],60,env)
    return p.stdout.strip().splitlines()[-1] if p.returncode==0 and p.stdout.strip() else ''
# Event-driven gate: do not burn a full 20-account production contract while a
# deploy is running or source is still changing.  Observe cheaply until the
# release boundary is eligible, then take repeated independent contract samples.
while True:
    cycle += 1
    busy,procs=deploy_busy(); quiet=source_quiet_state()
    current_content_fp=backend_content_fp()
    if current_content_fp and current_content_fp == source_content_fp:
        content_age=max(0.0,time.time()-source_content_since)
    else:
        source_content_fp=current_content_fp; source_content_since=time.time(); content_age=0.0
    quiet['content_fingerprint']=current_content_fp
    quiet['content_stable_sec']=round(content_age,1)
    quiet['content_quiet']=bool(current_content_fp and content_age>=SOURCE_QUIET_SEC)
    eligible=(not busy and quiet['quiet'] and quiet['content_quiet'])
    if eligible:
        rc,c,err=contract(); q=queue_state()
        services={u:active(u) for u in CORE}; http={'health':public('https://boris-ai.pro/health'),'dashboard':public('https://boris-ai.pro/dashboard')}
        qi=q.get('queue') or {}; ds=q.get('dispatcher') or {}
        contract_pass=(c.get('status')=='pass' and int(c.get('passed_accounts') or -1)==int(c.get('accounts') or -2) and not c.get('failures'))
        invariants=(qi.get('DUPLICATE_ACTIVE_TASKS')==0 and qi.get('DEAD_ASSIGNMENTS')==0 and qi.get('CLIENTS_PRESERVED')=='4/4' and ds.get('stale') is False)
        core_ok=all(services.values()) and http['health']==200 and http['dashboard']==200 and invariants
        fp=str(c.get('generation_fingerprint') or '')
        # SAMPLE_GENERATION_FENCE_V1: a full contract is not instantaneous.
        # Count the sample only if source bytes and release-writer state stayed
        # unchanged for the whole observation. Otherwise the next cycle must
        # wait for a fresh quiet window; never certify a mixed generation.
        post_content_fp=backend_content_fp()
        post_busy,post_procs=deploy_busy()
        candidate_unchanged=bool(current_content_fp and post_content_fp==current_content_fp and not post_busy)
        sample_ok=bool(contract_pass and core_ok and fp and candidate_unchanged)
        if not candidate_unchanged:
            source_content_fp=post_content_fp
            source_content_since=time.time()
            quiet['content_fingerprint']=post_content_fp
            quiet['content_stable_sec']=0.0
            quiet['content_quiet']=False
            if post_busy:
                procs=post_procs
    else:
        # Cheap observation only. This is not a failed production sample: there
        # is no stable release candidate to certify yet.
        rc=None; c={}; err=''; q={}; qi={}; ds={}; fp=''; sample_ok=False
        services={u:active(u) for u in CORE}; http={'health':public('https://boris-ai.pro/health'),'dashboard':public('https://boris-ai.pro/dashboard')}
    if sample_ok and fp==streak_fp: streak+=1
    elif sample_ok: streak_fp=fp; streak=1
    elif eligible: streak=0; streak_fp=None
    # If source/deploy churn resumes, any previous candidate is obsolete.
    else: streak=0; streak_fp=None
    false_shared_checks=sorted(k for k,v in (c.get('shared_checks') or {}).items() if v is False)
    phase='sampling' if eligible else ('waiting_deploy' if busy else 'waiting_source_quiet')
    row={'ts':now(),'cycle':cycle,'phase':phase,'eligible_for_contract':eligible,'contract_executed':eligible,'sample_ok':sample_ok,'stable_pass_streak':streak,'required_streak':REQUIRED_STREAK,'deploy_busy':busy,'deploy_processes':procs,'source_quiet':quiet,'contract_rc':rc,'contract_status':c.get('status'),'accounts':c.get('accounts'),'passed_accounts':c.get('passed_accounts'),'failures':c.get('failures'),'false_shared_checks':false_shared_checks,'generation_fingerprint':fp,'post_content_fingerprint':post_content_fp if eligible else None,'candidate_unchanged_during_sample':candidate_unchanged if eligible else None,'core_services':services,'http':http,'queue':{k:qi.get(k) for k in ['ACTIVE_ORDERS_PER_TASK_MAX','DUPLICATE_ACTIVE_TASKS','CLIENTS_PRESERVED','DEAD_ASSIGNMENTS']} if eligible else {},'dispatcher_stale':ds.get('stale') if eligible else None,'contract_stderr':err if eligible and not c else ''}
    append(row); last=row
    status={'contract':'BORIS_PRODUCTION_CONVERGENCE_CERTIFIER_V2','ok':streak>=REQUIRED_STREAK,'state':'pass' if streak>=REQUIRED_STREAK else 'observing','phase':phase,'started_at':datetime.fromtimestamp(started,timezone.utc).isoformat(),'updated_at':now(),'stable_pass_streak':streak,'required_streak':REQUIRED_STREAK,'last':row,'policy':{'scope':'cross_contour_generation_convergence_only','read_only':True,'self_heal_disabled':True,'paid_ai_calls':0,'provider_mutations':0,'client_mutations':0,'functional_qa_owned_elsewhere':True,'event_driven_gate':True,'contract_only_when_candidate_stable':True}}
    write_latest(status); print(json.dumps(row,ensure_ascii=False),flush=True)
    if streak>=REQUIRED_STREAK:
        print('PRODUCTION_CONVERGENCE_CERTIFIER_V2=PASS',flush=True); sys.exit(0)
    if MAX_CYCLES>0 and cycle>=MAX_CYCLES:
        status['state']='timeout_without_stable_pass'; status['ok']=False; write_latest(status)
        print('PRODUCTION_CONVERGENCE_CERTIFIER_V2=FAIL',flush=True); sys.exit(1)
    time.sleep(INTERVAL)
