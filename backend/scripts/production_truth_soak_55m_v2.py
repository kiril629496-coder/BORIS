#!/root/BORIS/backend/venv/bin/python
from __future__ import annotations
import json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
R=Path('/root/BORIS/backend'); LOG=R/'logs/production_truth_soak_55m_v2.log'
SERV=['boris-backend.service','boris-backend-replica.service','boris-background-worker.service','boris-dispatcher.service','boris-ops.service','boris-frontend.service','boris-crm-sync.service','boris-telephony-runtime.service']
def run(cmd,to=240):
 try:
  p=subprocess.run(cmd,cwd=R,capture_output=True,text=True,timeout=to); return p.returncode,(p.stdout or '')+(p.stderr or '')
 except Exception as e:return 125,repr(e)
def log(x):
 with LOG.open('a') as f:f.write(str(x)+'\n')
def last_json(out):
 for line in reversed(out.splitlines()):
  try:return json.loads(line)
  except: pass
 return {}
LOG.write_text('START '+datetime.now(timezone.utc).isoformat()+' PRODUCTION_TRUTH_SOAK_55M_V2 NO_MONEY\n')
for c in range(1,12):
 log('===CYCLE %02d %s==='%(c,datetime.now(timezone.utc).isoformat()))
 rc,o=run(['systemctl','is-active',*SERV],20); log('SERVICES rc=%s %s'%(rc,','.join(o.split())))
 for port in ('8000','8001'):
  rc,o=run(['curl','-fsS','--max-time','5',f'http://127.0.0.1:{port}/health'],10); log(f'HEALTH{port} rc={rc} {o.strip()}')
 rc,o=run(['./venv/bin/python','control_plane_guardian_runner.py','--json'],150); d=last_json(o); log('GUARDIAN rc=%s status=%s open=%s waiting=%s business_degraded=%s'%(rc,d.get('status'),d.get('open_p0_p1'),d.get('waiting_owner_policy'),d.get('business_modules_degraded')))
 rc,o=run(['./venv/bin/python','production_contract_runner.py'],300); d=last_json(o); log('CONTRACT rc=%s status=%s passed=%s/%s failures=%s false=%s'%(rc,d.get('status'),d.get('passed_accounts'),d.get('accounts'),len(d.get('failures') or []),[k for k,v in (d.get('shared_checks') or {}).items() if not v][:12]))
 rc,o=run(['systemctl','show','boris-ai-marketer.service','-p','Result','-p','ExecMainStatus','-p','ActiveState','-p','SubState'],20); log('AI '+o.strip().replace('\n',';'))
 rc,o=run(['bash','-lc',"grep 'AI_MARKETER_DONE' logs/ai_marketer_hourly.log | tail -1"],10); log('AI_LAST '+o.strip())
 rc,a=run(['systemctl','is-enabled','boris-gitdeploy.service'],10); rc,b=run(['systemctl','is-active','boris-gitdeploy.service'],10); log('GIT '+a.strip()+'/'+b.strip())
 if c<11: time.sleep(300)
log('DONE '+datetime.now(timezone.utc).isoformat())
