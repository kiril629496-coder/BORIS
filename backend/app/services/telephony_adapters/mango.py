from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .base import AdapterResult, TelephonyProviderAdapter
from app.services.exception_observability import observe_suppressed


class MangoAdapter(TelephonyProviderAdapter):
    """MANGO OFFICE VPBX transport adapter using the documented signed form API."""

    key = "mango"
    capabilities = {"api", "outbound_callback", "native_webhooks", "recordings"}
    api_origin = "https://app.mango-office.ru"

    @staticmethod
    def _keys(credentials: dict[str, Any]) -> tuple[str, str]:
        api_key = str(credentials.get("vpbx_api_key") or credentials.get("api_key") or "").strip()
        api_salt = str(credentials.get("vpbx_api_salt") or credentials.get("api_salt") or credentials.get("api_secret") or "").strip()
        return api_key, api_salt

    @classmethod
    def _post(cls, path: str, payload: dict[str, Any], credentials: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        api_key, api_salt = cls._keys(credentials)
        if not api_key or not api_salt:
            raise ValueError("MANGO: нужны vpbx_api_key и vpbx_api_salt")
        raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        sign = hashlib.sha256((api_key + raw_json + api_salt).encode("utf-8")).hexdigest()
        body = urlencode({"vpbx_api_key": api_key, "sign": sign, "json": raw_json}).encode("utf-8")
        req = Request(cls.api_origin + path, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "BORIS-Phone/1.0"})
        try:
            with urlopen(req, timeout=15) as resp:
                raw = resp.read(262144)
                status = int(getattr(resp, "status", 200) or 200)
        except HTTPError as exc:
            raw = exc.read(262144)
            status = int(exc.code or 500)
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"MANGO API недоступен: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"raw": raw.decode("utf-8", errors="replace")[:2000]}
        return status, data if isinstance(data, dict) else {"data": data}

    @staticmethod
    def _provider_error(data: dict[str, Any], http_status: int) -> str | None:
        # MANGO responses vary by endpoint/version. Treat explicit non-zero result
        # codes and common error fields as rejection; do not invent success.
        code = data.get("result")
        if isinstance(code, dict):
            code = code.get("code")
        if code not in (None, 0, "0", "success", "ok"):
            return str(data.get("message") or data.get("error") or f"MANGO result={code}")
        if data.get("error"):
            return str(data.get("error"))
        if not 200 <= http_status < 300:
            return f"HTTP {http_status}"
        return None

    def health(self, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        # Authenticated read-only request: list users. It verifies the key/salt
        # without originating a call or causing telephony spend.
        try:
            status, data = self._post("/vpbx/config/users/request", {}, credentials)
        except (ValueError, RuntimeError) as exc:
            return AdapterResult(False, "credentials_invalid" if isinstance(exc, ValueError) else "provider_unavailable", error=str(exc))
        error = self._provider_error(data, status)
        ok = error is None
        return AdapterResult(ok, "ok" if ok else "provider_rejected", payload={"http_status": status, "provider_result": data.get("result")}, error=error)

    def make_call(self, *, to_number: str, from_number: str | None, credentials: dict[str, Any], public_config: dict[str, Any], metadata: dict[str, Any]) -> AdapterResult:
        extension = str(public_config.get("extension") or "").strip()
        from_value = str(public_config.get("sip") or public_config.get("from_number") or from_number or "").strip()
        if not extension and not from_value:
            return AdapterResult(False, "configuration_required", error="MANGO: укажите extension и/или SIP/номер сотрудника")
        command_id = "BORIS-" + uuid.uuid4().hex[:20]
        from_block: dict[str, Any] = {}
        if extension:
            from_block["extension"] = extension
        if from_value:
            from_block["number"] = from_value
        payload: dict[str, Any] = {"command_id": command_id, "from": from_block, "to_number": to_number}
        line_number = str(public_config.get("line_number") or public_config.get("outbound_number") or "").strip()
        if line_number:
            payload["line_number"] = line_number
        try:
            status, data = self._post("/vpbx/commands/callback", payload, credentials)
        except (ValueError, RuntimeError) as exc:
            return AdapterResult(False, "credentials_invalid" if isinstance(exc, ValueError) else "provider_unavailable", error=str(exc))
        error = self._provider_error(data, status)
        ok = error is None
        provider_call_id = str(data.get("call_id") or data.get("entry_id") or "").strip() or None if ok else None
        return AdapterResult(ok, "accepted" if ok else "provider_rejected", provider_call_id=provider_call_id, payload={"http_status": status, "command_id": command_id, "provider_result": data.get("result")}, error=error)

    def command(self, *, provider_call_id: str, command: str, payload: dict[str, Any], credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, "command_unsupported", error="MANGO call-control не включён до отдельной проверки command API contract")

    def media_session(self, *, device_id: str, call_id: str, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, "media_pending", error="MANGO media/WebRTC transport требует отдельной подтверждённой конфигурации")

    @staticmethod
    def verify_webhook_signature(raw_json: str, supplied_api_key: str, supplied_sign: str, credentials: dict[str, Any]) -> bool:
        api_key, api_salt = MangoAdapter._keys(credentials)
        if not api_key or not api_salt or not raw_json or not supplied_sign:
            return False
        if not hmac.compare_digest(api_key, str(supplied_api_key or '').strip()):
            return False
        expected = hashlib.sha256((api_key + raw_json + api_salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(expected, str(supplied_sign or '').strip().lower())

    def recording_link(self, *, provider_recording_id: str, credentials: dict[str, Any]) -> AdapterResult:
        """Get the documented temporary MANGO recording URL without downloading it here."""
        from urllib.request import build_opener, HTTPRedirectHandler
        api_key, api_salt = self._keys(credentials)
        if not api_key or not api_salt:
            return AdapterResult(False, "credentials_invalid", error="MANGO: нужны vpbx_api_key и vpbx_api_salt")
        rid = str(provider_recording_id or '').strip()
        if not rid:
            return AdapterResult(False, "recording_unavailable", error="MANGO recording_id отсутствует")
        payload = {"recording_id": rid, "action": "download"}
        raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        sign = hashlib.sha256((api_key + raw_json + api_salt).encode("utf-8")).hexdigest()
        body = urlencode({"vpbx_api_key": api_key, "sign": sign, "json": raw_json}).encode("utf-8")
        class _NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        req = Request(self.api_origin + "/vpbx/queries/recording/post/", data=body, method="POST", headers={"Content-Type":"application/x-www-form-urlencoded","User-Agent":"BORIS-Phone/1.0"})
        try:
            resp = build_opener(_NoRedirect).open(req, timeout=15)
            location = str(resp.headers.get("Location") or '').strip()
            status = int(getattr(resp, "status", 200) or 200)
            try: resp.close()
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=140)
        except HTTPError as exc:
            status = int(exc.code or 500)
            location = str(exc.headers.get("Location") or '').strip()
        except (URLError, TimeoutError) as exc:
            return AdapterResult(False, "provider_unavailable", error=f"MANGO recording API недоступен: {exc}")
        ok = status in {200, 301, 302, 303, 307, 308} and bool(location)
        return AdapterResult(ok, "ok" if ok else "recording_unavailable", payload={"url":location,"http_status":status}, error=None if ok else f"MANGO не вернул ссылку на запись (HTTP {status})")

    def normalize_webhook(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        # Normalization is pure: ingress/audit may still need the original body.
        payload = dict(payload or {})
        kind = str(payload.pop("_event_kind", "") or "").strip().lower()
        entry_id = str(payload.get("entry_id") or "").strip()
        if kind == "recording":
            state = str(payload.get("recording_state") or "").strip().lower()
            code = str(payload.get("completion_code") or "").strip()
            if state != "completed":
                return {"kind":"ignored","reason":"recording_not_completed"}
            if code and code != "1000":
                return {"kind":"ignored","reason":"recording_failed","completion_code":code}
            rid = str(payload.get("recording_id") or "").strip()
            if not entry_id or not rid:
                raise ValueError("MANGO recording webhook requires entry_id and recording_id")
            return {"kind":"recording.ready","provider_call_id":entry_id,"provider_recording_id":rid,"payload":payload}
        if kind == "summary":
            if not entry_id:
                raise ValueError("MANGO summary requires entry_id")
            try: direction_code = int(payload.get("call_direction"))
            except Exception: direction_code = 1
            direction = "outbound" if direction_code == 2 else ("internal" if direction_code == 0 else "inbound")
            src = payload.get("from") if isinstance(payload.get("from"),dict) else {}
            dst = payload.get("to") if isinstance(payload.get("to"),dict) else {}
            from_number = str(src.get("number") or src.get("extension") or "")
            to_number = str(dst.get("number") or dst.get("extension") or payload.get("line_number") or "")
            try: talked = int(payload.get("talk_time") or 0) > 0
            except Exception: talked = bool(payload.get("talk_time"))
            try: success = int(payload.get("entry_result") or 0) == 1
            except Exception: success = str(payload.get("entry_result") or "").strip() == "1"
            event_type = "call.ended" if talked and success else ("call.missed" if direction == "inbound" else "call.failed")
            event_id = hashlib.sha256(("summary|"+entry_id+"|"+str(payload.get("end_time") or "")+"|"+str(payload.get("disconnect_reason") or "")).encode("utf-8")).hexdigest()
            duration = None
            try:
                talk_time=int(payload.get("talk_time") or 0); end_time=int(payload.get("end_time") or 0)
                duration=max(0,end_time-talk_time) if talk_time and end_time>=talk_time else 0
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=185)
            return {"kind":"call.event","type":event_type,"provider_call_id":entry_id,"event_id":event_id,
                    "payload":{**payload,"direction":direction,"from":from_number,"to":to_number,"source":"mango","talk_duration_sec":duration}}
        if kind == "call":
            if not entry_id:
                raise ValueError("MANGO call event requires entry_id")
            state = str(payload.get("call_state") or "").strip().lower()
            event_map = {"appeared":"call.ringing","connected":"call.answered","onhold":"call.hold"}
            event_type = event_map.get(state)
            if not event_type:
                return {"kind":"ignored","reason":"call_state_not_canonical","call_state":state}
            src = payload.get("from") if isinstance(payload.get("from"),dict) else {}
            dst = payload.get("to") if isinstance(payload.get("to"),dict) else {}
            from_number = str(src.get("number") or src.get("extension") or "")
            to_number = str(dst.get("number") or dst.get("extension") or "")
            direction = "outbound" if src.get("extension") and not dst.get("extension") else "inbound"
            event_id = hashlib.sha256(("call|"+entry_id+"|"+str(payload.get("call_id") or "")+"|"+str(payload.get("seq") or "")+"|"+state).encode("utf-8")).hexdigest()
            return {"kind":"call.event","type":event_type,"provider_call_id":entry_id,"event_id":event_id,
                    "payload":{**payload,"direction":direction,"from":from_number,"to":to_number,"source":"mango"}}
        if kind == "ping":
            return {"kind":"ping"}
        raise ValueError("unsupported MANGO event kind")
