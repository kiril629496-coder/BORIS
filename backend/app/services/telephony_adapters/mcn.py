from __future__ import annotations

import hashlib
import secrets
from typing import Any

from .base import AdapterResult, TelephonyProviderAdapter


class MCNAdapter(TelephonyProviderAdapter):
    """BORIS-owned PBX transport over Asterisk + MCN SIP trunk.

    MCN is treated as PSTN/SIP carrier only. Routing, recording, CRM, calltracking
    and AI stay in BORIS. This adapter never talks to a MCN VATS API.
    """

    key = "mcn"
    capabilities = {
        "sip", "asterisk", "call_control", "recordings", "sip_media",
        "own_calltracking", "carrier_cdr",
    }
    supported_commands = {"answer", "hold", "resume", "hangup", "transfer", "dtmf"}

    def health(self, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        # credentials here are only a BORIS provider-selection marker. Actual MCN
        # SIP credentials are encrypted in telephony_trunks and never duplicated.
        from app.services.asterisk_gateway import asterisk_health, application_health, mcn_account_transport_health
        from app.services.mcn_core import readiness
        from app.services.mcn_network_guard import mcn_firewall_health
        account_id = str(public_config.get("account_id") or credentials.get("account_id") or "").strip()
        if not account_id:
            return AdapterResult(False, "configuration_required", error="MCN: account_id отсутствует в provider config")
        ah = asterisk_health()
        if not ah.get("ready"):
            return AdapterResult(False, "asterisk_unavailable", payload={"asterisk": ah})
        app = application_health()
        if not app.get("ready"):
            return AdapterResult(False, "asterisk_bridge_unavailable", payload={"asterisk": ah, "ari_application": app})
        # Carrier SIP/RTP reachability is part of provider truth. A registered
        # endpoint without the required MCN network allow-list must never make
        # the owner UI green: inbound INVITE/RTP could still be blocked.
        network = mcn_firewall_health()
        if not network.get("ready"):
            return AdapterResult(False, "mcn_network_not_ready", payload={
                "asterisk": ah,
                "ari_application": app,
                "mcn_network": network,
            })
        # Live transport is checked before DB readiness. This is important
        # after a transient carrier outage: REGISTER may already have recovered
        # while the database still contains configured_unverified. Explicit
        # health-check must heal that stale state immediately instead of waiting
        # for the minute guardian or asking the owner to press anything again.
        transport = mcn_account_transport_health(account_id, sync_db=True)
        if not transport.get("ready"):
            return AdapterResult(False, "mcn_transport_not_ready", payload={
                "asterisk": ah,
                "ari_application": app,
                "mcn_transport": transport,
            })
        ready = readiness(account_id)
        if not ready.get("ready"):
            return AdapterResult(False, "mcn_not_ready", payload={
                "asterisk": ah,
                "ari_application": app,
                "mcn_blockers": ready.get("blockers") or [],
                "mcn_transport": transport,
            })
        return AdapterResult(True, "ok", payload={
            "asterisk_version": ah.get("version"),
            "mcn_trunks": ready.get("connected_trunks"),
            "mcn_active_dids": ready.get("active_dids"),
            "mcn_transport": transport,
        })

    def make_call(self, *, to_number: str, from_number: str | None,
                  credentials: dict[str, Any], public_config: dict[str, Any],
                  metadata: dict[str, Any]) -> AdapterResult:
        from app.services.asterisk_gateway import originate_mcn
        account_id = str(metadata.get("account_id") or public_config.get("account_id") or credentials.get("account_id") or "").strip()
        if not account_id:
            return AdapterResult(False, "configuration_required", error="MCN: account_id отсутствует")
        result = originate_mcn(account_id, to_number, from_number, metadata)
        status = str(result.get("status") or "provider_error")
        ok = status == "accepted"
        return AdapterResult(
            ok,
            status,
            provider_call_id=str(result.get("provider_call_id") or "") or None,
            payload=result.get("payload") if isinstance(result.get("payload"), dict) else {
                "http_status": result.get("http_status"),
                "blockers": result.get("blockers") or [],
                "live_status": result.get("live_status"),
                "registration_state": result.get("registration_state"),
                "self_heal": result.get("self_heal"),
                "owner_action_required": bool(result.get("owner_action_required")),
            },
            error=None if ok else status,
        )

    def command(self, *, provider_call_id: str, command: str, payload: dict[str, Any],
                credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        from app.services.asterisk_gateway import command_mcn
        account_id = str(payload.get("account_id") or public_config.get("account_id") or credentials.get("account_id") or "").strip()
        if not account_id:
            return AdapterResult(False, "configuration_required", provider_call_id=provider_call_id,
                                 error="MCN: account_id отсутствует")
        result = command_mcn(account_id, provider_call_id, command, payload)
        status = str(result.get("status") or "provider_error")
        ok = status == "ok"
        command_payload = {k: result.get(k) for k in (
            "http_status", "live_status", "registration_state", "self_heal", "owner_action_required"
        ) if result.get(k) is not None}
        return AdapterResult(ok, status, provider_call_id=provider_call_id,
                             payload=command_payload,
                             error=None if ok else status)

    def media_session(self, *, device_id: str, call_id: str, credentials: dict[str, Any],
                      public_config: dict[str, Any]) -> AdapterResult:
        from app.services.phone_media_gateway import provision_webrtc_endpoint
        account_id = str(public_config.get("account_id") or credentials.get("account_id") or "").strip()
        if not account_id or not device_id or not call_id:
            return AdapterResult(False, "configuration_required", error="MCN media: account/device/call отсутствуют")
        nonce = secrets.token_hex(8)
        eid = "bp_" + hashlib.sha256(f"{account_id}|{device_id}|{call_id}|{nonce}".encode()).hexdigest()[:48]
        result = provision_webrtc_endpoint(eid, 90)
        if result.get("status") != "ok" or not isinstance(result.get("media"), dict):
            return AdapterResult(False, str(result.get("status") or "media_gateway_unavailable"),
                                 error="BORIS WebPhone endpoint не создан")
        return AdapterResult(True, "ok", payload=dict(result["media"]))

    def normalize_webhook(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        # MCN carrier events are consumed from Asterisk ARI/CDR, not public carrier webhooks.
        raise ValueError("MCN events are accepted only from the local Asterisk ARI bridge")
