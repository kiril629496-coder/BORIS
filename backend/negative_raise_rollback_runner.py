#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
sys.path.insert(0,'/root/BORIS/backend')
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.api.cpx_advisor import ApplyOneBody,apply_one,negative_raise_rollback_candidates
from app.services.control_plane_adapters_ext import marketing_service_entitlement
STATE_KEY='negative_raise_rollback_runtime'
def save(db,a,p):
 raw=json.dumps(p,ensure_ascii=False); r=db.query(Storage).filter(Storage.account_id==a,Storage.key==STATE_KEY).order_by(Storage.id.desc()).first()
 if r:r.value=raw
 else:db.add(Storage(account_id=a,key=STATE_KEY,value=raw))
 db.commit()
def main():
 db=SessionLocal()
 try: accounts=[str(x[0]) for x in db.query(Storage.account_id).filter(Storage.key=='cpx_learning_journal').distinct().all()]
 finally:db.close()
 S={'checked_accounts':0,'candidates':0,'applied':0,'blocked':0,'skipped':0,'errors':0,'details':[]}
 for a in accounts:
  db=SessionLocal()
  try:
   ent=marketing_service_entitlement(db,a) or {}
   if str(ent.get('state') or '').lower()!='active':continue
   cand=negative_raise_rollback_candidates(db,a); S['checked_accounts']+=1; S['candidates']+=len(cand)
   if not cand: save(db,a,{'status':'ok','candidate_count':0,'checked_at':datetime.now(timezone.utc).isoformat()}); continue
   cand.sort(key=lambda x:str(x.get('finished_at') or ''),reverse=True); c=cand[0]; db.close()
   r=apply_one(ApplyOneBody(account_id=a,item_id=int(c['item_id']),action='lower',max_bid_delta_pct=10,actor_type='boris_auto',source='negative_raise_rollback',trigger='worsened_raise_feedback',request_id=f"neg-rollback:{a}:{c['item_id']}:{c.get('finished_at') or 'unknown'}")) or {}
   st=str(r.get('status') or 'error'); changed=bool(st=='ok' and r.get('changed_avito') is not False)
   # NEGATIVE_ROLLBACK_RUNTIME_MONOTONIC_VERIFY_V1: the generic money writer
   # already blocks lower->raise inversion. The self-heal runner independently
   # verifies the returned money receipt too, so future writer regressions cannot
   # be counted as a successful safety rollback. No second provider write is made.
   if changed:
    try:
     _old=float(r.get('old_bid_rub')); _new=float(r.get('new_bid_rub'))
    except Exception:
     _old=_new=None
    if _old is None or _new is None or _new >= _old:
     st='error'; changed=False; S['errors']+=1
     r=dict(r); r['reason_code']='negative_rollback_non_monotonic_receipt'
   if changed:S['applied']+=1
   elif st=='blocked':S['blocked']+=1
   elif st=='skipped':S['skipped']+=1
   else:S['errors']+=1
   rec={'account_id':a,'candidate':c,'status':st,'changed_avito':changed,'reason':r.get('blocked_by') or r.get('reason_code') or r.get('reason') or r.get('message')}; S['details'].append(rec)
   db=SessionLocal(); save(db,a,{'status':st,'candidate_count':len(cand),'candidate':c,'result':rec,'checked_at':datetime.now(timezone.utc).isoformat()})
  except Exception as e:S['errors']+=1;S['details'].append({'account_id':a,'status':'error','reason':str(e)[:220]})
  finally:
   try:db.close()
   except Exception:pass
 print(json.dumps(S,ensure_ascii=False)); return 1 if S['errors'] else 0
if __name__=='__main__':raise SystemExit(main())
