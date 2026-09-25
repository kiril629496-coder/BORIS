from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.mcn_core import normalize_e164

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_default)


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        db = SessionLocal()
        try:
            db.execute(text("SELECT pg_advisory_xact_lock(721946323)"))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.commit()
            _SCHEMA_READY = True
        finally:
            db.close()


def _domain(value: str | None) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if "://" not in v:
        v = "https://" + v
    try:
        host = (urlparse(v).hostname or "").lower().strip(".")
    except Exception:
        return ""
    if not host or len(host) > 253:
        return ""
    return host


def _domain_allowed(host: str, allowed_domains: list[Any]) -> bool:
    h = _domain(host)
    if not h:
        return False
    for item in allowed_domains or []:
        a = _domain(str(item))
        if not a:
            continue
        if h == a or h.endswith("." + a):
            return True
    return False


def _site_by_public_key(public_key: str, origin: str | None = None) -> dict[str, Any] | None:
    ensure_schema()
    key = str(public_key or "").strip()
    if not re.fullmatch(r"ct_[A-Za-z0-9_-]{24,96}", key):
        return None
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM calltracking_sites WHERE public_key=:k AND enabled=true"), {"k": key}).mappings().first()
        if not row:
            return None
        out = dict(row)
        allowed = out.get("allowed_domains") if isinstance(out.get("allowed_domains"), list) else []
        if origin and not _domain_allowed(origin, allowed):
            return None
        return out
    finally:
        db.close()


def site_upsert(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    name = str(payload.get("name") or "Основной сайт").strip()[:160]
    domains = []
    for x in payload.get("allowed_domains") or []:
        d = _domain(str(x))
        if d and d not in domains:
            domains.append(d)
    if not domains:
        return {"status": "allowed_domains_required"}
    default_pool_id = payload.get("default_pool_id")
    db = SessionLocal()
    try:
        existing = db.execute(text("SELECT * FROM calltracking_sites WHERE account_id=:a AND name=:n FOR UPDATE"),
                              {"a": account_id, "n": name}).mappings().first()
        if default_pool_id is not None:
            pool = db.execute(text("SELECT id FROM calltracking_pools WHERE id=:id AND account_id=:a"),
                              {"id": int(default_pool_id), "a": account_id}).first()
            if not pool:
                return {"status": "invalid_default_pool"}
        if existing:
            row = db.execute(text("""
            UPDATE calltracking_sites SET allowed_domains=CAST(:d AS jsonb),default_pool_id=:p,
              enabled=:e,updated_at=now() WHERE id=:id RETURNING *
            """), {"d": _json(domains), "p": int(default_pool_id) if default_pool_id is not None else existing.get("default_pool_id"),
                   "e": bool(payload.get("enabled", True)), "id": existing["id"]}).mappings().one()
            rotated = False
        else:
            public_key = "ct_" + secrets.token_urlsafe(28)
            row = db.execute(text("""
            INSERT INTO calltracking_sites(account_id,name,public_key,allowed_domains,default_pool_id,enabled)
            VALUES(:a,:n,:k,CAST(:d AS jsonb),:p,:e) RETURNING *
            """), {"a": account_id, "n": name, "k": public_key, "d": _json(domains),
                   "p": int(default_pool_id) if default_pool_id is not None else None,
                   "e": bool(payload.get("enabled", True))}).mappings().one()
            rotated = True
        db.commit()
        return {"status": "ok", "site": dict(row), "public_key_created": rotated}
    finally:
        db.close()


def site_list(account_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT * FROM calltracking_sites WHERE account_id=:a ORDER BY id"), {"a": account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.close()


def static_binding_upsert(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    did_id = payload.get("did_id")
    if did_id is None:
        return {"status": "did_id_required"}
    source = str(payload.get("source") or "").strip()[:120]
    if not source:
        return {"status": "source_required"}
    db = SessionLocal()
    try:
        did = db.execute(text("""
        SELECT id,purpose,status,account_id FROM telephony_dids
        WHERE id=:id AND account_id=:a AND released_at IS NULL
        """), {"id": int(did_id), "a": account_id}).mappings().first()
        if not did:
            return {"status": "did_not_found"}
        binding_id = payload.get("id")
        if binding_id:
            row = db.execute(text("""
            UPDATE calltracking_static_bindings SET source=:s,source_ref=:sr,campaign_id=:ci,ad_id=:ad,location=:loc,
              active_from=COALESCE(:af,active_from),active_to=:at,enabled=:en,metadata_json=CAST(:m AS jsonb),updated_at=now()
            WHERE id=:id AND account_id=:a RETURNING *
            """), {"s": source, "sr": str(payload.get("source_ref") or "")[:200] or None,
                   "ci": str(payload.get("campaign_id") or "")[:200] or None,
                   "ad": str(payload.get("ad_id") or "")[:200] or None,
                   "loc": str(payload.get("location") or "")[:200] or None,
                   "af": payload.get("active_from"), "at": payload.get("active_to"),
                   "en": bool(payload.get("enabled", True)), "m": _json(payload.get("metadata") or {}),
                   "id": int(binding_id), "a": account_id}).mappings().first()
            if not row:
                return {"status": "binding_not_found"}
        else:
            row = db.execute(text("""
            INSERT INTO calltracking_static_bindings(account_id,did_id,source,source_ref,campaign_id,ad_id,location,
              active_from,active_to,enabled,metadata_json)
            VALUES(:a,:did,:s,:sr,:ci,:ad,:loc,COALESCE(:af,now()),:at,:en,CAST(:m AS jsonb))
            RETURNING *
            """), {"a": account_id, "did": int(did_id), "s": source,
                   "sr": str(payload.get("source_ref") or "")[:200] or None,
                   "ci": str(payload.get("campaign_id") or "")[:200] or None,
                   "ad": str(payload.get("ad_id") or "")[:200] or None,
                   "loc": str(payload.get("location") or "")[:200] or None,
                   "af": payload.get("active_from"), "at": payload.get("active_to"),
                   "en": bool(payload.get("enabled", True)), "m": _json(payload.get("metadata") or {})}).mappings().one()
        db.execute(text("UPDATE telephony_dids SET purpose='calltracking_static',updated_at=now() WHERE id=:id"),
                   {"id": int(did_id)})
        db.commit()
        return {"status": "ok", "binding": dict(row)}
    finally:
        db.close()


def static_binding_list(account_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text("""
        SELECT b.*,d.number_e164 FROM calltracking_static_bindings b
        JOIN telephony_dids d ON d.id=b.did_id
        WHERE b.account_id=:a ORDER BY b.id DESC
        """), {"a": account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.close()


def pool_upsert(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    name = str(payload.get("name") or "Основной динамический пул").strip()[:160]
    ttl = max(120, min(int(payload.get("lease_ttl_sec") or 1800), 86400))
    cooloff = max(60, min(int(payload.get("cooloff_sec") or 900), 86400))
    default_did_id = payload.get("default_did_id")
    db = SessionLocal()
    try:
        if default_did_id is not None:
            did = db.execute(text("SELECT id FROM telephony_dids WHERE id=:id AND account_id=:a AND released_at IS NULL"),
                             {"id": int(default_did_id), "a": account_id}).first()
            if not did:
                return {"status": "invalid_default_did"}
        row = db.execute(text("""
        INSERT INTO calltracking_pools(account_id,name,default_did_id,lease_ttl_sec,cooloff_sec,enabled)
        VALUES(:a,:n,:d,:ttl,:cool,:e)
        ON CONFLICT(account_id,name) DO UPDATE SET default_did_id=excluded.default_did_id,
          lease_ttl_sec=excluded.lease_ttl_sec,cooloff_sec=excluded.cooloff_sec,enabled=excluded.enabled,updated_at=now()
        RETURNING *
        """), {"a": account_id, "n": name, "d": int(default_did_id) if default_did_id is not None else None,
               "ttl": ttl, "cool": cooloff, "e": bool(payload.get("enabled", True))}).mappings().one()
        db.commit()
        return {"status": "ok", "pool": dict(row)}
    finally:
        db.close()


def pool_add_number(account_id: str, pool_id: int, did_id: int) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        pool = db.execute(text("SELECT * FROM calltracking_pools WHERE id=:p AND account_id=:a FOR UPDATE"),
                          {"p": int(pool_id), "a": account_id}).mappings().first()
        if not pool:
            return {"status": "pool_not_found"}
        did = db.execute(text("""
        SELECT id,number_e164,purpose,status FROM telephony_dids
        WHERE id=:d AND account_id=:a AND released_at IS NULL AND inbound_enabled=true
        """), {"d": int(did_id), "a": account_id}).mappings().first()
        if not did:
            return {"status": "did_not_found"}
        if did.get("status") != "active":
            return {"status": "did_not_active"}
        db.execute(text("""
        INSERT INTO calltracking_pool_numbers(pool_id,did_id,status)
        VALUES(:p,:d,'available') ON CONFLICT(pool_id,did_id) DO NOTHING
        """), {"p": int(pool_id), "d": int(did_id)})
        db.execute(text("UPDATE telephony_dids SET purpose='calltracking_dynamic',updated_at=now() WHERE id=:d"),
                   {"d": int(did_id)})
        db.commit()
        return {"status": "ok", "pool_id": int(pool_id), "did": dict(did)}
    finally:
        db.close()


def pool_list(account_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text("""
        SELECT p.*,
          count(n.*) AS numbers,
          count(n.*) FILTER(WHERE n.status='available' AND (n.cooldown_until IS NULL OR n.cooldown_until<=now())) AS available,
          count(n.*) FILTER(WHERE n.status='leased' AND n.leased_until>now()) AS leased,
          count(n.*) FILTER(WHERE n.status='cooldown' AND n.cooldown_until>now()) AS cooldown
        FROM calltracking_pools p
        LEFT JOIN calltracking_pool_numbers n ON n.pool_id=p.id
        WHERE p.account_id=:a GROUP BY p.id ORDER BY p.id
        """), {"a": account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.close()


def _touchpoint(db, session_id: str, account_id: str, payload: dict[str, Any]) -> None:
    channel = str(payload.get("utm_source") or payload.get("channel") or "").strip()[:160] or None
    campaign = str(payload.get("utm_campaign") or "").strip()[:240] or None
    db.execute(text("""
    INSERT INTO calltracking_touchpoints(account_id,session_id,channel,campaign,referrer,landing_url,metadata_json)
    VALUES(:a,:s,:ch,:c,:r,:l,CAST(:m AS jsonb))
    """), {"a": account_id, "s": session_id, "ch": channel, "c": campaign,
           "r": str(payload.get("referrer") or "")[:2000] or None,
           "l": str(payload.get("landing_url") or "")[:3000] or None,
           "m": _json({"utm_medium": payload.get("utm_medium"), "utm_content": payload.get("utm_content"),
                       "utm_term": payload.get("utm_term"), "yclid": payload.get("yclid"),
                       "gclid": payload.get("gclid"), "vk_click_id": payload.get("vk_click_id")})})


def session_allocate(public_key: str, payload: dict[str, Any], origin: str | None) -> dict[str, Any]:
    ensure_schema()
    site = _site_by_public_key(public_key, origin)
    if not site:
        return {"status": "site_not_found_or_origin_denied"}
    visitor_id = str(payload.get("visitor_id") or "").strip()[:160]
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", visitor_id):
        return {"status": "invalid_visitor_id"}
    browser_session_id = str(payload.get("session_id") or "").strip()[:160] or None
    db = SessionLocal()
    try:
        lock_material = f"{site['id']}|{visitor_id}"
        lock_id = int.from_bytes(hashlib.blake2b(lock_material.encode(), digest_size=8).digest(), "big", signed=False)
        if lock_id >= (1 << 63):
            lock_id -= (1 << 64)
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_id})
        existing = db.execute(text("""
        SELECT s.*,d.number_e164,p.cooloff_sec,p.lease_ttl_sec
        FROM calltracking_sessions s
        LEFT JOIN telephony_dids d ON d.id=s.assigned_did_id
        LEFT JOIN calltracking_pool_numbers pn ON pn.did_id=s.assigned_did_id AND pn.current_session_id=s.id
        LEFT JOIN calltracking_pools p ON p.id=pn.pool_id
        WHERE s.site_id=:site AND s.visitor_id=:v AND s.state='active'
          AND s.released_at IS NULL AND s.expires_at>now()
        ORDER BY s.last_seen_at DESC LIMIT 1 FOR UPDATE OF s
        """), {"site": site["id"], "v": visitor_id}).mappings().first()
        if existing:
            ttl = int(existing.get("lease_ttl_sec") or 1800)
            cooloff = int(existing.get("cooloff_sec") or 900)
            row = db.execute(text("""
            UPDATE calltracking_sessions SET last_seen_at=now(),
              expires_at=now()+make_interval(secs=>:ttl),
              attribution_until=now()+make_interval(secs=>(:ttl+:cool)),
              browser_session_id=COALESCE(:bs,browser_session_id),updated_at=now()
            WHERE id=:id RETURNING *
            """), {"ttl": ttl, "cool": cooloff, "bs": browser_session_id, "id": existing["id"]}).mappings().one()
            if row.get("assigned_did_id"):
                db.execute(text("""
                UPDATE calltracking_pool_numbers SET leased_until=now()+make_interval(secs=>:ttl),updated_at=now()
                WHERE did_id=:d AND current_session_id=:s
                """), {"ttl": ttl, "d": row["assigned_did_id"], "s": row["id"]})
            _touchpoint(db, row["id"], site["account_id"], payload)
            db.commit()
            number = existing.get("number_e164")
            return {"status": "ok", "reused": True, "session_id": row["id"],
                    "number": number, "allocation_mode": row["allocation_mode"],
                    "expires_at": row["expires_at"], "confidence": 0.98 if row["allocation_mode"] == "dynamic" else 0.25}
        pool_id = site.get("default_pool_id")
        if pool_id is None:
            pool_id = db.execute(text("""
            SELECT id FROM calltracking_pools WHERE account_id=:a AND enabled=true ORDER BY id LIMIT 1
            """), {"a": site["account_id"]}).scalar()
        if pool_id is None:
            return {"status": "pool_not_configured"}
        pool = db.execute(text("""
        SELECT * FROM calltracking_pools WHERE id=:p AND account_id=:a AND enabled=true FOR UPDATE
        """), {"p": int(pool_id), "a": site["account_id"]}).mappings().first()
        if not pool:
            return {"status": "pool_not_found"}
        sid = "cts_" + uuid.uuid4().hex
        candidate = db.execute(text("""
        SELECT pn.did_id,d.number_e164
        FROM calltracking_pool_numbers pn
        JOIN telephony_dids d ON d.id=pn.did_id
        WHERE pn.pool_id=:p
          AND d.account_id=:a AND d.status='active' AND d.inbound_enabled=true AND d.released_at IS NULL
          AND (
            (pn.status='available' AND (pn.cooldown_until IS NULL OR pn.cooldown_until<=now()))
            OR (pn.status='leased' AND pn.leased_until<=now())
            OR (pn.status='cooldown' AND pn.cooldown_until<=now())
          )
        ORDER BY COALESCE(pn.last_assigned_at,to_timestamp(0)),pn.did_id
        FOR UPDATE OF pn SKIP LOCKED LIMIT 1
        """), {"p": int(pool["id"]), "a": site["account_id"]}).mappings().first()
        ttl = int(pool.get("lease_ttl_sec") or 1800)
        cooloff = int(pool.get("cooloff_sec") or 900)
        did_id = candidate.get("did_id") if candidate else pool.get("default_did_id")
        allocation_mode = "dynamic" if candidate else "fallback"
        number = candidate.get("number_e164") if candidate else None
        if not candidate and did_id is not None:
            fallback = db.execute(text("""
            SELECT number_e164 FROM telephony_dids WHERE id=:d AND account_id=:a
              AND released_at IS NULL AND status='active' AND inbound_enabled=true
            """), {"d": int(did_id), "a": site["account_id"]}).mappings().first()
            number = fallback.get("number_e164") if fallback else None
        if not did_id or not number:
            return {"status": "pool_exhausted_no_fallback", "pool_id": int(pool["id"])}
        row = db.execute(text("""
        INSERT INTO calltracking_sessions(
          id,site_id,account_id,visitor_id,browser_session_id,landing_url,referrer,
          utm_source,utm_medium,utm_campaign,utm_content,utm_term,yclid,gclid,vk_click_id,
          assigned_did_id,allocation_mode,expires_at,attribution_until,metadata_json
        ) VALUES (
          :id,:site,:a,:v,:bs,:landing,:ref,:us,:um,:uc,:uco,:ut,:yclid,:gclid,:vk,
          :did,:mode,now()+make_interval(secs=>:ttl),now()+make_interval(secs=>(:ttl+:cool)),CAST(:m AS jsonb)
        ) RETURNING *
        """), {"id": sid, "site": site["id"], "a": site["account_id"], "v": visitor_id, "bs": browser_session_id,
               "landing": str(payload.get("landing_url") or "")[:3000] or None,
               "ref": str(payload.get("referrer") or "")[:2000] or None,
               "us": str(payload.get("utm_source") or "")[:160] or None,
               "um": str(payload.get("utm_medium") or "")[:160] or None,
               "uc": str(payload.get("utm_campaign") or "")[:240] or None,
               "uco": str(payload.get("utm_content") or "")[:240] or None,
               "ut": str(payload.get("utm_term") or "")[:240] or None,
               "yclid": str(payload.get("yclid") or "")[:500] or None,
               "gclid": str(payload.get("gclid") or "")[:500] or None,
               "vk": str(payload.get("vk_click_id") or "")[:500] or None,
               "did": int(did_id), "mode": allocation_mode, "ttl": ttl, "cool": cooloff,
               "m": _json(payload.get("metadata") or {})}).mappings().one()
        if candidate:
            db.execute(text("""
            UPDATE calltracking_pool_numbers SET status='leased',current_session_id=:s,
              leased_until=now()+make_interval(secs=>:ttl),cooldown_until=NULL,last_assigned_at=now(),updated_at=now()
            WHERE pool_id=:p AND did_id=:d
            """), {"s": sid, "ttl": ttl, "p": int(pool["id"]), "d": int(did_id)})
        _touchpoint(db, sid, site["account_id"], payload)
        db.commit()
        return {"status": "ok", "reused": False, "session_id": sid, "number": number,
                "allocation_mode": allocation_mode, "pool_exhausted": not bool(candidate),
                "expires_at": row["expires_at"], "confidence": 0.98 if candidate else 0.25}
    finally:
        db.close()


def session_heartbeat(public_key: str, session_id: str, visitor_id: str, origin: str | None) -> dict[str, Any]:
    ensure_schema()
    site = _site_by_public_key(public_key, origin)
    if not site:
        return {"status": "site_not_found_or_origin_denied"}
    db = SessionLocal()
    try:
        row = db.execute(text("""
        SELECT s.*,p.lease_ttl_sec,p.cooloff_sec
        FROM calltracking_sessions s
        LEFT JOIN calltracking_pool_numbers pn ON pn.did_id=s.assigned_did_id AND pn.current_session_id=s.id
        LEFT JOIN calltracking_pools p ON p.id=pn.pool_id
        WHERE s.id=:s AND s.site_id=:site AND s.visitor_id=:v AND s.state='active' AND s.released_at IS NULL
        FOR UPDATE OF s
        """), {"s": session_id, "site": site["id"], "v": visitor_id}).mappings().first()
        if not row:
            return {"status": "session_not_found"}
        ttl = int(row.get("lease_ttl_sec") or 1800)
        cooloff = int(row.get("cooloff_sec") or 900)
        updated = db.execute(text("""
        UPDATE calltracking_sessions SET last_seen_at=now(),expires_at=now()+make_interval(secs=>:ttl),
          attribution_until=now()+make_interval(secs=>(:ttl+:cool)),updated_at=now()
        WHERE id=:s RETURNING expires_at,attribution_until
        """), {"ttl": ttl, "cool": cooloff, "s": session_id}).mappings().one()
        if row.get("assigned_did_id") and row.get("allocation_mode") == "dynamic":
            db.execute(text("""
            UPDATE calltracking_pool_numbers SET leased_until=now()+make_interval(secs=>:ttl),updated_at=now()
            WHERE did_id=:d AND current_session_id=:s
            """), {"ttl": ttl, "d": row["assigned_did_id"], "s": session_id})
        db.commit()
        return {"status": "ok", **dict(updated)}
    finally:
        db.close()


def session_release(public_key: str, session_id: str, visitor_id: str, origin: str | None) -> dict[str, Any]:
    ensure_schema()
    site = _site_by_public_key(public_key, origin)
    if not site:
        return {"status": "site_not_found_or_origin_denied"}
    db = SessionLocal()
    try:
        row = db.execute(text("""
        SELECT s.*,p.id pool_id,p.cooloff_sec
        FROM calltracking_sessions s
        LEFT JOIN calltracking_pool_numbers pn ON pn.did_id=s.assigned_did_id AND pn.current_session_id=s.id
        LEFT JOIN calltracking_pools p ON p.id=pn.pool_id
        WHERE s.id=:s AND s.site_id=:site AND s.visitor_id=:v FOR UPDATE OF s
        """), {"s": session_id, "site": site["id"], "v": visitor_id}).mappings().first()
        if not row:
            return {"status": "session_not_found"}
        if row.get("released_at"):
            return {"status": "ok", "already_released": True, "attribution_until": row.get("attribution_until")}
        cooloff = int(row.get("cooloff_sec") or 900)
        updated = db.execute(text("""
        UPDATE calltracking_sessions SET state='released',released_at=now(),
          attribution_until=greatest(attribution_until,now()+make_interval(secs=>:cool)),updated_at=now()
        WHERE id=:s RETURNING attribution_until
        """), {"cool": cooloff, "s": session_id}).mappings().one()
        if row.get("allocation_mode") == "dynamic" and row.get("assigned_did_id") and row.get("pool_id"):
            db.execute(text("""
            UPDATE calltracking_pool_numbers SET status='cooldown',current_session_id=NULL,leased_until=NULL,
              cooldown_until=now()+make_interval(secs=>:cool),updated_at=now()
            WHERE pool_id=:p AND did_id=:d AND current_session_id=:s
            """), {"cool": cooloff, "p": row["pool_id"], "d": row["assigned_did_id"], "s": session_id})
        db.commit()
        return {"status": "ok", "already_released": False, **dict(updated)}
    finally:
        db.close()


def guardian(limit: int = 1000) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    released = available = 0
    try:
        rows = db.execute(text("""
        SELECT s.id,s.assigned_did_id,s.allocation_mode,pn.pool_id,p.cooloff_sec
        FROM calltracking_sessions s
        LEFT JOIN calltracking_pool_numbers pn ON pn.current_session_id=s.id
        LEFT JOIN calltracking_pools p ON p.id=pn.pool_id
        WHERE s.state='active' AND s.released_at IS NULL AND s.expires_at<=now()
        ORDER BY s.expires_at LIMIT :l FOR UPDATE OF s SKIP LOCKED
        """), {"l": max(1, min(int(limit), 5000))}).mappings().all()
        for row in rows:
            cooloff = int(row.get("cooloff_sec") or 900)
            db.execute(text("""
            UPDATE calltracking_sessions SET state='expired',released_at=now(),
              attribution_until=greatest(attribution_until,now()+make_interval(secs=>:cool)),updated_at=now()
            WHERE id=:s
            """), {"cool": cooloff, "s": row["id"]})
            if row.get("allocation_mode") == "dynamic" and row.get("pool_id") and row.get("assigned_did_id"):
                db.execute(text("""
                UPDATE calltracking_pool_numbers SET status='cooldown',current_session_id=NULL,leased_until=NULL,
                  cooldown_until=now()+make_interval(secs=>:cool),updated_at=now()
                WHERE pool_id=:p AND did_id=:d AND current_session_id=:s
                """), {"cool": cooloff, "p": row["pool_id"], "d": row["assigned_did_id"], "s": row["id"]})
            released += 1
        returned = db.execute(text("""
        UPDATE calltracking_pool_numbers SET status='available',current_session_id=NULL,leased_until=NULL,cooldown_until=NULL,updated_at=now()
        WHERE status='cooldown' AND cooldown_until<=now()
        RETURNING did_id
        """)).fetchall()
        available = len(returned)
        db.commit()
        return {"status": "ok", "expired_sessions": released, "numbers_returned": available}
    finally:
        db.close()


def _touch_views(db, session_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = db.execute(text("""
    SELECT channel,campaign,referrer,landing_url,metadata_json,occurred_at
    FROM calltracking_touchpoints WHERE session_id=:s ORDER BY occurred_at,id
    """), {"s": session_id}).mappings().all()
    if not rows:
        return {}, {}
    return dict(rows[0]), dict(rows[-1])


def resolve_inbound(account_id: str, to_number: str, at: datetime | None = None, db=None) -> dict[str, Any]:
    ensure_schema()
    number = normalize_e164(to_number)
    if not number:
        return {"status": "unattributed", "mode": "none", "confidence": 0.0}
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        did = db.execute(text("""
        SELECT * FROM telephony_dids
        WHERE provider='mcn' AND number_e164=:n AND released_at IS NULL
        ORDER BY id DESC LIMIT 1
        """), {"n": number}).mappings().first()
        if not did or did.get("account_id") != account_id:
            return {"status": "unattributed", "mode": "none", "confidence": 0.0}
        did = dict(did)
        at = at or utcnow()
        session = db.execute(text("""
        SELECT s.* FROM calltracking_sessions s
        WHERE s.account_id=:a AND s.assigned_did_id=:did
          AND s.allocation_mode='dynamic'
          AND s.first_seen_at<=:at AND s.attribution_until>=:at
        ORDER BY s.last_seen_at DESC LIMIT 2
        """), {"a": account_id, "did": did["id"], "at": at}).mappings().all()
        if session:
            chosen = dict(session[0])
            first, last = _touch_views(db, chosen["id"])
            src = str(chosen.get("utm_source") or last.get("channel") or "").strip()
            if not src:
                ref = _domain(chosen.get("referrer"))
                src = ref or "direct"
            ref_parts = [str(chosen.get("utm_campaign") or "").strip(), str(chosen.get("browser_session_id") or "").strip()]
            source_ref = ":".join([x for x in ref_parts if x])[:300] or chosen["id"]
            confidence = 0.98 if chosen.get("allocation_mode") == "dynamic" else 0.25
            if len(session) > 1 and session[1].get("allocation_mode") == "dynamic":
                confidence = min(confidence, 0.75)
            return {"status": "attributed", "mode": chosen.get("allocation_mode") or "dynamic",
                    "confidence": confidence, "did_id": did["id"], "session_id": chosen["id"],
                    "source": src[:120], "source_ref": source_ref, "first_touch": first, "last_touch": last,
                    "evidence": {"did": did.get("number_e164"), "session_window": True, "candidate_sessions": len(session)}}
        static = db.execute(text("""
        SELECT * FROM calltracking_static_bindings
        WHERE account_id=:a AND did_id=:did AND enabled=true
          AND active_from<=:at AND (active_to IS NULL OR active_to>=:at)
        ORDER BY active_from DESC,id DESC LIMIT 1
        """), {"a": account_id, "did": did["id"], "at": at}).mappings().first()
        if static:
            s = dict(static)
            return {"status": "attributed", "mode": "static", "confidence": 1.0,
                    "did_id": did["id"], "static_binding_id": s["id"], "session_id": None,
                    "source": s["source"], "source_ref": s.get("source_ref"),
                    "first_touch": {}, "last_touch": {},
                    "evidence": {"did": did.get("number_e164"), "binding_id": s["id"],
                                 "campaign_id": s.get("campaign_id"), "ad_id": s.get("ad_id")}}
        return {"status": "unattributed", "mode": "none", "confidence": 0.0,
                "did_id": did["id"], "evidence": {"did": did.get("number_e164")}}
    finally:
        if own_db:
            db.close()


def attach_call(account_id: str, call_id: str) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        call = db.execute(text("""
        SELECT id,direction,to_number,started_at,source,source_ref,metadata_json
        FROM telephony_calls WHERE account_id=:a AND id=:c FOR UPDATE
        """), {"a": account_id, "c": call_id}).mappings().first()
        if not call:
            return {"status": "call_not_found"}
        if call.get("direction") != "inbound":
            return {"status": "not_applicable"}
        existing = db.execute(text("SELECT * FROM calltracking_attributions WHERE account_id=:a AND call_id=:c"),
                              {"a": account_id, "c": call_id}).mappings().first()
        if existing:
            return {"status": "existing", "attribution": dict(existing)}
        resolution = resolve_inbound(account_id, call.get("to_number"), call.get("started_at"), db=db)
        if resolution.get("status") != "attributed":
            return resolution
        source = str(resolution.get("source") or "")[:120] or None
        source_ref = str(resolution.get("source_ref") or "")[:300] or None
        row = db.execute(text("""
        INSERT INTO calltracking_attributions(account_id,call_id,did_id,session_id,static_binding_id,attribution_mode,
          confidence,source,source_ref,first_touch_json,last_touch_json,evidence_json)
        VALUES(:a,:c,:did,:s,:sb,:mode,:conf,:src,:sr,CAST(:ft AS jsonb),CAST(:lt AS jsonb),CAST(:ev AS jsonb))
        ON CONFLICT(account_id,call_id) DO NOTHING RETURNING *
        """), {"a": account_id, "c": call_id, "did": resolution.get("did_id"),
               "s": resolution.get("session_id"), "sb": resolution.get("static_binding_id"),
               "mode": resolution.get("mode"), "conf": str(resolution.get("confidence") or 0),
               "src": source, "sr": source_ref, "ft": _json(resolution.get("first_touch") or {}),
               "lt": _json(resolution.get("last_touch") or {}), "ev": _json(resolution.get("evidence") or {})}).mappings().first()
        if not row:
            row = db.execute(text("SELECT * FROM calltracking_attributions WHERE account_id=:a AND call_id=:c"),
                             {"a": account_id, "c": call_id}).mappings().one()
        # Only enrich a call when there is no stronger source already present.
        existing_source = str(call.get("source") or "").strip()
        safe_to_replace = not existing_source or existing_source in {"telephony", "mcn", "direct", "unknown"}
        meta = dict(call.get("metadata_json") or {})
        meta["calltracking"] = {
            "mode": resolution.get("mode"), "confidence": resolution.get("confidence"),
            "attribution_id": row["id"], "session_id": resolution.get("session_id"),
            "did_id": resolution.get("did_id")
        }
        db.execute(text("""
        UPDATE telephony_calls SET source=CASE WHEN :replace THEN COALESCE(:src,source) ELSE source END,
          source_ref=CASE WHEN :replace THEN COALESCE(:sr,source_ref) ELSE source_ref END,
          metadata_json=CAST(:m AS jsonb),updated_at=now()
        WHERE account_id=:a AND id=:c
        """), {"replace": safe_to_replace, "src": source, "sr": source_ref,
               "m": _json(meta), "a": account_id, "c": call_id})
        db.commit()
        return {"status": "ok", "attribution": dict(row), "call_source_updated": safe_to_replace}
    finally:
        db.close()


def account_status(account_id: str) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        row = dict(db.execute(text("""
        SELECT
          (SELECT count(*) FROM calltracking_sites WHERE account_id=:a AND enabled=true) sites,
          (SELECT count(*) FROM calltracking_pools WHERE account_id=:a AND enabled=true) pools,
          (SELECT count(*) FROM calltracking_pool_numbers n JOIN calltracking_pools p ON p.id=n.pool_id WHERE p.account_id=:a) pool_numbers,
          (SELECT count(*) FROM calltracking_sessions WHERE account_id=:a AND state='active' AND expires_at>now()) active_sessions,
          (SELECT count(*) FROM calltracking_static_bindings WHERE account_id=:a AND enabled=true AND active_from<=now() AND (active_to IS NULL OR active_to>=now())) static_bindings,
          (SELECT count(*) FROM calltracking_attributions WHERE account_id=:a AND created_at>=now()-interval '30 days') attributed_calls,
          (SELECT count(*) FROM calltracking_attributions WHERE account_id=:a AND created_at>=now()-interval '30 days' AND attribution_mode='dynamic') dynamic_calls
        """), {"a": account_id}).mappings().one())
        pool_health = [dict(x) for x in db.execute(text("""
        SELECT p.id,p.name,count(n.*) total,
          count(n.*) FILTER(WHERE n.status='available' AND (n.cooldown_until IS NULL OR n.cooldown_until<=now())) available,
          count(n.*) FILTER(WHERE n.status='leased' AND n.leased_until>now()) leased,
          count(n.*) FILTER(WHERE n.status='cooldown' AND n.cooldown_until>now()) cooldown
        FROM calltracking_pools p LEFT JOIN calltracking_pool_numbers n ON n.pool_id=p.id
        WHERE p.account_id=:a AND p.enabled=true GROUP BY p.id,p.name ORDER BY p.id
        """), {"a": account_id}).mappings().all()]
    finally:
        db.close()
    exhausted = [x for x in pool_health if int(x.get("total") or 0) > 0 and int(x.get("available") or 0) == 0]
    return {"status": "ok", **row, "pool_health": pool_health,
            "health": "degraded" if exhausted else "pass",
            "problems": [{"code": "CALLTRACKING_POOL_EXHAUSTED", "pool_id": x["id"], "pool": x["name"]} for x in exhausted]}


def script_source(public_key: str) -> str:
    # The public key is intentionally public. Tenant/domain checks happen at the session endpoint.
    key = str(public_key or "").replace("\\", "").replace("'", "")[:120]
    return f"""(()=>{{const K='{key}',S=document.currentScript,O=S?new URL(S.src,location.href).origin:'',B=O+'/api/calltracking/v1';if(!K||!O)return;
const vid=()=>{{let v=localStorage.getItem('boris_ct_vid');if(!v){{v='v_'+crypto.randomUUID().replaceAll('-','');localStorage.setItem('boris_ct_vid',v)}}return v}};
const sid=()=>{{let v=sessionStorage.getItem('boris_ct_sid');if(!v){{v='s_'+crypto.randomUUID().replaceAll('-','');sessionStorage.setItem('boris_ct_sid',v)}}return v}};
const qs=new URLSearchParams(location.search),body=()=>({{visitor_id:vid(),session_id:sid(),landing_url:location.href,referrer:document.referrer||'',utm_source:qs.get('utm_source'),utm_medium:qs.get('utm_medium'),utm_campaign:qs.get('utm_campaign'),utm_content:qs.get('utm_content'),utm_term:qs.get('utm_term'),yclid:qs.get('yclid'),gclid:qs.get('gclid'),vk_click_id:qs.get('vk_click_id')}});
let CT=null;
const replace=n=>{{if(!n)return;document.querySelectorAll('[data-boris-phone],a[href^="tel:"]').forEach(el=>{{if(el.dataset.borisOriginal===undefined)el.dataset.borisOriginal=el.textContent||'';if(el.matches('a[href^="tel:"]'))el.setAttribute('href','tel:'+n.replace(/[^+0-9]/g,''));if(el.hasAttribute('data-boris-phone'))el.textContent=n}})}};
fetch(B+'/session?site_key='+encodeURIComponent(K),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body()),credentials:'omit'}}).then(r=>r.json()).then(x=>{{if(x.status==='ok'){{CT=x;replace(x.number);new MutationObserver(()=>replace(x.number)).observe(document.documentElement,{{childList:true,subtree:true}})}}}}).catch(()=>{{}});
setInterval(()=>{{if(!CT)return;fetch(B+'/heartbeat?site_key='+encodeURIComponent(K),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{visitor_id:vid(),session_id:CT.session_id}}),credentials:'omit'}}).catch(()=>{{}})}},60000);
addEventListener('pagehide',()=>{{if(!CT)return;navigator.sendBeacon(B+'/release?site_key='+encodeURIComponent(K),new Blob([JSON.stringify({{visitor_id:vid(),session_id:CT.session_id}})],{{type:'application/json'}}))}});
}})();"""


__all__ = [
    "ensure_schema", "site_upsert", "site_list", "static_binding_upsert", "static_binding_list",
    "pool_upsert", "pool_add_number", "pool_list", "session_allocate", "session_heartbeat",
    "session_release", "guardian", "resolve_inbound", "attach_call", "account_status", "script_source"
]
