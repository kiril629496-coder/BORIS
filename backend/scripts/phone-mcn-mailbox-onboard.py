#!/usr/bin/env python3
"""Safely discover one MCN setup letter and, only when explicitly allowed, onboard it.

Safety:
- IMAP is readonly and uses BODY.PEEK; no Seen flag or mailbox cursor is changed.
- SIP username/password/DID/registrar are never printed.
- No mutation occurs unless --apply is passed.
- Target account is explicit or auto-selected only when exactly one active paid Phone slot exists.
- Multiple distinct complete MCN letters fail closed.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import html
import importlib.util
import json
import sys
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DISCOVERY = ROOT / "scripts" / "mcn-mailbox-discovery.py"
SPEC = importlib.util.spec_from_file_location("mcn_mailbox_discovery_safe", DISCOVERY)
D = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(D)

from app.db.session import SessionLocal
from app.services.mcn_core import (
    onboarding_upsert,
    normalize_e164,
    normalize_source_ip,
    normalize_sip_target,
    readiness as mcn_readiness,
)
from app.services.telephony_core import active_phone_entitlements


def _clean(value: str | None) -> str:
    return html.unescape(str(value or "")).strip().strip(" \t\r\n;,")


def _value(pattern, text_value: str) -> str | None:
    m = pattern.search(text_value)
    return _clean(m.group(1)) if m else None


def extract_candidate(raw: bytes, uid: str) -> dict[str, Any] | None:
    msg = email.message_from_bytes(raw)
    subject = D._decode_header(msg.get("Subject"))
    sender = D._decode_header(msg.get("From"))
    date_value = D._decode_header(msg.get("Date"))
    body = D._plain_message(msg)
    combined = "\n".join((sender, subject, body))
    analysis = D.analyze_text(combined)
    if not analysis.get("candidate_complete"):
        return None

    registrar_raw = _value(D._REGISTRAR, combined)
    username = _value(D._USERNAME, combined)
    password = _value(D._PASSWORD, combined)
    did_raw = _value(D._DID, combined)
    source_ip_raw = _value(D._SOURCE_IP, combined)

    registrar = normalize_sip_target(registrar_raw) if registrar_raw else None
    did = normalize_e164(did_raw) if did_raw else None
    source_ip = normalize_source_ip(source_ip_raw) if source_ip_raw else None

    registration_complete = bool(registrar and username and password and did)
    ip_complete = bool(source_ip and did)
    if not (registration_complete or ip_complete):
        return None

    auth_mode = "registration" if registration_complete else "ip"
    fingerprint_material = json.dumps(
        [auth_mode, registrar or "", username or "", password or "", did or "", source_ip or ""],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    addr = parseaddr(sender)[1]
    sender_domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else None
    return {
        "uid": str(uid),
        "date": date_value,
        "from": sender,
        "sender_domain": sender_domain,
        "subject": subject,
        "auth_mode": auth_mode,
        "registrar": registrar,
        "username": username,
        "password": password,
        "did": did,
        "source_ip": source_ip,
        "fingerprint": hashlib.sha256(fingerprint_material.encode()).hexdigest(),
    }


def classify_operational_mcn_reply(raw: bytes, uid: str) -> dict[str, Any] | None:
    msg = email.message_from_bytes(raw)
    sender = D._decode_header(msg.get("From"))
    addr = parseaddr(sender)[1]
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    if domain != "mcn.ru" and not domain.endswith(".mcn.ru"):
        return None
    subject = D._decode_header(msg.get("Subject"))
    date_value = D._decode_header(msg.get("Date"))
    message_id = str(msg.get("Message-ID") or "").strip()[:500] or None
    body = " ".join(D._plain_message(msg).lower().split())
    company_card_gate = (
        ("пришлите" in body or "нужна" in body or "нужен" in body)
        and "карточк" in body
        and ("компан" in body or "ип" in body)
        and (
            "подготовим" in body
            or "заполню данные" in body
            or "проект договора" in body
            or "оферт" in body
            or "эдо" in body
        )
    )
    if company_card_gate:
        return {
            "uid": str(uid),
            "date": date_value,
            "message_id": message_id,
            "subject": subject[:500],
            "sender_domain": domain,
            "code": "mcn_company_card_required",
            "owner_action_required": True,
            "actor": "owner",
            "requirements": ["company_or_ip_card"],
            "provider_path": [
                "confirm_current_company_card",
                "send_company_card_to_mcn",
                "mcn_prepares_client_and_agency_contracts",
                "choose_offer_or_edo",
                "receive_sip_credentials",
            ],
        }

    contract_gate = (
        "договор" in body
        and ("до заключения договора" in body or "до оформления договора" in body)
        and ("настрой" in body or "sip" in body)
        and ("не сможем" in body or "не можем" in body or "не выда" in body)
    )
    if not contract_gate:
        return None
    requirements = []
    if "карточка компании" in body or "карточка ип" in body:
        requirements.append("company_or_ip_card")
    if "св-ва о регистрации" in body or "свидетельств" in body or "лист записи" in body:
        requirements.append("registration_document")
    if "решение о назначении директора" in body:
        requirements.append("director_appointment_decision")
    return {
        "uid": str(uid),
        "date": date_value,
        "message_id": message_id,
        "subject": subject[:500],
        "sender_domain": domain,
        "code": "mcn_contract_required_before_credentials",
        "owner_action_required": True,
        "actor": "owner",
        "requirements": requirements,
        "provider_path": ["client_contract_for_test", "agency_contract_in_parallel"],
    }


def _redact_metadata_text(value: str | None, candidate: dict[str, Any]) -> str:
    out = str(value or "")
    for key in ("registrar", "username", "password", "did", "source_ip"):
        secret_value = str(candidate.get(key) or "").strip()
        if secret_value:
            out = out.replace(secret_value, "[redacted]")
    return out[:500]


def public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": candidate.get("uid"),
        "date": candidate.get("date"),
        "from": candidate.get("from"),
        "sender_domain": candidate.get("sender_domain"),
        "subject": _redact_metadata_text(candidate.get("subject"), candidate),
        "auth_mode": candidate.get("auth_mode"),
        "fields_found": {
            "registrar": bool(candidate.get("registrar")),
            "username": bool(candidate.get("username")),
            "password": bool(candidate.get("password")),
            "did": bool(candidate.get("did")),
            "source_ip": bool(candidate.get("source_ip")),
        },
    }


def scan_mailbox(mailbox_id: int, limit: int = 1500) -> dict[str, Any]:
    row = D._row(int(mailbox_id))
    if not row:
        return {"status": "blocked", "reason": "mailbox_not_found", "mailbox_id": int(mailbox_id)}
    secret = D._decrypt_mailbox_secret(row["secret_encrypted"])
    if not secret:
        return {
            "status": "blocked",
            "reason": "mailbox_secret_decryption_unavailable",
            "mailbox_id": int(mailbox_id),
            "mailbox": row.get("email_address"),
        }

    cls = D.imaplib.IMAP4_SSL if row["imap_ssl"] else D.imaplib.IMAP4
    conn = None
    try:
        conn = cls(row["imap_host"], int(row["imap_port"]), timeout=12)
        conn.login(row["username"], secret)
        status, count_data = conn.select("INBOX", readonly=True)
        if status != "OK":
            return {"status": "blocked", "reason": "inbox_select_failed", "mailbox_id": int(mailbox_id)}
        search_sets = []
        for criteria in (
            ("FROM", "\"mcn\""),
            ("TEXT", "\"MCN\""),
            ("TEXT", "\"SIP\""),
            ("TEXT", "\"registrar\""),
            ("TEXT", "\"proxy\""),
        ):
            try:
                status, uid_data = conn.uid("search", None, *criteria)
                ids = uid_data[0].split() if status == "OK" and uid_data and uid_data[0] else []
                search_sets.append(set(ids))
            except Exception:
                continue
        uids = sorted(set().union(*search_sets) if search_sets else set(), key=lambda x: int(x))
        chosen = uids[-max(1, min(int(limit), 5000)):]
        unique: dict[str, dict[str, Any]] = {}
        latest_operational_reply: dict[str, Any] | None = None
        scanned = 0
        for uid_raw in reversed(chosen):
            uid = uid_raw.decode(errors="ignore")
            status, fetched = conn.uid("fetch", uid_raw, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            raw = b"".join(
                item[1] for item in fetched
                if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray))
            )
            if not raw:
                continue
            scanned += 1
            if latest_operational_reply is None:
                latest_operational_reply = classify_operational_mcn_reply(raw, uid)
            candidate = extract_candidate(raw, uid)
            if not candidate:
                continue
            sender_domain = str(candidate.get("sender_domain") or "").lower().lstrip("@")
            if sender_domain != "mcn.ru" and not sender_domain.endswith(".mcn.ru"):
                continue
            unique.setdefault(candidate["fingerprint"], candidate)
        candidates = list(unique.values())
        candidates.sort(key=lambda x: int(str(x.get("uid") or "0") or "0"), reverse=True)
        return {
            "status": "ok",
            "mailbox_id": int(mailbox_id),
            "mailbox": row.get("email_address"),
            "messages_available": int(count_data[0]) if count_data and count_data[0] else None,
            "messages_scanned": scanned,
            "search_strategy": "server_side_mcn_sip_registrar_proxy",
            "candidate_uid_count": len(chosen),
            "unique_complete_candidates": len(candidates),
            "operational_reply": latest_operational_reply,
            "_candidates": candidates,
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}:{' '.join(str(exc).split())[:180]}"
        return {
            "status": "blocked",
            "reason": D.mailbox_error_kind(reason) or "imap_access_failed",
            "error_type": type(exc).__name__,
            "mailbox_id": int(mailbox_id),
            "mailbox": row.get("email_address"),
        }
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


def resolve_target_account(explicit_account_id: str | None) -> dict[str, Any]:
    explicit = str(explicit_account_id or "").strip()
    entitled = {
        str(x.get("account_id") or "").strip(): dict(x)
        for x in active_phone_entitlements()
        if str(x.get("account_id") or "").strip()
    }
    if explicit:
        if explicit in entitled:
            return {
                "status": "ok",
                "account_id": explicit,
                "resolution": "explicit_active_phone_entitlement",
            }
        db = SessionLocal()
        try:
            exists = db.execute(
                text("SELECT account_id FROM accounts WHERE account_id=:a LIMIT 1"),
                {"a": explicit},
            ).first()
        finally:
            db.close()
        return (
            {"status": "blocked", "reason": "phone_entitlement_inactive"}
            if exists
            else {"status": "blocked", "reason": "account_not_found"}
        )

    rows = sorted(entitled)
    if len(rows) == 1:
        return {"status": "ok", "account_id": rows[0], "resolution": "single_active_phone_entitlement"}
    if not rows:
        return {"status": "blocked", "reason": "no_active_phone_account", "accounts": 0}
    return {"status": "blocked", "reason": "ambiguous_active_phone_accounts", "accounts": len(rows)}


def provider_preflight(account_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT lower(provider) FROM telephony_provider_configs WHERE account_id=:a LIMIT 1"),
            {"a": account_id},
        ).first()
        if row and str(row[0] or "").lower() not in ("", "mcn"):
            return {"status": "blocked", "reason": "provider_conflict"}
        owner = db.execute(
            text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),
            {"a": account_id},
        ).first()
        return {"status": "ok", "owner_user_id": owner[0] if owner else None}
    finally:
        db.close()


def build_payloads(candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    did = str(candidate["did"])
    metadata = {
        "source": "mcn_mailbox_auto_onboard",
        "mailbox_id": None,
        "message_uid": str(candidate.get("uid") or ""),
        "sender_domain": candidate.get("sender_domain"),
    }
    trunk = {
        "provider": "mcn",
        "name": "MCN primary",
        "auth_mode": candidate["auth_mode"],
        "enabled": True,
        "allowed_cli": [did],
        "metadata": dict(metadata),
    }
    if candidate.get("registrar"):
        trunk["registrar"] = candidate["registrar"]
    if candidate["auth_mode"] == "registration":
        trunk["username"] = candidate["username"]
        trunk["password"] = candidate["password"]
    if candidate.get("source_ip"):
        trunk["source_ips"] = [candidate["source_ip"]]

    did_payload = {
        "provider": "mcn",
        "number": did,
        "purpose": "main",
        "inbound_enabled": True,
        "outbound_cli_enabled": True,
        "status": "active",
        "metadata": dict(metadata),
    }
    return trunk, did_payload


def apply_candidate(account_id: str, mailbox_id: int, candidate: dict[str, Any]) -> dict[str, Any]:
    preflight = provider_preflight(account_id)
    if preflight.get("status") != "ok":
        return preflight
    trunk, did_payload = build_payloads(candidate)
    trunk["metadata"]["mailbox_id"] = int(mailbox_id)
    did_payload["metadata"]["mailbox_id"] = int(mailbox_id)

    result = onboarding_upsert(account_id, trunk, did_payload)
    if result.get("status") != "ok":
        return {
            "status": "blocked",
            "reason": "onboarding_failed",
            "detail_status": result.get("status"),
        }

    from app.services.telephony_core import save_provider_config, verify_provider_connection
    from app.services.asterisk_gateway import mcn_pjsip_guardian

    actor_id = preflight.get("owner_user_id")
    provider_cfg = save_provider_config(
        account_id,
        "mcn",
        credentials={"transport": "mcn_trunk", "account_id": account_id},
        public_config={"operator": "mcn", "account_id": account_id},
        actor_user_id=actor_id,
    )
    try:
        guardian = mcn_pjsip_guardian()
    except Exception as exc:
        guardian = {"status": "degraded", "error_type": type(exc).__name__[:120]}
    try:
        verification = verify_provider_connection(account_id, actor_id)
    except Exception as exc:
        verification = {
            "status": "guardian_error",
            "connected": False,
            "error_type": type(exc).__name__[:120],
        }

    return {
        "status": "ok",
        "account_id": account_id,
        "source": "mailbox",
        "message": public_candidate(candidate),
        "provider_config": {"status": provider_cfg.get("status"), "provider": "mcn"},
        "pjsip_guardian": {
            "status": guardian.get("status"),
            "owner_action_required": bool(guardian.get("owner_action_required", False)),
        } if isinstance(guardian, dict) else {"status": "unknown"},
        "provider_verification": {
            "status": verification.get("status"),
            "connected": bool(verification.get("connected")),
        } if isinstance(verification, dict) else {"status": "unknown", "connected": False},
        "readiness": mcn_readiness(account_id),
        "truth": "credentials were consumed inside BORIS and were not returned",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mailbox-id", type=int, default=2)
    ap.add_argument("--account-id", default=None)
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expected-uid", default="")
    ap.add_argument("--expected-date", default="")
    args = ap.parse_args()

    scan = scan_mailbox(args.mailbox_id, limit=args.limit)
    if scan.get("status") != "ok":
        print(json.dumps(scan, ensure_ascii=False, default=str, indent=2))
        return 3

    candidates = list(scan.pop("_candidates", []))
    scan["candidates"] = [public_candidate(x) for x in candidates[:20]]
    if len(candidates) != 1:
        scan["status"] = "blocked"
        operational = scan.get("operational_reply") or {}
        scan["reason"] = (
            str(operational.get("code"))
            if not candidates and operational.get("owner_action_required") and operational.get("code")
            else "no_complete_mcn_letter"
            if not candidates
            else "ambiguous_complete_mcn_letters"
        )
        print(json.dumps(scan, ensure_ascii=False, default=str, indent=2))
        return 4

    current_candidate = candidates[0]
    expected_uid = str(args.expected_uid or "").strip()
    expected_date = str(args.expected_date or "").strip()
    if expected_uid and str(current_candidate.get("uid") or "").strip() != expected_uid:
        scan["status"] = "blocked"
        scan["reason"] = "stale_mcn_candidate_evidence"
        print(json.dumps(scan, ensure_ascii=False, default=str, indent=2))
        return 7
    if expected_date and str(current_candidate.get("date") or "").strip() != expected_date:
        scan["status"] = "blocked"
        scan["reason"] = "stale_mcn_candidate_evidence"
        print(json.dumps(scan, ensure_ascii=False, default=str, indent=2))
        return 7

    target = resolve_target_account(args.account_id)
    scan["target"] = target
    if target.get("status") != "ok":
        scan["status"] = "blocked"
        scan["reason"] = target.get("reason")
        print(json.dumps(scan, ensure_ascii=False, default=str, indent=2))
        return 5

    if not args.apply:
        scan["status"] = "ready_to_apply"
        scan["truth"] = "read-only discovery; rerun with --apply to mutate telephony"
        print(json.dumps(scan, ensure_ascii=False, default=str, indent=2))
        return 0

    out = apply_candidate(
        str(target["account_id"]),
        int(args.mailbox_id),
        candidates[0],
    )
    print(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    return 0 if out.get("status") == "ok" else 6


if __name__ == "__main__":
    raise SystemExit(main())
