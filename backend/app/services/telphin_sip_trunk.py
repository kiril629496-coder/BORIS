from __future__ import annotations

import grp
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.exception_observability import observe_suppressed

GENERATED_DIR = Path("/var/lib/asterisk-boris/generated")
GENERATED_PJSIP = GENERATED_DIR / "pjsip_telphin.conf"
ASTERISK_BIN = "/opt/boris/asterisk-22/sbin/asterisk"
ASTERISK_CONF = "/etc/asterisk-boris/asterisk.conf"
ASTERISK_USER = "asterisk-boris"
ASTERISK_SERVICE = "boris-asterisk.service"
BRIDGE_SERVICE = "boris-asterisk-bridge.service"
ALLOWED_REGISTRARS = {
    "sip.telphin.com",
    "vip1.sip.telphin.com",
    "vip2.sip.telphin.com",
    "vip3.sip.telphin.com",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _account_key(account_id: str) -> str:
    return hashlib.sha256(str(account_id or "").encode()).hexdigest()[:16]


def _safe_registrar(value: str | None) -> str:
    raw = str(value or "sip.telphin.com").strip().lower()
    raw = re.sub(r"^sips?:", "", raw, flags=re.I).lstrip("/").split("/", 1)[0]
    if raw not in ALLOWED_REGISTRARS:
        return ""
    return raw


def _safe_line(value: Any) -> str:
    line = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.+*#@-]{2,128}", line):
        return ""
    return line


def _safe_password(value: Any) -> str:
    password = str(value or "")
    if not (4 <= len(password) <= 256):
        return ""
    if any(ch in password for ch in "\r\n\x00;[]"):
        return ""
    return password


def _e164(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return "+" + digits if 7 <= len(digits) <= 15 else ""


def _provider_rows() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.execute(text("""SELECT account_id,provider,credentials_enc,public_config_json,status
          FROM telephony_provider_configs
          WHERE provider='telphin'
            AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]')
          ORDER BY account_id""")).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _decrypt_credentials(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    from app.crypto_utils import decrypt_secret
    try:
        decoded = json.loads(decrypt_secret(str(value)))
        return dict(decoded) if isinstance(decoded, dict) else {}
    except Exception:
        return {}


def _desired(row: dict[str, Any]) -> dict[str, Any]:
    account_id = str(row.get("account_id") or "").strip()
    creds = _decrypt_credentials(row.get("credentials_enc"))
    public = dict(row.get("public_config_json") or {})
    line = _safe_line(creds.get("sip_line"))
    password = _safe_password(creds.get("sip_password"))
    registrar = _safe_registrar(public.get("sip_registrar") or "sip.telphin.com")
    explicitly_disabled = public.get("sip_enabled") is False
    key = _account_key(account_id)
    enabled = bool(account_id and line and password and registrar and not explicitly_disabled)
    return {
        "account_id": account_id,
        "key": key,
        "enabled": enabled,
        "line": line,
        "password": password,
        "registrar": registrar,
        "outbound_number": _e164(public.get("sip_outbound_number") or public.get("src_num")),
        "registration_id": f"telphin-reg-{key}",
        "endpoint_id": f"telphin-trunk-{key}",
        "auth_id": f"telphin-auth-{key}",
        "aor_id": f"telphin-aor-{key}",
        "reason": (
            "disabled" if explicitly_disabled else
            "sip_line_missing_or_invalid" if not line else
            "sip_password_missing_or_invalid" if not password else
            "registrar_invalid" if not registrar else
            "ok"
        ),
    }


def _state_upsert(item: dict[str, Any], *, status: str, rendered_sha: str | None = None,
                  applied_sha: str | None = None, health_status: str | None = None,
                  error: str | None = None, registered: bool = False,
                  metadata: dict[str, Any] | None = None) -> None:
    db = SessionLocal()
    try:
        db.execute(text("""INSERT INTO telephony_telphin_trunk_state(
          account_id,enabled,registrar,registration_id,endpoint_id,status,rendered_sha256,applied_sha256,
          last_rendered_at,last_applied_at,last_health_at,last_health_status,last_registered_at,last_error,metadata_json
        ) VALUES(
          :a,:en,:r,:reg,:ep,:st,:rs,:aps,
          CASE WHEN :rs IS NOT NULL THEN now() ELSE NULL END,
          CASE WHEN :aps IS NOT NULL THEN now() ELSE NULL END,
          CASE WHEN :hs IS NOT NULL THEN now() ELSE NULL END,:hs,
          CASE WHEN :registered THEN now() ELSE NULL END,:err,CAST(:m AS jsonb)
        )
        ON CONFLICT(account_id) DO UPDATE SET
          enabled=excluded.enabled,registrar=excluded.registrar,registration_id=excluded.registration_id,
          endpoint_id=excluded.endpoint_id,status=excluded.status,
          rendered_sha256=COALESCE(excluded.rendered_sha256,telephony_telphin_trunk_state.rendered_sha256),
          applied_sha256=COALESCE(excluded.applied_sha256,telephony_telphin_trunk_state.applied_sha256),
          last_rendered_at=CASE WHEN excluded.rendered_sha256 IS NOT NULL THEN now() ELSE telephony_telphin_trunk_state.last_rendered_at END,
          last_applied_at=CASE WHEN excluded.applied_sha256 IS NOT NULL THEN now() ELSE telephony_telphin_trunk_state.last_applied_at END,
          last_health_at=CASE WHEN excluded.last_health_status IS NOT NULL THEN now() ELSE telephony_telphin_trunk_state.last_health_at END,
          last_health_status=COALESCE(excluded.last_health_status,telephony_telphin_trunk_state.last_health_status),
          last_registered_at=CASE WHEN :registered THEN now() ELSE telephony_telphin_trunk_state.last_registered_at END,
          last_error=:err,metadata_json=telephony_telphin_trunk_state.metadata_json || excluded.metadata_json,
          updated_at=now()"""), {
            "a": item["account_id"], "en": bool(item.get("enabled")), "r": item.get("registrar"),
            "reg": item.get("registration_id"), "ep": item.get("endpoint_id"), "st": status,
            "rs": rendered_sha, "aps": applied_sha, "hs": health_status,
            "registered": bool(registered), "err": str(error or "")[:240] or None,
            "m": json.dumps(metadata or {}, ensure_ascii=False),
        })
        db.commit()
    finally:
        db.close()


def _mark_removed_accounts(current_accounts: set[str], rendered_sha: str) -> None:
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT account_id FROM telephony_telphin_trunk_state")).scalars().all()
        for account_id in rows:
            if str(account_id) not in current_accounts:
                db.execute(text("""UPDATE telephony_telphin_trunk_state SET enabled=false,status='disabled',
                  rendered_sha256=:s,last_rendered_at=now(),last_error=NULL,updated_at=now()
                  WHERE account_id=:a"""), {"a": str(account_id), "s": rendered_sha})
        db.commit()
    finally:
        db.close()


def render_telphin_pjsip_config() -> dict[str, Any]:
    """Render only explicit Telphin SIP credentials; API OAuth alone never opens a SIP listener."""
    desired = [_desired(r) for r in _provider_rows()]
    enabled = [x for x in desired if x["enabled"]]
    sections: list[str] = [
        "; GENERATED BY BORIS — DO NOT EDIT",
        "; Telphin SIP secrets are sourced from encrypted DB credentials.",
        "; File permissions are forced to 0640 root:asterisk-boris.",
    ]
    if enabled:
        sections += [
            "",
            "[transport-telphin-udp]",
            "type=transport",
            "protocol=udp",
            "bind=0.0.0.0:5064",
            "allow_reload=no",
        ]
    for item in enabled:
        line = item["line"]
        password = item["password"]
        registrar = item["registrar"]
        auth = item["auth_id"]
        aor = item["aor_id"]
        endpoint = item["endpoint_id"]
        registration = item["registration_id"]
        block = [
            "",
            f"[{auth}]",
            "type=auth",
            "auth_type=userpass",
            f"username={line}",
            f"password={password}",
            "",
            f"[{aor}]",
            "type=aor",
            f"contact=sip:{registrar}",
            "",
            f"[{endpoint}]",
            "type=endpoint",
            "transport=transport-telphin-udp",
            "context=boris-telphin-inbound",
            "disallow=all",
            "allow=alaw,ulaw",
            f"aors={aor}",
            f"outbound_auth={auth}",
            f"from_user={line}",
            f"from_domain={registrar}",
            "direct_media=no",
            "rtp_symmetric=yes",
            "force_rport=yes",
            "rewrite_contact=yes",
            "dtmf_mode=rfc4733",
            "send_pai=yes",
            "trust_id_outbound=yes",
            f"set_var=BORIS_TELPHIN_ENDPOINT={endpoint}",
        ]
        if item.get("outbound_number"):
            block.append(f"callerid={item['outbound_number']}")
        block += [
            "",
            f"[{registration}]",
            "type=registration",
            "transport=transport-telphin-udp",
            f"outbound_auth={auth}",
            f"server_uri=sip:{registrar}",
            f"client_uri=sip:{line}@{registrar}",
            f"contact_user={line}",
            "retry_interval=30",
            "forbidden_retry_interval=300",
            "fatal_retry_interval=300",
            "expiration=60",
            f"endpoint={endpoint}",
            "line=yes",
        ]
        sections.extend(block)

    content = "\n".join(sections) + "\n"
    sha = hashlib.sha256(content.encode()).hexdigest()
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    old = GENERATED_PJSIP.read_text("utf-8") if GENERATED_PJSIP.exists() else ""
    changed = old != content
    if changed:
        fd, tmp = tempfile.mkstemp(prefix=".pjsip_telphin.", dir=str(GENERATED_DIR))
        try:
            os.write(fd, content.encode())
            os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
        os.replace(tmp, GENERATED_PJSIP)
        try:
            gid = grp.getgrnam("asterisk-boris").gr_gid
            os.chown(GENERATED_PJSIP, 0, gid)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=277)
        os.chmod(GENERATED_PJSIP, 0o640)

    current_accounts = {str(x["account_id"]) for x in desired}
    for item in desired:
        status = "configured_unverified" if item["enabled"] else ("disabled" if item["reason"] == "disabled" else "waiting_credentials")
        _state_upsert(item, status=status, rendered_sha=sha,
                      error=None if item["enabled"] or item["reason"] == "disabled" else item["reason"],
                      metadata={"secret_fields_persisted_here": False})
    _mark_removed_accounts(current_accounts, sha)
    return {
        "status": "ok",
        "changed": changed,
        "sha256": sha,
        "configured": len(enabled),
        "waiting_credentials": sum(1 for x in desired if not x["enabled"] and x["reason"] != "disabled"),
        "disabled": sum(1 for x in desired if x["reason"] == "disabled"),
        "external_transport": bool(enabled),
        "accounts": [{"account_id": x["account_id"], "enabled": x["enabled"], "reason": x["reason"],
                      "registration_id": x["registration_id"], "endpoint_id": x["endpoint_id"]} for x in desired],
    }


def _cli(command: str, timeout: int = 8) -> tuple[int, str]:
    cp = subprocess.run(
        ["sudo", "-u", ASTERISK_USER, ASTERISK_BIN, "-C", ASTERISK_CONF, "-rx", command],
        timeout=timeout, check=False, capture_output=True, text=True,
    )
    return int(cp.returncode), (cp.stdout or "") + "\n" + (cp.stderr or "")


def registration_health(registration_id: str) -> dict[str, Any]:
    rid = str(registration_id or "").strip()
    if not re.fullmatch(r"telphin-reg-[a-f0-9]{16}", rid):
        return {"status": "invalid_registration_id", "ready": False}
    try:
        code, output = _cli(f"pjsip show registration {rid}")
    except Exception as exc:
        return {"status": "cli_error", "ready": False, "error_code": type(exc).__name__[:100]}
    lower = output.lower()
    if code != 0 or "unable to connect to remote asterisk" in lower:
        return {"status": "asterisk_cli_unavailable", "ready": False}
    if "unregistered" in lower:
        state = "unregistered"
    elif "rejected" in lower:
        state = "rejected"
    elif re.search(r"\bregistered\b", lower):
        state = "registered"
    elif "not found" in lower or "no objects found" in lower or "unable to find" in lower:
        state = "missing"
    else:
        state = "unknown"
    return {"status": state, "ready": state == "registered"}


def _apply_config(sha: str, external_transport: bool) -> dict[str, Any]:
    from app.services.asterisk_gateway import active_channel_count
    channels = active_channel_count()
    if channels < 0:
        return {"status": "deferred", "reason": "active_channel_count_unknown", "active_channels": channels}
    if channels > 0:
        return {"status": "deferred", "reason": "active_calls_present", "active_channels": channels}
    # Transport bind changes are not reload-safe. Restart only at the verified
    # zero-active-call boundary; the ARI bridge has its own reconnect loop.
    try:
        cp = subprocess.run(["systemctl", "restart", ASTERISK_SERVICE], timeout=20,
                            check=False, capture_output=True, text=True)
    except Exception as exc:
        return {"status": "failed", "reason": type(exc).__name__[:100]}
    if cp.returncode != 0:
        return {"status": "failed", "reason": "asterisk_restart_failed"}
    try:
        bcp = subprocess.run(["systemctl", "restart", BRIDGE_SERVICE], timeout=15,
                             check=False, capture_output=True, text=True)
        bridge_ok = bcp.returncode == 0
    except Exception:
        bridge_ok = False
    return {"status": "applied", "sha256": sha, "active_channels": channels,
            "external_transport": bool(external_transport), "bridge_restarted": bridge_ok}


def _state_rows(enabled_only: bool = False) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        where = "WHERE enabled=true" if enabled_only else ""
        rows = db.execute(text(f"""SELECT account_id,enabled,registrar,registration_id,endpoint_id,status,
          rendered_sha256,applied_sha256,last_rendered_at,last_applied_at,last_health_at,last_health_status,
          last_registered_at,last_error,metadata_json,updated_at
          FROM telephony_telphin_trunk_state {where} ORDER BY account_id""")).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def account_for_endpoint(endpoint_id: str) -> str | None:
    endpoint_id = str(endpoint_id or "").strip()
    if not re.fullmatch(r"telphin-trunk-[a-f0-9]{16}", endpoint_id):
        return None
    db = SessionLocal()
    try:
        row = db.execute(text("""SELECT account_id FROM telephony_telphin_trunk_state
          WHERE endpoint_id=:e AND enabled=true ORDER BY account_id LIMIT 1"""), {"e": endpoint_id}).first()
        return str(row[0]) if row and row[0] else None
    finally:
        db.close()


def trunk_status(account_id: str) -> dict[str, Any]:
    account_id = str(account_id or "").strip()
    rows = [r for r in _state_rows(False) if str(r.get("account_id")) == account_id]
    if not rows:
        return {"status": "not_configured", "enabled": False, "ready": False}
    row = rows[0]
    return {
        "status": row.get("status"),
        "enabled": bool(row.get("enabled")),
        "ready": bool(row.get("enabled") and row.get("status") == "registered"),
        "registrar": row.get("registrar"),
        "registration_id": row.get("registration_id"),
        "endpoint_id": row.get("endpoint_id"),
        "last_health_at": row.get("last_health_at"),
        "last_health_status": row.get("last_health_status"),
        "last_registered_at": row.get("last_registered_at"),
        "last_error": row.get("last_error"),
    }


def _pending_apply(sha: str) -> int:
    db = SessionLocal()
    try:
        return int(db.execute(text("""SELECT count(*) FROM telephony_telphin_trunk_state
          WHERE rendered_sha256=:s AND COALESCE(applied_sha256,'')<>:s"""), {"s": str(sha or "")}).scalar() or 0)
    finally:
        db.close()


def _mark_config_applied(sha: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("""UPDATE telephony_telphin_trunk_state
          SET applied_sha256=:s,last_applied_at=now(),
              status=CASE
                WHEN enabled=true THEN 'configured_unverified'
                WHEN status='disabled' THEN 'disabled'
                ELSE status
              END,
              last_error=CASE WHEN enabled=true THEN NULL ELSE last_error END,
              updated_at=now()
          WHERE rendered_sha256=:s"""), {"s": str(sha or "")})
        db.commit()
    finally:
        db.close()


def telphin_sip_guardian() -> dict[str, Any]:
    """DETECT -> render -> safe apply -> registration proof -> bounded self-heal."""
    rendered = render_telphin_pjsip_config()
    sha = str(rendered.get("sha256") or "")
    changed = bool(rendered.get("changed"))
    pending_apply = _pending_apply(sha)
    apply_result: dict[str, Any] = {"status": "not_needed"}
    if changed or pending_apply:
        apply_result = _apply_config(sha, bool(rendered.get("external_transport")))
        if apply_result.get("status") == "applied":
            _mark_config_applied(sha)
        else:
            for row in _state_rows(True):
                item = {
                    "account_id": row["account_id"], "enabled": True, "registrar": row.get("registrar"),
                    "registration_id": row.get("registration_id"), "endpoint_id": row.get("endpoint_id"),
                }
                if apply_result.get("status") == "deferred":
                    _state_upsert(item, status="apply_deferred", error=str(apply_result.get("reason") or "deferred"),
                                  metadata={"active_channels": apply_result.get("active_channels")})
                else:
                    _state_upsert(item, status="degraded", error=str(apply_result.get("reason") or "apply_failed"))
            # Old Asterisk registration state is not proof for the newly rendered
            # desired config. Do not report Registered until the exact SHA is applied.
            return {
                "status": "degraded",
                "rendered": rendered,
                "apply": apply_result,
                "apply_pending": True,
                "registered": 0,
                "unregistered": 0,
                "rejected": 0,
                "unknown": 0,
                "self_heal_requested": 0,
                "owner_action_required": False,
                "details": [
                    {"account_id": row["account_id"], "registration_id": row.get("registration_id"),
                     "status": "apply_deferred" if apply_result.get("status") == "deferred" else "degraded",
                     "ready": False}
                    for row in _state_rows(True)
                ],
            }

    registered = unregistered = rejected = unknown = healed = 0
    details = []
    for row in _state_rows(True):
        health = registration_health(str(row.get("registration_id") or ""))
        hs = str(health.get("status") or "unknown")
        if health.get("ready"):
            state = "registered"
            registered += 1
            _state_upsert(row, status=state, health_status=hs, registered=True)
        elif hs == "rejected":
            state = "rejected"
            rejected += 1
            _state_upsert(row, status=state, health_status=hs, error="telphin_registration_rejected")
        elif hs in {"unregistered", "missing"}:
            state = "unregistered"
            unregistered += 1
            _state_upsert(row, status=state, health_status=hs, error="telphin_registration_not_active")
            # Missing/unregistered may be a lost registration after restart/network
            # flap. One explicit retry is safe; rejected credentials are never brute-forced.
            try:
                code, _ = _cli(f"pjsip send register {row['registration_id']}")
                if code == 0:
                    healed += 1
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=498)
        else:
            state = "degraded"
            unknown += 1
            _state_upsert(row, status=state, health_status=hs, error="telphin_registration_health_unknown")
        details.append({"account_id": row["account_id"], "registration_id": row.get("registration_id"),
                        "status": state, "ready": bool(health.get("ready"))})

    owner_action_required = rejected > 0
    return {
        "status": "ok" if rejected == 0 and unknown == 0 else "degraded",
        "rendered": rendered,
        "apply": apply_result,
        "registered": registered,
        "unregistered": unregistered,
        "rejected": rejected,
        "unknown": unknown,
        "self_heal_requested": healed,
        "owner_action_required": owner_action_required,
        "details": details,
    }


__all__ = [
    "render_telphin_pjsip_config", "registration_health", "account_for_endpoint",
    "trunk_status", "telphin_sip_guardian",
]
