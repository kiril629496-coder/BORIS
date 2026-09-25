from __future__ import annotations
import hashlib, json
from sqlalchemy import text
from app.models.storage import Storage

# NOTIFICATION_STORAGE_ATOMIC_V1

def _lock_key(account_id: str) -> int:
    d=hashlib.blake2b(("notifications|"+str(account_id or "")).encode(),digest_size=8).digest()
    n=int.from_bytes(d,"big",signed=False)
    return n if n < (1<<63) else n-(1<<64)

def _load_locked(db, account_id: str):
    db.execute(text("select pg_advisory_xact_lock(:k)"),{"k":_lock_key(account_id)})
    rows=(db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="notifications")
          .order_by(Storage.id.asc()).with_for_update().all())
    items=[]
    for row in rows:
        try: part=json.loads(row.value or "[]")
        except Exception: part=[]
        if isinstance(part,list): items.extend(x for x in part if isinstance(x,dict))
    # Historical duplicate rows may contain the same logical notification.
    merged=[]; seen=set()
    for x in items:
        sig=str(x.get("notification_id") or "") or json.dumps({"ts":x.get("ts"),"text":x.get("text"),"related_results":x.get("related_results")},ensure_ascii=False,sort_keys=True,default=str)
        if sig in seen: continue
        seen.add(sig); merged.append(x)
    return rows, merged

def _persist(db, account_id: str, rows, items, max_items=50):
    items=list(items or [])[-max(1,int(max_items)):]
    raw=json.dumps(items,ensure_ascii=False)
    if rows:
        canonical=rows[-1]; canonical.value=raw
        for old in rows[:-1]: db.delete(old)
    else:
        db.add(Storage(account_id=account_id,key="notifications",value=raw))
    return items

def append_notification(db, account_id: str, item: dict, *, max_items: int=50, idempotency_key: str="", commit: bool=True):
    rows,items=_load_locked(db,account_id)
    obj=dict(item or {})
    if idempotency_key:
        obj["notification_id"]=str(idempotency_key)
        for existing in items:
            if str(existing.get("notification_id") or "")==str(idempotency_key):
                if commit: db.commit()
                return existing
    items.append(obj)
    _persist(db,account_id,rows,items,max_items=max_items)
    if commit: db.commit()
    return obj

def mark_read(db, account_id: str, ts: str, *, commit: bool=True):
    rows,items=_load_locked(db,account_id)
    changed=0
    for n in items:
        if str(n.get("ts") or "")==str(ts):
            if not n.get("read"): changed+=1
            n["read"]=True
    _persist(db,account_id,rows,items)
    if commit: db.commit()
    return changed

def read_notifications(db, account_id: str):
    rows=(db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="notifications").order_by(Storage.id.asc()).all())
    items=[]; seen=set()
    for row in rows:
        try: part=json.loads(row.value or "[]")
        except Exception: part=[]
        if not isinstance(part,list): continue
        for x in part:
            if not isinstance(x,dict): continue
            sig=str(x.get("notification_id") or "") or json.dumps({"ts":x.get("ts"),"text":x.get("text"),"related_results":x.get("related_results")},ensure_ascii=False,sort_keys=True,default=str)
            if sig in seen: continue
            seen.add(sig); items.append(x)
    return items
