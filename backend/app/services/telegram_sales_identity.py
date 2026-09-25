from __future__ import annotations

def trusted_author_identity(external_author_id, payload):
    payload=payload or {}
    if not isinstance(payload,dict) or bool(payload.get("author_is_bot")) or bool(payload.get("author_is_min")):
        return None,None
    try:
        peer=int(str(external_author_id or "").strip())
        access_hash=int(str(payload.get("author_access_hash") or "").strip())
    except Exception:
        return None,None
    if peer<=0 or access_hash==0:
        return None,None
    return peer,access_hash
