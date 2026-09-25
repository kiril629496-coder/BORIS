# -*- coding: utf-8 -*-
"""Canonical result contract for BORIS business operations.

Expected business outcomes are data, never Python failures.  The stable runtime
states are kept for backward compatibility while the architecture-audit public
semantics (ALLOW/BLOCK/WAIT/RETRY/SUCCESS/DEGRADED/FAIL) map deterministically
onto them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OperationState(str, Enum):
    OK = "OK"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    WAIT_EXTERNAL = "WAIT_EXTERNAL"
    RETRY = "RETRY"
    DEGRADED = "DEGRADED"
    FAILED_INTERNAL = "FAILED_INTERNAL"


class OperationSemantic(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    WAIT = "WAIT"
    RETRY = "RETRY"
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAIL = "FAIL"


SEMANTIC_TO_STATE = {
    OperationSemantic.ALLOW: OperationState.OK,
    OperationSemantic.SUCCESS: OperationState.OK,
    OperationSemantic.BLOCK: OperationState.BLOCKED_POLICY,
    OperationSemantic.WAIT: OperationState.WAIT_EXTERNAL,
    OperationSemantic.RETRY: OperationState.RETRY,
    OperationSemantic.DEGRADED: OperationState.DEGRADED,
    OperationSemantic.FAIL: OperationState.FAILED_INTERNAL,
}
STATE_TO_SEMANTIC = {
    OperationState.OK: OperationSemantic.SUCCESS,
    OperationState.BLOCKED_POLICY: OperationSemantic.BLOCK,
    OperationState.WAIT_EXTERNAL: OperationSemantic.WAIT,
    OperationState.RETRY: OperationSemantic.RETRY,
    OperationState.DEGRADED: OperationSemantic.DEGRADED,
    OperationState.FAILED_INTERNAL: OperationSemantic.FAIL,
}


def normalize_state(value: OperationState | OperationSemantic | str) -> OperationState:
    if isinstance(value, OperationState):
        return value
    if isinstance(value, OperationSemantic):
        return SEMANTIC_TO_STATE[value]
    raw = str(value or "").strip().upper()
    try:
        return OperationState(raw)
    except ValueError:
        return SEMANTIC_TO_STATE[OperationSemantic(raw)]


def outcome(
    state: OperationState | OperationSemantic | str,
    code: str,
    *,
    reason: str | None = None,
    data: Any = None,
    retry_after_seconds: int | None = None,
    operation_id: str | None = None,
    account_id: str | None = None,
    module: str | None = None,
    action: str | None = None,
    human_reason: str | None = None,
    retryable: bool | None = None,
    next_retry_at: str | None = None,
    provider: str | None = None,
    provider_operation_id: str | None = None,
    evidence: Any = None,
    created_at: str | None = None,
) -> dict:
    """Return one normalized, backward-compatible operation result envelope."""
    normalized = normalize_state(state)
    semantic = STATE_TO_SEMANTIC[normalized]
    if retryable is None:
        retryable = normalized in {OperationState.WAIT_EXTERNAL, OperationState.RETRY, OperationState.DEGRADED}
    payload = {
        "state": normalized.value,
        "semantic": semantic.value,
        "ok": normalized is OperationState.OK,
        "code": str(code or "unknown"),
        "reason_code": str(code or "unknown"),
        "retryable": bool(retryable),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    if reason:
        payload["reason"] = str(reason)
    if human_reason or reason:
        payload["human_reason"] = str(human_reason or reason)
    for key, value in (
        ("operation_id", operation_id),
        ("account_id", account_id),
        ("module", module),
        ("action", action),
        ("next_retry_at", next_retry_at),
        ("provider", provider),
        ("provider_operation_id", provider_operation_id),
    ):
        if value not in (None, ""):
            payload[key] = str(value)
    if data is not None:
        payload["data"] = data
    if evidence is not None:
        payload["evidence"] = evidence
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = max(0, int(retry_after_seconds))
    return payload


def internal_failure(
    code: str,
    exc: BaseException,
    *,
    data: Any = None,
    operation_id: str | None = None,
    account_id: str | None = None,
    module: str | None = None,
    action: str | None = None,
    evidence: Any = None,
) -> dict:
    """Normalize an internal fault without leaking exception text/PII/secrets."""
    return outcome(
        OperationState.FAILED_INTERNAL,
        code,
        reason=type(exc).__name__,
        human_reason="internal operation failure",
        data=data,
        operation_id=operation_id,
        account_id=account_id,
        module=module,
        action=action,
        evidence=evidence,
        retryable=False,
    )
