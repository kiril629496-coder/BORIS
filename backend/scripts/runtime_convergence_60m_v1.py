#!/root/BORIS/backend/venv/bin/python
import subprocess,time,os,json
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/root/BORIS/backend'); LOG=ROOT/'logs/runtime_convergence_60m_v1.log'
def log(s):
 with LOG.open('a') as f:f.write(str(s)+'\n')
def run(cmd,to=180,env=None):
 try:
  p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=to,env=env); return p.returncode,(p.stdout or '')+(p.stderr or '')
 except Exception as e:return 125,repr(e)
def newest_source():
 newest=(0.0,'')
 for base in [ROOT/'app',ROOT]:
  for p in base.rglob('*') if base.name=='app' else base.glob('*.py'):
   if not p.is_file() or p.suffix not in ('.py','.sh'): continue
   try:m=p.stat().st_mtime
   except:continue
   if m>newest[0]: newest=(m,str(p))
 return newest
def svc_epoch(name):
 rc,o=run(['systemctl','show',name,'-p','ExecMainStartTimestamp','--value'],10)
 if rc:return 0
 rc2,x=run(['date','-d',o.strip(),'+%s'],10); return int(x.strip()) if rc2==0 and x.strip().isdigit() else 0
LOG.write_text('START '+datetime.now(timezone.utc).isoformat()+'\n')
for cycle in range(1,13):
 log('===CYCLE %02d %s==='%(cycle,datetime.now(timezone.utc).isoformat()))
 m,p=newest_source(); b=svc_epoch('boris-backend.service'); r=svc_epoch('boris-backend-replica.service')
 log(f'GEN newest={m:.0f} backend={b} replica={r} path={p}')
 stale = m>min(b,r)
 if stale:
  # require 45s quiescence before touching runtime; if source still moving, defer safely.
  time.sleep(45); m2,p2=newest_source()
  if m2==m:
   # DEPLOY_COORDINATOR_OWNS_ROLL_V1: delegate normal generation convergence
   # to the canonical trusted deploy service instead of racing its cgroup.
   rc,o=run(['systemctl','is-active','boris-deploy.service'],20); log('DEPLOY_OWNER='+o.strip())
   if o.strip()!='active':
    rc,o=run(['sudo','systemctl','start','boris-deploy.service'],30); log('DEPLOY_START_RC='+str(rc))
   else: log('ROLL_DELEGATED=boris-deploy.service')
  else: log(f'DEFER source_churn old={m:.0f} new={m2:.0f} path={p2}')
 rc,o=run(['./venv/bin/python','production_contract_runner.py'],300); lines=[x for x in o.splitlines() if x.strip()]; log('CONTRACT_RC='+str(rc)); log(lines[-1] if lines else '')
 rc,o=run(['systemctl','is-active','boris-backend.service','boris-backend-replica.service','boris-background-worker.service','boris-telephony-runtime.service','boris-frontend.service','boris-crm-sync.service'],20); log('SERVICES='+','.join(o.split()))
 rc,g1=run(['systemctl','is-enabled','boris-gitdeploy.service'],10); rc,g2=run(['systemctl','is-active','boris-gitdeploy.service'],10); log('GIT='+g1.strip()+'/'+g2.strip())
 if cycle<12:time.sleep(255)
log('DONE '+datetime.now(timezone.utc).isoformat())
