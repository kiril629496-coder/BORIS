from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from app.services.exception_observability import observe_suppressed


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _configuration() -> dict[str, str]:
    return {
        "ari_url": _env("BORIS_MEDIA_ARI_URL").rstrip("/"),
        "ari_user": _env("BORIS_MEDIA_ARI_USER"),
        "ari_password": _env("BORIS_MEDIA_ARI_PASSWORD"),
        "wss_url": _env("BORIS_MEDIA_WSS_URL"),
        "sip_domain": _env("BORIS_MEDIA_SIP_DOMAIN"),
        "turn_urls": _env("BORIS_MEDIA_TURN_URLS"),
        "turn_secret": _env("BORIS_MEDIA_TURN_SECRET"),
    }


def _safe_wss(value: str) -> bool:
    try:
        p = urlparse(str(value or "").strip())
    except Exception:
        return False
    return bool(p.scheme == "wss" and p.hostname and not p.username and not p.password and len(value) <= 2048)


def _auth_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _request(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 4.0) -> tuple[int, Any]:
    cfg = _configuration()
    if not cfg["ari_url"] or not cfg["ari_user"] or not cfg["ari_password"]:
        raise RuntimeError("media_gateway_not_configured")
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": _auth_header(cfg["ari_user"], cfg["ari_password"]),
        "Accept": "application/json",
        "User-Agent": "BORIS-Phone-Media/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(cfg["ari_url"] + path, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read(262144)
    except HTTPError as exc:
        status = int(exc.code or 500)
        raw = exc.read(262144)
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("media_gateway_unavailable") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        payload = {}
    return status, payload


def gateway_health() -> dict[str, Any]:
    cfg = _configuration()
    binary_installed = bool(shutil.which("asterisk"))
    turn_installed = bool(shutil.which("turnserver"))
    wss_valid = _safe_wss(cfg["wss_url"])
    configured = bool(cfg["ari_url"] and cfg["ari_user"] and cfg["ari_password"] and wss_valid)
    out: dict[str, Any] = {
        "status": "not_configured",
        "binary_installed": binary_installed,
        "turn_installed": turn_installed,
        "configured": configured,
        "runtime_ready": False,
        "wss_configured": wss_valid,
        "turn_configured": bool(cfg["turn_urls"] and cfg["turn_secret"]),
    }
    if not configured:
        return out
    try:
        status, payload = _request("GET", "/asterisk/ping", timeout=2.0)
    except RuntimeError:
        out["status"] = "unavailable"
        return out
    ready = bool(200 <= status < 300 and isinstance(payload, dict) and str(payload.get("ping") or "").lower() == "pong")
    out.update({"status": "ok" if ready else "unhealthy", "runtime_ready": ready, "http_status": status})
    return out



def webrtc_endpoint_health(endpoint_id: str) -> dict[str, Any]:
    """Live registration state of one short-lived BORIS WebPhone endpoint."""
    try:
        eid=_endpoint_id(endpoint_id)
    except ValueError:
        return {"status":"invalid_endpoint","ready":False}
    try:
        code,payload=_request("GET",f"/endpoints/PJSIP/{quote(eid,safe='')}",timeout=2.0)
    except RuntimeError:
        return {"status":"unavailable","ready":False}
    if code==404:
        return {"status":"missing","ready":False,"http_status":404}
    state=str((payload or {}).get("state") or "").strip().lower() if isinstance(payload,dict) else ""
    ready=bool(200<=code<300 and state=="online")
    return {"status":"ok" if ready else (state or "not_registered"),"ready":ready,"state":state or None,"http_status":code}


def _turn_ice_servers(ttl_seconds: int, identity: str="boris-phone") -> list[dict[str, Any]]:
    """Mint one bounded TURN REST credential for this BORIS media endpoint.

    TURN allocations are refreshed independently from the short BORIS ownership
    lease, so a 90-180s TURN username would break otherwise healthy long calls.
    The browser rotates the whole BORIS-managed endpoint before this credential
    expires; the shared server secret never leaves the backend.
    """
    cfg = _configuration()
    raw_urls = cfg["turn_urls"]
    secret = cfg["turn_secret"]
    if not raw_urls or not secret:
        return []
    try:
        parsed = json.loads(raw_urls)
        urls = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        urls = [x.strip() for x in raw_urls.split(",") if x.strip()]
    clean = []
    for value in urls[:8]:
        u = str(value or "").strip()
        if u.startswith(("turn:", "turns:", "stun:", "stuns:")) and len(u) <= 1024:
            clean.append(u)
    if not clean:
        return []
    try:
        configured_ttl=int(_env("BORIS_MEDIA_TURN_CREDENTIAL_TTL_SECONDS") or "3600")
    except Exception:
        configured_ttl=3600
    turn_ttl=max(900,min(configured_ttl,7200))
    expiry = int(time.time()) + turn_ttl
    label="boris-phone-"+hashlib.sha256(str(identity or "boris-phone").encode("utf-8")).hexdigest()[:16]
    username = f"{expiry}:{label}"
    credential = base64.b64encode(hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()).decode("ascii")
    return [{"urls": clean, "username": username, "credential": credential}]



def _turn_target() -> tuple[str | None, int | None]:
    """Return a safe host/port for TURN health probes without credential material."""
    raw=_configuration().get("turn_urls") or ""
    try:
        parsed=json.loads(raw)
        values=parsed if isinstance(parsed,list) else [parsed]
    except Exception:
        values=[x.strip() for x in raw.split(",") if x.strip()]
    for value in values[:8]:
        raw=str(value or "").strip()
        if not raw.startswith(("turn:","turns:")):
            continue
        scheme,rest=raw.split(":",1)
        authority=rest.split("?",1)[0].strip()
        if not authority or "@" in authority:
            continue
        try:
            p=urlparse("//"+authority)
            host=p.hostname
            port=int(p.port or (5349 if scheme=="turns" else 3478))
        except Exception:
            continue
        if host and 1<=port<=65535:
            return host,port
    return None,None


def _turn_tls_target() -> tuple[str | None, int | None]:
    """Return the first configured TURN-over-TLS endpoint."""
    raw=_configuration().get("turn_urls") or ""
    try:
        parsed=json.loads(raw)
        values=parsed if isinstance(parsed,list) else [parsed]
    except Exception:
        values=[x.strip() for x in raw.split(",") if x.strip()]
    for value in values[:8]:
        item=str(value or "").strip()
        if not item.startswith("turns:"):
            continue
        authority=item.split(":",1)[1].split("?",1)[0].strip()
        if not authority or "@" in authority:
            continue
        try:
            parsed_url=urlparse("//"+authority)
            host=parsed_url.hostname
            port=int(parsed_url.port or 5349)
        except Exception:
            continue
        if host and 1 <= port <= 65535:
            return host,port
    return None,None


def _turn_tls_probe(host: str | None=None, port: int | None=None) -> bool | None:
    """Validate the externally served TURN TLS certificate.

    This is a fallback for read-only/non-root diagnostics that cannot read the
    root-owned runtime/Let's Encrypt files. System trust and hostname validation
    stay enabled; no insecure context is used.
    """
    target_host,target_port=_turn_tls_target()
    host=host or target_host
    port=port or target_port
    if not host or not port:
        return None
    try:
        ctx=ssl.create_default_context()
        with socket.create_connection((host,int(port)),timeout=4) as raw_sock:
            with ctx.wrap_socket(raw_sock,server_hostname=host):
                return True
    except Exception:
        return False


def _turn_service_active() -> bool:
    try:
        cp=subprocess.run(["systemctl","is-active","--quiet","coturn.service"],timeout=3,check=False,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return cp.returncode==0
    except Exception:
        return False


def _turn_stun_probe(host: str | None=None, port: int | None=None) -> bool:
    target_host=host or _turn_target()[0]
    probe_host=_env("BORIS_MEDIA_TURN_HEALTH_HOST") or "127.0.0.1"
    port=port or _turn_target()[1]
    if not target_host or not probe_host or not port or not shutil.which("turnutils_stunclient"):
        return False
    try:
        cp=subprocess.run(["turnutils_stunclient","-p",str(int(port)),str(probe_host)],timeout=4,check=False,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return cp.returncode==0
    except Exception:
        return False


def _file_sha256(path: Path) -> str | None:
    try:
        h=hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda:f.read(131072),b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def turn_runtime_status(probe: bool=False) -> dict[str,Any]:
    """Read-only TURN status. No restart, no secret values, no credential minting."""
    cfg=_configuration()
    configured=bool(cfg.get("turn_urls") and cfg.get("turn_secret"))
    host,port=_turn_target()
    runtime_cert=Path(_env("BORIS_MEDIA_TURN_RUNTIME_CERT") or "/run/boris-turn/fullchain.pem")
    source_cert=Path(_env("BORIS_MEDIA_TURN_SOURCE_CERT") or "/etc/letsencrypt/live/boris-ai.pro/fullchain.pem")
    active=_turn_service_active() if configured else False
    runtime_hash=_file_sha256(runtime_cert) if configured else None
    source_hash=_file_sha256(source_cert) if configured else None
    file_evidence_available=bool(runtime_hash or source_hash)
    tls_ok=(
        _turn_tls_probe()
        if configured and active and not file_evidence_available
        else None
    )
    if file_evidence_available:
        cert_present=bool(runtime_hash)
        cert_current=bool(
            runtime_hash and source_hash
            and hmac.compare_digest(runtime_hash,source_hash)
        )
        cert_evidence="file_hash"
    else:
        cert_present=tls_ok is True
        cert_current=tls_ok is True
        cert_evidence="live_tls" if tls_ok is not None else "unavailable"
    stun_ok=_turn_stun_probe(host,port) if configured and active and probe else None
    ready=bool(configured and active and host and port and cert_present and cert_current and (stun_ok is not False))
    return {
        "configured":configured,"service_active":active,"host_configured":bool(host),"port":port,
        "certificate_present":cert_present,"certificate_current":cert_current,
        "certificate_evidence":cert_evidence,"tls_probe_ok":tls_ok,
        "stun_probe_ok":stun_ok,"runtime_ready":ready,
        "status":"ok" if ready else ("not_configured" if not configured else "degraded"),
    }


def turn_runtime_guardian(active_media_sessions: int=0) -> dict[str,Any]:
    """DETECT -> DIAGNOSE -> SELF-HEAL -> VERIFY for Coturn.

    A broken service is restarted immediately. A certificate-only refresh is
    deferred while live BORIS media exists, avoiding avoidable call disruption.
    """
    before=turn_runtime_status(probe=True)
    if not before.get("configured"):
        return {**before,"action":"none","recovered":False,"deferred":False,"owner_action_required":False}
    active_media=max(0,int(active_media_sessions or 0))
    reason=None
    if not before.get("service_active"):
        reason="service_inactive"
    elif before.get("stun_probe_ok") is not True:
        reason="stun_probe_failed"
    elif not before.get("certificate_present"):
        reason="runtime_certificate_missing"
    elif not before.get("certificate_current"):
        reason="certificate_refresh_required"
    if not reason:
        return {**before,"action":"none","recovered":False,"deferred":False,"owner_action_required":False}

    certificate_only=reason=="certificate_refresh_required"
    if certificate_only and active_media>0:
        return {**before,"action":"deferred","reason":reason,"active_media_sessions":active_media,
                "recovered":False,"deferred":True,"owner_action_required":False}

    try:
        cp=subprocess.run(["systemctl","restart","coturn.service"],timeout=20,check=False,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        restart_ok=cp.returncode==0
    except Exception:
        restart_ok=False
    after=turn_runtime_status(probe=True) if restart_ok else turn_runtime_status(probe=False)
    recovered=bool(restart_ok and after.get("runtime_ready") and after.get("stun_probe_ok") is True and after.get("certificate_current"))
    return {**after,"action":"restart","reason":reason,"active_media_sessions":active_media,
            "restart_ok":restart_ok,"recovered":recovered,"deferred":False,
            "owner_action_required":not recovered}

def _endpoint_id(value: str) -> str:
    v = str(value or "").strip().lower()
    if not (4 <= len(v) <= 64 and v.startswith("bp_") and all(c.isalnum() or c == "_" for c in v)):
        raise ValueError("invalid_media_endpoint_id")
    return v


def provision_webrtc_endpoint(endpoint_id: str, ttl_seconds: int = 90) -> dict[str, Any]:
    """Create one temporary browser SIP/WebRTC endpoint in local Asterisk.

    Long-lived provider credentials never cross this boundary. The returned
    password is newly generated and is not persisted by this module.
    """
    eid = _endpoint_id(endpoint_id)
    ttl = max(30, min(int(ttl_seconds or 90), 180))
    cfg = _configuration()
    health = gateway_health()
    if not health.get("runtime_ready"):
        return {"status": "media_gateway_unavailable", "health": health}
    if not _safe_wss(cfg["wss_url"]):
        return {"status": "media_gateway_wss_invalid"}

    password = secrets.token_urlsafe(30)
    base = "/asterisk/config/dynamic/res_pjsip"
    auth = {"fields": [
        {"attribute": "auth_type", "value": "userpass"},
        {"attribute": "username", "value": eid},
        {"attribute": "password", "value": password},
    ]}
    aor = {"fields": [
        {"attribute": "max_contacts", "value": "1"},
        {"attribute": "remove_existing", "value": "yes"},
        {"attribute": "support_path", "value": "yes"},
        {"attribute": "minimum_expiration", "value": "30"},
        {"attribute": "maximum_expiration", "value": str(max(60, ttl))},
        {"attribute": "default_expiration", "value": str(max(60, ttl))},
    ]}
    endpoint = {"fields": [
        {"attribute": "aors", "value": eid},
        {"attribute": "auth", "value": eid},
        {"attribute": "context", "value": "boris-webrtc-client"},
        {"attribute": "disallow", "value": "all"},
        {"attribute": "allow", "value": "ulaw,alaw"},
        {"attribute": "webrtc", "value": "yes"},
        {"attribute": "force_rport", "value": "yes"},
        {"attribute": "rewrite_contact", "value": "yes"},
        {"attribute": "rtp_symmetric", "value": "yes"},
        {"attribute": "direct_media", "value": "no"},
        {"attribute": "media_use_received_transport", "value": "yes"},
        {"attribute": "transport", "value": "transport-ws"},
    ]}
    created: list[str] = []
    try:
        for kind, body in (("auth", auth), ("aor", aor), ("endpoint", endpoint)):
            status, _ = _request("PUT", f"{base}/{kind}/{eid}", body)
            if not 200 <= status < 300:
                raise RuntimeError(f"media_gateway_create_{kind}_failed")
            created.append(kind)
    except Exception:
        for kind in reversed(created):
            try:
                _request("DELETE", f"{base}/{kind}/{eid}")
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=410)
        return {"status": "media_gateway_provision_failed"}

    now = datetime.now(timezone.utc)
    domain = cfg["sip_domain"] or (urlparse(cfg["wss_url"]).hostname or "boris-ai.pro")
    return {
        "status": "ok",
        "endpoint_id": eid,
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "media": {
            "transport": "sip_ws",
            "managed_by": "boris_gateway",
            "endpoint_id": eid,
            "signaling_protocol": "boris-sip-v1",
            "signaling_url": cfg["wss_url"],
            "session_id": eid,
            "session_token": password,
            "authorization_username": eid,
            "sip_uri": f"sip:{eid}@{domain}",
            "register_expires_seconds": ttl,
            "ttl_seconds": ttl,
            "ice_servers": _turn_ice_servers(ttl, eid),
        },
    }


def delete_webrtc_endpoint(endpoint_id: str) -> dict[str, Any]:
    eid = _endpoint_id(endpoint_id)
    base = "/asterisk/config/dynamic/res_pjsip"
    errors: list[str] = []
    for kind in ("endpoint", "aor", "auth"):
        try:
            status, _ = _request("DELETE", f"{base}/{kind}/{eid}")
            if status not in {200, 204, 404}:
                errors.append(kind)
        except RuntimeError:
            errors.append(kind)
    return {"status": "ok" if not errors else "cleanup_pending", "endpoint_id": eid, "errors": errors}
