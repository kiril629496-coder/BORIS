from app.services.operation_contract import (
    OperationSemantic,
    OperationState,
    internal_failure,
    normalize_state,
    outcome,
)


def test_architecture_semantics_map_to_stable_runtime_states():
    assert normalize_state("ALLOW") is OperationState.OK
    assert normalize_state("SUCCESS") is OperationState.OK
    assert normalize_state("BLOCK") is OperationState.BLOCKED_POLICY
    assert normalize_state("WAIT") is OperationState.WAIT_EXTERNAL
    assert normalize_state("RETRY") is OperationState.RETRY
    assert normalize_state("DEGRADED") is OperationState.DEGRADED
    assert normalize_state("FAIL") is OperationState.FAILED_INTERNAL


def test_outcome_carries_full_business_envelope():
    row = outcome(
        OperationSemantic.WAIT,
        "provider_unavailable",
        operation_id="op-1",
        account_id="acc-1",
        module="email",
        action="EMAIL_SEND",
        human_reason="waiting for provider",
        next_retry_at="2026-09-12T00:00:00+00:00",
        provider="smtp",
        provider_operation_id="m-1",
        evidence={"receipt": "pending"},
    )
    assert row["state"] == "WAIT_EXTERNAL"
    assert row["semantic"] == "WAIT"
    assert row["reason_code"] == "provider_unavailable"
    assert row["retryable"] is True
    for key in ("operation_id", "account_id", "module", "action", "provider", "provider_operation_id", "evidence", "created_at"):
        assert key in row


def test_policy_block_is_data_not_failure():
    row = outcome("BLOCK", "daily_budget_exhausted", account_id="a", module="marketing", action="AVITO_BID_WRITE")
    assert row == {**row, "state": "BLOCKED_POLICY", "semantic": "BLOCK", "ok": False}
    assert row["retryable"] is False


def test_internal_failure_never_leaks_exception_text():
    secret = "client@example.com token=TOPSECRET"
    row = internal_failure("internal_error", RuntimeError(secret), module="messages", action="MESSAGE_SEND")
    rendered = repr(row)
    assert row["state"] == "FAILED_INTERNAL"
    assert row["semantic"] == "FAIL"
    assert row["reason"] == "RuntimeError"
    assert secret not in rendered
    assert "TOPSECRET" not in rendered
