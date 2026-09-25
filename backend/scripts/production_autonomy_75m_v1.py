#!/root/BORIS/backend/venv/bin/python
import json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
R=Path('/root/BORIS/backend'); L=R/'logs/production_autonomy_75m_v1.log'
def run(cmd,to=240):
 try:
  p=subprocess.run(cmd,cwd=R,capture_output=True,text=True,timeout=to); return p.returncode,(p.stdout or '')+(p.stderr or '')
 except Exception as e:return 125,repr(e)
def log(x):
 with L.open('a') as f:f.write(str(x)+'\n')
def j(out):
 for x in reversed(out.splitlines()):
  try:return json.loads(x)
  except:pass
 return {}
L.write_text('START '+datetime.now(timezone.utc).isoformat()+' PRODUCTION_AUTONOMY_75M_V1\n')
for c in range(1,13):
 log('===CYCLE %02d %s==='%(c,datetime.now(timezone.utc).isoformat()))
 rc,o=run(['systemctl','is-active','boris-backend.service','boris-backend-replica.service','boris-background-worker.service','boris-dispatcher.service','boris-ops.service','boris-frontend.service','boris-crm-sync.service','boris-telephony-runtime.service'],20); log('SERVICES='+','.join(o.split()))
 for port in ('8000','8001'):
  rc,o=run(['curl','-fsS','--max-time','5',f'http://127.0.0.1:{port}/health'],10); log(f'HEALTH{port}_RC={rc} '+o.strip())
 rc,o=run(['systemctl','show','boris-ai-marketer.service','-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStatus'],20); log('AI='+o.strip().replace('\n',';'))
 rc,o=run(['./venv/bin/python','control_plane_guardian_runner.py','--json'],120); d=j(o); log('GUARDIAN rc=%s status=%s open=%s waiting=%s degraded=%s'%(rc,d.get('status'),d.get('open_p0_p1'),d.get('waiting_owner_policy'),d.get('business_modules_degraded')))
 rc,o=run(['./venv/bin/python','qa_marketer_rollout.py'],90); log('QA_ROLLOUT rc=%s %s'%(rc,o.splitlines()[-1] if o.splitlines() else ''))
 # PRODUCTION_AUTONOMY_OBSERVER_ONLY_V2: this remote soak is evidence-only.
 # It must never self-heal/restart runtime services; canonical deploy/guardians own recovery.
 rc,o=run(['env','BORIS_POLICY_SELF_HEAL=0','./venv/bin/python','production_contract_runner.py'],300); d=j(o); log('CONTRACT rc=%s status=%s passed=%s/%s failures=%s'%(rc,d.get('status'),d.get('passed_accounts'),d.get('accounts'),len(d.get('failures') or [])))
 rc,a=run(['systemctl','is-enabled','boris-gitdeploy.service'],10); rc,b=run(['systemctl','is-active','boris-gitdeploy.service'],10); log('GIT='+a.strip()+'/'+b.strip())
 if c<12:time.sleep(360)
log('DONE '+datetime.now(timezone.utc).isoformat())
