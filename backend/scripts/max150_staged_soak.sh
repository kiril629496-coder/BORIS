#!/usr/bin/env bash
set -u
cd /root/BORIS/backend
LOG=logs/max150_staged_soak_20260903.log
exec >>"$LOG" 2>&1
echo START $(date -u +%FT%TZ)
echo GIT $(systemctl is-enabled boris-gitdeploy.service 2>/dev/null || true) $(systemctl is-active boris-gitdeploy.service 2>/dev/null || true)
for cycle in $(seq 1 7); do
  echo ===MAX150_STAGED_$cycle $(date -u +%FT%TZ)===
  ./venv/bin/python -m py_compile app/services/marketing_money_policy.py app/api/cpx_advisor.py marketer_rollout_runner.py control_plane_guardian_runner.py production_contract_runner.py || echo COMPILE_FAIL
  ./venv/bin/python control_plane_guardian_runner.py --json || true
  timeout 240 env BORIS_MARKETER_MONEY_LANE=staged_rollout_v1 ./venv/bin/python marketer_rollout_runner.py || echo ROLLOUT_TIMEOUT_OR_FAIL
  ./venv/bin/python qa_marketer_reach_safety.py | tail -n 1
  ./venv/bin/python qa_marketer_rollout.py | tail -n 1
  BORIS_POLICY_SELF_HEAL=0 ./venv/bin/python production_contract_runner.py | tail -n 1
  ./venv/bin/python -c "import json; from app.db.session import SessionLocal; from app.models.storage import Storage; A=['3411770_94346','andrey_launzh_mebel_akkaunt_penza_82435','andrey_mebel_launzh_moskva_69737','antonybatono_78732','dinara_spb_vodnaya_ekipirovka_18294','evgeniy_peregorodki_12447','evz_denis_aleksandra_evakuatory_i_pricepy_22264','garik_plitka_mo_58647','georgiy_mo_elektrosamokaty_88067','mbroker_27859','otdushi','pbi_artashes_plitka_mo_83993','planetazayavki_65985','prodazha_bytovok_25677','vasyailchenko_82650']; db=SessionLocal(); vals=[]; [vals.append((a,(lambda r: json.loads(r.value or '{}').get('hard_max_bid_rub') if r else None)(db.query(Storage).filter(Storage.account_id==a,Storage.key=='kpi_settings').order_by(Storage.id.desc()).first()))) for a in A]; print('CAPS',vals); print('CAPS_150',sum(1 for _,v in vals if float(v or 0)==150.0),'OF',len(vals)); db.close()"
  echo GIT $(systemctl is-enabled boris-gitdeploy.service 2>/dev/null || true) $(systemctl is-active boris-gitdeploy.service 2>/dev/null || true)
  sleep 360
done
echo FINAL $(date -u +%FT%TZ)
./venv/bin/python qa_marketer_reach_safety.py | tail -n 1
./venv/bin/python qa_marketer_rollout.py | tail -n 1
BORIS_POLICY_SELF_HEAL=0 ./venv/bin/python production_contract_runner.py | tail -n 1
echo DONE
