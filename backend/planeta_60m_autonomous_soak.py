# RETIRED_OWNERLESS_PLANETA_MONEY_SOAK_V1
# Historical diagnostic only. It used direct provider reads, first-bid money
# bootstrap calls and a direct rolling-restart attempt outside the canonical
# trusted production lanes. Never execute it again; keep source for forensics.
raise SystemExit("BLOCKED_RETIRED_OWNERLESS_PLANETA_MONEY_SOAK")

import json,time,subprocess,httpx,traceback
from datetime import datetime,timezone
from pathlib import Path
from app.api.avito import get_avito_token,_kpi_fetch_live_item,kpi_goal_tick,_kpi_apply_log_load
from app.api.cpx_advisor import bootstrap_new_no_promo
from app.db.session import SessionLocal
ACC="planetazayavki_65985"; ITEM="8200446429"; EXPECTED="Гардеробная по индивидуальным размерам Ижевск"
BASELINE_UPLOAD=589643343
LOG=Path("/root/BORIS/backend/.boris_logs/planeta_60m_autonomous_soak_20260903.jsonl")
SUMMARY=Path("/root/BORIS/backend/.boris_logs/planeta_60m_autonomous_soak_20260903.summary.json")
LOG.parent.mkdir(parents=True,exist_ok=True)
def emit(kind,**data):
 rec={"ts":datetime.now(timezone.utc).isoformat(),"kind":kind,**data}; line=json.dumps(rec,ensure_ascii=False,default=str); print(line,flush=True); LOG.open("a",encoding="utf-8").write(line+"\n"); return rec
def token_headers():
 t=get_avito_token(ACC); tok=t.get("access_token") if isinstance(t,dict) else t; return {"Authorization":f"Bearer {tok}"} if tok else {}
def upload_current():
 h=token_headers()
 if not h:return {"status":"no_token"}
 try:
  r=httpx.get("https://api.avito.ru/autoload/v4/uploads/current",headers=h,timeout=20)
  if r.status_code!=200:return {"status":"http_error","http":r.status_code,"body":r.text[:200]}
  b=r.json() or {}; return {"status":"ok","upload_id":b.get("upload_id"),"avito_status":b.get("status"),"started_at":b.get("started_at"),"source":b.get("source"),"stats":b.get("stats")}
 except Exception as e:return {"status":"error","error":f"{type(e).__name__}: {str(e)[:200]}"}
def op_state():
 d=SessionLocal()
 try:
  _,h=_kpi_apply_log_load(d,ACC); op=h[-1] if h else {}; return {k:op.get(k) for k in ("operation_id","status","rollback_retry_count","rollback_publish_requested_at","rollback_retry_scheduled_at","rolled_back_published_at")}
 finally:d.close()
def contract():
 try:
  p=subprocess.run(["/root/BORIS/backend/venv/bin/python","production_contract_runner.py"],cwd="/root/BORIS/backend",capture_output=True,text=True,timeout=180)
  last=(p.stdout or "").strip().splitlines()[-1] if (p.stdout or "").strip() else ""
  try:b=json.loads(last)
  except:b={"raw":last[-1200:]}
  return {"rc":p.returncode,"status":b.get("status"),"policy":b.get("policy_version"),"failures":b.get("failures") or [],"runtime_self_heal":b.get("runtime_self_heal")}
 except Exception as e:return {"rc":999,"status":"error","error":f"{type(e).__name__}: {str(e)[:300]}"}
def maybe_heal_runtime(c):
 s=json.dumps(c,ensure_ascii=False)
 if "backend_runtime_generation" not in s:return None
 try:
  p=subprocess.run(["sudo","/root/BORIS/backend/rolling_restart_backend.sh"],capture_output=True,text=True,timeout=240)
  return {"rc":p.returncode,"tail":((p.stdout or "")+(p.stderr or ""))[-1500:]}
 except Exception as e:return {"rc":999,"error":str(e)[:300]}
start=time.time(); deadline=start+3600; iteration=0; matched=False; new_upload=False; contract_passes=0; first_bid_runs=[]
emit("soak_start",duration_sec=3600,baseline_upload=BASELINE_UPLOAD,expected_title=EXPECTED)
while time.time()<deadline:
 iteration+=1
 try:
  live=_kpi_fetch_live_item(ACC,ITEM) or {}; it=live.get("item") or {}; title=str(it.get("title") or it.get("Title") or "")
  upl=upload_current(); op=op_state()
  uid=int(upl.get("upload_id") or 0) if isinstance(upl,dict) else 0
  if uid and uid!=BASELINE_UPLOAD:new_upload=True
  emit("proof_cycle",iteration=iteration,live_status=live.get("status"),live_title=title,upload=upl,operation=op)
  if live.get("status")=="ok" and title.strip()==EXPECTED:
   matched=True
   time.sleep(5)
   tick=kpi_goal_tick(ACC,allow_stale_non_money=True) or {}; emit("planeta_confirm_tick",iteration=iteration,tick=tick)
   time.sleep(3)
   after=op_state(); emit("planeta_confirmed",iteration=iteration,operation=after)
  elif new_upload:
   # New official upload is proof Avito resumed pulling. Advance only one bounded state transition.
   tick=kpi_goal_tick(ACC,allow_stale_non_money=True) or {}; emit("new_upload_state_tick",iteration=iteration,tick=tick)
  if iteration in (1,6,11,16):
   for a in ("planetazayavki_65985","antonybatono_78732","mbroker_27859"):
    try:
     b=bootstrap_new_no_promo(a,max_items=50) or {}; rec={"account":a,"status":b.get("status"),"applied":len(b.get("applied") or []),"blocked":len(b.get("blocked") or []),"failed":len(b.get("failed") or []),"changed_avito":bool(b.get("changed_avito"))}; first_bid_runs.append(rec); emit("first_bid_check",**rec)
    except Exception as e:emit("first_bid_exception",account=a,error=f"{type(e).__name__}: {str(e)[:250]}")
  if iteration in (1,6,11,16):
   c=contract(); emit("production_contract",iteration=iteration,contract=c)
   if c.get("status")=="pass":contract_passes+=1
   else:
    heal=maybe_heal_runtime(c)
    if heal:emit("runtime_heal",iteration=iteration,result=heal)
 except Exception as e:
  emit("cycle_exception",iteration=iteration,error=f"{type(e).__name__}: {str(e)[:500]}",trace=traceback.format_exc()[-1200:])
 # 3-minute provider-friendly cadence; no double-read storm.
 remaining=deadline-time.time()
 if remaining>0:time.sleep(min(180,remaining))
final_live=_kpi_fetch_live_item(ACC,ITEM) or {}; fit=final_live.get("item") or {}; final_title=str(fit.get("title") or fit.get("Title") or "")
final_upload=upload_current(); final_op=op_state(); final_contract=contract()
summary={"finished_at":datetime.now(timezone.utc).isoformat(),"iterations":iteration,"planeta_live_title":final_title,"planeta_rollback_confirmed":final_title.strip()==EXPECTED,"new_upload_seen":new_upload,"final_upload":final_upload,"operation":final_op,"contract":final_contract,"contract_passes_during_soak":contract_passes,"first_bid_checks":first_bid_runs[-12:]}
SUMMARY.write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
emit("soak_finish",summary=summary)
