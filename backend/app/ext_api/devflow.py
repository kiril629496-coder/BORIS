# -*- coding: utf-8 -*-
"""One high-level development action for BORIS Agent Gateway.

The caller supplies a normal-language task. The server owns the lower-level
Dev/A2A/Executor/Lead loop. This module never grants admin and never calls
production deploy/restart/external-write operations.
"""
import re

from . import auth, db, dev, a2a, repo, sched
from .errors import ApiError

DEFAULT_SCOPE_PATHS = ["backend/app", "backend/tests", "frontend"]
RUNNER_POOL = a2a.POOL
FORBIDDEN_CHANGES = [
    "DB schema or migrations",
    "production deploy or restart/systemd",
    "Avito/VK/Telegram/OAuth/billing/paid external writes",
    "secrets or credentials",
    "destructive operations",
    "safety-policy changes",
    "admin grants",
    "Agent tool-binding changes",
]
REQUIRED_QA = [
    "real compile/build with exit code",
    "real automated tests with exit code",
    "runtime/E2E check when applicable; otherwise NOT_PROVEN/NOT_APPLICABLE with evidence",
]
REQUIRED_EVIDENCE = [
    "applied patch id and files",
    "backup/verify output",
    "compile/build command output and exit code",
    "test command output and exit code",
    "runtime/E2E output",
]
# OWNER_NOT_OPERATOR_ACCEPTANCE_V1: the owner may describe only the business
# problem and desired result. BORIS supplies the non-negotiable engineering
# proof internally instead of making the owner invent technical acceptance.
SYSTEM_ACCEPTANCE = [
    "Requested owner-visible behavior is demonstrated in a realistic scenario after the change",
    "Root cause is identified and fixed; the result is not only a local symptom workaround",
    "A regression test protects this bug/change class when technically applicable, otherwise NOT_APPLICABLE is evidenced",
    "Previously working behavior in the affected scope passes regression checks",
    "No new hidden parallel writer, source of truth, scheduler, or shadow implementation is introduced",
    "Runtime/E2E result is evidenced; no production-ready/PASS claim is made without proof",
]
TERMINAL_ORDER = {a2a.ACCEPTED, a2a.BLOCKED_HUMAN, a2a.BLOCKED_INFRA, a2a.FAILED, a2a.CANCELLED}


def _autonomous_priority(requested):
    """Keep server-owned development urgent, but below the client priority class."""
    requested = int(requested or 50)
    ceiling = max(0, int(sched.CLIENT_FLOOR) - 1)
    floor = min(int(sched.P0_PRIORITY), ceiling)
    return max(floor, min(requested, ceiling))


def _title(task):
    one = " ".join(str(task or "").strip().split())
    return one[:120] or "Autonomous development task"


def _normalize_acceptance(owner_criteria=None):
    """Keep explicit owner criteria and always append BORIS engineering gates."""
    out = []
    for item in list(owner_criteria or []) + list(SYSTEM_ACCEPTANCE):
        value = " ".join(str(item or "").strip().split())
        if value and value not in out:
            out.append(value)
    return out


def _status_from_text(text, markers):
    low = (text or "").lower()
    return "PASS" if any(re.search(marker, low) for marker in markers) else "NOT_PROVEN"


def _report(job_id):
    job = dev.get(job_id)
    orders = a2a.listing(dev_job_id=job_id, limit=200)
    patches = repo.list_patches(job_id, limit=200)
    applied = [p for p in patches if p.get("status") == "applied"]
    evidence_parts, blockers = [], []
    for patch in applied:
        if patch.get("verify_output"):
            evidence_parts.append(f"patch {patch['patch_id']}: {patch['verify_output']}")
    for order in orders:
        ev = order.get("last_evidence") or {}
        if ev.get("evidence"):
            evidence_parts.append(f"order {order['order_id']}: {ev['evidence']}")
        blockers.extend(ev.get("blockers") or [])
        if order.get("blocked_reason"):
            blockers.append(order["blocked_reason"])
    evidence = "\n".join(evidence_parts)[-30000:]
    criteria = job.get("acceptance_criteria") or []
    accepted = bool(criteria) and all(bool(c.get("done")) for c in criteria)
    latest = sorted(orders, key=lambda x: x["order_id"])[-1] if orders else None
    compile_state = _status_from_text(
        evidence, [r"compile[^\n]*exit_code=0", r"py_compile[^\n]*(?:code|exit_code)[=: ]+0"])
    test_state = _status_from_text(
        evidence, [r"test[^\n]*exit_code=0", r"pytest[^\n]*(?:code|exit_code)[=: ]+0"])
    runtime_state = _status_from_text(
        evidence, [r"http[^\n]*(?:200|exit_code=0)", r"runtime[^\n]*(?:pass|200|exit_code=0)"])
    e2e_state = _status_from_text(
        evidence, [r"e2e[^\n]*(?:pass|exit_code=0)", r"browser[^\n]*(?:200|pass)"])
    status = ("COMPLETED" if job.get("status") == "completed" else
              ("BLOCKED" if job.get("status") in ("blocked", "failed") or blockers else "RUNNING"))
    return {
        "DEV_JOB": job["dev_job_id"],
        "A2A_ORDER": (latest or {}).get("order_id"),
        "STATUS": status,
        "PHASE": job.get("current_phase"),
        "FILES_CHANGED": job.get("files_changed") or [],
        "PATCHES_APPLIED": [p["patch_id"] for p in applied],
        "COMPILE": compile_state,
        "TESTS": test_state,
        "RUNTIME_QA": runtime_state,
        "E2E": e2e_state,
        "ACCEPTANCE": "PASS" if accepted else "NOT_PROVEN",
        "REVIEW": (latest or {}).get("status") or "NOT_PROVEN",
        "EVIDENCE": evidence or "NOT_PROVEN",
        "BLOCKERS": list(dict.fromkeys(str(x) for x in blockers if x)),
    }


def start_development_task(key, request_id, task, acceptance_criteria,
                           priority=50, scope_paths=None):
    auth.need_scope(key, "write")
    auth.need_scope(key, "tasks")
    if "admin" in (key.get("scope_set") or set()):
        raise ApiError("FORBIDDEN_SCOPE", "runDevelopmentTask не использует admin capability")
    if not task or not str(task).strip():
        raise ApiError("MISSING_INPUT", "task обязателен")
    if acceptance_criteria is not None and not isinstance(acceptance_criteria, list):
        raise ApiError("MISSING_INPUT", "acceptance_criteria должен быть списком, если передан")
    acceptance = _normalize_acceptance(acceptance_criteria)

    paths = [str(p).strip().lstrip("/") for p in (scope_paths or DEFAULT_SCOPE_PATHS)
             if str(p).strip()]
    if not paths:
        raise ApiError("MISSING_INPUT", "development file scope пуст")
    for path in paths:
        repo.safe_path(path)

    # Idempotency is anchored in ext_dev_jobs.request_id. The HTTP operation
    # passes a stable Idempotency-Key-derived request_id, so retries return the
    # original task instead of creating another job/order.
    existing = db.one("SELECT id FROM ext_dev_jobs WHERE request_id=:r ORDER BY id LIMIT 1",
                      r=str(request_id))
    if existing:
        out = _report("dev_%s" % existing["id"])
        out["IDEMPOTENT_REPLAY"] = True
        return out

    from . import executor, lead
    autonomous_priority = _autonomous_priority(priority)
    job = dev.create(
        key, request_id, _title(task), str(task), acceptance,
        scope={"paths": paths, "autonomous_dev": True, "server_owned": True},
        owner=executor.AGENT, reviewer=lead.AGENT, priority=autonomous_priority, locks=paths,
    )
    dev.update_phase(key, job["dev_job_id"], "PLANNING", "running",
                     notes="startDevelopmentTask: server-owned lifecycle")

    planning_acceptance = [
        "Architecture and relevant repository state were actually inspected before implementation",
        "Implementation scope and safety boundaries are explicit in evidence",
    ]
    first = a2a.create_order(
        key, job["dev_job_id"], "PLANNING",
        "Investigate the task, then drive it to a real implementation. Do not claim any PASS without tool evidence.",
        scope={"paths": paths, "autonomous_dev": True, "server_owned": True},
        acceptance=planning_acceptance,
        forbidden=FORBIDDEN_CHANGES, required_qa=REQUIRED_QA,
        required_evidence=REQUIRED_EVIDENCE, assigned_to=RUNNER_POOL,
        max_iterations=6, priority=autonomous_priority,
    )

    # The HTTP action ends here. From this point the existing long-running
    # gpt-executor / lead / boris-deploy services own the lifecycle. Closing
    # the browser cannot cancel development work.
    report = _report(job["dev_job_id"])
    report["STATUS"] = "RUNNING"
    report["INITIAL_A2A_ORDER"] = first["order_id"]
    report["SERVER_OWNED"] = True
    return report


def run_development_task(key, request_id, task, acceptance_criteria,
                         priority=50, scope_paths=None, max_cycles=20):
    """Backward-compatible alias; execution is intentionally server-owned."""
    return start_development_task(key, request_id, task, acceptance_criteria,
                                  priority=priority, scope_paths=scope_paths)
