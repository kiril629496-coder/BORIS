#!/usr/bin/env python3
"""Repair duplicate logical singleton rows in Storage without guessing business data.

Known list/object/cache keys have deterministic merge policies. Unknown duplicate keys
are reported and left untouched. No provider calls, no external side effects.
"""
from __future__ import annotations
import json, hashlib
from app.db.session import SessionLocal
from app.models.storage import Storage
from sqlalchemy import text

KNOWN_EXACT_KEYS={"kpi_action_history","templates"}
KNOWN_KEEP_OLDEST_KEYS={"revenue_analytics_setup_v1"}
KNOWN_KEEP_LATEST_KEYS={"mcn_repeat_card_auto_resend_v1"}
KNOWN_PREFIXES=("execution_receipt:","global_policy_generation:","revenue_call_count_cache:")

def _lock_key(account_id,key):
    d=hashlib.blake2b((str(account_id)+"|"+str(key)).encode(),digest_size=8).digest()
    n=int.from_bytes(d,"big",signed=False)
    return n if n < (1<<63) else n-(1<<64)

def _merge_list_rows(key, rows):
    values=[]
    for r in rows:
        try:v=json.loads(r.value or "[]")
        except Exception:v=[]
        if isinstance(v,list): values.extend(x for x in v if isinstance(x,dict))
    if key=="kpi_action_history":
        seen={};
        for x in values:
            sig=json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
            seen[sig]=x
        return json.dumps(sorted(seen.values(),key=lambda x:str(x.get("ts") or ""))[-500:],ensure_ascii=False)
    if key=="templates":
        byid={}; order=[]
        for x in values:
            tid=str(x.get("id") if x.get("id") is not None else json.dumps(x,sort_keys=True,default=str))
            if tid not in byid: order.append(tid)
            if tid not in byid or len(json.dumps(x,ensure_ascii=False,default=str)) > len(json.dumps(byid[tid],ensure_ascii=False,default=str)):
                byid[tid]=x
        return json.dumps([byid[i] for i in order],ensure_ascii=False)
    raise ValueError(key)

def reconcile_once():
    db=SessionLocal(); report={"repaired":[],"unknown":[],"remaining":[]}
    try:
        groups=db.execute(text("select account_id,key,count(*) c from storage group by account_id,key having count(*)>1 order by account_id,key")).fetchall()
        for account_id,key,count in groups:
            known=key in KNOWN_EXACT_KEYS or key in KNOWN_KEEP_OLDEST_KEYS or key in KNOWN_KEEP_LATEST_KEYS or any(str(key).startswith(p) for p in KNOWN_PREFIXES)
            if not known:
                report["unknown"].append({"account_id":account_id,"key":key,"count":count}); continue
            db.execute(text("select pg_advisory_xact_lock(:k)"),{"k":_lock_key(account_id,key)})
            rows=(db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).order_by(Storage.id.asc()).with_for_update().all())
            if len(rows)<2: continue
            if key in KNOWN_KEEP_OLDEST_KEYS:
                canonical=rows[0]
                strategy="keep_oldest_external_key"
                deleted=[int(r.id) for r in rows[1:]]
                for r in rows[1:]: db.delete(r)
            else:
                canonical=rows[-1]
                if key in KNOWN_EXACT_KEYS:
                    canonical.value=_merge_list_rows(key,rows)
                    strategy="merge_list"
                else:
                    # receipts/policy/cache are snapshots: newest row is canonical.
                    strategy="keep_latest"
                deleted=[int(r.id) for r in rows[:-1]]
                for r in rows[:-1]: db.delete(r)
            db.commit()
            report["repaired"].append({"account_id":account_id,"key":key,"kept":int(canonical.id),"deleted":deleted,"strategy":strategy})
        report["remaining"]=[{"account_id":a,"key":k,"count":c} for a,k,c in db.execute(text("select account_id,key,count(*) c from storage group by account_id,key having count(*)>1 order by account_id,key")).fetchall()]
        report["status"]="PASS" if not report["remaining"] else ("PARTIAL" if report["unknown"] else "FAIL")
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report["status"] in ("PASS","PARTIAL") else 2
    finally: db.close()

if __name__=='__main__': raise SystemExit(reconcile_once())
