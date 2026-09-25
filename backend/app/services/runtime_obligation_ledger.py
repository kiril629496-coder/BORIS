# -*- coding: utf-8 -*-
"""Canonical BORIS plan -> attempt -> fact materialization.

This module does not create a second ledger.  It writes bounded hourly snapshots
into the existing Control Plane execution/step/attempt/evidence tables.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.control_plane import ControlRequirement
from app.services.control_plane import (
    add_evidence,
    initialize_execution_steps,
    record_execution_attempt,
    start_execution,
    transition_execution,
)
from app.services.control_plane_adapters import adapter_diagnose, get as get_adapter

REQUIRED_LEDGER_MODULES = (
    "marketing", "mop", "email", "telephony", "feed_factory", "social", "prospecting",
)


def _json_safe(value: Any):
    """Normalize adapter runtime facts before JSONB persistence.

    RUNTIME_LEDGER_JSON_SAFE_FACT_V1: adapters may expose native datetime,
    Decimal, UUID or other diagnostic values. Runtime Ledger is evidence, so
    preserve primitive values and serialize only non-JSON leaves instead of
    letting one module abort the whole 7-module snapshot.
    """
    import json
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    return int(round(_num(value)))


def _state_from_diagnosis(diagnosis: dict, *, waiting_quantity: int = 0, failed_quantity: int = 0) -> str:
    health = str((diagnosis or {}).get("health") or "").lower()
    dependency = str((diagnosis or {}).get("dependency_state") or "").lower()
    if dependency == "waiting_external" or health == "waiting_external":
        return "WAIT_EXTERNAL"
    if health in {"waiting_owner", "waiting_human"}:
        return "BLOCKED_POLICY"
    if health in {"failed", "error", "critical"}:
        return "FAILED_INTERNAL"
    if health in {"degraded", "warning"} or failed_quantity > 0:
        return "DEGRADED"
    if waiting_quantity > 0 and health not in {"pass", "ok", "healthy"}:
        return "RETRY"
    return "OK"


def _facts(module: str, state: dict, diagnosis: dict) -> dict:
    now = datetime.now(timezone.utc)
    plan = attempted = succeeded = failed = waiting = 0
    metrics: dict[str, Any] = _json_safe(dict(state or {}))
    if module == "marketing":
        plan = _int(state.get("target_leads_total") if state.get("state") == "portfolio" else state.get("target_leads_per_day"))
        succeeded = _int(state.get("actual_leads_total") if state.get("state") == "portfolio" else state.get("actual_leads"))
        if state.get("state") == "portfolio":
            rows = [x for x in state.get("portfolio_accounts", []) if isinstance(x, dict)]
            attempted = sum(_int(x.get("runtime_planned")) for x in rows)
            failed = sum(_int(x.get("runtime_failed")) for x in rows)
        else:
            attempted = _int(state.get("runtime_planned"))
            failed = _int(state.get("runtime_failed"))
        waiting = max(0, plan - succeeded)
    elif module == "mop":
        plan = _int(state.get("drafts_24h"))
        attempted = plan
        succeeded = _int(state.get("sent_24h"))
        failed = _int(state.get("unresolved_send_failed")) + _int(state.get("stale_analyzing"))
        waiting = _int(state.get("waiting_external")) + _int(state.get("auto_send_unresolved_ready")) + _int(state.get("owner_required_human_required"))
    elif module == "email":
        succeeded = _int(state.get("sent_24h"))
        waiting = _int(state.get("queued")) + _int(state.get("stale_due")) + _int(state.get("stale_sending"))
        failed = _int(state.get("failed_24h"))
        plan = succeeded + waiting + failed
        attempted = succeeded + failed
    elif module == "telephony":
        plan = _int(state.get("calls_24h"))
        attempted = plan
        succeeded = max(0, plan - _int(state.get("missed_24h")))
        failed = _int(state.get("stale_runtime_commands")) + _int(state.get("unhealthy_provider_configs"))
        waiting = sum(_int(state.get(k)) for k in (
            "recording_pending_24h", "transcription_pending_24h", "analysis_pending_24h", "overdue_callbacks",
        ))
    elif module == "feed_factory":
        waiting = _int(state.get("queued")) + _int(state.get("running")) + _int(state.get("waiting_external_24h"))
        failed = _int(state.get("retryable_failures_24h")) + _int(state.get("unresolved_failed_24h"))
        attempted = waiting + failed + _int(state.get("recovered_failed_24h"))
        succeeded = _int(state.get("recovered_failed_24h"))
        plan = attempted
    elif module == "social":
        plan = _int(state.get("active_projects"))
        attempted = plan
        details = state.get("scheduler_details") if isinstance(state.get("scheduler_details"), dict) else {}
        succeeded = _int(details.get("published_count") or details.get("posted_count") or 0)
        waiting = _int(details.get("blocked_count") or 0)
        failed = 1 if str(state.get("scheduler_state") or "").lower() in {"error", "failed", "critical"} else 0
    elif module == "prospecting":
        runtime = state.get("runtime_health") if isinstance(state.get("runtime_health"), dict) else {}
        plan = _int(runtime.get("fresh_24h"))
        attempted = plan
        succeeded = max(0, plan - _int(runtime.get("outreach_blocked")) - _int(runtime.get("stuck_outreach")))
        failed = _int(runtime.get("stuck_outreach"))
        waiting = _int(runtime.get("outreach_retrying")) + _int(runtime.get("outreach_blocked")) + _int(runtime.get("sources_blocked_external"))
    fact_state = _state_from_diagnosis(diagnosis, waiting_quantity=waiting, failed_quantity=failed)
    next_retry = None if fact_state == "OK" else (now + timedelta(hours=1)).isoformat()
    return {
        "plan_quantity": plan,
        "attempted_quantity": attempted,
        "succeeded_quantity": succeeded,
        "failed_quantity": failed,
        "waiting_quantity": waiting,
        "last_attempt_at": now.isoformat() if attempted or waiting or failed else None,
        "last_success_at": now.isoformat() if succeeded else None,
        "next_retry_at": next_retry,
        "state": fact_state,
        "reason": str((diagnosis or {}).get("classification") or "runtime_state_observed"),
        "diagnosis": _json_safe(diagnosis or {}),
        "metrics": metrics,
    }


def obligation_requirement(db, module: str) -> ControlRequirement | None:
    return db.query(ControlRequirement).filter(
        ControlRequirement.key == f"runtime_ledger.{module}.hourly",
        ControlRequirement.status == "active",
    ).first()


def record_module_snapshot(db, module: str, *, account_id: str | None = None, bucket: str | None = None):
    if module not in REQUIRED_LEDGER_MODULES:
        raise ValueError(f"runtime ledger module not required: {module}")
    requirement = obligation_requirement(db, module)
    if requirement is None:
        raise RuntimeError(f"runtime ledger obligation missing: {module}")
    adapter = get_adapter(module)
    if adapter is None:
        raise RuntimeError(f"module adapter missing: {module}")
    now = datetime.now(timezone.utc)
    bucket = bucket or now.strftime("%Y%m%d%H")
    state = adapter.get_state(db, account_id) or {}
    diagnosis = adapter_diagnose(db, adapter, account_id, requirement=requirement, actual_state=state) or {}
    fact = _facts(module, state, diagnosis)
    plan_date = str(state.get("business_date") or now.date().isoformat())
    due_at = (now.replace(minute=59, second=59, microsecond=0)).isoformat()
    plan = {
        "kind": "runtime_plan_fact_snapshot",
        "obligation_id": f"{module}:{account_id or '*'}:{bucket}",
        "module": module,
        "operation": "RUNTIME_LEDGER_SNAPSHOT",
        "plan_date": plan_date,
        "plan_quantity": fact["plan_quantity"],
        "due_at": due_at,
        "owner": str((requirement.source_json or {}).get("owner") or ""),
        "steps": ["read_actual_state", "diagnose", "materialize_plan_fact", "record_evidence"],
    }
    idem = f"runtime-ledger:{module}:{account_id or '*'}:{bucket}"
    execution = start_execution(db, requirement, account_id=account_id, plan=plan, idempotency_key=idem)
    if str(execution.status) in {"pass", "degraded", "failed", "blocked", "waiting_external", "waiting_owner"}:
        # RUNTIME_LEDGER_VERIFIED_NOT_EQUAL_PASS_V1: this invocation already
        # performed a fresh adapter read + diagnosis above. A degraded/waiting
        # business fact is still freshly verified evidence; never conflate
        # verification freshness with business success.
        requirement.last_verified_at = now
        return execution
    steps = initialize_execution_steps(db, execution)
    for status in ("parsed", "mapped_to_requirements", "planned", "preflight", "executing", "technical_tested"):
        try:
            transition_execution(db, execution, status)
        except ValueError:
            pass
    record_execution_attempt(
        db, execution, step_id=None, strategy="read_only_adapter_snapshot", status="pass",
        result={"state": fact["state"], "attempted_quantity": fact["attempted_quantity"], "succeeded_quantity": fact["succeeded_quantity"]},
    )
    add_evidence(
        db, execution, level="L5", evidence_type="runtime_plan_fact", source=f"adapter:{module}", passed=True,
        payload={"plan": plan, "fact": fact},
    )
    transition_execution(db, execution, "runtime_verified", actual_state=fact)
    if fact["state"] == "OK":
        transition_execution(db, execution, "pass", actual_state=fact)
    else:
        transition_execution(
            db, execution, "degraded", actual_state=fact,
            reason=fact["reason"], error_code=f"RUNTIME_LEDGER_{fact['state']}",
        )
    return execution


def record_required_snapshots(db, *, bucket: str | None = None) -> dict:
    """Record all required module snapshots without letting one failure roll back the tick."""
    rows = {}
    errors = {}
    for module in REQUIRED_LEDGER_MODULES:
        try:
            # A module-specific failure rolls back only this snapshot.  Never
            # discard incidents/evidence produced earlier in the Guardian tick.
            with db.begin_nested():
                row = record_module_snapshot(db, module, bucket=bucket)
                rows[module] = {"execution_id": row.id, "status": row.status, "actual": row.actual_state_json or {}}
        except Exception as exc:
            errors[module] = type(exc).__name__
    return {"ok": not errors, "modules": rows, "errors": errors, "required_modules": list(REQUIRED_LEDGER_MODULES)}
