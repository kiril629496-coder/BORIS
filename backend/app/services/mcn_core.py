from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_e164(value: str | None) -> str:
    raw = re.sub(r"\D+", "", str(value or ""))
    if len(raw) == 11 and raw.startswith("8"):
        raw = "7" + raw[1:]
    if len(raw) == 10:
        raw = "7" + raw
    if not raw:
        return ""
    if len(raw) < 7 or len(raw) > 15:
        return ""
    return "+" + raw



def normalize_sip_target(value: str | None) -> str:
    """Normalize a SIP registrar/proxy host[:port] without allowing config injection."""
    raw = str(value or "").strip()
    if not raw or any(ord(ch) < 32 for ch in raw):
        return ""
    raw = re.sub(r"^sips?:", "", raw, flags=re.I).lstrip("/")
    raw = raw.split("/", 1)[0].strip()
    if not raw or len(raw) > 255 or "@" in raw or ";" in raw:
        return ""
    if raw.startswith("["):
        m = re.fullmatch(r"\[([0-9A-Fa-f:]+)\](?::(\d{1,5}))?", raw)
        if not m:
            return ""
        try:
            ipaddress.IPv6Address(m.group(1))
        except ValueError:
            return ""
        if m.group(2) and not (1 <= int(m.group(2)) <= 65535):
            return ""
        return raw.lower()
    try:
        ipaddress.ip_address(raw)
        return raw.lower()
    except ValueError:
        pass
    host, port = raw, None
    if raw.count(":") == 1:
        left, right = raw.rsplit(":", 1)
        if right.isdigit():
            host, port = left, int(right)
    if port is not None and not (1 <= port <= 65535):
        return ""
    host = host.rstrip(".").lower()
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
        return ""
    labels = host.split(".")
    if any(not x or len(x) > 63 or x.startswith("-") or x.endswith("-") for x in labels):
        return ""
    return host + (f":{port}" if port is not None else "")


def normalize_source_ip(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw or any(ord(ch) < 32 for ch in raw):
        return ""
    try:
        if "/" in raw:
            return str(ipaddress.ip_network(raw, strict=False))
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        db = SessionLocal()
        try:
            db.execute(text("SELECT pg_advisory_xact_lock(721946322)"))
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
            db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
            db.commit()
            _SCHEMA_READY = True
        finally:
            db.close()


def _encrypt(value: str | None) -> str | None:
    v = str(value or "")
    if not v:
        return None
    if not os.getenv("FERNET_KEY"):
        raise RuntimeError("encryption_unavailable")
    from app.crypto_utils import encrypt_secret
    return encrypt_secret(v)


def _decrypt(value: str | None) -> str:
    if not value:
        return ""
    from app.crypto_utils import decrypt_secret
    try:
        return decrypt_secret(str(value))
    except Exception:
        return ""


def trunk_upsert(account_id: str, payload: dict[str, Any], *, _db=None, _commit: bool = True) -> dict[str, Any]:
    ensure_schema()
    account_id = str(account_id or "").strip()
    if not account_id:
        return {"status": "invalid_account"}
    provider = str(payload.get("provider") or "mcn").strip().lower()
    if provider != "mcn":
        return {"status": "invalid_provider", "allowed": ["mcn"]}
    name = str(payload.get("name") or "MCN primary").strip()[:120]
    auth_mode_present = "auth_mode" in payload
    auth_mode = str(payload.get("auth_mode") or "registration").strip().lower()
    if auth_mode not in {"registration", "ip"}:
        return {"status": "invalid_auth_mode"}
    # MCN's standard SIP registrar is the create-time default. On an update,
    # omission means "preserve the already issued registrar"; an explicit empty
    # value is allowed to clear it and correctly returns the trunk to waiting.
    registrar_present = "registrar" in payload
    registrar_value = payload.get("registrar") if registrar_present else "sip.mcn.ru"
    registrar_raw = str(registrar_value or "").strip()[:512]
    registrar = normalize_sip_target(registrar_raw) if registrar_raw else None
    if registrar_raw and not registrar:
        return {"status": "invalid_registrar"}
    proxy_present = "outbound_proxy" in payload
    proxy_raw = str(payload.get("outbound_proxy") or "").strip()[:512]
    proxy = normalize_sip_target(proxy_raw) if proxy_raw else None
    if proxy_raw and not proxy:
        return {"status": "invalid_outbound_proxy"}
    username = payload.get("username")
    password = payload.get("password")
    if username is not None:
        username = str(username).strip()
        if (len(username) > 512 or any(ord(ch) < 32 for ch in username)
                or re.search(r"[\s;@]", username)):
            return {"status": "invalid_sip_username"}
    if password is not None:
        password = str(password)
        if len(password) > 1024 or any(ord(ch) < 32 for ch in password):
            return {"status": "invalid_sip_password"}
    source_ips_present = "source_ips" in payload
    source_raw = [str(x).strip() for x in (payload.get("source_ips") or []) if str(x).strip()][:64]
    source_ips = [normalize_source_ip(x) for x in source_raw]
    if any(not x for x in source_ips):
        return {"status": "invalid_source_ip"}
    codecs_present = "codecs" in payload
    codecs = [str(x).strip().lower() for x in (payload.get("codecs") or ["alaw"]) if str(x).strip()][:16]
    allowed_cli_present = "allowed_cli" in payload
    allowed_cli = [normalize_e164(x) for x in (payload.get("allowed_cli") or [])]
    allowed_cli = [x for x in allowed_cli if x][:256]
    dtmf_present = "dtmf_mode" in payload
    max_channels_present = "max_channels" in payload
    max_cps_present = "max_cps" in payload
    priority_present = "priority" in payload
    enabled_present = "enabled" in payload
    metadata_present = "metadata" in payload
    try:
        username_enc = _encrypt(username) if username is not None else None
        password_enc = _encrypt(password) if password is not None else None
    except RuntimeError:
        return {"status": "encryption_unavailable"}
    _owned_db = _db is None
    db = _db or SessionLocal()
    try:
        existing = db.execute(text("""
        SELECT (username_enc IS NOT NULL AND username_enc<>'') AS has_username,
               (password_enc IS NOT NULL AND password_enc<>'') AS has_password,
               auth_mode,registrar,outbound_proxy,source_ips,codecs,dtmf_mode,
               max_channels,max_cps,allowed_cli,priority,enabled,metadata_json
        FROM telephony_trunks WHERE account_id=:a AND provider=:p AND name=:n
        """), {"a": account_id, "p": provider, "n": name}).mappings().first()
        if existing:
            if not auth_mode_present:
                auth_mode = str(existing.get("auth_mode") or auth_mode)
            if not registrar_present:
                registrar = str(existing.get("registrar") or "").strip() or None
            if not proxy_present:
                proxy = str(existing.get("outbound_proxy") or "").strip() or None
            if not source_ips_present:
                source_ips = [str(x) for x in (existing.get("source_ips") or []) if str(x).strip()]
            if not codecs_present:
                codecs = [str(x) for x in (existing.get("codecs") or []) if str(x).strip()]
            if not allowed_cli_present:
                allowed_cli = [str(x) for x in (existing.get("allowed_cli") or []) if str(x).strip()]

        # BORIS media path for MCN is deliberately conservative: MCN documents
        # G.711a and G.729, while this Asterisk has no G.729 transcoder. Keep
        # every MCN trunk on PCMA/alaw and RFC4733 so WebPhone/AI media remains
        # transcodable and DTMF does not depend on in-band tones.
        codecs = ["alaw"]
        dtmf_mode = "rfc4733"
        max_channels = (
            int(payload["max_channels"]) if max_channels_present and payload.get("max_channels") is not None
            else (None if max_channels_present else (existing.get("max_channels") if existing else None))
        )
        max_cps = (
            str(payload["max_cps"]) if max_cps_present and payload.get("max_cps") is not None
            else (None if max_cps_present else (existing.get("max_cps") if existing else None))
        )
        priority = (
            max(1, min(int(payload.get("priority") or 100), 10000))
            if priority_present or not existing else int(existing.get("priority") or 100)
        )
        enabled = bool(payload.get("enabled", True)) if enabled_present or not existing else bool(existing.get("enabled"))
        metadata = (
            payload.get("metadata") or {}
            if metadata_present or not existing else dict(existing.get("metadata_json") or {})
        )

        effective_username = bool(username_enc) or bool(existing and existing.get("has_username"))
        effective_password = bool(password_enc) or bool(existing and existing.get("has_password"))
        status = "configured_unverified" if (
            (auth_mode == "ip" and bool(source_ips) and bool(registrar)) or
            (auth_mode == "registration" and bool(registrar) and effective_username and effective_password)
        ) else "waiting_mcn"
        row = db.execute(text("""
        INSERT INTO telephony_trunks(
            account_id,provider,name,auth_mode,registrar,outbound_proxy,username_enc,password_enc,
            source_ips,codecs,dtmf_mode,max_channels,max_cps,allowed_cli,priority,enabled,status,metadata_json
        ) VALUES (
            :a,:p,:n,:am,:r,:op,:u,:pw,CAST(:ips AS jsonb),CAST(:codecs AS jsonb),:dtmf,:mc,:cps,
            CAST(:cli AS jsonb),:pr,:en,:st,CAST(:meta AS jsonb)
        )
        ON CONFLICT(account_id,provider,name) DO UPDATE SET
            auth_mode=excluded.auth_mode,
            registrar=excluded.registrar,
            outbound_proxy=excluded.outbound_proxy,
            username_enc=COALESCE(excluded.username_enc,telephony_trunks.username_enc),
            password_enc=COALESCE(excluded.password_enc,telephony_trunks.password_enc),
            source_ips=excluded.source_ips,
            codecs=excluded.codecs,
            dtmf_mode=excluded.dtmf_mode,
            max_channels=excluded.max_channels,
            max_cps=excluded.max_cps,
            allowed_cli=excluded.allowed_cli,
            priority=excluded.priority,
            enabled=excluded.enabled,
            status=CASE
              WHEN excluded.status='waiting_mcn' AND telephony_trunks.status='connected' THEN 'configured_unverified'
              ELSE excluded.status
            END,
            metadata_json=excluded.metadata_json,
            updated_at=now()
        RETURNING id,account_id,provider,name,auth_mode,registrar,outbound_proxy,source_ips,codecs,dtmf_mode,
                  max_channels,max_cps,allowed_cli,priority,enabled,status,last_health_at,last_health_status,last_error,
                  (username_enc IS NOT NULL AND username_enc<>'') AS has_username,
                  (password_enc IS NOT NULL AND password_enc<>'') AS has_password,
                  created_at,updated_at
        """), {
            "a": account_id, "p": provider, "n": name, "am": auth_mode, "r": registrar, "op": proxy,
            "u": username_enc, "pw": password_enc, "ips": json.dumps(source_ips), "codecs": json.dumps(codecs),
            "dtmf": dtmf_mode,
            "mc": max_channels,
            "cps": max_cps,
            "cli": json.dumps(allowed_cli), "pr": priority,
            "en": enabled, "st": status,
            "meta": json.dumps(metadata, ensure_ascii=False)
        }).mappings().one()
        if _commit:
            db.commit()
        return {"status": "ok", "trunk": dict(row)}
    finally:
        if _owned_db:
            db.close()


def trunk_list(account_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text("""
        SELECT id,account_id,provider,name,auth_mode,registrar,outbound_proxy,source_ips,codecs,dtmf_mode,
               max_channels,max_cps,allowed_cli,priority,enabled,status,last_health_at,last_health_status,last_error,
               (username_enc IS NOT NULL AND username_enc<>'') AS has_username,
               (password_enc IS NOT NULL AND password_enc<>'') AS has_password,
               created_at,updated_at
        FROM telephony_trunks WHERE account_id=:a ORDER BY priority,id
        """), {"a": account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.close()


def trunk_credentials(account_id: str, trunk_id: int | None = None) -> dict[str, Any] | None:
    ensure_schema()
    db = SessionLocal()
    try:
        if trunk_id is not None:
            row = db.execute(text("SELECT * FROM telephony_trunks WHERE account_id=:a AND id=:id AND enabled=true"),
                             {"a": account_id, "id": int(trunk_id)}).mappings().first()
        else:
            row = db.execute(text("""
            SELECT * FROM telephony_trunks
            WHERE account_id=:a AND enabled=true
            ORDER BY CASE WHEN status='connected' THEN 0 ELSE 1 END,priority,id LIMIT 1
            """), {"a": account_id}).mappings().first()
        if not row:
            return None
        out = dict(row)
        out["username"] = _decrypt(out.pop("username_enc", None))
        out["password"] = _decrypt(out.pop("password_enc", None))
        return out
    finally:
        db.close()


def trunk_mark_health(account_id: str, trunk_id: int, ok: bool, status: str, error: str | None = None) -> dict[str, Any]:
    ensure_schema()
    safe_status = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(status or ""))[:100] or "unknown"
    safe_error = str(error or "")[:500] or None
    db = SessionLocal()
    try:
        row = db.execute(text("""
        UPDATE telephony_trunks SET status=:st,last_health_at=now(),last_health_status=:hs,last_error=:e,updated_at=now()
        WHERE account_id=:a AND id=:id
        RETURNING id,status,last_health_at,last_health_status,last_error
        """), {"a": account_id, "id": int(trunk_id), "st": "connected" if ok else "configured_unverified",
                "hs": safe_status, "e": safe_error}).mappings().first()
        db.commit()
        return {"status": "ok" if row else "not_found", "trunk": dict(row) if row else None}
    finally:
        db.close()


def did_upsert(account_id: str | None, payload: dict[str, Any], *, _db=None, _commit: bool = True) -> dict[str, Any]:
    ensure_schema()
    number = normalize_e164(payload.get("number") or payload.get("number_e164"))
    if not number:
        return {"status": "invalid_number"}
    provider = str(payload.get("provider") or "mcn").strip().lower()
    if provider != "mcn":
        return {"status": "invalid_provider", "allowed": ["mcn"]}
    purpose = str(payload.get("purpose") or "main").strip().lower()
    if purpose not in {"main", "calltracking_static", "calltracking_dynamic", "campaign", "reserve", "tollfree"}:
        return {"status": "invalid_purpose"}
    trunk_id = payload.get("trunk_id")
    _owned_db = _db is None
    db = _db or SessionLocal()
    try:
        if trunk_id is not None:
            trunk = db.execute(text("SELECT id FROM telephony_trunks WHERE id=:id AND account_id=:a"),
                               {"id": int(trunk_id), "a": account_id}).first()
            if not trunk:
                return {"status": "invalid_trunk"}
        existing = db.execute(text("""
        SELECT id,account_id,trunk_id,region,number_type,purpose,
               inbound_enabled,outbound_cli_enabled,monthly_cost,connection_cost,
               status,metadata_json
        FROM telephony_dids
        WHERE provider=:p AND number_e164=:n AND released_at IS NULL FOR UPDATE
        """), {"p": provider, "n": number}).mappings().first()
        if existing and existing.get("account_id") not in {None, account_id}:
            return {"status": "number_already_assigned", "number": number}
        region_present = "region" in payload
        number_type_present = "number_type" in payload
        trunk_id_present = "trunk_id" in payload
        purpose_present = "purpose" in payload
        inbound_present = "inbound_enabled" in payload
        outbound_cli_present = "outbound_cli_enabled" in payload
        monthly_cost_present = "monthly_cost" in payload
        connection_cost_present = "connection_cost" in payload
        status_present = "status" in payload
        metadata_present = "metadata" in payload

        effective_account_id = account_id if account_id is not None else (
            existing.get("account_id") if existing else None
        )
        effective_trunk_id = (
            int(trunk_id) if trunk_id_present and trunk_id is not None
            else (None if trunk_id_present else (existing.get("trunk_id") if existing else None))
        )
        effective_region = (
            str(payload.get("region") or "")[:120] or None
            if region_present or not existing else existing.get("region")
        )
        effective_number_type = (
            str(payload.get("number_type") or "")[:80] or None
            if number_type_present or not existing else existing.get("number_type")
        )
        effective_purpose = (
            purpose if purpose_present or not existing else str(existing.get("purpose") or "main")
        )
        effective_inbound = (
            bool(payload.get("inbound_enabled", True))
            if inbound_present or not existing else bool(existing.get("inbound_enabled"))
        )
        effective_outbound_cli = (
            bool(payload.get("outbound_cli_enabled", False))
            if outbound_cli_present or not existing else bool(existing.get("outbound_cli_enabled"))
        )
        effective_monthly_cost = (
            str(payload["monthly_cost"]) if monthly_cost_present and payload.get("monthly_cost") is not None
            else (None if monthly_cost_present else (existing.get("monthly_cost") if existing else None))
        )
        effective_connection_cost = (
            str(payload["connection_cost"]) if connection_cost_present and payload.get("connection_cost") is not None
            else (None if connection_cost_present else (existing.get("connection_cost") if existing else None))
        )
        effective_status = (
            str(payload.get("status") or "active")[:40]
            if status_present or not existing else str(existing.get("status") or "active")[:40]
        )
        effective_metadata = (
            payload.get("metadata") or {}
            if metadata_present or not existing else dict(existing.get("metadata_json") or {})
        )
        params = {
            "p": provider, "n": number, "region": effective_region,
            "nt": effective_number_type, "a": effective_account_id,
            "tr": effective_trunk_id, "purpose": effective_purpose,
            "inb": effective_inbound,
            "outb": effective_outbound_cli,
            "mc": effective_monthly_cost,
            "cc": effective_connection_cost,
            "status": effective_status,
            "meta": json.dumps(effective_metadata, ensure_ascii=False)
        }
        if existing:
            row = db.execute(text("""
            UPDATE telephony_dids SET region=:region,number_type=:nt,account_id=:a,trunk_id=:tr,purpose=:purpose,
                inbound_enabled=:inb,outbound_cli_enabled=:outb,monthly_cost=:mc,connection_cost=:cc,status=:status,
                activated_at=COALESCE(activated_at,CASE WHEN :status='active' THEN now() ELSE NULL END),
                metadata_json=CAST(:meta AS jsonb),updated_at=now()
            WHERE id=:id RETURNING *
            """), {**params, "id": existing["id"]}).mappings().one()
        else:
            row = db.execute(text("""
            INSERT INTO telephony_dids(provider,number_e164,region,number_type,account_id,trunk_id,purpose,
                inbound_enabled,outbound_cli_enabled,monthly_cost,connection_cost,status,activated_at,metadata_json)
            VALUES(:p,:n,:region,:nt,:a,:tr,:purpose,:inb,:outb,:mc,:cc,:status,
                CASE WHEN :status='active' THEN now() ELSE NULL END,CAST(:meta AS jsonb))
            RETURNING *
            """), params).mappings().one()
        if _commit:
            db.commit()
        return {"status": "ok", "did": dict(row)}
    finally:
        if _owned_db:
            db.close()


def onboarding_upsert(account_id: str, trunk_payload: dict[str, Any],
                      did_payload: dict[str, Any]) -> dict[str, Any]:
    """Atomically persist the MCN trunk and DID for one owner onboarding action."""
    ensure_schema()
    account_id = str(account_id or "").strip()
    if not account_id:
        return {"status": "invalid_account", "step": "preflight"}

    db = SessionLocal()
    try:
        trunk_body = dict(trunk_payload or {})
        trunk_body["provider"] = "mcn"
        trunk_result = trunk_upsert(account_id, trunk_body, _db=db, _commit=False)
        if trunk_result.get("status") != "ok":
            db.rollback()
            return {"status": "onboarding_failed", "step": "trunk", "detail": trunk_result, "atomic": True}

        did_body = dict(did_payload or {})
        did_body["provider"] = "mcn"
        did_body["trunk_id"] = int(trunk_result["trunk"]["id"])
        did_result = did_upsert(account_id, did_body, _db=db, _commit=False)
        if did_result.get("status") != "ok":
            db.rollback()
            return {"status": "onboarding_failed", "step": "did", "detail": did_result, "atomic": True}

        db.commit()
        return {
            "status": "ok",
            "trunk": trunk_result["trunk"],
            "did": did_result["did"],
            "atomic": True,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def did_list(account_id: str | None = None, include_inventory: bool = False) -> list[dict[str, Any]]:
    ensure_schema()
    db = SessionLocal()
    try:
        if include_inventory and account_id is None:
            rows = db.execute(text("SELECT * FROM telephony_dids WHERE released_at IS NULL ORDER BY number_e164")).mappings().all()
        else:
            rows = db.execute(text("""
            SELECT * FROM telephony_dids WHERE account_id=:a AND released_at IS NULL ORDER BY purpose,number_e164
            """), {"a": account_id}).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.close()


def did_resolve(number: str, *, active_only: bool = True) -> dict[str, Any] | None:
    ensure_schema()
    n = normalize_e164(number)
    if not n:
        return None
    db = SessionLocal()
    try:
        where = "provider='mcn' AND number_e164=:n AND released_at IS NULL"
        if active_only:
            where += " AND inbound_enabled=true AND status='active'"
        row = db.execute(text(f"SELECT * FROM telephony_dids WHERE {where} ORDER BY id DESC LIMIT 1"), {"n": n}).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()


def carrier_link_upsert(account_id: str, call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    carrier_call_id = str(payload.get("carrier_call_id") or "").strip()[:200] or None
    uniqueid = str(payload.get("asterisk_uniqueid") or "").strip()[:200] or None
    linkedid = str(payload.get("asterisk_linkedid") or "").strip()[:200] or None
    db = SessionLocal()
    try:
        if carrier_call_id:
            conflict = db.execute(text("""
            SELECT account_id,call_id FROM telephony_call_carrier_links
            WHERE provider='mcn' AND carrier_call_id=:cid AND NOT (account_id=:a AND call_id=:c)
            """), {"cid": carrier_call_id, "a": account_id, "c": call_id}).mappings().first()
            if conflict:
                return {"status": "carrier_call_identity_conflict"}
        row = db.execute(text("""
        INSERT INTO telephony_call_carrier_links(account_id,call_id,provider,trunk_id,did_id,carrier_call_id,
            asterisk_uniqueid,asterisk_linkedid,metadata_json)
        VALUES(:a,:c,'mcn',:tr,:did,:cc,:u,:l,CAST(:m AS jsonb))
        ON CONFLICT(account_id,call_id) DO UPDATE SET
            trunk_id=COALESCE(excluded.trunk_id,telephony_call_carrier_links.trunk_id),
            did_id=COALESCE(excluded.did_id,telephony_call_carrier_links.did_id),
            carrier_call_id=COALESCE(excluded.carrier_call_id,telephony_call_carrier_links.carrier_call_id),
            asterisk_uniqueid=COALESCE(excluded.asterisk_uniqueid,telephony_call_carrier_links.asterisk_uniqueid),
            asterisk_linkedid=COALESCE(excluded.asterisk_linkedid,telephony_call_carrier_links.asterisk_linkedid),
            metadata_json=telephony_call_carrier_links.metadata_json || excluded.metadata_json,
            updated_at=now()
        RETURNING *
        """), {"a": account_id, "c": call_id,
                "tr": int(payload["trunk_id"]) if payload.get("trunk_id") is not None else None,
                "did": int(payload["did_id"]) if payload.get("did_id") is not None else None,
                "cc": carrier_call_id, "u": uniqueid, "l": linkedid,
                "m": json.dumps(payload.get("metadata") or {}, ensure_ascii=False)}).mappings().one()
        db.commit()
        return {"status": "ok", "link": dict(row)}
    finally:
        db.close()


def current_mcn_accounts(include_synthetic: bool = False) -> list[str]:
    """Canonical discovery of accounts whose current telephony provider is MCN.

    Explicit provider selection is authoritative: an existing non-MCN provider
    suppresses orphaned legacy MCN trunk/DID rows. Trunk/DID evidence is accepted
    only when provider-config is absent, so the provider-config guardian can
    reconstruct legacy/current MCN state without guessing.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            WITH provider_choice AS (
              SELECT account_id,lower(provider) AS provider
              FROM telephony_provider_configs
              WHERE account_id IS NOT NULL AND btrim(account_id)<>''
            ), candidates AS (
              SELECT p.account_id
              FROM provider_choice p
              WHERE p.provider='mcn'

              UNION

              SELECT t.account_id
              FROM telephony_trunks t
              LEFT JOIN provider_choice p ON p.account_id=t.account_id
              WHERE lower(t.provider)='mcn'
                AND t.enabled=true
                AND p.account_id IS NULL

              UNION

              SELECT d.account_id
              FROM telephony_dids d
              LEFT JOIN provider_choice p ON p.account_id=d.account_id
              WHERE lower(d.provider)='mcn'
                AND d.account_id IS NOT NULL
                AND d.released_at IS NULL
                AND d.status='active'
                AND (d.inbound_enabled=true OR d.outbound_cli_enabled=true)
                AND p.account_id IS NULL
            )
            SELECT DISTINCT account_id
            FROM candidates
            WHERE account_id IS NOT NULL
              AND btrim(account_id)<>''
              AND (:include_synthetic OR NOT (
                    lower(account_id) ~ '^__.*qa'
                    OR lower(account_id) ~ '^qa[-_]'
              ))
            ORDER BY account_id
        """), {"include_synthetic": bool(include_synthetic)}).all()
        return [str(row[0]).strip() for row in rows if str(row[0] or "").strip()]
    finally:
        db.close()


def readiness(account_id: str) -> dict[str, Any]:
    ensure_schema()
    trunks = trunk_list(account_id)
    dids = did_list(account_id)
    active_trunks = [x for x in trunks if x.get("enabled")]
    healthy_statuses = {"ok", "registration_registered"}
    connected = [x for x in active_trunks if x.get("status") == "connected" and x.get("last_health_status") in healthy_statuses]
    active_dids = [x for x in dids if x.get("status") == "active" and x.get("inbound_enabled")]
    blockers: list[str] = []
    if not active_trunks:
        blockers.append("mcn_trunk_missing")
    elif not connected:
        blockers.append("mcn_trunk_not_verified")
    if not active_dids:
        blockers.append("mcn_active_did_missing")

    steps = {
        "trunk_saved": bool(active_trunks),
        "sip_registered": bool(connected),
        "did_active": bool(active_dids),
    }
    completed_steps = sum(1 for v in steps.values() if v)
    if "mcn_trunk_missing" in blockers:
        next_action = {
            "code": "mcn_trunk_credentials",
            "actor": "owner",
            "owner_action_required": True,
            "text": "Вставьте настройки из письма MCN целиком: BORIS сам распознает SIP login/password, сервер и номер, затем настроит Asterisk и запустит регистрацию.",
        }
    elif "mcn_trunk_not_verified" in blockers:
        next_action = {
            "code": "mcn_registration_self_heal",
            "actor": "boris",
            "owner_action_required": False,
            "text": "Настройки сохранены. BORIS сам ждёт Registered, проверяет SIP и безопасно повторяет регистрацию при временном сбое.",
        }
    elif "mcn_active_did_missing" in blockers:
        next_action = {
            "code": "mcn_did_required",
            "actor": "owner",
            "owner_action_required": True,
            "text": "SIP зарегистрирован. Добавьте выданный номер MCN, чтобы включить реальные входящие звонки.",
        }
    else:
        next_action = {
            "code": "mcn_ready",
            "actor": "boris",
            "owner_action_required": False,
            "text": "MCN подключён: SIP зарегистрирован и номер активен. Следующий этап — реальный входящий и исходящий звонок.",
        }

    return {
        "status": "ok",
        "ready": not blockers,
        "operator": "mcn",
        "trunks": len(active_trunks),
        "connected_trunks": len(connected),
        "active_dids": len(active_dids),
        "blockers": blockers,
        "steps": steps,
        "steps_completed": completed_steps,
        "steps_total": len(steps),
        "primary_next_action": next_action,
        "external_dependency": "MCN SIP credentials/DID/channel policy are required for real PSTN proof" if blockers else None,
    }


def tariff_version_upsert(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    name = str(payload.get("name") or "").strip()[:200]
    effective_from = str(payload.get("effective_from") or "").strip()
    if not name or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_from):
        return {"status": "invalid_version"}
    db = SessionLocal()
    try:
        row = db.execute(text("""
        INSERT INTO telephony_tariff_versions(operator,name,effective_from,vat_mode,billing_increment_sec,minimum_billable_sec,
            source_sha256,source_name,status,metadata_json)
        VALUES('mcn',:n,:d,:vat,:inc,:min,:sha,:src,:st,CAST(:meta AS jsonb))
        ON CONFLICT(operator,name,effective_from) DO UPDATE SET
            vat_mode=excluded.vat_mode,billing_increment_sec=excluded.billing_increment_sec,
            minimum_billable_sec=excluded.minimum_billable_sec,source_sha256=COALESCE(excluded.source_sha256,telephony_tariff_versions.source_sha256),
            source_name=COALESCE(excluded.source_name,telephony_tariff_versions.source_name),
            metadata_json=excluded.metadata_json,status=excluded.status
        RETURNING *
        """), {"n": name, "d": effective_from, "vat": str(payload.get("vat_mode") or "without_vat")[:40],
                "inc": int(payload["billing_increment_sec"]) if payload.get("billing_increment_sec") is not None else None,
                "min": int(payload["minimum_billable_sec"]) if payload.get("minimum_billable_sec") is not None else None,
                "sha": str(payload.get("source_sha256") or "")[:64] or None,
                "src": str(payload.get("source_name") or "")[:255] or None,
                "st": str(payload.get("status") or "draft")[:40],
                "meta": json.dumps(payload.get("metadata") or {}, ensure_ascii=False)}).mappings().one()
        db.commit()
        return {"status": "ok", "version": dict(row)}
    finally:
        db.close()


def tariff_rate_bulk_replace(version_id: int, rates: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_schema()
    cleaned: list[dict[str, Any]] = []
    for i, x in enumerate(rates[:200000]):
        try:
            price = Decimal(str(x.get("price")))
        except Exception:
            return {"status": "invalid_rate", "row": i, "field": "price"}
        if price < 0:
            return {"status": "invalid_rate", "row": i, "field": "price"}
        direction = str(x.get("direction_type") or "").strip().lower()
        label = str(x.get("label") or "").strip()
        prefix = re.sub(r"\D+", "", str(x.get("prefix") or ""))[:15] or None
        if not direction or not label:
            return {"status": "invalid_rate", "row": i, "field": "direction_or_label"}
        def _num(v):
            if v is None or str(v).strip() == "":
                return None
            return str(int(Decimal(str(v))))
        cleaned.append({
            "direction_type": direction[:80], "label": label[:300], "prefix": prefix,
            "range_from": _num(x.get("range_from")), "range_to": _num(x.get("range_to")),
            "price": str(price), "currency": str(x.get("currency") or "RUB")[:8],
            "country": str(x.get("destination_country") or "")[:120] or None,
            "region": str(x.get("destination_region") or "")[:160] or None,
            "meta": json.dumps(x.get("metadata") or {}, ensure_ascii=False)
        })
    db = SessionLocal()
    try:
        v = db.execute(text("SELECT id FROM telephony_tariff_versions WHERE id=:id FOR UPDATE"), {"id": int(version_id)}).first()
        if not v:
            return {"status": "version_not_found"}
        db.execute(text("DELETE FROM telephony_tariff_rates WHERE tariff_version_id=:id"), {"id": int(version_id)})
        for x in cleaned:
            db.execute(text("""
            INSERT INTO telephony_tariff_rates(tariff_version_id,direction_type,label,prefix,range_from,range_to,price,currency,
                destination_country,destination_region,metadata_json)
            VALUES(:vid,:direction_type,:label,:prefix,:range_from,:range_to,:price,:currency,:country,:region,CAST(:meta AS jsonb))
            """), {"vid": int(version_id), **x})
        db.commit()
        return {"status": "ok", "version_id": int(version_id), "rates": len(cleaned)}
    finally:
        db.close()


def tariff_rate_resolve(number: str, *, direction_type: str | None = None, at_date: str | None = None) -> dict[str, Any] | None:
    ensure_schema()
    n = normalize_e164(number)
    digits = re.sub(r"\D+", "", n)
    if not digits:
        return None
    db = SessionLocal()
    try:
        numeric = int(digits)
        params: dict[str, Any] = {"digits": digits, "numeric": numeric}
        date_expr = "CURRENT_DATE"
        if at_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(at_date)):
            date_expr = "CAST(:at AS date)"
            params["at"] = str(at_date)
        extra = ""
        if direction_type:
            extra = " AND r.direction_type=:direction"
            params["direction"] = str(direction_type).strip().lower()
        rows = db.execute(text(f"""
        SELECT r.*,v.name tariff_name,v.effective_from,v.vat_mode,v.billing_increment_sec,v.minimum_billable_sec
        FROM telephony_tariff_rates r
        JOIN telephony_tariff_versions v ON v.id=r.tariff_version_id
        WHERE v.operator='mcn' AND v.status='active' AND v.effective_from<={date_expr}
          {extra}
          AND (r.prefix IS NULL OR :digits LIKE r.prefix || '%')
          AND (r.range_from IS NULL OR r.range_from<=:numeric)
          AND (r.range_to IS NULL OR r.range_to>=:numeric)
        ORDER BY v.effective_from DESC, length(COALESCE(r.prefix,'')) DESC, r.id
        LIMIT 1
        """), params).mappings().first()
        return dict(rows) if rows else None
    finally:
        db.close()



def tariff_direction_hint(number: str) -> dict[str, Any]:
    """Classify only destinations that are unambiguous from E.164 alone.

    Russian fixed destinations intentionally remain unresolved because choosing
    local_fixed vs intercity_ru requires the account/home-region context.
    """
    normalized = normalize_e164(number)
    digits = re.sub(r"\D+", "", normalized)
    if not digits:
        return {"status": "invalid_number", "direction_type": None}
    if digits.startswith("79"):
        return {"status": "ok", "direction_type": "mobile_ru", "reason": "ru_mobile_e164"}
    if digits.startswith("7"):
        return {
            "status": "direction_required",
            "direction_type": None,
            "reason": "ru_fixed_or_other_plus7_requires_home_region",
        }
    return {"status": "ok", "direction_type": "international", "reason": "non_plus7_destination"}


def tariff_call_cost_estimate(number: str, seconds: int, *,
                              direction_type: str | None = None,
                              at_date: str | None = None) -> dict[str, Any]:
    """Estimate MCN carrier net cost from active tariff reference data.

    The imported MCN source prices are explicitly without VAT. This helper
    therefore returns a net estimate only; it does not invent VAT/gross values.
    """
    sec = max(0, int(seconds or 0))
    if sec <= 0:
        return {
            "status": "no_billable_seconds",
            "amount_rub": "0",
            "billed_seconds": 0,
            "vat_mode": "without_vat",
        }
    direction = str(direction_type or "").strip().lower()
    hint = None
    if not direction:
        hint = tariff_direction_hint(number)
        direction = str(hint.get("direction_type") or "")
        if not direction:
            return {
                "status": hint.get("status") or "direction_required",
                "reason": hint.get("reason"),
                "direction_type": None,
            }
    row = tariff_rate_resolve(number, direction_type=direction, at_date=at_date)
    if not row:
        return {
            "status": "rate_not_found",
            "direction_type": direction,
            "number": normalize_e164(number),
        }
    increment = max(1, int(row.get("billing_increment_sec") or 1))
    minimum = max(0, int(row.get("minimum_billable_sec") or 0))
    billed = ((sec + increment - 1) // increment) * increment
    billed = max(billed, minimum)
    rate = Decimal(str(row.get("price") or 0))
    amount = (rate * Decimal(billed) / Decimal(60)).quantize(Decimal("0.0001"))
    return {
        "status": "ok",
        "source": "tariff_estimate",
        "direction_type": direction,
        "number": normalize_e164(number),
        "seconds": sec,
        "billed_seconds": billed,
        "rate_per_minute_rub": str(rate),
        "amount_rub": str(amount),
        "currency": str(row.get("currency") or "RUB"),
        "vat_mode": str(row.get("vat_mode") or "without_vat"),
        "tariff_version_id": int(row.get("tariff_version_id") or 0) or None,
        "tariff_name": row.get("tariff_name"),
        "rate_id": int(row.get("id") or 0) or None,
        "label": row.get("label"),
        "effective_from": row.get("effective_from"),
        "billing_increment_sec": increment,
        "minimum_billable_sec": minimum or None,
        "classification": (hint or {}).get("reason") if hint else "explicit_direction",
    }


def call_provider_cost(account_id: str, call_id: str) -> dict[str, Any]:
    """Return exact MCN CDR cost when available, else a safe tariff estimate."""
    ensure_schema()
    db = SessionLocal()
    try:
        call = db.execute(text("""
          SELECT id,account_id,provider,direction,to_number,talk_duration_sec,started_at
          FROM telephony_calls WHERE account_id=:a AND id=:c
        """), {"a": account_id, "c": call_id}).mappings().first()
        if not call:
            return {"status": "call_not_found"}
        if str(call.get("provider") or "").lower() != "mcn":
            return {"status": "not_mcn"}
        if str(call.get("direction") or "").lower() != "outbound":
            return {"status": "not_outbound"}
        exact = db.execute(text("""
          SELECT c.id,c.carrier_amount,c.currency,c.tariff_version_id,c.updated_at
          FROM telephony_call_carrier_links l
          JOIN telephony_carrier_cdr c
            ON c.operator='mcn' AND c.carrier_call_id=l.carrier_call_id
          WHERE l.provider='mcn' AND l.account_id=:a AND l.call_id=:call
            AND c.carrier_amount IS NOT NULL
          ORDER BY c.updated_at DESC,c.id DESC LIMIT 1
        """), {"a": account_id, "call": call_id}).mappings().first()
    finally:
        db.close()
    if exact and str(exact.get("currency") or "RUB").upper() == "RUB":
        return {
            "status": "ok",
            "source": "carrier_cdr",
            "amount_rub": str(Decimal(str(exact.get("carrier_amount") or 0)).quantize(Decimal("0.0001"))),
            "currency": "RUB",
            "carrier_cdr_id": int(exact["id"]),
            "tariff_version_id": int(exact["tariff_version_id"]) if exact.get("tariff_version_id") is not None else None,
        }
    started = call.get("started_at")
    at = started.date().isoformat() if started is not None else None
    return tariff_call_cost_estimate(
        str(call.get("to_number") or ""),
        int(call.get("talk_duration_sec") or 0),
        at_date=at,
    )


def carrier_cdr_upsert(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    carrier_call_id = str(payload.get("carrier_call_id") or "").strip()[:200]
    if not carrier_call_id:
        return {"status": "carrier_call_id_required"}
    def ts(v):
        if not v:
            return None
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return None
    db = SessionLocal()
    try:
        row = db.execute(text("""
        INSERT INTO telephony_carrier_cdr(operator,account_id,carrier_call_id,direction,from_number,to_number,started_at,answered_at,
            ended_at,actual_duration_sec,billed_duration_sec,carrier_amount,currency,tariff_version_id,raw_json)
        VALUES('mcn',:a,:cid,:d,:f,:t,:s,:ans,:e,:dur,:bdur,:amt,:cur,:tv,CAST(:raw AS jsonb))
        ON CONFLICT(operator,carrier_call_id) DO UPDATE SET
            account_id=COALESCE(excluded.account_id,telephony_carrier_cdr.account_id),
            direction=excluded.direction,from_number=excluded.from_number,to_number=excluded.to_number,
            started_at=excluded.started_at,answered_at=excluded.answered_at,ended_at=excluded.ended_at,
            actual_duration_sec=excluded.actual_duration_sec,billed_duration_sec=excluded.billed_duration_sec,
            carrier_amount=COALESCE(excluded.carrier_amount,telephony_carrier_cdr.carrier_amount),
            currency=CASE WHEN excluded.carrier_amount IS NULL THEN telephony_carrier_cdr.currency ELSE excluded.currency END,
            tariff_version_id=COALESCE(excluded.tariff_version_id,telephony_carrier_cdr.tariff_version_id),
            raw_json=excluded.raw_json,updated_at=now()
        RETURNING *
        """), {"a": str(payload.get("account_id") or "") or None, "cid": carrier_call_id,
                "d": str(payload.get("direction") or "")[:20] or None,
                "f": normalize_e164(payload.get("from_number")), "t": normalize_e164(payload.get("to_number")),
                "s": ts(payload.get("started_at")), "ans": ts(payload.get("answered_at")), "e": ts(payload.get("ended_at")),
                "dur": int(payload.get("actual_duration_sec") or 0), "bdur": int(payload.get("billed_duration_sec") or 0),
                "amt": None if payload.get("carrier_amount") is None else str(payload.get("carrier_amount")),
                "cur": str(payload.get("currency") or "RUB")[:8],
                "tv": int(payload["tariff_version_id"]) if payload.get("tariff_version_id") is not None else None,
                "raw": json.dumps(payload.get("raw") or {}, ensure_ascii=False)}).mappings().one()
        db.commit()
        return {"status": "ok", "cdr": dict(row)}
    finally:
        db.close()


def reconcile_carrier_cdr(account_id: str | None = None, limit: int = 500,
                          include_synthetic: bool = False) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    reconciled = matched = mismatched = missing = cost_updates = 0
    try:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 5000)),
                                  "include_synthetic": bool(include_synthetic)}
        where = "c.operator='mcn'"
        if account_id:
            where += " AND c.account_id=:a"
            params["a"] = account_id
        elif not include_synthetic:
            where += """ AND (c.account_id IS NULL OR NOT (
              lower(c.account_id) ~ '^__.*qa' OR lower(c.account_id) ~ '^qa[-_]'
            ))"""
        rows = db.execute(text(f"""
        SELECT c.* FROM telephony_carrier_cdr c
        LEFT JOIN telephony_cdr_reconciliation r ON r.operator=c.operator AND r.carrier_cdr_id=c.id
        WHERE {where} AND (r.id IS NULL OR c.updated_at>r.updated_at)
        ORDER BY c.id LIMIT :limit
        """), params).mappings().all()
        for c in rows:
            link = db.execute(text("""
            SELECT l.*,t.account_id AS call_account_id,t.direction AS call_direction,
                   t.to_number AS call_to_number,t.talk_duration_sec,t.started_at,t.answered_at,t.ended_at
            FROM telephony_call_carrier_links l
            JOIN telephony_calls t ON t.account_id=l.account_id AND t.id=l.call_id
            WHERE l.provider='mcn' AND l.carrier_call_id=:cid LIMIT 1
            """), {"cid": c["carrier_call_id"]}).mappings().first()
            status = "matched"
            call_id = None
            duration_delta = None
            amount_delta = None
            details: dict[str, Any] = {}
            if not link:
                status = "missing_in_boris"
                missing += 1
            else:
                call_id = link["call_id"]
                carrier_duration = int(c.get("actual_duration_sec") or 0)
                boris_duration = int(link.get("talk_duration_sec") or 0)
                duration_delta = carrier_duration - boris_duration
                details["boris_duration_sec"] = boris_duration
                details["carrier_duration_sec"] = carrier_duration
                if abs(duration_delta) > 2:
                    status = "duration_mismatch"
                    mismatched += 1
                else:
                    matched += 1

                # Exact carrier money is stronger evidence than the tariff estimate
                # recorded at call end. Replace, never add, so COGS stays exactly-once.
                call_account_id = str(
                    link.get("call_account_id") or link.get("account_id")
                    or c.get("account_id") or ""
                ).strip()
                carrier_amount = c.get("carrier_amount")
                carrier_currency = str(c.get("currency") or "RUB").upper()
                if carrier_amount is None:
                    details["provider_cost_source"] = "carrier_amount_missing"
                elif carrier_currency != "RUB":
                    details["provider_cost_source"] = "carrier_cdr_non_rub_pending"
                    details["carrier_currency"] = carrier_currency
                    details["carrier_amount"] = str(carrier_amount)
                else:
                    actual_amount = Decimal(str(carrier_amount))
                    details["provider_cost_source"] = "carrier_cdr"
                    details["carrier_currency"] = "RUB"
                    details["carrier_amount"] = str(actual_amount)
                    usage = None
                    if call_account_id:
                        usage = db.execute(text("""
                          SELECT id,provider_cost_rub
                          FROM telephony_minute_usage
                          WHERE account_id=:a AND call_id=:call
                          ORDER BY id DESC LIMIT 1 FOR UPDATE
                        """), {"a": call_account_id, "call": call_id}).mappings().first()
                    if usage:
                        prior_amount = Decimal(str(usage.get("provider_cost_rub") or 0))
                        amount_delta = actual_amount - prior_amount
                        if actual_amount != prior_amount:
                            db.execute(text("""
                              UPDATE telephony_minute_usage
                              SET provider_cost_rub=:amount
                              WHERE id=:id
                            """), {"amount": str(actual_amount), "id": int(usage["id"])})
                            cost_updates += 1
                        details["minute_usage_id"] = int(usage["id"])
                        details["prior_provider_cost_rub"] = str(prior_amount)
                        details["provider_cost_updated"] = bool(actual_amount != prior_amount)
                    else:
                        details["provider_cost_updated"] = False
                        details["provider_cost_pending_metering"] = True
                    if call_account_id:
                        cost_meta = {
                            "source": "carrier_cdr",
                            "carrier_cdr_id": int(c["id"]),
                            "amount_rub": str(actual_amount),
                            "currency": "RUB",
                            "tariff_version_id": (
                                int(c["tariff_version_id"])
                                if c.get("tariff_version_id") is not None else None
                            ),
                        }
                        db.execute(text("""
                          UPDATE telephony_calls
                          SET metadata_json=COALESCE(metadata_json,'{}'::jsonb)
                              || jsonb_build_object('mcn_cost',CAST(:cost AS jsonb)),
                              updated_at=now()
                          WHERE account_id=:a AND id=:call
                        """), {
                            "cost": json.dumps(cost_meta, ensure_ascii=False),
                            "a": call_account_id,
                            "call": call_id,
                        })
            db.execute(text("""
            INSERT INTO telephony_cdr_reconciliation(
              operator,account_id,call_id,carrier_cdr_id,status,duration_delta_sec,amount_delta,details_json
            )
            VALUES('mcn',:a,:call,:cdr,:st,:delta,:amount_delta,CAST(:details AS jsonb))
            ON CONFLICT(operator,carrier_cdr_id) DO UPDATE SET
              account_id=excluded.account_id,call_id=excluded.call_id,status=excluded.status,
              duration_delta_sec=excluded.duration_delta_sec,amount_delta=excluded.amount_delta,
              details_json=excluded.details_json,updated_at=now()
            """), {"a": c.get("account_id") or (link.get("account_id") if link else None), "call": call_id,
                    "cdr": c["id"], "st": status, "delta": duration_delta,
                    "amount_delta": str(amount_delta) if amount_delta is not None else None,
                    "details": json.dumps(details, ensure_ascii=False)})
            reconciled += 1
        db.commit()
        return {"status": "ok", "picked": len(rows), "reconciled": reconciled,
                "matched": matched, "mismatched": mismatched, "missing_in_boris": missing}
    finally:
        db.close()


def status(account_id: str) -> dict[str, Any]:
    ensure_schema()
    db = SessionLocal()
    try:
        counts = dict(db.execute(text("""
        SELECT
          (SELECT count(*) FROM telephony_trunks WHERE account_id=:a AND enabled=true) trunks,
          (SELECT count(*) FROM telephony_trunks WHERE account_id=:a AND enabled=true AND status='connected' AND last_health_status IN ('ok','registration_registered')) connected_trunks,
          (SELECT count(*) FROM telephony_dids WHERE account_id=:a AND released_at IS NULL) dids,
          (SELECT count(*) FROM telephony_dids WHERE account_id=:a AND released_at IS NULL AND status='active') active_dids,
          (SELECT count(*) FROM telephony_carrier_cdr WHERE account_id=:a) carrier_cdrs,
          (SELECT count(*) FROM telephony_cdr_reconciliation WHERE account_id=:a AND status<>'matched') cdr_mismatches
        """), {"a": account_id}).mappings().one())
    finally:
        db.close()
    return {"status": "ok", "operator": "mcn", **counts, "readiness": readiness(account_id)}


# MCN_COMPANY_CARD_MANUAL_DELIVERY_V1
# Durable owner assertion for a company-card message that was sent outside the
# protected BORIS SMTP path. It never contains banking/document contents.
_MCN_MANUAL_DELIVERY_ACCOUNT = "__mcn_mailbox_watch__"
_MCN_MANUAL_DELIVERY_KEY = "mcn_company_card_manual_delivery_v1"


def mcn_company_card_manual_delivery_confirmation() -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"a": _MCN_MANUAL_DELIVERY_ACCOUNT, "k": _MCN_MANUAL_DELIVERY_KEY}).first()
    finally:
        db.close()
    if not row or not row[0]:
        return {}
    try:
        payload = json.loads(str(row[0]))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def confirm_mcn_company_card_manual_delivery(
    mailbox_id: int,
    request_message_id: str,
    sent_at: str,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    mailbox = int(mailbox_id or 0)
    request_mid = str(request_message_id or "").strip()[:500]
    sent_raw = str(sent_at or "").strip()[:100]
    if mailbox <= 0 or not request_mid or not sent_raw:
        raise ValueError("manual_delivery_confirmation_invalid")
    try:
        sent_dt = datetime.fromisoformat(sent_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("manual_delivery_sent_at_invalid") from exc
    if sent_dt.tzinfo is None:
        sent_dt = sent_dt.replace(tzinfo=timezone.utc)
    sent_iso = sent_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    actor = None
    try:
        if actor_user_id is not None:
            actor = int(actor_user_id)
    except (TypeError, ValueError):
        actor = None
    payload = {
        "status": "confirmed",
        "mailbox_id": mailbox,
        "request_message_id": request_mid,
        "sent_at": sent_iso,
        "confirmed_at": utcnow().replace(microsecond=0).isoformat(),
        "actor_user_id": actor,
        "source": "owner_manual_delivery_confirmation",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    db = SessionLocal()
    try:
        lock_key = f"mcn-company-card-manual-delivery|{mailbox}|{request_mid}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": lock_key})
        row = db.execute(text("""
            SELECT id FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
        """), {"a": _MCN_MANUAL_DELIVERY_ACCOUNT, "k": _MCN_MANUAL_DELIVERY_KEY}).first()
        if row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:i"), {"v": raw, "i": int(row[0])})
        else:
            db.execute(text("""
                INSERT INTO storage(account_id,key,value)
                VALUES(:a,:k,:v)
            """), {"a": _MCN_MANUAL_DELIVERY_ACCOUNT, "k": _MCN_MANUAL_DELIVERY_KEY, "v": raw})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return dict(payload)
