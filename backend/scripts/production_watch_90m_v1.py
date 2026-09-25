#!/root/BORIS/backend/venv/bin/python
import subprocess,time,json,os
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/root/BORIS/backend'); LOG=ROOT/'logs/production_watch_90m_v1.log'
ACCOUNTS=['3411770_94346','andrey_launzh_mebel_akkaunt_penza_82435','andrey_mebel_launzh_moskva_69737','antonybatono_78732','dinara_spb_vodnaya_ekipirovka_18294','evgeniy_peregorodki_12447','evz_denis_aleksandra_evakuatory_i_pricepy_22264','garik_plitka_mo_58647','georgiy_mo_elektrosamokaty_88067','mbroker_27859','otdushi','pbi_artashes_plitka_mo_83993','planetazayavki_65985','prodazha_bytovok_25677','vasyailchenko_82650']
def run(cmd,to=180,env=None):
 try:
  p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=to,env=env); return p.returncode,(p.stdout or '')+(p.stderr or '')
 except Exception as e:return 125,repr(e)
def log(s):
 with LOG.open('a') as f:f.write(str(s)+'\n')
def caps():
 code="""from app.db.session import SessionLocal
from app.models.storage import Storage
import json
A=%r
db=SessionLocal(); out=[]
for a in A:
 r=db.query(Storage).filter(Storage.account_id==a,Storage.key=='kpi_settings').order_by(Storage.id.desc()).first()
 try:k=json.loads(r.value or '{}') if r else {}
 except:k={}
 out.append((a,float(k.get('hard_max_bid_rub') or 0)))
print(json.dumps(out)); db.close()
"""%ACCOUNTS
 rc,o=run(['./venv/bin/python','-c',code],30); return o.strip() if rc==0 else 'ERROR:'+o[-300:]
LOG.write_text('START '+datetime.now(timezone.utc).isoformat()+'\n')
for cycle in range(1,19):
 log('===WATCH %02d %s==='%(cycle,datetime.now(timezone.utc).isoformat()))
 rc,o=run(['systemctl','is-active','boris-backend.service','boris-backend-replica.service','boris-background-worker.service','boris-dispatcher.service','boris-ops.service','boris-frontend.service','boris-crm-sync.service','boris-telephony-runtime.service'],20); log('SERVICES_RC=%s %s'%(rc,'|'.join(x.strip() for x in o.splitlines())))
 rc,g1=run(['systemctl','is-enabled','boris-gitdeploy.service'],10); rc2,g2=run(['systemctl','is-active','boris-gitdeploy.service'],10); log('GIT='+g1.strip()+'/'+g2.strip())
 log('CAPS='+caps())
 rc,o=run(['./venv/bin/python','control_plane_guardian_runner.py','--json'],90); log('GUARDIAN_RC='+str(rc)+' '+(o.splitlines()[-1] if o.splitlines() else ''))
 rc,o=run(['./venv/bin/python','production_contract_runner.py'],300); line=o.splitlines()[-1] if o.splitlines() else ''; log('CONTRACT_RC='+str(rc)+' '+line[:12000])
 if cycle<18: time.sleep(300)
log('DONE '+datetime.now(timezone.utc).isoformat())
