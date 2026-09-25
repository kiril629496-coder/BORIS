from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import AdapterResult, TelephonyProviderAdapter


class TelphinAdapter(TelephonyProviderAdapter):
    """Telphin Office REST adapter using trusted-app OAuth2.

    Only contracts confirmed by Telphin REST documentation are advertised:
    read-only health, callback initiation and active-call control. Provider
    media/WebRTC and native event ingress remain separate fail-closed gates.
    """

    key = "telphin"
    capabilities = {"api", "outbound_callback", "call_control", "media_session", "sip_media", "webrtc"}
    supported_commands = {"answer", "hold", "resume", "hangup", "transfer"}
    primary_origin = "https://apiproxy.telphin.ru"
    fallback_origin = "https://altapi.telphin.ru"

    @staticmethod
    def _keys(credentials: dict[str, Any]) -> tuple[str, str]:
        client_id = str(credentials.get("client_id") or credentials.get("app_id") or "").strip()
        client_secret = str(credentials.get("client_secret") or credentials.get("app_secret") or "").strip()
        return client_id, client_secret

    @classmethod
    def _open(cls, req: Request, *, read_only: bool = False) -> tuple[int, bytes]:
        """Use reserve host only for safe/read-only network failures.

        Side-effecting callback/control calls are deliberately not replayed after
        an ambiguous timeout: a lost HTTP response must never create a duplicate
        paid call or repeat a control command.
        """
        try:
            with urlopen(req, timeout=12) as resp:
                return int(getattr(resp, "status", 200) or 200), resp.read(262144)
        except HTTPError as exc:
            return int(exc.code or 500), exc.read(262144)
        except (URLError, TimeoutError) as exc:
            if not read_only:
                raise RuntimeError("Telphin API transport result is ambiguous") from exc
            fallback_url = req.full_url.replace(cls.primary_origin, cls.fallback_origin, 1)
            fallback = Request(fallback_url, data=req.data, method=req.get_method(), headers=dict(req.header_items()))
            try:
                with urlopen(fallback, timeout=12) as resp:
                    return int(getattr(resp, "status", 200) or 200), resp.read(262144)
            except HTTPError as e2:
                return int(e2.code or 500), e2.read(262144)
            except (URLError, TimeoutError) as e2:
                raise RuntimeError("Telphin API недоступен по основному и резервному адресу") from e2

    @classmethod
    def _token(cls, credentials: dict[str, Any]) -> str:
        client_id, client_secret = cls._keys(credentials)
        if not client_id or not client_secret:
            raise ValueError("Telphin: нужны client_id и client_secret trusted-приложения")
        body = urlencode({"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}).encode("utf-8")
        req = Request(cls.primary_origin + "/oauth/token", data=body, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "BORIS-Phone/1.0"
        })
        status, raw = cls._open(req, read_only=True)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        token = str(data.get("access_token") or "").strip() if isinstance(data, dict) else ""
        if not (200 <= status < 300 and token):
            raise ValueError("Telphin OAuth credentials rejected")
        return token

    @classmethod
    def _api(cls, method: str, path: str, credentials: dict[str, Any], body: dict[str, Any] | None = None, *, read_only: bool = False) -> tuple[int, dict[str, Any]]:
        token = cls._token(credentials)
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "BORIS-Phone/1.0"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = Request(cls.primary_origin + path, data=payload, method=method, headers=headers)
        status, raw = cls._open(req, read_only=read_only)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        return status, data if isinstance(data, dict) else {"data": data}

    @staticmethod
    def _extension(public_config: dict[str, Any]) -> str:
        value = str(public_config.get("extension_id") or "").strip()
        return value if value.isdigit() else ""

    @staticmethod
    def _account_id(public_config: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
        return str((metadata or {}).get("account_id") or public_config.get("__boris_account_id") or "").strip()

    @staticmethod
    def _sip_configured(credentials: dict[str, Any], public_config: dict[str, Any]) -> bool:
        return bool(str(credentials.get("sip_line") or "").strip() and str(credentials.get("sip_password") or "")) or public_config.get("sip_enabled") is True

    def health(self, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        try:
            status, data = self._api("GET", "/api/ver1.0/user/", credentials, read_only=True)
        except ValueError as exc:
            return AdapterResult(False, "credentials_invalid", error=str(exc))
        except RuntimeError as exc:
            return AdapterResult(False, "provider_unavailable", error=str(exc))
        ok = 200 <= status < 300 and data.get("id") is not None
        return AdapterResult(ok, "ok" if ok else "provider_rejected", payload={
            "http_status": status, "client_id_present": data.get("client_id") is not None,
            "extension_id_present": data.get("extension_id") is not None,
        }, error=None if ok else f"Telphin health HTTP {status}")

    def make_call(self, *, to_number: str, from_number: str | None, credentials: dict[str, Any], public_config: dict[str, Any], metadata: dict[str, Any]) -> AdapterResult:
        account_id = self._account_id(public_config, metadata)
        if self._sip_configured(credentials, public_config):
            if not account_id:
                return AdapterResult(False, "configuration_required", error="BORIS account identity отсутствует для Telphin SIP")
            from app.services.telphin_sip_trunk import trunk_status
            trunk = trunk_status(account_id)
            if not trunk.get("ready"):
                return AdapterResult(False, "telphin_sip_not_registered", error="Telphin SIP линия ещё не зарегистрирована в BORIS")
            from app.services.asterisk_gateway import originate_telphin
            out = originate_telphin(account_id, to_number, from_number, metadata)
            ok = out.get("status") == "accepted" and bool(out.get("provider_call_id"))
            return AdapterResult(
                ok,
                "accepted" if ok else str(out.get("status") or "provider_rejected"),
                provider_call_id=str(out.get("provider_call_id") or "") or None,
                payload=dict(out.get("payload") or {}),
                error=None if ok else str(out.get("status") or "telphin_sip_originate_failed"),
            )

        extension_id = self._extension(public_config)
        src_num = str(public_config.get("src_num") or public_config.get("callback_from") or from_number or "").strip()
        if not extension_id:
            return AdapterResult(False, "configuration_required", error="Telphin: укажите extension_id")
        if not src_num:
            return AdapterResult(False, "configuration_required", error="Telphin: укажите src_num")
        body: dict[str, Any] = {"dst_num": to_number, "src_num": src_num}
        try:
            status, data = self._api("POST", f"/api/ver1.0/extension/{extension_id}/callback/", credentials, body)
        except ValueError as exc:
            return AdapterResult(False, "credentials_invalid", error=str(exc))
        except RuntimeError as exc:
            return AdapterResult(False, "provider_unavailable", error=str(exc))
        call_api_id = str(data.get("call_api_id") or "").strip()
        call_id = str(data.get("call_id") or "").strip()
        ok = 200 <= status < 300 and bool(call_api_id)
        return AdapterResult(ok, "accepted" if ok else "provider_rejected", provider_call_id=call_api_id or None,
                             payload={"http_status": status, "call_id_present": bool(call_id), "transport": "rest_callback"},
                             error=None if ok else f"Telphin callback HTTP {status}")

    def command(self, *, provider_call_id: str, command: str, payload: dict[str, Any], credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        account_id = self._account_id(public_config)
        if self._sip_configured(credentials, public_config):
            if not account_id:
                return AdapterResult(False, "configuration_required", error="BORIS account identity отсутствует для Telphin SIP")
            from app.services.asterisk_gateway import command_telphin
            out = command_telphin(account_id, str(provider_call_id or ""), command, payload or {})
            ok = out.get("status") == "ok"
            return AdapterResult(ok, "ok" if ok else str(out.get("status") or "provider_rejected"),
                                 provider_call_id=str(provider_call_id or "") or None,
                                 payload={"http_status": out.get("http_status"), "action": str(command or "").lower(),
                                          "transport": "asterisk_sip"},
                                 error=None if ok else str(out.get("status") or "telphin_sip_control_failed"))

        extension_id = self._extension(public_config)
        call_api_id = str(provider_call_id or "").strip()
        if not extension_id:
            return AdapterResult(False, "configuration_required", error="Telphin: укажите extension_id")
        if not call_api_id or len(call_api_id) > 160:
            return AdapterResult(False, "invalid_provider_call_id", error="Telphin call_api_id отсутствует")
        base = f"/api/ver1.0/extension/{extension_id}/current_calls/{call_api_id}"
        cmd = str(command or "").strip().lower()
        if cmd == "hangup":
            method, body = "DELETE", None
        elif cmd in {"hold", "resume"}:
            method, body = "PUT", {"action": "hold" if cmd == "hold" else "offhold"}
        elif cmd == "transfer":
            target = str((payload or {}).get("to_number") or "").strip()
            if not target:
                return AdapterResult(False, "configuration_required", error="Telphin transfer destination отсутствует")
            method, body = "PUT", {"action": "transfer", "send_dst": target}
        else:
            return AdapterResult(False, "command_unsupported", error="Telphin command не включён в подтверждённый REST contract")
        try:
            status, data = self._api(method, base, credentials, body)
        except ValueError as exc:
            return AdapterResult(False, "credentials_invalid", error=str(exc))
        except RuntimeError as exc:
            return AdapterResult(False, "provider_unavailable", error=str(exc))
        ok = 200 <= status < 300
        return AdapterResult(ok, "ok" if ok else "provider_rejected", provider_call_id=call_api_id,
                             payload={"http_status": status, "action": cmd, "transport": "rest_api"},
                             error=None if ok else f"Telphin command HTTP {status}")

    def recording_link(self, *, provider_recording_id: str, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        extension_id = self._extension(public_config)
        record_uuid = str(provider_recording_id or "").strip()
        if not extension_id:
            return AdapterResult(False, "configuration_required", error="Telphin: укажите extension_id")
        if not record_uuid or len(record_uuid) > 180:
            return AdapterResult(False, "recording_unavailable", error="Telphin RecID отсутствует")
        try:
            status, data = self._api(
                "GET",
                f"/api/ver1.0/extension/{extension_id}/record/{record_uuid}/storage_url/",
                credentials,
                read_only=True,
            )
        except ValueError as exc:
            return AdapterResult(False, "credentials_invalid", error=str(exc))
        except RuntimeError as exc:
            return AdapterResult(False, "provider_unavailable", error=str(exc))
        storage_url = str(data.get("storage_url") or "").strip()
        ok = 200 <= status < 300 and storage_url.startswith("https://")
        return AdapterResult(
            ok,
            "ok" if ok else "recording_unavailable",
            payload={"url": storage_url if ok else "", "http_status": status},
            error=None if ok else f"Telphin recording HTTP {status}",
        )

    def media_session(self, *, device_id: str, call_id: str, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        account_id = self._account_id(public_config)
        if not self._sip_configured(credentials, public_config):
            return AdapterResult(False, "media_pending", error="Для звука BORIS Phone укажите отдельные SIP-данные линии Telphin")
        if not account_id:
            return AdapterResult(False, "configuration_required", error="BORIS account identity отсутствует для Telphin media")
        from app.services.telphin_sip_trunk import trunk_status
        trunk = trunk_status(account_id)
        if not trunk.get("ready"):
            return AdapterResult(False, "telphin_sip_not_registered", error="Telphin SIP линия не зарегистрирована")
        from app.services.asterisk_gateway import media_interconnect_health
        interconnect = media_interconnect_health()
        if not interconnect.get("ready"):
            return AdapterResult(False, "media_gateway_unavailable", error="Внутренний BORIS media bridge не готов")
        from app.services.phone_media_gateway import provision_webrtc_endpoint
        digest = hashlib.sha256(f"{account_id}|{device_id}|{call_id}|{secrets.token_hex(8)}".encode()).hexdigest()[:28]
        endpoint_id = "bp_" + digest
        provisioned = provision_webrtc_endpoint(endpoint_id, 90)
        if provisioned.get("status") != "ok":
            return AdapterResult(False, str(provisioned.get("status") or "media_gateway_unavailable"),
                                 error="Не удалось выдать временный SIP/WebRTC endpoint")
        media = dict(provisioned.get("media") or {})
        return AdapterResult(True, "ok", payload=media)

    def normalize_webhook(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """Normalize Telphin extension call events after transport authentication.

        This method is intentionally pure. The public ingress must authenticate
        the delivery before calling it; Telphin event subscriptions do not by
        themselves prove a signed webhook transport.
        """
        event_type = str(payload.get("EventType") or payload.get("event_type") or "").strip().lower()
        call_api_id = str(payload.get("CallAPIID") or payload.get("call_api_id") or "").strip()
        if event_type not in {"dial-in", "dial-out", "answer", "hangup"}:
            raise ValueError("unsupported Telphin event")
        if not call_api_id or len(call_api_id) > 160:
            raise ValueError("Telphin CallAPIID is required")

        raw_flow = str(payload.get("CallFlow") or payload.get("call_flow") or "").strip().lower()
        if event_type == "dial-in" or raw_flow in {"in", "inbound", "dial-in"}:
            direction = "inbound"
        elif event_type == "dial-out" or raw_flow in {"out", "outbound", "dial-out"}:
            direction = "outbound"
        else:
            # answer/hangup without flow are ambiguous and must not be guessed.
            raise ValueError("Telphin CallFlow is required for answer/hangup")

        call_status = str(payload.get("CallStatus") or "").strip().upper()
        bridged = str(payload.get("Bridged") or "").strip().lower()
        if event_type in {"dial-in", "dial-out"}:
            normalized_type = "call.ringing"
        elif event_type == "answer":
            normalized_type = "call.answered"
        else:
            answered = call_status == "ANSWER" or bridged in {"yes", "true", "1"}
            normalized_type = "call.ended" if answered else ("call.missed" if direction == "inbound" else "call.failed")

        if direction == "inbound":
            from_number = str(payload.get("CallerIDNum") or payload.get("RemoteNumber") or "").strip()
            to_number = str(payload.get("CalledDID") or payload.get("CalledNumber") or "").strip()
        else:
            from_number = str(payload.get("CallerExtension") or payload.get("CallerIDNum") or "").strip()
            to_number = str(payload.get("CalledNumber") or payload.get("RemoteNumber") or "").strip()

        stable_parts = [
            event_type,
            call_api_id,
            str(payload.get("CallID") or ""),
            str(payload.get("SubCallID") or ""),
            str(payload.get("EventTime") or ""),
            call_status,
            str(payload.get("Duration") or ""),
            str(payload.get("RecID") or ""),
        ]
        event_id = hashlib.sha256("|".join(stable_parts).encode("utf-8")).hexdigest()
        record_id = str(payload.get("RecID") or "").strip() or None
        return {
            "kind": "call.event",
            "type": normalized_type,
            "provider_call_id": call_api_id,
            "provider_recording_id": record_id,
            "event_id": event_id,
            "payload": {
                **payload,
                "direction": direction,
                "from": from_number,
                "to": to_number,
                "source": "telphin",
            },
        }
