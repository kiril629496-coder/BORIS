from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import AdapterResult, TelephonyProviderAdapter


class UISAdapter(TelephonyProviderAdapter):
    """UIS / CoMagic Call API v4.0 transport adapter.

    Supports authenticated health, direct employee callback and the proven
    hold/resume/hangup call-control subset. Native webhook/recording/media
    ingress stays outside this adapter until separately verified.
    """

    key = "uis"
    capabilities = {"api", "outbound_callback", "call_control", "native_webhooks", "recordings"}
    supported_commands = {"hold", "resume", "hangup"}

    @staticmethod
    def _origin(public_config: dict[str, Any]) -> str:
        portal = str(public_config.get("portal") or "uis").strip().lower()
        return "https://callapi.comagic.ru/v4.0" if portal == "comagic" else "https://callapi.uiscom.ru/v4.0"

    @classmethod
    def _rpc(cls, method: str, params: dict[str, Any], credentials: dict[str, Any], public_config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        body = json.dumps({"jsonrpc":"2.0","id":"boris-"+uuid.uuid4().hex[:16],"method":method,"params":params}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = Request(cls._origin(public_config), data=body, method="POST", headers={"Content-Type":"application/json; charset=UTF-8","Accept":"application/json","User-Agent":"BORIS-Phone/1.0"})
        try:
            with urlopen(req, timeout=15) as resp:
                raw = resp.read(262144); status = int(getattr(resp,"status",200) or 200)
        except HTTPError as exc:
            raw = exc.read(262144); status = int(exc.code or 500)
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"UIS Call API недоступен: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {"raw": raw.decode("utf-8", errors="replace")[:2000]}
        return status, data if isinstance(data, dict) else {"data": data}

    @classmethod
    def _access_token(cls, credentials: dict[str, Any], public_config: dict[str, Any], *, allow_login: bool=True) -> tuple[str | None, AdapterResult | None]:
        token = str(credentials.get("access_token") or credentials.get("api_key") or "").strip()
        if token:
            return token, None
        login = str(credentials.get("login") or "").strip(); password = str(credentials.get("password") or "").strip()
        if not login or not password:
            return None, AdapterResult(False,"credentials_invalid",error="UIS: нужен access_token либо login/password")
        if not allow_login:
            return None, AdapterResult(False,"credentials_invalid",error="UIS: для этого действия нужен постоянный access_token")
        try:
            status, data = cls._rpc("login.user", {"login":login,"password":password}, credentials, public_config)
        except RuntimeError as exc:
            return None, AdapterResult(False,"provider_unavailable",error=str(exc))
        err = data.get("error")
        result = data.get("result") if isinstance(data.get("result"),dict) else {}
        body = result.get("data") if isinstance(result.get("data"),dict) else {}
        token = str(body.get("access_token") or "").strip()
        if not (200 <= status < 300) or err or not token:
            message = (err or {}).get("message") if isinstance(err,dict) else err
            return None, AdapterResult(False,"provider_rejected",payload={"http_status":status},error=str(message or f"HTTP {status}"))
        return token, None

    @staticmethod
    def _result(data: dict[str, Any], http_status: int) -> tuple[bool, Any, str | None]:
        err = data.get("error")
        result = data.get("result") if isinstance(data.get("result"),dict) else {}
        payload = result.get("data")
        if not 200 <= http_status < 300 or err:
            message = (err or {}).get("message") if isinstance(err,dict) else err
            return False, payload, str(message or f"HTTP {http_status}")
        return True, payload, None

    def health(self, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        token, failure = self._access_token(credentials, public_config)
        if failure:
            return failure
        # login.user itself proves login/password. A permanent token is verified
        # by read-only list.calls; no call is originated.
        if not (credentials.get("access_token") or credentials.get("api_key")):
            return AdapterResult(True,"ok",payload={"auth_mode":"session_login","portal":str(public_config.get("portal") or "uis")})
        try:
            status, data = self._rpc("list.calls", {"access_token":token}, credentials, public_config)
        except RuntimeError as exc:
            return AdapterResult(False,"provider_unavailable",error=str(exc))
        ok, payload, error = self._result(data,status)
        return AdapterResult(ok,"ok" if ok else "provider_rejected",payload={"http_status":status,"auth_mode":"access_token","active_calls":len(payload) if isinstance(payload,list) else None},error=error)

    def make_call(self, *, to_number: str, from_number: str | None, credentials: dict[str, Any], public_config: dict[str, Any], metadata: dict[str, Any]) -> AdapterResult:
        token, failure = self._access_token(credentials, public_config)
        if failure: return failure
        employee_id = public_config.get("employee_id")
        employee_phone = str(public_config.get("employee_phone") or from_number or "").strip()
        if employee_id is None and not employee_phone:
            return AdapterResult(False,"configuration_required",error="UIS: укажите employee_id и/или employee_phone")
        employee: dict[str,Any] = {}
        if employee_id is not None:
            try: employee["id"] = int(employee_id)
            except Exception: return AdapterResult(False,"configuration_required",error="UIS: employee_id должен быть числом")
        if employee_phone: employee["phone_number"] = employee_phone
        params: dict[str,Any] = {"access_token":token,"first_call":str(public_config.get("first_call") or "employee"),"switch_at_once":bool(public_config.get("switch_at_once",True)),"contact":to_number,"employee":employee,"external_id":"boris-"+uuid.uuid4().hex[:20]}
        virtual = str(public_config.get("virtual_phone_number") or public_config.get("outbound_number") or "").strip()
        if virtual:
            params["virtual_phone_number"] = virtual; params["show_virtual_phone_number"] = True
        try:
            status,data = self._rpc("start.employee_call",params,credentials,public_config)
        except RuntimeError as exc:
            return AdapterResult(False,"provider_unavailable",error=str(exc))
        ok,payload,error = self._result(data,status)
        call_id = str((payload or {}).get("call_session_id") or "").strip() if isinstance(payload,dict) else ""
        ok = ok and bool(call_id)
        return AdapterResult(ok,"accepted" if ok else "provider_rejected",provider_call_id=call_id or None,payload={"http_status":status,"external_id":params["external_id"]},error=None if ok else (error or "UIS не вернул call_session_id"))

    def command(self, *, provider_call_id: str, command: str, payload: dict[str, Any], credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        mapping = {"hold":"hold.call","resume":"unhold.call","hangup":"release.call"}
        method = mapping.get(str(command or "").strip().lower())
        if not method:
            return AdapterResult(False,"command_unsupported",error=f"UIS command {command!r} не включён в доказанный contract")
        token,failure = self._access_token(credentials,public_config)
        if failure: return failure
        try: call_session_id = int(str(provider_call_id))
        except Exception: return AdapterResult(False,"invalid_provider_call_id",error="UIS call_session_id должен быть числом")
        params: dict[str,Any] = {"access_token":token,"call_session_id":call_session_id}
        try:
            status,data = self._rpc(method,params,credentials,public_config)
        except RuntimeError as exc:
            return AdapterResult(False,"provider_unavailable",error=str(exc))
        ok,result,error = self._result(data,status)
        success = (result or {}).get("success") if isinstance(result,dict) else None
        if success not in (None,True,"true",1,"1"): ok=False; error=error or "UIS command rejected"
        return AdapterResult(ok,"ok" if ok else "provider_rejected",provider_call_id=str(provider_call_id),payload={"http_status":status,"method":method},error=error)

    def media_session(self, *, device_id: str, call_id: str, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False,"media_pending",error="UIS media/WebRTC transport требует отдельной подтверждённой конфигурации")

    def normalize_webhook(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """Normalize a UIS configurable HTTP notification template.

        BORIS expects the documented UIS call fields plus its own shared secret
        at the public ingress. The secret is stripped before this method.
        """
        body = payload.get("calls") if isinstance(payload.get("calls"), dict) else payload
        if not isinstance(body, dict):
            raise ValueError("invalid UIS webhook payload")
        call_session_id = str(body.get("call_session_id") or "").strip()
        if not call_session_id:
            raise ValueError("UIS call_session_id is required")
        mnemonic = str(body.get("notification_mnemonic") or payload.get("notification_mnemonic") or "").strip().lower()
        record_url = str(body.get("file_link") or body.get("full_record_file_link") or "").strip()
        if record_url or mnemonic == "talk_record_file":
            if not record_url:
                raise ValueError("UIS recording notification has no file link")
            return {"kind":"recording.ready","provider_call_id":call_session_id,"provider_recording_id":call_session_id,"source_url":record_url,"payload":body}
        direction_raw = str(body.get("direction") or "").strip().lower()
        direction = "outbound" if direction_raw in {"out","outbound","исходящий","исходящий звонок"} else "inbound"
        contact = str(body.get("contact_phone_number") or body.get("contact") or "")
        virtual = str(body.get("virtual_phone_number") or body.get("called_phone_number") or "")
        is_lost = body.get("is_lost")
        lost = is_lost is True or str(is_lost).strip().lower() in {"1","true","yes"}
        if lost or "lost" in mnemonic:
            event_type = "call.missed"
        elif body.get("talk_time_duration") is not None or "finish" in mnemonic or "end" in mnemonic:
            event_type = "call.ended"
        elif body.get("employee_id") is not None or "talk" in mnemonic or "answer" in mnemonic:
            event_type = "call.answered"
        else:
            event_type = "call.ringing"
        from_number = contact if direction == "inbound" else str(body.get("employee_phone_number") or virtual or "")
        to_number = virtual if direction == "inbound" else contact
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, "uis:"+call_session_id+":"+mnemonic+":"+str(payload.get("notification_time") or body.get("notification_time") or "")+":"+event_type).hex
        return {"kind":"call.event","type":event_type,"provider_call_id":call_session_id,"event_id":event_id,
                "payload":{**body,"direction":direction,"from":from_number,"to":to_number,"source":"uis"}}
