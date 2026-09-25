from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AdapterResult:
    ok: bool
    status: str
    provider_call_id: str | None = None
    provider_event_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

class TelephonyProviderAdapter:
    """Provider boundary. Business state stays in telephony_core; adapters only translate transport/API calls."""
    key = 'base'
    capabilities: set[str] = set()
    supported_commands: set[str] = set()

    def health(self, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, 'not_implemented', error='Production adapter is not implemented')

    def make_call(self, *, to_number: str, from_number: str | None, credentials: dict[str, Any], public_config: dict[str, Any], metadata: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, 'not_implemented', error='Production outbound transport is not implemented')

    def command(self, *, provider_call_id: str, command: str, payload: dict[str, Any], credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, 'not_implemented', error='Production call control is not implemented')

    def media_session(self, *, device_id: str, call_id: str, credentials: dict[str, Any], public_config: dict[str, Any]) -> AdapterResult:
        return AdapterResult(False, 'media_pending', error='Production SIP/WebRTC media transport is not implemented')

    def normalize_webhook(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        raise NotImplementedError
