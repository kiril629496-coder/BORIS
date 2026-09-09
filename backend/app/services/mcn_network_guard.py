from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal

STATE_PATH = Path("/var/lib/asterisk-boris/generated/mcn_firewall_state.json")
LOCK_PATH = Path("/var/lib/asterisk-boris/generated/mcn_firewall.lock")
RTP_CONF = Path("/etc/asterisk-boris/rtp.conf")
UFW_BIN = "/usr/sbin/ufw"


def _is_synthetic(account_id: str) -> bool:
    v = str(account_id or "").strip().lower()
    return bool(re.match(r"^__.*qa", v) or re.match(r"^qa[-_]", v))


def _canonical_ipv4_network(value: str) -> str | None:
    try:
        net = ipaddress.ip_network(str(value or "").strip(), strict=False)
    except ValueError:
        return None
    if net.version != 4:
        return None
    return str(net)


def _registrar_host(value: str) -> str | None:
    """Extract one registrar host without trusting arbitrary URI fragments."""
    raw=str(value or "").strip()
    if not raw:
        return None
    raw=re.sub(r"^sips?:","",raw,flags=re.I)
    raw=raw.split(";",1)[0].strip().strip("/")
    if "@" in raw:
        raw=raw.rsplit("@",1)[1]
    if raw.startswith("["):
        return None  # IPv6 is intentionally unsupported by this IPv4 firewall guard.
    if raw.count(":")==1:
        host,port=raw.rsplit(":",1)
        if port.isdigit():
            raw=host
    host=raw.strip().rstrip(".").lower()
    return host or None


def _resolve_mcn_registrar_networks(value: str) -> list[str]:
    """Resolve a configured MCN registrar to narrow IPv4 /32 firewall sources.

    Automatic DNS-derived firewall entries are restricted to mcn.ru hosts or an
    explicit IPv4 registrar. Custom/non-MCN hostnames must provide source_ips.
    """
    host=_registrar_host(value)
    if not host:
        return []
    ipnet=_canonical_ipv4_network(host)
    if ipnet:
        try:
            net=ipaddress.ip_network(ipnet,strict=False)
            return [f"{net.network_address}/32"] if net.prefixlen==32 else [str(net)]
        except Exception:
            return []
    if not (host=="mcn.ru" or host.endswith(".mcn.ru")):
        return []
    try:
        cp=subprocess.run(["/usr/bin/getent","ahostsv4",host],
                          capture_output=True,text=True,timeout=3,check=False)
    except Exception:
        return []
    out=set()
    if cp.returncode==0:
        for line in (cp.stdout or "").splitlines():
            first=(line.split() or [""])[0]
            net=_canonical_ipv4_network(first)
            if net:
                try:
                    addr=ipaddress.ip_network(net,strict=False)
                    out.add(f"{addr.network_address}/32")
                except Exception:
                    pass
    return sorted(out)


def _desired_mcn_network_state(include_synthetic: bool = False) -> dict[str, Any]:
    db=SessionLocal()
    try:
        rows=db.execute(text("""
        SELECT t.account_id, t.source_ips, t.registrar, t.outbound_proxy
        FROM telephony_trunks t
        WHERE t.provider='mcn' AND t.enabled=true
          AND t.status IN ('configured_unverified','connected')
          AND EXISTS (
              SELECT 1 FROM telephony_entitlements e
              WHERE e.account_id=t.account_id AND e.enabled=true AND e.paid_until>now()
          )
        ORDER BY t.account_id,t.id
        """)).mappings().all()
    finally:
        db.close()

    networks:set[str]=set()
    unresolved:list[dict[str,str]]=[]
    active_real_trunks=0
    for row in rows:
        account_id=str(row.get("account_id") or "")
        if not include_synthetic and _is_synthetic(account_id):
            continue
        if not _is_synthetic(account_id):
            active_real_trunks += 1
        manual=[]
        for raw in (row.get("source_ips") or []):
            net=_canonical_ipv4_network(str(raw))
            if net:
                manual.append(net)
                networks.add(net)
        resolved:set[str]=set()
        for key in ("registrar","outbound_proxy"):
            raw=str(row.get(key) or "").strip()
            if not raw:
                continue
            for net in _resolve_mcn_registrar_networks(raw):
                resolved.add(net)
                networks.add(net)
        # A manual source list is an explicit operator/owner override. Without
        # it, a configured registrar must resolve or firewall readiness is false.
        if not manual and str(row.get("registrar") or "").strip() and not resolved:
            unresolved.append({
                "account_id":account_id,
                "registrar":_registrar_host(str(row.get("registrar") or "")) or "invalid",
            })
    return {
        "networks":sorted(networks),
        "unresolved_registrars":unresolved,
        "active_real_trunks":active_real_trunks,
    }


def _sip_port() -> int:
    raw = str(os.getenv("BORIS_MCN_SIP_BIND") or "0.0.0.0:5064").strip()
    m = re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}:(\d{1,5})", raw)
    if not m:
        raise ValueError("BORIS_MCN_SIP_BIND invalid")
    port = int(m.group(1))
    if not (1 <= port <= 65535):
        raise ValueError("BORIS_MCN_SIP_BIND port invalid")
    return port


def _rtp_range() -> tuple[int, int]:
    start = end = None
    if RTP_CONF.exists():
        for raw in RTP_CONF.read_text("utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith(";") or line.startswith("#") or "=" not in line:
                continue
            key, value = [x.strip() for x in line.split("=", 1)]
            if key == "rtpstart" and value.isdigit():
                start = int(value)
            elif key == "rtpend" and value.isdigit():
                end = int(value)
    if start is None or end is None:
        start, end = 10000, 10999
    if not (1024 <= int(start) <= int(end) <= 65535):
        raise ValueError("Asterisk RTP range invalid")
    return int(start), int(end)


def desired_mcn_networks(include_synthetic: bool = False) -> list[str]:
    return list(_desired_mcn_network_state(include_synthetic).get("networks") or [])


def _read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_state_write(payload: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mcn_firewall.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _rule_args(action: str, source: str, port_spec: str, label: str | None = None) -> list[str]:
    if action not in {"allow", "delete"}:
        raise ValueError("invalid firewall action")
    args = [UFW_BIN]
    if action == "delete":
        args += ["--force", "delete", "allow"]
    else:
        args += ["allow"]
    args += ["from", source, "to", "any", "port", port_spec, "proto", "udp"]
    if action == "allow" and label:
        args += ["comment", label]
    return args


def firewall_plan(
    desired: list[str],
    applied: list[str],
    sip_port: int,
    rtp_start: int,
    rtp_end: int,
    *,
    rtp_any_required: bool = False,
    rtp_any_applied: bool = False,
    legacy_source_rtp: bool = False,
) -> list[dict[str, Any]]:
    """Build a convergent MCN firewall plan.

    SIP signalling stays source-restricted to resolved/configured MCN networks.
    RTP is separate because MCN documents the SIP proxy but does not publish one
    fixed media-source list. Asterisk protects the dedicated RTP range with
    Strict RTP, replay protection, direct_media=no and symmetric RTP. While at
    least one real MCN trunk is enabled, IPv4 RTP is allowed to that dedicated
    range from any source. When the last real trunk is removed, the rule is
    deleted automatically.

    legacy_source_rtp migrates older state where RTP rules were paired with each
    SIP source network.
    """
    want = sorted(set(desired))
    have = sorted(set(applied))
    actions: list[dict[str, Any]] = []
    for src in sorted(set(have) - set(want)):
        actions.append({"action": "delete", "source": src, "kind": "sip",
                        "argv": _rule_args("delete", src, str(sip_port))})
    for src in sorted(set(want) - set(have)):
        actions.append({"action": "allow", "source": src, "kind": "sip",
                        "argv": _rule_args("allow", src, str(sip_port), "BORIS MCN SIP")})

    if legacy_source_rtp:
        for src in have:
            actions.append({"action": "delete", "source": src, "kind": "rtp_legacy",
                            "argv": _rule_args("delete", src, f"{rtp_start}:{rtp_end}")})

    rtp_source = "0.0.0.0/0"
    if rtp_any_required and not rtp_any_applied:
        actions.append({"action": "allow", "source": rtp_source, "kind": "rtp_any",
                        "argv": _rule_args("allow", rtp_source, f"{rtp_start}:{rtp_end}", "BORIS MCN RTP Strict")})
    elif rtp_any_applied and not rtp_any_required:
        actions.append({"action": "delete", "source": rtp_source, "kind": "rtp_any",
                        "argv": _rule_args("delete", rtp_source, f"{rtp_start}:{rtp_end}")})
    return actions


def _ufw_active() -> bool:
    try:
        cp = subprocess.run([UFW_BIN, "status"], capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return False
    return cp.returncode == 0 and "Status: active" in (cp.stdout or "")


def mcn_firewall_health() -> dict[str, Any]:
    intent=_desired_mcn_network_state(False)
    desired=list(intent.get("networks") or [])
    unresolved=list(intent.get("unresolved_registrars") or [])
    rtp_any_required=int(intent.get("active_real_trunks") or 0) > 0
    state = _read_state()
    applied = [x for x in (state.get("networks") or []) if _canonical_ipv4_network(str(x))]
    rtp_any_applied=bool(state.get("rtp_any"))
    legacy_source_rtp=bool(state.get("legacy_source_rtp_pending")) or ("rtp_any" not in state and bool(applied))
    migrate_legacy_source_rtp=bool(legacy_source_rtp and not unresolved)
    sip = _sip_port()
    rs, re_ = _rtp_range()
    # DNS failure must never make BORIS delete the last-known-good MCN
    # sources. Preserve already-applied rules until registrar resolution
    # recovers; readiness still stays false so we do not claim a healthy link.
    effective_desired=sorted(set(desired) | (set(applied) if unresolved else set()))
    pending = firewall_plan(
        effective_desired, applied, sip, rs, re_,
        rtp_any_required=rtp_any_required,
        rtp_any_applied=rtp_any_applied,
        legacy_source_rtp=migrate_legacy_source_rtp,
    )
    ready=not pending and not unresolved
    return {
        "status": "ok" if ready else ("registrar_resolution_failed" if unresolved else "pending"),
        "ready": ready,
        "automatic": str(os.getenv("BORIS_MCN_FIREWALL_AUTOMATIC") or "0").strip().lower() in {"1","true","yes","on"},
        "ufw_active": _ufw_active(),
        "desired_networks": desired,
        "unresolved_registrars": unresolved,
        "applied_networks": sorted(set(applied)),
        "sip_port": sip,
        "rtp_range": [rs, re_],
        "rtp_any_required": rtp_any_required,
        "rtp_any_applied": rtp_any_applied,
        "legacy_source_rtp": legacy_source_rtp,
        "legacy_source_rtp_migration_pending": bool(legacy_source_rtp and not migrate_legacy_source_rtp),
        "pending_actions": len(pending),
    }


def _sync_mcn_firewall_unlocked(dry_run: bool = False) -> dict[str, Any]:
    intent=_desired_mcn_network_state(False)
    desired=list(intent.get("networks") or [])
    unresolved=list(intent.get("unresolved_registrars") or [])
    rtp_any_required=int(intent.get("active_real_trunks") or 0) > 0
    state = _read_state()
    applied = [x for x in (state.get("networks") or []) if _canonical_ipv4_network(str(x))]
    rtp_any_applied=bool(state.get("rtp_any"))
    legacy_source_rtp=bool(state.get("legacy_source_rtp_pending")) or ("rtp_any" not in state and bool(applied))
    migrate_legacy_source_rtp=bool(legacy_source_rtp and not unresolved)
    sip = _sip_port()
    rs, re_ = _rtp_range()
    # Keep last-known-good firewall sources during a transient registrar
    # DNS failure. We may add newly resolved sources, but never remove old MCN
    # rules until all configured registrars resolve again.
    effective_desired=sorted(set(desired) | (set(applied) if unresolved else set()))
    plan = firewall_plan(
        effective_desired, applied, sip, rs, re_,
        rtp_any_required=rtp_any_required,
        rtp_any_applied=rtp_any_applied,
        legacy_source_rtp=migrate_legacy_source_rtp,
    )
    automatic = str(os.getenv("BORIS_MCN_FIREWALL_AUTOMATIC") or "0").strip().lower() in {"1","true","yes","on"}

    if dry_run:
        return {
            "status": "dry_run",
            "ready": not plan and not unresolved,
            "automatic": automatic,
            "desired_networks": desired,
            "unresolved_registrars": unresolved,
            "applied_networks": sorted(set(applied)),
            "sip_port": sip,
            "rtp_range": [rs, re_],
            "rtp_any_required": rtp_any_required,
            "rtp_any_applied": rtp_any_applied,
            "legacy_source_rtp": legacy_source_rtp,
            "legacy_source_rtp_migration_pending": bool(legacy_source_rtp and not migrate_legacy_source_rtp),
            "actions": [{k:v for k,v in x.items() if k != "argv"} for x in plan],
        }

    if plan and not automatic:
        return {
            "status": "automatic_firewall_disabled",
            "ready": False,
            "automatic": False,
            "desired_networks": desired,
            "unresolved_registrars": unresolved,
            "applied_networks": sorted(set(applied)),
            "rtp_any_required": rtp_any_required,
            "rtp_any_applied": rtp_any_applied,
            "pending_actions": len(plan),
            "owner_action_required": False,
        }
    if not _ufw_active():
        return {
            "status": "ufw_not_active",
            "ready": False if (desired or unresolved or rtp_any_required) else True,
            "automatic": automatic,
            "desired_networks": desired,
            "unresolved_registrars": unresolved,
            "applied_networks": sorted(set(applied)),
            "rtp_any_required": rtp_any_required,
            "rtp_any_applied": rtp_any_applied,
            "pending_actions": len(plan),
            "owner_action_required": False,
        }

    executed: list[dict[str, Any]] = []
    for item in plan:
        cp = subprocess.run(item["argv"], capture_output=True, text=True, timeout=15, check=False)
        executed.append({
            "action": item["action"],
            "source": item["source"],
            "kind": item["kind"],
            "returncode": int(cp.returncode),
        })
        if cp.returncode != 0:
            return {
                "status": "firewall_apply_failed",
                "ready": False,
                "automatic": automatic,
                "desired_networks": desired,
                "unresolved_registrars": unresolved,
                "applied_networks": sorted(set(applied)),
                "rtp_any_required": rtp_any_required,
                "rtp_any_applied": rtp_any_applied,
                "executed": executed,
                "error_code": "ufw_command_failed",
                "owner_action_required": False,
            }

    _atomic_state_write({
        "version": 2,
        "networks": effective_desired,
        "rtp_any": rtp_any_required,
        "legacy_source_rtp_pending": bool(legacy_source_rtp and not migrate_legacy_source_rtp),
        "sip_port": sip,
        "rtp_start": rs,
        "rtp_end": re_,
    })
    return {
        "status": "registrar_resolution_failed" if unresolved else "ok",
        "ready": not unresolved,
        "automatic": automatic,
        "desired_networks": desired,
        "unresolved_registrars": unresolved,
        "preserved_last_known_good": bool(unresolved and set(applied)),
        "applied_networks": effective_desired,
        "sip_port": sip,
        "rtp_range": [rs, re_],
        "rtp_any_required": rtp_any_required,
        "rtp_any_applied": rtp_any_required,
        "legacy_source_rtp_migrated": migrate_legacy_source_rtp,
        "legacy_source_rtp_migration_pending": bool(legacy_source_rtp and not migrate_legacy_source_rtp),
        "executed": executed,
        "owner_action_required": False,
    }


def sync_mcn_firewall(dry_run: bool = False) -> dict[str, Any]:
    # Dry-run is read-only and does not need to serialize UFW changes. Real
    # applies are protected across the runtime guardian, systemd guardian and
    # onboarding requests so two processes cannot create duplicate UFW rules.
    if dry_run:
        return _sync_mcn_firewall_unlocked(dry_run=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_fh:
        try:
            os.chmod(LOCK_PATH, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            return _sync_mcn_firewall_unlocked(dry_run=False)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


__all__ = [
    "desired_mcn_networks",
    "firewall_plan",
    "mcn_firewall_health",
    "sync_mcn_firewall",
]
