"""Reliability primitives shared by BORIS modules.

No second queue and no second business engine: this module only provides
trace IDs, operational events, feature/kill gates and persistent circuits.
"""
from __future__ import annotations

import contextvars
import glob
import hashlib
import json
import os
import shutil
import uuid
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta

from sqlalchemy import text, func

from app.models.background_job import BackgroundJob
from app.models.reliability import (ReliabilityCircuit, ReliabilityEvent, ReliabilityFlag,
                                    ReliabilityKillSwitch, ReliabilityHeartbeat, ReliabilityRetryBudget)

_trace_id_var = contextvars.ContextVar("boris_trace_id", default=None)
_heartbeat_write_cache: dict[str, float] = {}

CRITICAL_KINDS = {"message_send", "inbound_message", "call_ingest", "crm_sync", "payment"}
INTERACTIVE_KINDS = {"campaign_deliver", "campaign_enhance", "campaign_prepare"}
HEAVY_KINDS = {"photo_session", "thumbnail_sweep", "media_reconcile", "media_backfill"}


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(value: str | None) -> str:
    value = (value or "").strip()[:64] or new_trace_id()
    _trace_id_var.set(value)
    return value


def get_trace_id() -> str:
    value = _trace_id_var.get()
    if not value:
        value = new_trace_id()
        _trace_id_var.set(value)
    return value


def job_priority(kind: str) -> int:
    kind = str(kind or "")
    if kind in CRITICAL_KINDS:
        return 0
    if kind in INTERACTIVE_KINDS:
        return 10
    if kind in HEAVY_KINDS:
        return 30
    return 20


def job_module(kind: str) -> str:
    kind = str(kind or "")
    if kind.startswith("campaign_"):
        return "marketing"
    if kind in {"photo_session", "thumbnail_sweep", "media_reconcile", "media_backfill"}:
        return "media"
    if kind in {"message_send", "inbound_message"}:
        return "messages"
    if kind == "call_ingest":
        return "telephony"
    if kind == "crm_sync":
        return "crm"
    if kind == "payment":
        return "billing"
    return "background"


def record_event(db, module: str, event_type: str, message: str = "", *,
                 severity: str = "info", state: str = "open",
                 account_id: str | None = None, details: dict | None = None,
                 trace_id: str | None = None) -> ReliabilityEvent:
    row = ReliabilityEvent(
        trace_id=(trace_id or get_trace_id()), account_id=account_id,
        module=str(module or "system")[:64], event_type=str(event_type or "event")[:64],
        severity=str(severity or "info")[:16], state=str(state or "open")[:32],
        message=(message or "")[:4000], details_json=(details or {}),
    )
    db.add(row)
    db.flush()
    return row


def feature_enabled(db, key: str, account_id: str | None = None, default: bool = True) -> bool:
    """Account override > global flag > default."""
    if account_id:
        row = db.query(ReliabilityFlag).filter(
            ReliabilityFlag.key == key,
            ReliabilityFlag.scope_type == "account",
            ReliabilityFlag.scope_id == str(account_id),
        ).first()
        if row is not None:
            return bool(row.enabled)
    row = db.query(ReliabilityFlag).filter(
        ReliabilityFlag.key == key,
        ReliabilityFlag.scope_type == "global",
        ReliabilityFlag.scope_id == "*",
    ).first()
    return bool(row.enabled) if row is not None else bool(default)


def module_blocked(db, module: str, account_id: str | None = None) -> tuple[bool, str | None]:
    """Account master switch wins, then account module, then global module.

    `actions` is the canonical tenant-wide OFF switch. Once set, every job
    module must fail closed even if a newly introduced module did not yet get
    its own explicit kill-switch row.
    """
    if account_id:
        master = db.query(ReliabilityKillSwitch).filter(
            ReliabilityKillSwitch.module == "actions",
            ReliabilityKillSwitch.account_id == str(account_id),
        ).first()
        if master is not None and master.blocked:
            return True, master.reason or "account actions disabled"
        row = db.query(ReliabilityKillSwitch).filter(
            ReliabilityKillSwitch.module == module,
            ReliabilityKillSwitch.account_id == str(account_id),
        ).first()
        if row is not None and row.blocked:
            return True, row.reason
    row = db.query(ReliabilityKillSwitch).filter(
        ReliabilityKillSwitch.module == module,
        ReliabilityKillSwitch.account_id == "*",
    ).first()
    return (bool(row.blocked), row.reason) if row is not None else (False, None)


_ACCOUNT_CIRCUIT_MARKER = "::acct::"


def _account_circuit_key(dependency: str, account_id: str | None) -> str:
    dependency = str(dependency or "unknown")[:96]
    account = str(account_id or "").strip()
    if not account:
        return dependency
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "-" for ch in account)
    digest = hashlib.sha256(account.encode("utf-8")).hexdigest()[:8]
    prefix = dependency + _ACCOUNT_CIRCUIT_MARKER
    budget = max(1, 96 - len(prefix) - len(digest) - 1)
    return (prefix + safe[:budget] + "-" + digest)[:96]


def _is_account_circuit_key(value: str | None) -> bool:
    return _ACCOUNT_CIRCUIT_MARKER in str(value or "")


def _base_circuit_dependency(value: str | None) -> str:
    return str(value or "").split(_ACCOUNT_CIRCUIT_MARKER, 1)[0]


def _account_ref_from_circuit(value: str | None) -> str | None:
    raw = str(value or "")
    if _ACCOUNT_CIRCUIT_MARKER not in raw:
        return None
    return raw.split(_ACCOUNT_CIRCUIT_MARKER, 1)[1] or None


def _shared_provider_failure(dependency: str, exc: Exception) -> bool:
    """Whether a failure is likely shared transport/provider health.

    Account/input/auth errors stay tenant-scoped. Network timeouts, disconnects,
    provider 5xx and shared rate limits also affect the base/global circuit so a
    real provider outage cannot stampede once per tenant.
    """
    name = type(exc).__name__.lower()
    text_value = (repr(exc) + " " + str(exc)).lower()
    # GEMINI_TENANT_TIMEOUT_ISOLATION_V1:
    # Gemini sales runs through a bounded CLI subprocess. A single account can
    # time out because of prompt/runtime variance while another account succeeds
    # seconds earlier. Treat a plain Gemini timeout as tenant-local; shared
    # rate-limit/5xx/network evidence below can still open the global circuit.
    if str(dependency or "").startswith("gemini.sales"):
        if "shared_quota_429" in text_value or "shared_unsupported_location_400" in text_value:
            return True
    if str(dependency or "").startswith("gemini.sales") and "timeout" in text_value:
        shared_non_timeout = (
            "connecterror", "connectionerror", "remoteprotocol",
            "networkerror", "serverdisconnected", "service unavailable",
            "connection reset", "name resolution", "dns", "502", "503", "504",
            "ratelimit", "rate limit", "too many requests", "429",
        )
        if not any(token in name or token in text_value for token in shared_non_timeout):
            return False

    shared_tokens = (
        "timeout", "connecterror", "connectionerror", "remoteprotocol",
        "networkerror", "serverdisconnected", "service unavailable",
        "connection reset", "name resolution", "dns", "502", "503", "504",
        "ratelimit", "rate limit", "too many requests", "429",
    )
    if any(token in name or token in text_value for token in shared_tokens):
        return True
    # OpenAI authentication is platform-key scoped in BORIS, unlike Avito where
    # credentials are per account. Treat it as a shared configuration outage.
    if str(dependency or "").startswith("openai.") and (
        "authentication" in name or "authentication" in text_value or "unauthorized" in text_value or "invalid api key" in text_value
    ):
        return True
    return False


def circuit_allows(db, dependency: str) -> bool:
    row = db.query(ReliabilityCircuit).filter(ReliabilityCircuit.dependency == dependency).first()
    if row is None or row.state == "closed":
        return True
    now = datetime.now(timezone.utc)
    if row.opened_until is not None and row.opened_until <= now:
        # Only one process/node may own the half-open probe. Lock the circuit
        # row so concurrent workers cannot all observe an expired OPEN state
        # and stampede the recovering provider at once.
        locked = db.query(ReliabilityCircuit).filter(
            ReliabilityCircuit.dependency == dependency,
        ).with_for_update().first()
        if locked is None or locked.state == "closed":
            return True
        now = datetime.now(timezone.utc)
        if locked.state in ("open", "half_open") and locked.opened_until is not None and locked.opened_until <= now:
            locked.state = "half_open"
            # Move opened_until forward as a distributed probe lease. Other
            # transactions now see HALF_OPEN and are denied until this owner
            # records success/failure. If the probe process itself dies, the
            # expired HALF_OPEN lease can be acquired by exactly one new caller.
            locked.opened_until = now + timedelta(seconds=30)
            db.flush()
            return True
        return False
    # HALF_OPEN is a lease owned by another transaction/process. Never allow
    # additional callers through; circuit_success/circuit_failure closes or
    # reopens it after the single probe finishes.
    return False


def circuit_success(db, dependency: str) -> None:
    now = datetime.now(timezone.utc)
    row = db.query(ReliabilityCircuit).filter(ReliabilityCircuit.dependency == dependency).first()
    if row is None:
        row = ReliabilityCircuit(dependency=dependency)
        db.add(row)
    row.state = "closed"
    row.consecutive_failures = 0
    row.opened_until = None
    row.last_success_at = now
    row.last_error = None
    db.flush()


def circuit_failure(db, dependency: str, error: str, *, threshold: int = 5, cooldown_seconds: int = 120) -> str:
    now = datetime.now(timezone.utc)
    row = db.query(ReliabilityCircuit).filter(ReliabilityCircuit.dependency == dependency).first()
    if row is None:
        row = ReliabilityCircuit(dependency=dependency)
        db.add(row)
        db.flush()
    row.consecutive_failures = int(row.consecutive_failures or 0) + 1
    row.last_failure_at = now
    row.last_error = str(error or "")[:2000]
    if row.consecutive_failures >= threshold:
        row.state = "open"
        row.opened_until = now + timedelta(seconds=max(30, int(cooldown_seconds)))
    db.flush()
    return row.state


class CircuitOpen(RuntimeError):
    """Dependency is temporarily isolated; caller should degrade or requeue."""


class ProviderDeferred(RuntimeError):
    """Per-tenant provider quota is temporarily exhausted; retry later, not a failure."""
    def __init__(self, dependency: str, retry_after_seconds: int = 30):
        self.dependency = str(dependency or "unknown")
        self.retry_after_seconds = max(5, int(retry_after_seconds or 30))
        super().__init__(f"provider temporarily deferred: {self.dependency}")


def dependency_call(dependency: str, fn, *args, threshold: int = 5, cooldown_seconds: int = 120, account_id: str | None = None, tenant_limit_per_minute: int = 120, **kwargs):
    """Call an external provider without holding a PostgreSQL transaction open.

    Circuit admission and outcome persistence use separate short DB sessions. The
    external network call itself runs with no reliability transaction checked out.
    This preserves half-open probe leasing while eliminating idle-in-transaction
    kills during slow provider requests.
    """
    from app.db.session import SessionLocal
    # AVITO_DEPENDENCY_SHARED_ACCOUNT_THROTTLE_V1: central Avito dependency
    # calls (currently token refresh, and any future avito.* integration) must
    # honor the same cross-process/provider-alias Retry-After ledger before
    # external I/O. This closes legacy callers that forgot their own precheck.
    if account_id is not None and str(dependency or "").startswith("avito."):
        try:
            from app.services.avito_account_throttle import account_throttle_remaining
            _avito_retry = account_throttle_remaining(str(account_id))
        except Exception:
            _avito_retry = 0
        if _avito_retry > 0:
            raise ProviderDeferred(str(dependency or "avito"), int(_avito_retry))
    if account_id is not None and not provider_tenant_budget_allow(dependency, account_id, limit_per_minute=tenant_limit_per_minute):
        retry_after = max(5, int(61 - (time.time() % 60)))
        raise ProviderDeferred(dependency, retry_after)

    base_key = str(dependency or "unknown")[:96]
    tenant_key = _account_circuit_key(base_key, account_id)

    # Phase 1: short admission transaction. If circuit_allows acquires a half-open
    # lease, commit it before the external request so other workers see the lease.
    db = SessionLocal()
    try:
        if not circuit_allows(db, base_key):
            db.rollback()
            raise CircuitOpen(f"dependency circuit open: {base_key}")
        if tenant_key != base_key and not circuit_allows(db, tenant_key):
            db.rollback()
            raise CircuitOpen(f"tenant dependency circuit open: {base_key}")
        db.commit()
    finally:
        db.close()

    try:
        value = fn(*args, **kwargs)
        # AVITO_DEPENDENCY_429_PUBLISH_V1: HTTP clients commonly return a 429
        # response instead of raising. Treat it as provider deferral, publish
        # Retry-After tenant-wide, and never mark the dependency call successful.
        if account_id is not None and str(dependency or "").startswith("avito.") and int(getattr(value, "status_code", 0) or 0) == 429:
            _retry = 30
            try:
                _raw = str((getattr(value, "headers", {}) or {}).get("Retry-After") or "").strip()
                if _raw:
                    try:
                        _retry = max(0, int(float(_raw)))
                    except Exception:
                        from email.utils import parsedate_to_datetime
                        _when = parsedate_to_datetime(_raw)
                        if _when.tzinfo is None:
                            _when = _when.replace(tzinfo=timezone.utc)
                        _retry = max(0, int((_when.astimezone(timezone.utc)-datetime.now(timezone.utc)).total_seconds()))
            except Exception:
                pass
            try:
                from app.services.avito_account_throttle import record_account_throttle
                _retry = record_account_throttle(str(account_id), _retry or 30, source=str(dependency or "avito")[:70]+"_429")
            except Exception:
                pass
            raise ProviderDeferred(str(dependency or "avito"), int(_retry or 30))
    except Exception as exc:
        # Phase 2 failure: persist outcome in a fresh short transaction.
        db = SessionLocal()
        try:
            _safe_error = (type(exc).__name__ + ": " + str(exc))[:1000]
            tenant_state = circuit_failure(db, tenant_key, _safe_error, threshold=threshold,
                                           cooldown_seconds=cooldown_seconds)
            global_state = None
            if tenant_key == base_key:
                global_state = tenant_state
            elif _shared_provider_failure(base_key, exc):
                global_state = circuit_failure(db, base_key, _safe_error, threshold=threshold,
                                               cooldown_seconds=cooldown_seconds)
            effective_state = global_state or tenant_state
            record_event(
                db, "dependency", "dependency_failure",
                f"Внешняя зависимость {base_key} вернула ошибку",
                severity="warning" if effective_state != "open" else "critical",
                account_id=str(account_id) if account_id is not None else None,
                details={
                    "dependency": base_key,
                    "scope": "account" if tenant_key != base_key else "global",
                    "tenant_circuit": tenant_key if tenant_key != base_key else None,
                    "tenant_state": tenant_state,
                    "global_state": global_state,
                    "shared_provider_failure": bool(global_state is not None and tenant_key != base_key),
                    "error_type": type(exc).__name__,
                    "error_detail": _safe_error,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        raise

    # Phase 2 success: close tenant/global circuits in a fresh short transaction.
    db = SessionLocal()
    try:
        circuit_success(db, tenant_key)
        if tenant_key != base_key:
            circuit_success(db, base_key)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return value


def resilient_call(db, dependency: str, fn, *args, threshold: int = 5,
                   cooldown_seconds: int = 120, account_id: str | None = None,
                   **kwargs):
    """Run one external operation behind global + tenant circuit isolation.

    A tenant/input failure opens only that account's circuit. Shared transport,
    provider 5xx/rate-limit failures also update the base/global circuit so a
    genuine outage still stops cross-tenant stampedes. Retry ownership remains at
    the operation/job layer; this function performs no nested retries.
    """
    base_key = str(dependency or "unknown")[:96]
    tenant_key = _account_circuit_key(base_key, account_id)

    if not circuit_allows(db, base_key):
        raise CircuitOpen(f"dependency circuit open: {base_key}")
    if tenant_key != base_key and not circuit_allows(db, tenant_key):
        raise CircuitOpen(f"tenant dependency circuit open: {base_key}")

    try:
        value = fn(*args, **kwargs)
    except Exception as exc:
        tenant_state = circuit_failure(
            db, tenant_key, type(exc).__name__, threshold=threshold,
            cooldown_seconds=cooldown_seconds,
        )
        global_state = None
        if tenant_key == base_key:
            global_state = tenant_state
        elif _shared_provider_failure(base_key, exc):
            global_state = circuit_failure(
                db, base_key, type(exc).__name__, threshold=threshold,
                cooldown_seconds=cooldown_seconds,
            )
        effective_state = global_state or tenant_state
        record_event(
            db, "dependency", "dependency_failure",
            f"Внешняя зависимость {base_key} вернула ошибку",
            severity="warning" if effective_state != "open" else "critical",
            account_id=str(account_id) if account_id is not None else None,
            details={
                "dependency": base_key,
                "scope": "account" if tenant_key != base_key else "global",
                "tenant_circuit": tenant_key if tenant_key != base_key else None,
                "tenant_state": tenant_state,
                "global_state": global_state,
                "shared_provider_failure": bool(global_state is not None and tenant_key != base_key),
                "error_type": type(exc).__name__,
            },
        )
        raise

    circuit_success(db, tenant_key)
    if tenant_key != base_key:
        # One successful tenant request proves shared provider transport is alive.
        circuit_success(db, base_key)
    return value


def heartbeat(module: str, worker_id: str, *, state: str = "ok",
              account_id: str = "*", details: dict | None = None,
              min_interval_seconds: int = 15) -> None:
    """Cheap throttled liveness pulse safe for hot worker loops."""
    key = f"{module}:{worker_id}:{account_id}"
    now_mono = time.monotonic()
    # GUARDIAN_COMPLETION_HEARTBEAT_PROOF_V1: the timer/service already proves
    # that the short System Brain oneshot is in-flight. Do not overwrite the
    # previous completed business proof with a transient "phase=start" pulse.
    if str(module)=="control_plane" and str(worker_id)=="guardian" and isinstance(details,dict) and str(details.get("phase") or "").lower()=="start":
        return
    # GUARDIAN_COMPLETION_HEARTBEAT_PROOF_V1: the System Brain is a short
    # oneshot. Its start heartbeat and completed heartbeat can be <5 seconds
    # apart. Never throttle terminal/error proof, otherwise the owner UI sees
    # only "started" and cannot prove what safe_recovery actually completed.
    terminal_proof = bool(
        str(state or "ok").lower() in {"error", "failed", "critical"}
        or (isinstance(details, dict) and ("safe_recovery" in details or str(details.get("phase") or "").lower() in {"completed", "completed_error"}))
    )
    if not terminal_proof and now_mono - _heartbeat_write_cache.get(key, 0.0) < max(5, min_interval_seconds):
        return
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        row = db.query(ReliabilityHeartbeat).filter(
            ReliabilityHeartbeat.module == str(module)[:64],
            ReliabilityHeartbeat.worker_id == str(worker_id)[:128],
            ReliabilityHeartbeat.account_id == str(account_id or "*")[:255],
        ).first()
        if row is None:
            row = ReliabilityHeartbeat(module=str(module)[:64], worker_id=str(worker_id)[:128],
                                       account_id=str(account_id or "*")[:255])
            db.add(row)
        row.state = str(state or "ok")[:16]
        row.details_json = details or {}
        row.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        _heartbeat_write_cache[key] = now_mono
    except Exception:
        db.rollback()
    finally:
        db.close()


def heartbeat_health(db, *, stale_seconds: int = 300) -> dict:
    now = datetime.now(timezone.utc)
    rows = db.query(ReliabilityHeartbeat).order_by(ReliabilityHeartbeat.module,
                                                   ReliabilityHeartbeat.worker_id).all()
    # Worker pool size can be reduced at runtime (for example 8 -> 4). Old
    # heartbeat rows remain in PostgreSQL by design, but they are historical
    # registrations, not dead current workers. Counting them as stale made
    # admission_control permanently critical and stopped every interactive
    # Campaign job after a safe pool resize. The current background runtime
    # heartbeat is the authority for the expected jobs pool size.
    expected_jobs = None
    runtime_rows = [r for r in rows if str(r.module or '') == 'runtime' and str(r.worker_id or '') == 'background_runtime']
    if runtime_rows:
        freshest = max(runtime_rows, key=lambda r: r.last_seen_at or datetime.min.replace(tzinfo=timezone.utc))
        try:
            expected_jobs = max(0, int((freshest.details_json or {}).get('job_workers')))
        except Exception:
            expected_jobs = None
    workers = []
    stale = 0
    retired = 0
    import re as _re_hb
    # PERIODIC_HEARTBEAT_SLA_V1: not every heartbeat belongs to a continuously
    # running worker. ROP and reactivation are timer-driven schedulers with
    # canonical domain stall thresholds of 30 minutes. Counting them stale
    # after the generic 5-minute threshold made capacity critical during their
    # normal idle window and unnecessarily blocked HA deploys.
    periodic_stale_seconds = {
        ("runtime", "rop_auto_scheduler"): 1800,
        ("runtime", "reactivation_scheduler"): 1800,
        # SOCIAL_AUX_HEARTBEAT_SLA_V1: these are timer-driven self-heal
        # workers, not continuously running daemons. Their heartbeat SLA must
        # reflect the configured cadence or generic 5-minute health would mark
        # a healthy 15-minute replacement verifier as stale.
        ("runtime", "social_due_rescue"): 360,
        ("runtime", "social_terminal_reconcile"): 180,
        ("runtime", "social_vk_replacement_watch"): 1200,
        ("runtime", "social_published_at_audit"): 360,
        ("runtime", "social_transport_readiness"): 2400,
        # CAMPAIGN_POST_PUBLISH_HEARTBEAT_SLA_V1: background worker executes
        # this DB-only watcher every 900s. The generic 300s SLA falsely marked
        # a healthy periodic worker stale for most of every cycle, making host
        # capacity critical and blocking canonical HA deployment.
        ("runtime", "campaign_post_publish_watchdog"): 1200,
        # MEDIA_PUBLISHED_SELF_HEAL_HEARTBEAT_SLA_V1: this worker is produced
        # only by the canonical daily media_reconcile cron (04:25 UTC). The
        # generic 5-minute SLA made a healthy daily worker stale for almost the
        # whole day and then blocked its own heavy recovery job via admission.
        # Allow one daily period plus recovery/queue jitter before declaring it
        # stale. The admission guard below separately prevents a true stale
        # copy of this exact worker from deadlocking its own recovery.
        ("media", "published_campaign_self_heal"): 93600,
    }
    rop_completed = next(
        (r for r in rows if str(r.module or "") == "runtime" and str(r.worker_id or "") == "rop_auto_scheduler"),
        None,
    )
    for row in rows:
        seen = row.last_seen_at
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = int((now - seen).total_seconds()) if seen else 10**9
        module_key = str(row.module or "")
        worker_key = str(row.worker_id or "")
        worker_stale_seconds = max(
            int(stale_seconds),
            int(periodic_stale_seconds.get((module_key, worker_key), stale_seconds)),
        )
        is_retired = False
        if expected_jobs is not None and module_key == 'jobs':
            m = _re_hb.fullmatch(r'background_jobs_(\d+)', worker_key)
            if m and int(m.group(1)) >= expected_jobs:
                is_retired = True
        # ROP_INFLIGHT_TERMINAL_RETIRE_V1: the inflight heartbeat is an execution
        # marker, not a persistent worker. Once the canonical scheduler heartbeat
        # is at least as new and proves phase=completed, the older inflight row is
        # historical evidence and must not count as a dead capacity worker.
        if worker_key == "rop_auto_scheduler_inflight" and rop_completed is not None and seen is not None:
            completed_seen = rop_completed.last_seen_at
            if completed_seen is not None and completed_seen.tzinfo is None:
                completed_seen = completed_seen.replace(tzinfo=timezone.utc)
            completed_phase = str((rop_completed.details_json or {}).get("phase") or "").lower()
            if completed_seen is not None and completed_seen >= seen and completed_phase in {"completed", "complete", "done"}:
                is_retired = True
        is_stale = (age > worker_stale_seconds) and not is_retired
        if is_stale:
            stale += 1
        if is_retired:
            retired += 1
        workers.append({"module": row.module, "worker_id": row.worker_id,
                        "account_id": row.account_id,
                        "state": "retired" if is_retired else ("stale" if is_stale else row.state),
                        "age_sec": age, "details": row.details_json or {}})
    return {"state": "critical" if stale else ("degraded" if not rows else "ok"),
            "registered": len(rows), "stale": stale, "retired": retired,
            "expected_job_workers": expected_jobs, "workers": workers}


def queue_breakdown(db) -> dict:
    """Queue pressure split by module and tenant to expose noisy neighbours."""
    rows = db.query(BackgroundJob).filter(
        BackgroundJob.status.in_(("queued", "running", "validating"))
    ).all()
    modules: dict[str, dict] = {}
    accounts: dict[str, dict] = {}
    for job in rows:
        mod = job_module(job.kind)
        acc = str(job.account_id or "unscoped")
        m = modules.setdefault(mod, {"queued": 0, "running": 0, "total": 0})
        a = accounts.setdefault(acc, {"queued": 0, "running": 0, "total": 0})
        for target in (m, a):
            target["total"] += 1
            if job.status == "queued":
                target["queued"] += 1
            else:
                target["running"] += 1
    hot_accounts = sorted(({"account_id": k, **v} for k, v in accounts.items()),
                          key=lambda x: (-x["total"], x["account_id"]))[:10]
    return {"modules": modules, "hot_accounts": hot_accounts}



# Process-local aggregate retry budgets. Circuit state remains persistent in DB;
# this fast guard prevents many tenants from collectively creating a retry storm.
import threading
_retry_budget_lock = threading.Lock()
_retry_budget_windows: dict[str, tuple[int, int]] = {}

def retry_budget_allow(dependency: str, *, limit_per_minute: int = 60) -> bool:
    """Atomically consume a cluster-wide minute budget in PostgreSQL.

    All API replicas and the background worker share this counter. Fail closed
    only when the bucket is actually exhausted; DB errors fall back to the old
    process-local guard so reliability storage cannot take down business traffic.
    """
    minute = int(time.time() // 60)
    key = str(dependency or "unknown")[:255]
    limit = max(1, int(limit_per_minute))
    try:
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(text("""
                INSERT INTO reliability_retry_budgets (budget_key, minute_bucket, used, updated_at)
                VALUES (:k, :m, 1, now())
                ON CONFLICT (budget_key, minute_bucket) DO UPDATE
                SET used = reliability_retry_budgets.used + 1, updated_at = now()
                WHERE reliability_retry_budgets.used < :lim
                RETURNING used
            """), {"k": key, "m": minute, "lim": limit}).fetchone()
            db.commit()
            allowed = row is not None
            # Cheap opportunistic cleanup, deterministic once per key/minute.
            if allowed and (hash(key) % 61 == minute % 61):
                try:
                    db.execute(text("DELETE FROM reliability_retry_budgets WHERE minute_bucket < :old"), {"old": minute - 120})
                    db.commit()
                except Exception:
                    db.rollback()
            return allowed
        finally:
            db.close()
    except Exception:
        with _retry_budget_lock:
            win, used = _retry_budget_windows.get(key, (minute, 0))
            if win != minute: win, used = minute, 0
            if used >= limit:
                _retry_budget_windows[key] = (win, used); return False
            _retry_budget_windows[key] = (win, used + 1); return True


def _provider_tenant_identity(provider: str, account_id: str | None) -> str:
    """Canonical provider-side tenant identity for shared rate budgets."""
    tenant = str(account_id or "platform").strip()[:96] or "platform"
    provider = str(provider or "unknown").strip()[:96] or "unknown"
    # AVITO_PROVIDER_BUDGET_ALIAS_COLLAPSE_V1: two BORIS aliases backed by one
    # avito_user_id must consume one cluster rate budget, otherwise each alias
    # can independently reach the provider limit before shared 429 is observed.
    if account_id is not None and provider.startswith("avito."):
        try:
            from app.services.avito_account_throttle import _tenant_keys
            uid_keys = [k for k in _tenant_keys(str(account_id)) if str(k).startswith("uid:")]
            if uid_keys:
                tenant = sorted(uid_keys)[0][:96]
        except Exception:
            pass
    return tenant


def provider_tenant_budget_allow(provider: str, account_id: str | None, *, limit_per_minute: int = 120) -> bool:
    """Consume a cluster-wide provider budget scoped to one real provider tenant.

    The underlying retry_budget_allow counter is PostgreSQL-backed, therefore
    API-1, API-2 and the background worker see one shared limit.
    """
    tenant = _provider_tenant_identity(provider, account_id)
    provider = str(provider or "unknown").strip()[:96] or "unknown"
    return retry_budget_allow(f"provider:{provider}:tenant:{tenant}", limit_per_minute=limit_per_minute)


def provider_tenant_budget_snapshot(db=None) -> dict:
    """Current cluster provider/tenant counters from PostgreSQL."""
    minute = int(time.time() // 60)
    own = False
    if db is None:
        from app.db.session import SessionLocal
        db = SessionLocal(); own = True
    try:
        rows = db.execute(text("""
            SELECT budget_key, minute_bucket, used
            FROM reliability_retry_budgets
            WHERE minute_bucket >= :m AND budget_key LIKE 'provider:%'
            ORDER BY minute_bucket DESC, budget_key ASC
            LIMIT 500
        """), {"m": minute - 1}).fetchall()
        return {str(r[0]): {"minute": int(r[1]), "used": int(r[2]), "current": int(r[1]) == minute} for r in rows}
    except Exception:
        return {k:v for k,v in retry_budget_snapshot().items() if k.startswith("provider:")}
    finally:
        if own: db.close()


def retry_budget_snapshot() -> dict:
    minute = int(time.time() // 60)
    with _retry_budget_lock:
        return {k: {"minute": w, "used": u, "current": w == minute}
                for k, (w, u) in _retry_budget_windows.items() if w >= minute - 1}


def retry_delay_seconds(attempt: int, job_id: int = 0, *, base: int = 5, cap: int = 300) -> int:
    """Exponential backoff with bounded jitter to prevent synchronized retry storms."""
    attempt = max(1, int(attempt or 1))
    delay = min(cap, base * (2 ** min(attempt - 1, 6)))
    jitter = random.Random((int(job_id or 0) * 1009) + attempt).uniform(0.85, 1.15)
    return max(base, min(cap, int(delay * jitter)))


def queue_health(db) -> dict:
    """Operational health of the one canonical background_jobs queue."""
    now = datetime.now(timezone.utc)
    queued = db.query(BackgroundJob).filter(BackgroundJob.status == "queued").count()
    ready_filter = (BackgroundJob.status == "queued",
                    ((BackgroundJob.scheduled_at.is_(None)) | (BackgroundJob.scheduled_at <= now)))
    ready_queued = db.query(BackgroundJob).filter(*ready_filter).count()
    deferred_queued = max(0, queued - ready_queued)
    running = db.query(BackgroundJob).filter(BackgroundJob.status.in_(("running", "validating"))).count()
    failed_rows = db.query(BackgroundJob).filter(
        BackgroundJob.status == "failed",
        BackgroundJob.finished_at >= now - timedelta(hours=24),
    ).all()
    # Business rejections (validation, tariff/data prerequisites) are visible to
    # the owning workflow but must not mark the BORIS platform itself degraded.
    # Guardian health is reserved for infrastructure/transient execution faults.
    business_markers = (
        "проверка avito не пройдена", "validation", "requires_confirmation",
        "тариф", "недостаточно данных", "business_rule", "blocked_",
        # Paid-AI budget/allowance guards are deliberate fail-closed business
        # outcomes, not platform crashes. They must stay visible in the owning
        # workflow without making the global Guardian red.
        "budget_not_allowed", "budget_reserve_failed", "vision_budget",
        # Campaign delivery can terminate on owner/content approval or provider
        # duplicate validation. These are workflow outcomes, not queue/runtime
        # infrastructure failures; keep them visible in campaign state without
        # making the global Guardian page the owner.
        "owner approval blocked invalid items", "owner_approval",
        "message_code 2010", "duplicate",
    )
    business_failed = 0
    platform_failed = 0
    for failed_job in failed_rows:
        err = str(getattr(failed_job, "error_text", "") or "").lower()
        # QUEUE_TERMINAL_RESULT_BUSINESS_CLASSIFICATION_V1: some durable
        # workflow failures are represented in result_json with error_text=NULL
        # (for example Avito duplicate code 2010). Classify from both terminal
        # evidence fields; otherwise Guardian falsely reports a platform crash.
        try:
            result_raw = getattr(failed_job, "result_json", None)
            result_text = json.dumps(result_raw, ensure_ascii=False).lower() if result_raw is not None else ""
        except Exception:
            result_text = str(getattr(failed_job, "result_json", "") or "").lower()
        failure_evidence = err + " " + result_text
        if any(marker in failure_evidence for marker in business_markers):
            business_failed += 1
        else:
            platform_failed += 1
    failed = platform_failed
    # Only runnable backlog contributes to queue-age health. Future-scheduled
    # retries/backpressure deferrals are intentional and must not create a
    # self-sustaining critical signal that prevents their own recovery.
    # QUEUE_AGE_EFFECTIVE_READY_TIME_V1: durable singleton/retry jobs keep the
    # original created_at for audit history while scheduled_at moves forward on
    # every bounded deferral. Ageing them from created_at makes a freshly due
    # recurring job look weeks old and can deadlock deployment/admission. Queue
    # pressure starts when the CURRENT scheduled attempt became runnable.
    oldest = db.query(BackgroundJob).filter(*ready_filter).order_by(func.coalesce(BackgroundJob.scheduled_at, BackgroundJob.created_at).asc()).first()
    oldest_age = 0
    if oldest is not None:
        ready_at = oldest.scheduled_at or oldest.created_at
        if ready_at is not None:
            if ready_at.tzinfo is None:
                ready_at = ready_at.replace(tzinfo=timezone.utc)
            oldest_age = max(0, int((now - ready_at).total_seconds()))
    if oldest_age > 900 or failed >= 20:
        state = "critical"
    elif oldest_age > 180 or failed > 0 or queued > 100:
        state = "degraded"
    else:
        state = "ok"
    return {
        "state": state, "queued": queued, "ready_queued": ready_queued,
        "deferred_queued": deferred_queued, "running": running,
        "failed_24h": failed, "business_failed_24h": business_failed,
        "all_failed_24h": len(failed_rows), "oldest_queued_age_sec": oldest_age,
    }


def storage_health(path: str = "/") -> dict:
    usage = shutil.disk_usage(path)
    free_pct = round((usage.free / usage.total) * 100, 1) if usage.total else 0.0
    used_pct = round(100.0 - free_pct, 1)
    state = "critical" if free_pct < 5 else ("degraded" if free_pct < 12 else "ok")
    return {
        "state": state,
        "used_pct": used_pct,
        "free_pct": free_pct,
        "free_gb": round(usage.free / (1024 ** 3), 1),
        "total_gb": round(usage.total / (1024 ** 3), 1),
    }


def dependency_health(db) -> dict:
    """Dependency isolation summary without making network calls from /health.

    Global provider circuits affect platform health. Account-scoped circuits are
    reported separately so one broken tenant remains visible without marking all
    other clients as degraded.
    """
    rows = db.query(ReliabilityCircuit).order_by(ReliabilityCircuit.dependency).all()
    items = []
    tenant_items = []
    for row in rows:
        data = {
            "dependency": _base_circuit_dependency(row.dependency),
            "state": row.state or "closed",
            "failures": int(row.consecutive_failures or 0),
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_failure_at": row.last_failure_at.isoformat() if row.last_failure_at else None,
            "opened_until": row.opened_until.isoformat() if row.opened_until else None,
        }
        if _is_account_circuit_key(row.dependency):
            data["account_ref"] = _account_ref_from_circuit(row.dependency)
            if data["state"] in ("open", "half_open"):
                tenant_items.append(data)
        else:
            items.append(data)
    open_items = [x for x in items if x["state"] in ("open", "half_open")]
    return {
        "state": "degraded" if open_items else "ok",
        "open": len(open_items),
        "items": items,
        "tenant_state": "degraded" if tenant_items else "ok",
        "tenant_open": len(tenant_items),
        "tenant_items": tenant_items[:100],
    }


def backup_health() -> dict:
    files = glob.glob("/root/BORIS/backups/boris_db_*.sql.gz")
    if not files:
        return {"state": "critical", "message": "Резервные копии БД не найдены"}
    latest = max(files, key=os.path.getmtime)
    mtime = datetime.fromtimestamp(os.path.getmtime(latest), tz=timezone.utc)
    age_hours = round((datetime.now(timezone.utc) - mtime).total_seconds() / 3600, 1)
    size_mb = round(os.path.getsize(latest) / (1024 ** 2), 1)
    state = "critical" if age_hours > 48 or size_mb < 1 else ("degraded" if age_hours > 30 else "ok")
    result = {
        "state": state, "age_hours": age_hours, "size_mb": size_mb,
        "created_at": mtime.isoformat(), "retained": len(files),
    }
    restore_status = "/root/BORIS/backups/restore_qa_status.json"
    try:
        import json
        data = json.loads(Path(restore_status).read_text())
        checked = datetime.fromisoformat(str(data.get("checked_at")).replace("Z", "+00:00"))
        restore_age = round((datetime.now(timezone.utc) - checked).total_seconds() / 3600, 1)
        result["restore_test"] = {**data, "age_hours": restore_age}
        if not data.get("ok") or restore_age > 48:
            result["state"] = "degraded" if result["state"] != "critical" else "critical"
    except Exception:
        result["restore_test"] = {"ok": False, "state": "not_verified"}
        if result["state"] == "ok": result["state"] = "degraded"
    return result


def capacity_plan(db) -> dict:
    """Conservative no-paid-call capacity projection for owner Guardian.

    This is a resource-budget model, not a promise of provider throughput. It uses
    current host/DB headroom and the configured canonical worker pool.
    """
    cap = capacity_health(db)
    workers = max(1, min(int(os.getenv("BORIS_JOB_WORKERS", "8") or 8), 16))
    db_max = int(cap.get("db_max_connections") or 100)
    db_now = int(cap.get("db_connections") or 0)
    # Keep 25% DB reserve for API, schedulers, maintenance and bursts.
    db_safe = max(1, int(db_max * 0.75))
    db_headroom = max(0, db_safe - db_now)
    scenarios = []
    for tenants in (10, 20, 30, 50, 100):
        # Fair queue guarantees progress across tenants; heavy jobs remain 1/account.
        waves = (tenants + workers - 1) // workers
        # Each active background job can hold its business DB session plus one
        # dedicated advisory-lock connection for the cross-node tenant slot.
        # Keep four more connections for heartbeat/control-plane work.
        db_need_peak = min(workers, tenants) * 2 + 4
        state = "ok"
        reasons = []
        if db_need_peak > db_headroom:
            state = "warning"; reasons.append("DB connection reserve is tight")
        if cap.get("memory_used_pct", 0) >= 75:
            state = "warning"; reasons.append("memory pressure")
        if cap.get("state") == "critical":
            state = "warning"; reasons.append("host currently under capacity pressure")
        scenarios.append({"tenants": tenants, "worker_slots": workers, "waves_min": waves,
                          "peak_background_db_connections_est": db_need_peak, "state": state,
                          "note": "; ".join(reasons) if reasons else "fits conservative local resource budget"})
    return {"worker_slots": workers, "db_safe_limit": db_safe, "db_current": db_now,
            "db_headroom_to_safe_limit": db_headroom, "scenarios": scenarios,
            "method": "synthetic resource projection; no paid AI/provider calls"}


def admission_control(db, kind: str) -> dict:
    """Capacity gate for expensive background work; critical jobs pass."""
    kind = str(kind or "")
    if kind in CRITICAL_KINDS:
        return {"allow": True, "class": "critical", "reason": "revenue_critical", "delay_seconds": 0}
    cap = capacity_health(db)
    signals = {x.get("key"): x for x in cap.get("signals", [])}
    critical = [k for k,v in signals.items() if v.get("state") == "critical"]
    warning = [k for k,v in signals.items() if v.get("state") == "warning"]
    is_heavy = kind in HEAVY_KINDS
    is_interactive = kind in INTERACTIVE_KINDS

    # MEDIA_RECONCILE_SELF_DEADLOCK_GUARD_V1: if the only worker-capacity
    # warning is the stale heartbeat that this exact daily media_reconcile job
    # is responsible for refreshing, do not let that heartbeat block its own
    # recovery. Any other stale worker or any real host/DB pressure still keeps
    # the heavy-job gate fail-closed.
    if kind == "media_reconcile" and "stale_workers" in warning:
        hb = heartbeat_health(db)
        stale_rows = [
            x for x in (hb.get("workers") or [])
            if str(x.get("state") or "") == "stale"
        ]
        only_own_stale = bool(stale_rows) and all(
            str(x.get("module") or "") == "media"
            and str(x.get("worker_id") or "") == "published_campaign_self_heal"
            for x in stale_rows
        )
        if only_own_stale:
            warning = [k for k in warning if k != "stale_workers"]

    # CAPACITY_QUEUE_AGE_SELF_REFERENCE_GUARD_V1: queue_age/depth must not block
    # interactive work that is itself the only reason the queue is old. Otherwise
    # a publication watcher creates queue_age pressure, admission blocks the same
    # watcher, and BORIS can never reach its terminal timeout/self-heal state.
    # Real host/DB/worker pressure still blocks it fail-closed.
    if is_interactive:
        critical=[k for k in critical if k not in {"queue_age","queue_depth"}]
    if critical and (is_heavy or is_interactive):
        return {"allow": False, "class": "interactive" if is_interactive else "heavy", "reason": "critical_capacity:" + ",".join(sorted(critical)), "delay_seconds": 45}
    if warning and is_heavy:
        return {"allow": False, "class": "heavy", "reason": "capacity_pressure:" + ",".join(sorted(warning)), "delay_seconds": 20}
    return {"allow": True, "class": "interactive" if is_interactive else ("heavy" if is_heavy else "normal"), "reason": "capacity_available", "delay_seconds": 0}


def capacity_recommendation(db) -> dict:
    """Operational sizing recommendation from current host and queue pressure.

    Advisory only: never changes worker count at runtime. This avoids feedback
    loops and duplicate embedded schedulers while still giving Guardian a clear
    scale signal.
    """
    c = capacity_health(db)
    q = queue_health(db)
    cpu = int(c.get("cpu_count") or 1)
    current = max(1, min(int(os.getenv("BORIS_JOB_WORKERS", str(cpu)) or cpu), 16))
    pressure = {x["key"]: x for x in c.get("signals", [])}
    queue_depth = int(q.get("queued") or 0)
    queue_age = int(q.get("oldest_queued_age_sec") or 0)
    db_pct = float(c.get("db_connections_pct") or 0)
    mem_pct = float(c.get("memory_used_pct") or 0)
    cpu_pct = float((pressure.get("cpu_load") or {}).get("value") or 0)
    if db_pct >= 65 or mem_pct >= 75 or cpu_pct >= 100:
        action = "hold"
        reason = "host_pressure"
        recommended = current
    elif queue_depth >= 50 or queue_age >= 120:
        recommended = min(16, current + 2)
        action = "scale_workers" if recommended > current else "split_workers"
        reason = "queue_pressure"
    else:
        action = "hold"
        reason = "healthy"
        recommended = current
    # Planning envelopes are conservative synthetic concurrency targets, not a
    # promise of paid-provider throughput. They help the owner compare 10/20/30
    # tenant scenarios without generating external API cost.
    scenarios = []
    for tenants in (10, 20, 30, 50, 100):
        heavy_slots = min(current, tenants)
        scenarios.append({"tenants": tenants, "worker_slots": current,
                          "max_parallel_heavy_tenants": heavy_slots,
                          "tenant_heavy_limit": 1, "tenant_normal_limit": 2,
                          "external_paid_calls": 0})
    return {"current_workers": current, "recommended_workers": recommended,
            "action": action, "reason": reason, "scenarios": scenarios}


def capacity_health(db) -> dict:
    """Cheap fleet-capacity snapshot; no paid/provider calls."""
    import os
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = 0.0
    cpu = max(1, int(os.cpu_count() or 1))
    load_pct = round((float(load1) / cpu) * 100, 1)
    mem_total = mem_available = 0
    try:
        vals = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            vals[k] = int(v.strip().split()[0])
        mem_total = int(vals.get("MemTotal", 0))
        mem_available = int(vals.get("MemAvailable", 0))
    except Exception:
        pass
    mem_used_pct = round((1 - mem_available / mem_total) * 100, 1) if mem_total else 0.0
    try:
        max_conn = int(db.execute(text("SHOW max_connections")).scalar() or 100)
        db_conn = int(db.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar() or 0)
        db_active = int(db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE state='active'")).scalar() or 0)
        idle_tx = db.execute(text("""SELECT count(*),COALESCE(max(extract(epoch from (now()-xact_start))),0)
          FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND state='idle in transaction'
            AND xact_start IS NOT NULL AND now()-xact_start>interval '30 seconds'""")).first()
        db_idle_in_tx_30s = int((idle_tx or (0,0))[0] or 0)
        db_oldest_idle_tx_sec = int(float((idle_tx or (0,0))[1] or 0))
    except Exception:
        max_conn, db_conn, db_active, db_idle_in_tx_30s, db_oldest_idle_tx_sec = 100, 0, 0, 0, 0
    db_pct = round(db_conn / max_conn * 100, 1) if max_conn else 0.0
    q = queue_health(db)
    workers = heartbeat_health(db)
    signals = []
    def sig(key, value, warn, crit, unit="%"):
        state = "critical" if value >= crit else ("warning" if value >= warn else "ok")
        signals.append({"key": key, "value": value, "unit": unit, "state": state, "warning_at": warn, "critical_at": crit})
    # Host load is capacity pressure, not by itself a platform outage. A frontend
    # build can legitimately saturate CPU while production remains healthy.
    sig("cpu_load", load_pct, 100, 180)
    sig("memory_used", mem_used_pct, 75, 90)
    sig("db_connections", db_pct, 65, 85)
    # PostgreSQL kills these at the configured idle-in-transaction timeout. Surface
    # the condition before it becomes an SSL disconnect/500 in application code.
    sig("db_idle_in_transaction", db_idle_in_tx_30s, 1, 3, "sessions")
    sig("db_oldest_idle_transaction", db_oldest_idle_tx_sec, 30, 55, "sec")
    sig("queue_age", int(q.get("oldest_queued_age_sec") or 0), 120, 600, "sec")
    sig("queue_depth", int(q.get("queued") or 0), 50, 200, "jobs")
    sig("stale_workers", int(workers.get("stale") or 0), 1, 2, "workers")
    state = "critical" if any(x["state"] == "critical" for x in signals) else ("degraded" if any(x["state"] == "warning" for x in signals) else "ok")
    return {"state": state, "cpu_count": cpu, "load1": round(load1,2), "load5": round(load5,2), "load15": round(load15,2),
            "memory_used_pct": mem_used_pct, "db_connections": db_conn, "db_active": db_active,
            "db_idle_in_transaction_30s": db_idle_in_tx_30s, "db_oldest_idle_transaction_sec": db_oldest_idle_tx_sec,
            "db_max_connections": max_conn, "db_connections_pct": db_pct, "signals": signals}


def job_execution_metrics(db, hours: int = 24) -> dict:
    """Operator-facing execution/retry/cost-avoidance counters.

    ``attempts`` historically mixes legitimate chunk continuations with actual
    failures, so reliability must not treat it as a retry count. New workers
    persist separate counters in payload_json; old jobs simply contribute zero.
    """
    hours = max(1, min(int(hours or 24), 168))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    totals = {"jobs": 0, "executions": 0, "continuations": 0,
              "provider_deferrals": 0, "tenant_deferrals": 0,
              "execution_lock_deferrals": 0, "recoveries": 0, "failures": 0,
              "avoided_paid_generations": 0}
    by_kind: dict[str, dict[str, int]] = {}
    try:
        jobs = db.query(BackgroundJob).filter(BackgroundJob.created_at >= cutoff).all()
        for job in jobs:
            payload = dict(job.payload_json or {})
            kind = str(job.kind or "unknown")
            row = by_kind.setdefault(kind, {"jobs": 0, "executions": 0, "continuations": 0,
                                            "provider_deferrals": 0, "tenant_deferrals": 0,
                                            "execution_lock_deferrals": 0, "recoveries": 0,
                                            "failures": 0})
            values = {
                "jobs": 1,
                "executions": int(payload.get("_execution_count") or 0),
                "continuations": int(payload.get("_continuation_count") or 0),
                "provider_deferrals": int(payload.get("_provider_deferral_count") or 0),
                "tenant_deferrals": int(payload.get("_distributed_tenant_deferred_count") or 0),
                "execution_lock_deferrals": int(payload.get("_execution_lock_deferred_count") or 0),
                "recoveries": int(payload.get("_recovery_count") or 0),
                "failures": int(payload.get("_failure_count") or 0),
            }
            for key, value in values.items():
                totals[key] += value
                row[key] += value
    except Exception:
        pass
    # Idempotency cache records exact recent-hit timestamps; each hit is one paid
    # generation the worker did not repeat after a crash/retry.
    try:
        cache_rows = db.execute(text("select value from storage where key like 'generation_idempotency:%' order by id desc limit 20000")).fetchall()
        avoided = 0
        for (raw,) in cache_rows:
            try:
                data = json.loads(raw or "{}")
            except Exception:
                continue
            for stamp in list(data.get("recent_hits") or []):
                try:
                    dtv = datetime.fromisoformat(str(stamp))
                    if dtv.tzinfo is None:
                        dtv = dtv.replace(tzinfo=timezone.utc)
                    if dtv >= cutoff:
                        avoided += 1
                except Exception:
                    continue
        totals["avoided_paid_generations"] = avoided
    except Exception:
        pass
    totals["hours"] = hours
    totals["by_kind"] = by_kind
    return totals


def email_delivery_health() -> dict:
    """Operational truth for BORIS email delivery; no SMTP call is made."""
    try:
        from app.services.email_queue import queue_stats
        q=queue_stats()
    except Exception as exc:
        return {"state":"degraded","reason":"email_queue_health_unavailable","error":type(exc).__name__}
    queued=int(q.get('queued') or 0)
    # queue_stats historically counts every queued row older than 30 minutes as
    # stuck, even when next_attempt_at intentionally schedules it for tomorrow.
    # Keep that raw value for diagnostics, but compute the production-health
    # stuck count below from eligibility time so scheduled outreach is not a
    # false critical incident.
    raw_stuck=int(q.get('stuck') or 0)
    stuck=raw_stuck
    scheduled_future=0
    unknown=int(q.get('delivery_unknown') or 0); retrying=int(q.get('retrying') or 0)
    worker_alive=bool(q.get('worker_alive'))
    active_prospect_campaigns=0
    active_owner_outreach_campaigns=0
    active_owner_ids=[]
    daily_outreach=[]
    owner_copy_integrity=[]
    owner_volume_safety={'state':'not_required','healthy':True}
    auth_backoff=0
    auth_max_attempts=0
    reply_alert_delivery_unknown=0
    reply_alert_stuck=0
    reply_alert_email_dead=0
    mailbox_smtp_unhealthy=0
    mailbox_imap_unhealthy=0
    mailbox_imap_stale=0
    smtp_probe_overdue=0
    mailbox_errors=[]
    mailbox_action_required=[]
    owner_ready_reserve=[]
    try:
        from app.db.session import SessionLocal
        db=SessionLocal()
        try:
            email_age_row=db.execute(text("""SELECT
              count(*) FILTER (
                WHERE status='sending'
                  AND updated_at < NOW()-INTERVAL '30 minutes'
              )::int
              + count(*) FILTER (
                WHERE status='queued'
                  AND COALESCE(next_attempt_at,created_at)
                      < NOW()-INTERVAL '30 minutes'
              )::int AS stuck,
              count(*) FILTER (
                WHERE status='queued' AND next_attempt_at > NOW()
              )::int AS scheduled_future
              FROM email_queue""")).mappings().one()
            stuck=int(email_age_row.get('stuck') or 0)
            scheduled_future=int(email_age_row.get('scheduled_future') or 0)
            active_prospect_campaigns=int(db.execute(text("SELECT count(*) FROM prospect_campaigns WHERE status='active'")).scalar() or 0)
            active_owner_outreach_campaigns=int(db.execute(text(
                "SELECT count(*) FROM prospect_campaigns "
                "WHERE status='active' AND account_id='__owner_outreach__'"
            )).scalar() or 0)
            active_owner_ids=[int(x[0]) for x in db.execute(text(
                "SELECT DISTINCT owner_id FROM prospect_campaigns WHERE status='active' AND owner_id IS NOT NULL"
            )).fetchall()]
            # EMAIL_READY_RESERVE_HEALTH_V1: owner outreach must warn before the
            # ready audience reaches zero. This is read-only detection; the
            # canonical minute replenisher remains the only self-heal writer.
            reserve_target=max(20,int(os.getenv('PROSPECT_READY_BUFFER_TARGET','120') or 120))
            owner_ready_reserve=[dict(x) for x in db.execute(text("""SELECT
              c.id campaign_id,c.owner_id,c.name,
              count(m.id) FILTER (WHERE m.status='ready')::int AS ready,
              CAST(:target AS integer) AS target,
              rr.last_discovery_at,rr.last_search_attempt_at,rr.last_error,
              CASE
                WHEN count(m.id) FILTER (WHERE m.status='ready') = 0 THEN 'critical'
                WHEN count(m.id) FILTER (WHERE m.status='ready') < LEAST(CAST(:target AS integer),GREATEST(20,c.daily_limit)) THEN 'degraded'
                ELSE 'ok'
              END AS state
              FROM prospect_campaigns c
              LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id
              LEFT JOIN prospect_replenish_runs rr ON rr.campaign_id=c.id
              WHERE c.status='active' AND c.account_id='__owner_outreach__'
              GROUP BY c.id,rr.last_discovery_at,rr.last_search_attempt_at,rr.last_error
              ORDER BY c.id"""),{'target':reserve_target}).mappings().all()]
            mailbox_row=db.execute(text("""SELECT
              count(*) FILTER (WHERE mb.smtp_last_error IS NOT NULL)::int AS smtp_bad,
              count(*) FILTER (WHERE mb.imap_last_error IS NOT NULL)::int AS imap_bad,
              count(*) FILTER (
                WHERE mb.imap_last_checked_at IS NULL
                   OR mb.imap_last_checked_at < NOW()-INTERVAL '5 minutes'
              )::int AS imap_stale,
              count(*) FILTER (
                WHERE mb.smtp_last_error IS NOT NULL
                  AND (mb.smtp_last_checked_at IS NULL
                       OR mb.smtp_last_checked_at < NOW()-INTERVAL '15 minutes')
              )::int AS smtp_probe_overdue
              FROM client_mailboxes mb
              WHERE mb.status='active'
                AND EXISTS(
                  SELECT 1 FROM prospect_campaigns c
                  WHERE c.status='active' AND c.mailbox_id=mb.id
                )""")).mappings().one()
            mailbox_smtp_unhealthy=int(mailbox_row.get('smtp_bad') or 0)
            mailbox_imap_unhealthy=int(mailbox_row.get('imap_bad') or 0)
            mailbox_imap_stale=int(mailbox_row.get('imap_stale') or 0)
            smtp_probe_overdue=int(mailbox_row.get('smtp_probe_overdue') or 0)
            mailbox_errors=[dict(x) for x in db.execute(text("""SELECT
              mb.id,mb.email_address,
              mb.smtp_last_checked_at,mb.smtp_last_error,
              mb.imap_last_checked_at,mb.imap_last_error
              FROM client_mailboxes mb
              WHERE mb.status='active'
                AND (mb.smtp_last_error IS NOT NULL OR mb.imap_last_error IS NOT NULL)
                AND EXISTS(
                  SELECT 1 FROM prospect_campaigns c
                  WHERE c.status='active' AND c.mailbox_id=mb.id
                )
              ORDER BY mb.updated_at DESC,mb.id
              LIMIT 10""")).mappings().all()]
            auth_row=db.execute(text("""SELECT count(*) AS n,coalesce(max(attempts),0) AS mx
              FROM email_queue
              WHERE status='queued' AND attempts>0
                AND last_error LIKE 'SMTPAuthenticationError%'""")).mappings().one()
            auth_backoff=int(auth_row.get('n') or 0)
            auth_max_attempts=int(auth_row.get('mx') or 0)
            if db.execute(text("SELECT to_regclass('public.prospect_reply_alerts') IS NOT NULL")).scalar():
                alert_row=db.execute(text("""SELECT
                  count(*) FILTER (WHERE status='delivery_unknown') AS ambiguous,
                  count(*) FILTER (WHERE status IN ('pending','sending','pending_email_fallback')
                    AND COALESCE(attempted_at,created_at)<NOW()-INTERVAL '10 minutes') AS stuck
                  FROM prospect_reply_alerts""")).mappings().one()
                reply_alert_delivery_unknown=int(alert_row.get('ambiguous') or 0)
                reply_alert_stuck=int(alert_row.get('stuck') or 0)
            reply_alert_email_dead=int(db.execute(text("""SELECT count(*)
              FROM email_queue
              WHERE source IN ('prospect_reply_alert','prospect_reply_alert_fallback')
                AND status='dead' AND updated_at>=NOW()-INTERVAL '24 hours'""")).scalar() or 0)
        finally:
            db.close()
    except Exception:
        pass

    if mailbox_errors:
        try:
            from app.services.client_mailboxes import mailbox_error_kind
            for item in mailbox_errors:
                mailbox_id=int(item.get('id'))
                for channel in ('smtp','imap'):
                    err=item.get(f'{channel}_last_error')
                    kind=mailbox_error_kind(err)
                    if kind=='application_password_required':
                        mailbox_action_required.append({
                            'mailbox_id':mailbox_id,
                            'channel':channel,
                            'kind':kind,
                            'checked_at':item.get(f'{channel}_last_checked_at'),
                        })
        except Exception:
            pass

    if active_owner_outreach_campaigns:
        try:
            from app.services.owner_outreach_volume_safety import health as owner_volume_safety_health
            owner_volume_safety=owner_volume_safety_health()
        except Exception as exc:
            owner_volume_safety={
                'state':'critical','healthy':False,
                'error':type(exc).__name__,
            }

    if active_owner_ids:
        try:
            from app.services.prospect_campaigns import owner_daily_delivery_health, owner_copy_integrity_health
            daily_outreach=[owner_daily_delivery_health(owner_id) for owner_id in active_owner_ids]
            owner_copy_integrity=[
                owner_copy_integrity_health(owner_id,self_heal=True)
                for owner_id in active_owner_ids
            ]
        except Exception as exc:
            daily_outreach=[{
                'state':'degraded','status':'daily_health_unavailable',
                'error':type(exc).__name__,'owner_action_required':False,
            }]
            owner_copy_integrity=[{
                'state':'critical','status':'copy_integrity_health_unavailable',
                'error':type(exc).__name__,'owner_action_required':False,
            }]

    # The prospect feeder and outbound queue share this worker. If an outreach
    # campaign is active, a dead worker is critical even when email_queue is empty.
    # SMTP auth backoff is a circuit breaker: one durable probe retries slowly;
    # the feeder must not enqueue fresh leads until it recovers.
    worker_required=bool(queued>0 or active_prospect_campaigns>0)
    daily_states=[str(x.get('state') or 'ok') for x in daily_outreach]
    copy_integrity_states=[str(x.get('state') or 'ok') for x in owner_copy_integrity]
    reserve_states=[str(x.get('state') or 'ok') for x in owner_ready_reserve]
    if (
        stuck>0
        or (worker_required and not worker_alive)
        or (auth_backoff>0 and auth_max_attempts>=3)
        or bool(mailbox_action_required)
        or (active_owner_outreach_campaigns>0 and not bool(owner_volume_safety.get('healthy')))
        or 'critical' in daily_states
        or 'critical' in copy_integrity_states
        or 'critical' in reserve_states
    ):
        state='critical'
    elif (
        unknown>0
        or retrying>=5
        or auth_backoff>0
        or reply_alert_delivery_unknown>0
        or reply_alert_stuck>0
        or reply_alert_email_dead>0
        or mailbox_smtp_unhealthy>0
        or mailbox_imap_unhealthy>0
        or mailbox_imap_stale>0
        or smtp_probe_overdue>0
        or 'degraded' in daily_states
        or 'degraded' in reserve_states
    ):
        state='degraded'
    else:
        state='ok'
    current_error=None
    primary_daily_issue=next(
        (x for x in daily_outreach if str(x.get('state') or '')=='critical'),
        None,
    )
    primary_copy_issue=next(
        (x for x in owner_copy_integrity if str(x.get('state') or '')=='critical'),
        None,
    )
    if active_owner_outreach_campaigns>0 and not bool(owner_volume_safety.get('healthy')):
        current_error='owner_outreach_volume_safety_unhealthy'
    elif mailbox_action_required:
        current_error='mailbox_application_password_required'
    elif primary_daily_issue:
        current_error='owner_outreach:'+str(primary_daily_issue.get('status') or 'daily_health_critical')
    elif primary_copy_issue:
        current_error='owner_outreach:'+str(primary_copy_issue.get('status') or 'copy_integrity_critical')
    elif mailbox_errors:
        first=mailbox_errors[0]
        parts=[]
        if first.get('smtp_last_error'):
            parts.append('SMTP: '+str(first.get('smtp_last_error')))
        if first.get('imap_last_error'):
            parts.append('IMAP: '+str(first.get('imap_last_error')))
        current_error='; '.join(parts) or None
    elif state!='ok':
        current_error=q.get('last_error')
    mailbox_channel_errors=[{
        'mailbox_id':int(x.get('id')),
        'smtp_last_checked_at':x.get('smtp_last_checked_at'),
        'smtp_last_error':x.get('smtp_last_error'),
        'imap_last_checked_at':x.get('imap_last_checked_at'),
        'imap_last_error':x.get('imap_last_error'),
    } for x in mailbox_errors]
    owner_action_required=bool(
        mailbox_action_required
        or any(bool(x.get('owner_action_required')) for x in daily_outreach)
        or any(bool(x.get('owner_action_required')) for x in owner_copy_integrity)
    )
    return {
      'state':state,'queued':queued,'sending':int(q.get('sending') or 0),
      'retrying':retrying,'delivery_unknown':unknown,'stuck':stuck,
      'raw_stuck':raw_stuck,'scheduled_future':scheduled_future,
      'worker_alive':worker_alive,'worker_age_min':q.get('worker_age_min'),
      'active_prospect_campaigns':active_prospect_campaigns,
      'active_owner_outreach_campaigns':active_owner_outreach_campaigns,
      'owner_volume_safety':owner_volume_safety,
      'daily_outreach':daily_outreach,
      'owner_copy_integrity':owner_copy_integrity,
      'owner_ready_reserve':owner_ready_reserve,
      'auth_backoff':auth_backoff,'auth_max_attempts':auth_max_attempts,
      'reply_alert_delivery_unknown':reply_alert_delivery_unknown,'reply_alert_stuck':reply_alert_stuck,
      'reply_alert_email_dead_24h':reply_alert_email_dead,
      'mailbox_smtp_unhealthy':mailbox_smtp_unhealthy,
      'mailbox_imap_unhealthy':mailbox_imap_unhealthy,
      'mailbox_imap_stale':mailbox_imap_stale,
      'smtp_probe_overdue':smtp_probe_overdue,
      'mailbox_channel_errors':mailbox_channel_errors,
      'mailbox_action_required':mailbox_action_required,
      'owner_action_required':owner_action_required,
      'self_heal':('waiting_for_mailbox_application_password_update'
                   if mailbox_action_required else 'automatic_or_not_needed'),
      'last_sent_at':q.get('last_sent_at'),'last_error':current_error,
      'truth':'Owner outreach volume is DB-final and self-healed before every canonical worker cycle; SMTP and IMAP health are independent; owner outreach also verifies the exact approved visible copy, DB campaign/queue locks and mandatory open tracking; SMTP failures block new sends until a rate-limited LOGIN/NOOP probe succeeds, IMAP failures block until the inbox poll succeeds, and SMTP delivery_unknown is never auto-retried because remote acceptance is ambiguous',
    }


def cpx_execution_health(db) -> dict:
    """Read-only health of fail-closed live CPX mutations."""
    try:
        rows=db.execute(text("""SELECT status,count(*) AS n,min(attempted_at) AS oldest
          FROM cpx_execution_receipts
          WHERE status IN ('attempting','delivery_unknown')
          GROUP BY status""")).mappings().all()
    except Exception as exc:
        return {'state':'degraded','reason':'cpx_execution_health_unavailable','error':type(exc).__name__}
    counts={str(r['status']):int(r['n']) for r in rows}
    unknown=counts.get('delivery_unknown',0); attempting=counts.get('attempting',0)
    oldest_age=0
    now=datetime.now(timezone.utc)
    for r in rows:
        dt=r.get('oldest')
        if dt:
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            oldest_age=max(oldest_age,int((now-dt).total_seconds()))
    state='critical' if unknown>=3 or oldest_age>=600 else ('degraded' if unknown>0 or attempting>0 else 'ok')
    return {'state':state,'attempting':attempting,'delivery_unknown':unknown,'oldest_ambiguous_age_sec':oldest_age,
            'truth':'ambiguous CPX writes are read-only reconciled; blind money retry is forbidden'}


def system_health(db) -> dict:
    db.execute(text("SELECT 1"))
    queue = queue_health(db)
    storage = storage_health()
    backup = backup_health()
    workers = heartbeat_health(db)
    breakdown = queue_breakdown(db)
    circuits = db.query(ReliabilityCircuit).order_by(ReliabilityCircuit.dependency).all()
    global_circuits = [c for c in circuits if not _is_account_circuit_key(c.dependency)]
    tenant_circuits = [c for c in circuits if _is_account_circuit_key(c.dependency)]
    open_circuits = [c.dependency for c in global_circuits if c.state in ("open", "half_open")]
    tenant_open_circuits = [c for c in tenant_circuits if c.state in ("open", "half_open")]
    dependencies = dependency_health(db)
    capacity = capacity_health(db)
    email = email_delivery_health()
    cpx = cpx_execution_health(db)
    core_components = [queue["state"], storage["state"], backup["state"], workers["state"], dependencies["state"], email["state"], cpx["state"]]
    state = "critical" if "critical" in core_components else ("degraded" if "degraded" in core_components or open_circuits or capacity["state"] in ("degraded", "critical") else "ok")
    db_tx_bad = int(capacity.get("db_idle_in_transaction_30s") or 0) > 0
    db_tx_critical = int(capacity.get("db_oldest_idle_transaction_sec") or 0) >= 55 or int(capacity.get("db_idle_in_transaction_30s") or 0) >= 3
    return {
        "state": state,
        "database": {
            "state": "critical" if db_tx_critical else ("degraded" if db_tx_bad else "ok"),
            "idle_in_transaction_30s": int(capacity.get("db_idle_in_transaction_30s") or 0),
            "oldest_idle_transaction_sec": int(capacity.get("db_oldest_idle_transaction_sec") or 0),
        },
        "queue": queue,
        "queue_breakdown": breakdown,
        "workers": workers,
        "storage": storage,
        "backup": backup,
        "dependencies": dependencies,
        "capacity": capacity,
        "email_delivery": email,
        "cpx_execution": cpx,
        "capacity_recommendation": capacity_recommendation(db),
        "job_execution_24h": job_execution_metrics(db, 24),
        "circuits": [{
            "dependency": c.dependency, "state": c.state,
            "failures": int(c.consecutive_failures or 0),
            "opened_until": c.opened_until.isoformat() if c.opened_until else None,
            "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
            "last_failure_at": c.last_failure_at.isoformat() if c.last_failure_at else None,
        } for c in global_circuits],
        "open_circuits": open_circuits,
        "tenant_open_circuits": [{
            "dependency": _base_circuit_dependency(c.dependency),
            "account_ref": _account_ref_from_circuit(c.dependency),
            "state": c.state,
            "failures": int(c.consecutive_failures or 0),
            "opened_until": c.opened_until.isoformat() if c.opened_until else None,
        } for c in tenant_open_circuits[:100]],
        "retry_budgets": retry_budget_snapshot(),
        "provider_tenant_budgets": provider_tenant_budget_snapshot(db),
        "trace_id": get_trace_id(),
    }
