#!/root/BORIS/backend/venv/bin/python
import json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,'/root/BORIS/backend')
from app.db.session import SessionLocal
from app.models.storage import Storage

ROOT=Path('/root/BORIS/backend')
LOG=ROOT/'logs/max150_followon_guard_20260903.log'
ACCOUNTS=['3411770_94346','andrey_launzh_mebel_akkaunt_penza_82435','andrey_mebel_launzh_moskva_69737','antonybatono_78732','dinara_spb_vodnaya_ekipirovka_18294','evgeniy_peregorodki_12447','evz_denis_aleksandra_evakuatory_i_pricepy_22264','garik_plitka_mo_58647','georgiy_mo_elektrosamokaty_88067','mbroker_27859','otdushi','pbi_artashes_plitka_mo_83993','planetazayavki_65985','prodazha_bytovok_25677','vasyailchenko_82650']

def stamp(): return datetime.now(timezone.utc).isoformat()
def log(s):
    with LOG.open('a',encoding='utf-8') as f: f.write(str(s)+'\n')
def run(cmd, timeout=260, env=None):
    try:
        p=subprocess.run(cmd,cwd=str(ROOT),capture_output=True,text=True,timeout=timeout,env=env)
        out=(p.stdout or '')+(('\n'+p.stderr) if p.stderr else '')
        return p.returncode,out
    except subprocess.TimeoutExpired as e:
        return 124,'TIMEOUT '+str(e)
    except Exception as e:
        return 125,type(e).__name__+': '+str(e)
def caps():
    db=SessionLocal(); vals=[]
    try:
        for a in ACCOUNTS:
            r=db.query(Storage).filter(Storage.account_id==a,Storage.key=='kpi_settings').order_by(Storage.id.desc()).first()
            try: k=json.loads(r.value or '{}') if r else {}
            except Exception: k={}
            vals.append((a,float(k.get('hard_max_bid_rub') or 0)))
    finally: db.close()
    return vals

LOG.write_text('START '+stamp()+'\nPRIMARY_SOAK_COMPLETE_START_IMMEDIATE\n',encoding='utf-8')
time.sleep(5)
for cycle in range(1,9):
    log('===FOLLOWON_%d %s==='%(cycle,stamp()))
    for name,cmd,to in [
        ('guardian',['./venv/bin/python','control_plane_guardian_runner.py','--json'],90),
        ('rollout',['./venv/bin/python','marketer_rollout_runner.py'],260),
        ('qa_reach',['./venv/bin/python','qa_marketer_reach_safety.py'],90),
        ('qa_rollout',['./venv/bin/python','qa_marketer_rollout.py'],90),
        ('contract',['./venv/bin/python','production_contract_runner.py'],300),
    ]:
        rc,out=run(cmd,to)
        lines=[x for x in out.splitlines() if x.strip()]
        if name in {'guardian','rollout'}:
            log(name.upper()+'_RC='+str(rc))
            for line in lines[-40:]: log(line)
        else:
            log(name.upper()+'_RC='+str(rc)+' LAST='+(lines[-1] if lines else 'NO_OUTPUT'))
    vals=caps()
    log('CAPS_150 %d OF %d %s'%(sum(1 for _,v in vals if v==150.0),len(vals),vals))
    rc1,o1=run(['systemctl','is-enabled','boris-gitdeploy.service'],10)
    rc2,o2=run(['systemctl','is-active','boris-gitdeploy.service'],10)
    log('GITDEPLOY '+o1.strip()+'/'+o2.strip())
    if cycle<8: time.sleep(300)
log('DONE '+stamp())
