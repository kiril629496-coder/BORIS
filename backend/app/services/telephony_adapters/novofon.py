from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import AdapterResult, TelephonyProviderAdapter


class NovofonAdapter(TelephonyProviderAdapter):
    """Novofon API v1 transport adapter.

    Implements only provider capabilities proven by the public API contract:
    authenticated read-only health and outbound callback. Native webhooks,
    recordings and media remain separate gates and are not advertised here.
    """

    key = "novofon"
    capabilities = {"api", "outbound_callback", "native_webhooks", "recordings"}
    api_origin = "https://api.novofon.com"

    @staticmethod
    def _keys(credentials: dict[str, Any]) -> tuple[str, str]:
        user_key = str(credentials.get("user_key") or credentials.get("api_key") or "").strip()
        secret_key = str(credentials.get("secret_key") or credentials.get("api_secret") or "").strip()
        return user_key, secret_key

    @classmethod
    def _signed_get(cls, method: str, params: dict[str, Any], credentials: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        user_key, secret_key = cls._keys(credentials)
        if not user_key or not secret_key:
            raise ValueError("Novofon: нужны user_key и secret_key")
        clean = {k: v for k, v in params.items() if v is not None and str(v) != ""}
        query = urlencode(sorted(clean.items()), doseq=True)
        sign_source = method + query + hashlib.md5(query.encode("utf-8")).hexdigest()
        digest = hmac.new(secret_key.encode("utf-8"), sign_source.encode("utf-8"), hashlib.sha1).digest()
        signature = base64.b64encode(digest).decode("ascii")
        url = cls.api_origin + method + ("?" + query if query else "")
        req = Request(url, method="GET", headers={"Authorization": f"{user_key}:{signature}", "Accept": "application/json", "User-Agent": "BORIS-Phone/1.0"})
        try:
            with urlopen(req, timeout=12) as resp:
                raw = resp.read(262144)
                status = int(getattr(resp, "status", 200) or 200)
        except HTTPError as exc:
            raw = exc.read(262144)
            status = int(exc.code or 500)
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Novofon API недоступен: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"raw": raw.decode("utf-8", errors="replace")[:2000]}
        return status, data if isinstance(data, dict) else {"data": data}

    def health(self, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        try:
            status, data = self._signed_get("/v1/info/balance/", {}, credentials)
        except (ValueError, RuntimeError) as exc:
            return AdapterResult(False, "credentials_invalid" if isinstance(exc, ValueError) else "provider_unavailable", error=str(exc))
        ok = 200 <= status < 300 and str(data.get("status") or "").lower() == "success"
        return AdapterResult(ok, "ok" if ok else "provider_rejected", payload={"http_status": status, "provider_status": data.get("status"), "currency": data.get("currency")}, error=None if ok else str(data.get("message") or data.get("error") or f"HTTP {status}"))

    def make_call(self, *, to_number: str, from_number: str | None, credentials: dict[str, Any], public_config: dict[str, Any], metadata: dict[str, Any]) -> AdapterResult:
        callback_from = str(public_config.get("callback_from") or public_config.get("sip") or from_number or "").strip()
        if not callback_from:
            return AdapterResult(False, "configuration_required", error="Novofon: укажите callback_from (SIP/внутренний номер/номер)")
        params: dict[str, Any] = {"from": callback_from, "to": to_number}
        if public_config.get("sip"):
            params["sip"] = str(public_config.get("sip"))
        if bool(public_config.get("predicted")):
            params["predicted"] = 1
        try:
            status, data = self._signed_get("/v1/request/callback/", params, credentials)
        except (ValueError, RuntimeError) as exc:
            return AdapterResult(False, "credentials_invalid" if isinstance(exc, ValueError) else "provider_unavailable", error=str(exc))
        ok = 200 <= status < 300 and str(data.get("status") or "").lower() == "success"
        provider_call_id = str(data.get("call_id") or data.get("pbx_call_id") or "").strip() or None
        return AdapterResult(ok, "accepted" if ok else "provider_rejected", provider_call_id=provider_call_id, payload={"http_status": status, "provider_status": data.get("status"), "from": data.get("from"), "to": data.get("to"), "time": data.get("time")}, error=None if ok else str(data.get("message") or data.get("error") or f"HTTP {status}"))

    def command(self, *, provider_call_id: str, command: str, payload: dict[str, Any], credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, "command_unsupported", error="Novofon callback API не подтверждает этот call-control command; BORIS не имитирует управление")

    def media_session(self, *, device_id: str, call_id: str, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, "media_pending", error="Novofon SIP/media требует отдельной подтверждённой конфигурации")

    @staticmethod
    def verify_webhook_signature(payload: dict[str, Any], signature: str, credentials: dict[str, Any]) -> bool:
        """Verify documented Novofon v1 PBX webhook HMAC-SHA1 signatures."""
        _, secret_key = NovofonAdapter._keys(credentials)
        if not secret_key or not signature:
            return False
        event = str(payload.get("event") or "").strip().upper()
        call_start = str(payload.get("call_start") or "")
        if event in {"NOTIFY_START", "NOTIFY_INTERNAL", "NOTIFY_END"}:
            message = str(payload.get("caller_id") or "") + str(payload.get("called_did") or "") + call_start
        elif event == "NOTIFY_ANSWER":
            message = str(payload.get("caller_id") or "") + str(payload.get("destination") or "") + call_start
        elif event in {"NOTIFY_OUT_START", "NOTIFY_OUT_END"}:
            message = str(payload.get("internal") or "") + str(payload.get("destination") or "") + call_start
        elif event == "NOTIFY_RECORD":
            message = str(payload.get("pbx_call_id") or "") + str(payload.get("call_id_with_rec") or "")
        else:
            return False
        expected = base64.b64encode(hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha1).digest()).decode("ascii")
        return hmac.compare_digest(expected.strip(), str(signature).strip())

    def recording_link(self, *, provider_call_id: str, provider_recording_id: str | None, credentials: dict[str, Any]) -> AdapterResult:
        params: dict[str, Any] = {"pbx_call_id": provider_call_id, "lifetime": 1800}
        if provider_recording_id:
            params = {"call_id": provider_recording_id, "lifetime": 1800}
        try:
            status, data = self._signed_get("/v1/pbx/record/request/", params, credentials)
        except (ValueError, RuntimeError) as exc:
            return AdapterResult(False, "credentials_invalid" if isinstance(exc, ValueError) else "provider_unavailable", error=str(exc))
        ok = 200 <= status < 300 and str(data.get("status") or "").lower() == "success"
        link = str(data.get("link") or "").strip()
        if not link and isinstance(data.get("links"), list) and data.get("links"):
            link = str(data["links"][0] or "").strip()
        return AdapterResult(ok and bool(link), "ok" if ok and link else "recording_unavailable", payload={"url": link, "lifetime_till": data.get("lifetime_till"), "http_status": status}, error=None if ok and link else str(data.get("message") or data.get("error") or "Novofon не вернул ссылку на запись"))

    def normalize_webhook(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        event = str(payload.get("event") or "").strip().upper()
        pbx_call_id = str(payload.get("pbx_call_id") or "").strip()
        direction = "outbound" if event.startswith("NOTIFY_OUT_") else "inbound"
        disposition = str(payload.get("disposition") or "").strip().lower()
        event_map = {
            "NOTIFY_START": "call.ringing",
            "NOTIFY_INTERNAL": "call.ringing",
            "NOTIFY_ANSWER": "call.answered",
            "NOTIFY_OUT_START": "call.ringing",
        }
        if event in {"NOTIFY_END", "NOTIFY_OUT_END"}:
            event_type = {
                "busy": "call.busy", "cancel": "call.cancelled", "no answer": "call.missed",
                "failed": "call.failed", "call failed": "call.failed",
            }.get(disposition, "call.ended" if disposition == "answered" else "call.failed")
        else:
            event_type = event_map.get(event)
        if event == "NOTIFY_RECORD":
            if not pbx_call_id:
                raise ValueError("Novofon recording webhook has no pbx_call_id")
            return {"kind": "recording.ready", "provider_call_id": pbx_call_id, "provider_recording_id": str(payload.get("call_id_with_rec") or "").strip() or None, "payload": payload}
        if not event_type or not pbx_call_id:
            raise ValueError("unsupported or incomplete Novofon webhook")
        from_number = str(payload.get("caller_id") or payload.get("internal") or "")
        to_number = str(payload.get("called_did") or payload.get("destination") or "")
        event_id = hashlib.sha256((event + "|" + pbx_call_id + "|" + str(payload.get("call_start") or "") + "|" + str(payload.get("internal") or "") + "|" + disposition).encode("utf-8")).hexdigest()
        return {
            "kind": "call.event", "type": event_type, "provider_call_id": pbx_call_id, "event_id": event_id,
            "payload": {**payload, "direction": direction, "from": from_number, "to": to_number, "source": "novofon"},
        }
