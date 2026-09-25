# -*- coding: utf-8 -*-
"""Development Orchestrator — Job типа development.

Шлюз ведёт состояние разработки: фазы, критерии приёмки, изменённые файлы,
тесты, ошибки, решения, передачи между агентами. Код пишут агенты, сборку и
тесты выполняет BORIS. Шлюз следит за одним: фаза не может стать PASS без
доказательства, а Job не может стать COMPLETED, пока хоть один обязательный
гейт не PASS.
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from . import db, auth, costs
from .errors import ApiError
from app.services.exception_observability import observe_suppressed

PHASES = ["PLANNING", "BACKEND", "FRONTEND", "UX", "INTEGRATION", "TESTING", "FIXING",
          "REGRESSION", "ACCEPTANCE", "DEPLOYMENT", "PRODUCTION_QA", "COMPLETED"]

PHASE_STATES = ("pending", "running", "pass", "fail", "skipped", "blocked")

# Обязательны всегда. FRONTEND и UX добавляются, если в scope есть ui.
REQUIRED_ALWAYS = ["PLANNING", "BACKEND", "TESTING", "INTEGRATION", "REGRESSION",
                   "ACCEPTANCE", "PRODUCTION_QA"]
REQUIRED_IF_UI = ["FRONTEND", "UX"]

# Полный набор состояний Development Job (п. 5 ТЗ). Хранятся в нижнем регистре,
# наружу отдаются как есть — регистр косметический.
STATUSES = ("queued", "planning", "running", "waiting", "blocked", "review",
            "testing", "fixing", "acceptance", "completed", "failed", "cancelled")

# Фаза → состояние. COMPLETED выставляется только через complete() после приёмки.
PHASE_TO_STATE = {
    "PLANNING": "planning", "BACKEND": "running", "FRONTEND": "running",
    "UX": "review", "INTEGRATION": "running", "TESTING": "testing",
    "FIXING": "fixing", "REGRESSION": "testing", "ACCEPTANCE": "acceptance",
    "DEPLOYMENT": "running", "PRODUCTION_QA": "testing",
}

# Дочерние операции одного Job (п. 19 ТЗ) — у каждой свой статус.
OP_NAMES = ("TEXT", "IMAGES", "BANNERS", "FEED", "PUBLISH", "QA")
OP_STATES = ("waiting", "running", "completed", "blocked", "failed", "skipped")
MAX_FIX_LOOPS = 5

# Диспетчер: сколько задач один агент реально тянет одновременно.
MAX_PARALLEL_PER_AGENT = 3

# Какая фаза кому ближе — используется при назначении.
PHASE_KIND = {
    "PLANNING": "planning", "BACKEND": "code", "FRONTEND": "code", "UX": "architecture",
    "INTEGRATION": "code", "TESTING": "qa", "FIXING": "code", "REGRESSION": "qa",
    "ACCEPTANCE": "review", "DEPLOYMENT": "code", "PRODUCTION_QA": "qa",
}


def _j(x, default=None):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return default
    return x if x is not None else default


def _norm_lock_path(value):
    return str(value or "").strip().lstrip("/").replace("\\", "/").rstrip("/")


def _lock_overlap(a, b):
    """True when two logical/file locks cover the same resource tree."""
    a = _norm_lock_path(a)
    b = _norm_lock_path(b)
    return bool(a and b and (a == b or a.startswith(b + "/") or b.startswith(a + "/")))


def _lock_conflicts(left, right):
    """Return deterministic human-readable hierarchical lock collisions."""
    out = []
    for a in sorted({_norm_lock_path(x) for x in (left or []) if _norm_lock_path(x)}):
        for b in sorted({_norm_lock_path(x) for x in (right or []) if _norm_lock_path(x)}):
            if _lock_overlap(a, b):
                label = a if a == b else (a + " ↔ " + b)
                if label not in out:
                    out.append(label)
    return out


def _scope_lock_paths(row):
    """Derive conservative locks from explicit dev-job scope when locks are absent."""
    scope = _j((row or {}).get("scope_json"), {}) or {}
    out = []
    for key in ("paths", "files", "allowed_paths"):
        value = scope.get(key)
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            for item in value:
                norm = _norm_lock_path(item)
                if norm and norm not in out:
                    out.append(norm)
    return sorted(out)


LEGACY_SCOPE_NARROWING = {
    "CALL_STT_LOCAL": {
        "broad_locks": {"backend/app/services"},
        "paths": [
            "backend/app/services/telephony_core.py",
            "backend/app/api/calltracking.py",
        ],
        "workstream_id": "telephony_core",
        "reason": (
            "telephony_core.process_ai_pipeline imports calltracking._transcribe; "
            "legacy app/services scope is broader than the proven implementation surface"
        ),
    },
    "CALL_AUDIT_CLAUDE": {
        "broad_locks": {"backend/app/services"},
        "paths": [
            "backend/app/services/telephony_core.py",
            "backend/app/api/calltracking.py",
        ],
        "workstream_id": "telephony_core",
        "reason": (
            "telephony_core.process_ai_pipeline imports calltracking._analyze_call; "
            "legacy app/services scope is broader than the proven implementation surface"
        ),
    },
    "CLIENT_CABINET": {
        "broad_locks": {
            "backend/app/api",
            "backend/app/services",
            "frontend/app/components",
            "frontend/app/dashboard",
        },
        "paths": [
            "frontend/app/dashboard/campaign/page.tsx",
            "frontend/app/ui/menu.ts",
            "frontend/app/listings/page.tsx",
        ],
        "read_only_paths": [
            "backend/app/api/payments.py",
            "backend/dev_820_generate_campaigns.py",
        ],
        "workstream_id": "frontend_mobile_ui",
        "reason": (
            "dev_820 BACKEND phase is already PASS; current phase is FRONTEND. "
            "Accepted evidence and applied patches prove the remaining client-visible "
            "surface is /dashboard/campaign plus its menu/listings frontend files. "
            "Historical billing/feed-generation files are evidence only, not current writers."
        ),
    },
    "DEV_PARALLEL_SCALE": {
        "broad_locks": {"backend/app/ext_api"},
        "paths": [
            "backend/app/ext_api/aiprov.py",
            "backend/app/ext_api/a2a.py",
            "backend/app/ext_api/executor.py",
            "backend/app/ext_api/lead.py",
            "backend/app/ext_api/recover.py",
        ],
        "workstream_id": "autonomous_dev_execution",
        "reason": (
            "A2A order 54617 proves this legacy task is execution-pool work "
            "(provider health, lease, checkpoint, failover), not scheduler/repo ownership"
        ),
    },
    "SERVER_COMMAND_BRIDGE": {
        "broad_locks": {"backend/app/ext_api", "backend/app/services"},
        "paths": [
            "backend/app/ext_api/a2a.py",
            "backend/app/ext_api/executor.py",
            "backend/app/ext_api/router.py",
            "backend/app/ext_api/schema.py",
        ],
        "workstream_id": "autonomous_dev_execution",
        "reason": (
            "applied patch 56 proves the command bridge lives on the autonomous "
            "A2A/executor/router/schema surface; the app/services tree claim was legacy"
        ),
    },
    "Replace the fragile autonomous development lifecycle": {
        "broad_locks": {"backend/app/ext_api", "backend/tests"},
        "paths": [
            "backend/app/ext_api/aiprov.py",
            "backend/app/ext_api/a2a.py",
            "backend/app/ext_api/executor.py",
            "backend/app/ext_api/lead.py",
            "backend/app/ext_api/recover.py",
            "backend/app/ext_api/devflow.py",
            "backend/app/ext_api/router.py",
            "backend/app/ext_api/schema.py",
            "backend/tests/test_lead_reconcile_server_owned.py",
        ],
        "read_only_paths": [
            "backend/tests/test_dev_cost_safety.py",
        ],
        "workstream_id": "autonomous_dev_execution",
        "reason": (
            "applied patches 193-194 prove the autonomous execution state-machine "
            "surface; blanket app/ext_api + tests ownership is unnecessary and unsafe"
        ),
    },
}


def _legacy_scope_narrowing(title, scope_obj, locks):
    """Narrow only explicitly proven legacy broad scopes; never guess ownership."""
    title = str(title or "")
    current = {_norm_lock_path(x) for x in (locks or []) if _norm_lock_path(x)}
    scope_obj = dict(scope_obj or {})
    for prefix, spec in LEGACY_SCOPE_NARROWING.items():
        if not title.startswith(prefix):
            continue
        if current != set(spec["broad_locks"]):
            return scope_obj, sorted(current), None
        paths = list(spec["paths"])
        narrowed = dict(scope_obj)
        narrowed["paths"] = paths
        read_only_paths = list(spec.get("read_only_paths") or [])
        if read_only_paths:
            narrowed["read_only_paths"] = read_only_paths
        narrowed["workstream_id"] = spec["workstream_id"]
        narrowed["scope_split"] = spec["reason"]
        return narrowed, paths, {
            "prefix": prefix,
            "from": sorted(current),
            "to": paths,
            "read_only_paths": read_only_paths,
            "workstream_id": spec["workstream_id"],
        }
    return scope_obj, sorted(current), None


_SCOPE_RECOVERY_ORDER_PREFIXES = (
    "WORKSTREAM_SCOPE_AMBIGUOUS:",
    "WORKSTREAM_ORDER_SCOPE_STALE:",
    "INHERITED_PARENT_SCOPE_QUARANTINE: WORKSTREAM_SCOPE_AMBIGUOUS:",
)


def _requeue_scope_recovered_orders(dev_id, narrowed_scope, new_locks, workstream_id):
    """Requeue only A2A orders blocked by the obsolete broad workstream scope.

    Provider, dependency, owner-action and iteration-limit blocks are left
    untouched.  This closes the self-heal gap where the parent dev_job was
    narrowed but its child order remained permanently blocked on the old scope.
    """
    rows = db.rows(
        """SELECT id,phase,status,blocked_reason,scope_json
             FROM ext_a2a_orders
            WHERE dev_job_id=:d AND status='blocked_infra'
            ORDER BY id""",
        d=int(dev_id),
    )
    recovered = []
    for row in rows:
        reason = str(row.get("blocked_reason") or "")
        if not reason.startswith(_SCOPE_RECOVERY_ORDER_PREFIXES):
            continue
        phase = str(row.get("phase") or "").upper()
        old_scope = _j(row.get("scope_json"), {}) or {}
        if not isinstance(old_scope, dict):
            old_scope = {}
        scope = dict(old_scope)
        if phase == "PLANNING":
            for key in ("paths", "files", "allowed_paths"):
                scope.pop(key, None)
            scope["inspection_paths"] = list(new_locks)
            scope["read_only"] = True
            scope["scope_mode"] = "planning_read_only"
        else:
            scope = dict(narrowed_scope or {})
            scope["paths"] = list(new_locks)
        scope["workstream_id"] = workstream_id
        scope["scope_guard"] = "WORKSTREAM_LEGACY_SCOPE_RECOVERED_V1"
        db.q(
            """UPDATE ext_a2a_orders
                  SET status='queued',
                      scope_json=:scope,
                      blocked_reason=NULL,
                      claimed_by=NULL,
                      claimed_at=NULL,
                      updated_at=NOW()
                WHERE id=:o
                  AND status='blocked_infra'""",
            o=int(row["id"]),
            scope=json.dumps(scope, ensure_ascii=False),
        )
        recovered.append(int(row["id"]))
    return recovered


def required_phases(scope):
    need = list(REQUIRED_ALWAYS)
    if any(k in (scope or {}) and (scope or {}).get(k) for k in ("ui", "frontend")):
        need += REQUIRED_IF_UI
    return need


def create(key, request_id, title, goal, acceptance, scope=None, owner=None, reviewer=None,
           priority=50, depends_on=None, locks=None, batch_id=None):
    if not title or not goal:
        raise ApiError("MISSING_INPUT", "нужны title и goal")
    if not isinstance(acceptance, list) or not acceptance:
        raise ApiError("MISSING_INPUT",
                       "нужен непустой acceptance_criteria — без критериев приёмки "
                       "задачу нельзя будет честно закрыть")
    actor = auth.actor_of(key)["name"]
    scope_obj = scope or {}
    effective_locks = sorted({
        _norm_lock_path(x) for x in (locks or []) if _norm_lock_path(x)
    })
    if not effective_locks:
        effective_locks = _scope_lock_paths({"scope_json": scope_obj})
    scope_obj, effective_locks, legacy_narrow = _legacy_scope_narrowing(
        title, scope_obj, effective_locks
    )
    read_only = scope_obj.get("read_only") is True
    intake_status = "queued" if (effective_locks or read_only) else "waiting"
    intake_wait = (
        None if (effective_locks or read_only) else
        "UNSCOPED_DEV_JOB: explicit scope/locks required before execution"
    )
    phases = {p: {"state": "pending", "evidence": None, "by": None, "at": None}
              for p in PHASES if p != "COMPLETED"}
    crit = [{"text": str(c), "done": False, "evidence": None} for c in acceptance]
    row = db.one("""INSERT INTO ext_dev_jobs
            (request_id, title, goal, scope_json, acceptance_json, current_phase,
             owner_agent, reviewer_agent, status, wait_reason, phases_json, created_by,
             max_fix_loops, priority, depends_on_json, locks_json, batch_id)
          VALUES (:rq,:t,:g,:sc,:ac,'PLANNING',:ow,:rv,:st,:wr,:ph,:cb,:mx,
                  :pr,:dp,:lk,:bt) RETURNING id""",
        rq=request_id, t=title[:300], g=goal[:4000],
        sc=json.dumps(scope_obj, ensure_ascii=False),
        ac=json.dumps(crit, ensure_ascii=False), ow=(owner or actor), rv=reviewer,
        st=intake_status, wr=intake_wait,
        ph=json.dumps(phases, ensure_ascii=False), cb=actor, mx=MAX_FIX_LOOPS,
        pr=int(priority), dp=json.dumps([int(str(d).replace("dev_", "")) for d in (depends_on or [])]),
        lk=json.dumps(effective_locks, ensure_ascii=False), bt=batch_id)
    event(row["id"], "PLANNING", actor, "created", {
        "title": title, "criteria": len(crit),
        "locks": effective_locks,
        "scope_state": (
            "scoped" if effective_locks else
            ("read_only" if read_only else "unscoped_quarantined")
        ),
        "legacy_scope_narrowing": legacy_narrow,
    })
    return get(row["id"])


def event(dev_id, phase, actor, kind, payload=None):
    db.q("""INSERT INTO ext_dev_events (dev_job_id, phase, actor, kind, payload_json)
            VALUES (:d,:p,:a,:k,:pl)""", d=dev_id, p=phase, a=actor, k=kind,
         pl=json.dumps(auth._redact(payload or {}), ensure_ascii=False)[:20000])


def get(dev_id):
    if isinstance(dev_id, str) and dev_id.startswith("dev_"):
        dev_id = dev_id[4:]
    try:
        dev_id = int(dev_id)
    except (TypeError, ValueError):
        raise ApiError("JOB_NOT_FOUND", "неверный идентификатор development-задачи")
    r = db.one("SELECT * FROM ext_dev_jobs WHERE id=:i", i=dev_id)
    if not r:
        raise ApiError("JOB_NOT_FOUND", "development-задача не найдена")
    phases = _j(r["phases_json"], {})
    scope = _j(r["scope_json"], {})
    need = required_phases(scope)
    return {
        "dev_job_id": f"dev_{r['id']}", "id": r["id"], "request_id": r.get("request_id"),
        "type": "development", "title": r["title"], "goal": r["goal"], "scope": scope,
        "acceptance_criteria": _j(r["acceptance_json"], []),
        "current_phase": r["current_phase"], "status": r["status"],
        "owner_agent": r.get("owner_agent"), "reviewer_agent": r.get("reviewer_agent"),
        "created_by": r.get("created_by"),
        "phases": phases, "required_phases": need,
        "files_changed": _j(r.get("files_changed_json"), []),
        "tests": _j(r.get("tests_json"), []),
        "errors": _j(r.get("errors_json"), []),
        "decisions": _j(r.get("decisions_json"), []),
        "handoffs": db.rows("""SELECT id, from_actor, to_actor, reason, status, resolution
                               FROM ext_handoffs WHERE dev_job_id=:d ORDER BY id""", d=r["id"]),
        "operations": ops_of(r["id"]),
        "cost": costs.report(dev_job_id=r["id"]),
        "priority": r.get("priority"), "depends_on": ["dev_%s" % d for d in _j(r.get("depends_on_json"), [])],
        "locks": _j(r.get("locks_json"), []), "assigned_agent": r.get("assigned_agent"),
        "wait_reason": r.get("wait_reason"), "batch_id": r.get("batch_id"),
        "fix_loops": r.get("fix_loops"), "max_fix_loops": r.get("max_fix_loops"),
        "deployment_status": r.get("deployment_status"), "qa_status": r.get("qa_status"),
        "final_result": _j(r.get("final_result_json")),
        "blocked_reason": r.get("blocked_reason"),
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
    }


def _set(dev_id, **f):
    sets = ", ".join(f"{k}=:{k}" for k in f)
    db.q(f"UPDATE ext_dev_jobs SET {sets}, updated_at=NOW() WHERE id=:__id", __id=dev_id, **f)


def update_phase(key, dev_id, phase, state, evidence=None, files_changed=None, tests=None,
                 errors=None, decisions=None, notes=None):
    job = get(dev_id)
    actor = auth.actor_of(key)["name"]
    phase = (phase or "").upper()
    if phase not in PHASES or phase == "COMPLETED":
        raise ApiError("VALIDATION_ERROR", "фаза должна быть одной из: " +
                       ", ".join(p for p in PHASES if p != "COMPLETED"))
    if state not in PHASE_STATES:
        raise ApiError("VALIDATION_ERROR", "state должен быть одним из: " + ", ".join(PHASE_STATES))
    if state == "pass" and not (evidence and str(evidence).strip()):
        raise ApiError("VALIDATION_ERROR",
                       f"фазу {phase} нельзя отметить pass без evidence — "
                       "нужен вывод команды, сводка тестов или ссылка на проверку")
    if job["status"] in ("completed", "cancelled"):
        raise ApiError("JOB_NOT_RETRYABLE", f"задача уже {job['status']}")

    phases = job["phases"]
    phases[phase] = {"state": state, "evidence": (str(evidence)[:4000] if evidence else None),
                     "by": actor, "at": datetime.utcnow().isoformat(), "notes": notes}

    fix_loops = int(job["fix_loops"] or 0)
    status = job["status"]
    blocked = None
    if state == "fail":
        fix_loops += 1
        if fix_loops > int(job["max_fix_loops"] or MAX_FIX_LOOPS):
            status, blocked = "failed", (
                f"фаза {phase} не проходит после {fix_loops} циклов исправления: "
                f"{(errors or [{}])[-1] if errors else notes or 'причина не указана'}")

    merged_files = list(dict.fromkeys((job["files_changed"] or []) + (files_changed or [])))
    merged_tests = (job["tests"] or []) + (tests or [])
    merged_errors = (job["errors"] or []) + (errors or [])
    merged_dec = (job["decisions"] or []) + (decisions or [])

    nxt = job["current_phase"]
    if state == "pass":
        # PHASE_TRUTH_V1: current_phase must follow the same required-phase
        # sequence used by lead._next_phase(). Using the global PHASES list
        # made non-UI jobs display FRONTEND while their real next A2A order was
        # TESTING. Keep legacy fallback only for optional/non-required phases.
        required = list(job.get("required_phases") or [])
        if phase in required:
            for candidate in required[required.index(phase) + 1:]:
                if (phases.get(candidate) or {}).get("state") not in ("pass", "skipped"):
                    nxt = candidate
                    break
        else:
            order = [p for p in PHASES if p != "COMPLETED"]
            if phase in order and order.index(phase) + 1 < len(order):
                nxt = order[order.index(phase) + 1]
    elif state == "fail":
        nxt = "FIXING"

    if status not in ("blocked", "failed", "cancelled", "completed"):
        # пока фаза идёт — состояние по ней самой; когда закрыта — по следующей
        status = PHASE_TO_STATE.get(phase if state == "running" else nxt, "running")

    _set(job["id"], phases_json=json.dumps(phases, ensure_ascii=False),
         files_changed_json=json.dumps(merged_files, ensure_ascii=False)[:40000],
         tests_json=json.dumps(merged_tests, ensure_ascii=False)[:40000],
         errors_json=json.dumps(merged_errors, ensure_ascii=False)[:40000],
         decisions_json=json.dumps(merged_dec, ensure_ascii=False)[:40000],
         current_phase=nxt, status=status, fix_loops=fix_loops, blocked_reason=blocked,
         qa_status=("PASS" if phase == "PRODUCTION_QA" and state == "pass" else
                    ("FAIL" if phase == "PRODUCTION_QA" and state == "fail" else job["qa_status"])),
         deployment_status=("deployed" if phase == "DEPLOYMENT" and state == "pass"
                            else job["deployment_status"]))
    event(job["id"], phase, actor, f"phase_{state}",
          {"evidence": (str(evidence)[:1000] if evidence else None), "notes": notes})
    if status in ("blocked", "failed"):
        release(job["id"])
        schedule()
    return get(job["id"])


def mark_criteria(key, dev_id, index, done, evidence=None):
    job = get(dev_id)
    crit = job["acceptance_criteria"]
    if not isinstance(index, int) or not (0 <= index < len(crit)):
        raise ApiError("VALIDATION_ERROR", f"индекс критерия вне диапазона 0..{len(crit) - 1}")
    if done and not (evidence and str(evidence).strip()):
        raise ApiError("VALIDATION_ERROR", "критерий закрывается только с evidence")
    crit[index]["done"] = bool(done)
    crit[index]["evidence"] = (str(evidence)[:2000] if evidence else None)
    crit[index]["by"] = auth.actor_of(key)["name"]
    _set(job["id"], acceptance_json=json.dumps(crit, ensure_ascii=False))
    event(job["id"], job["current_phase"], auth.actor_of(key)["name"], "criteria",
          {"index": index, "done": bool(done)})
    return get(job["id"])


def add_criteria(key, dev_id, texts, why=None):
    """Добавить критерии приёмки по ходу задачи.

    Разведка закрывает свои критерии и рождает следующие: пока не посмотрел,
    сформулировать их честно нельзя. Добавление видно в истории задачи.
    """
    job = get(dev_id)
    crit = job["acceptance_criteria"]
    have = {(c.get("text") or "").strip() for c in crit}
    added = []
    for t in (texts or []):
        t = str(t).strip()
        if t and t not in have:
            crit.append({"text": t, "done": False, "evidence": None})
            have.add(t)
            added.append(t)
    if not added:
        return get(job["id"])
    _set(job["id"], acceptance_json=json.dumps(crit, ensure_ascii=False))
    event(job["id"], job["current_phase"], auth.actor_of(key)["name"], "criteria_added",
          {"added": added, "why": (why or "")[:300]})
    return get(job["id"])


def set_op(key, dev_id, name, status, requested=None, actual=None, evidence=None,
           error_code=None, note=None):
    """Статус одной дочерней операции. completed — только с доказательством."""
    job = get(dev_id)
    name = (name or "").upper()
    if name not in OP_NAMES:
        raise ApiError("VALIDATION_ERROR", "операция должна быть одной из: " + ", ".join(OP_NAMES))
    if status not in OP_STATES:
        raise ApiError("VALIDATION_ERROR", "статус должен быть одним из: " + ", ".join(OP_STATES))
    if status == "completed":
        ok_by_count = (requested is not None and actual is not None and int(actual) == int(requested))
        if not ok_by_count and not (evidence and str(evidence).strip()):
            raise ApiError("VALIDATION_ERROR",
                           f"операцию {name} нельзя закрыть без доказательства: "
                           "нужен либо evidence, либо совпадение requested и actual")
    db.q("""INSERT INTO ext_job_ops (dev_job_id, name, status, requested, actual,
                evidence, error_code, note, updated_at)
            VALUES (:d,:n,:s,:rq,:ac,:ev,:ec,:nt,NOW())
            ON CONFLICT (COALESCE(dev_job_id,0), COALESCE(job_id,0), name) DO UPDATE
              SET status=:s, requested=COALESCE(:rq, ext_job_ops.requested),
                  actual=COALESCE(:ac, ext_job_ops.actual),
                  evidence=COALESCE(:ev, ext_job_ops.evidence),
                  error_code=:ec, note=COALESCE(:nt, ext_job_ops.note), updated_at=NOW()""",
         d=job["id"], n=name, s=status, rq=requested, ac=actual,
         ev=(str(evidence)[:2000] if evidence else None), ec=error_code,
         nt=(note or "")[:500] or None)
    event(job["id"], job["current_phase"], auth.actor_of(key)["name"], f"op_{status}",
          {"op": name, "requested": requested, "actual": actual})
    return get(job["id"])


def ops_of(dev_id):
    return db.rows("""SELECT name, status, requested, actual, error_code, note, updated_at
                      FROM ext_job_ops WHERE dev_job_id=:d ORDER BY name""", d=int(dev_id))


def gate_report(job):
    """Что мешает закрыть задачу. Пустой список = можно закрывать."""
    missing = []
    for p in job["required_phases"]:
        ph = job["phases"].get(p) or {}
        st = ph.get("state")
        if st == "pass":
            continue
        # Пропуск фазы допустим, но только объяснённый: причина записана и
        # видна в истории. Молчаливый skipped гейтом не считается.
        if st == "skipped" and (ph.get("notes") or "").strip():
            continue
        missing.append({"gate": p, "state": st or "pending",
                        "why": ("пропуск без объяснения" if st == "skipped" else None)})
    for i, c in enumerate(job["acceptance_criteria"]):
        if not c.get("done"):
            missing.append({"gate": f"acceptance[{i}]", "state": "not_done", "text": c["text"]})
    for op in job.get("operations") or []:
        if op["status"] not in ("completed", "skipped"):
            missing.append({"gate": f"operation:{op['name']}", "state": op["status"]})
    # LEVEL 4. Компиляция, тесты, зелёный selftest и даже ACCEPT — это
    # промежуточные доказательства: они говорят, что реализация принята, а не
    # что клиент что-то получил. Клиентская задача закрывается только когда
    # результат виден там, где его должен увидеть клиент.
    try:
        from . import report as _report
        biz = _report.business_gate(job.get("title"))
        if biz and not biz["ok"]:
            missing.append({"gate": "business_truth", "state": biz["state"],
                            "why": biz["why"]})
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=588)
    return missing


def complete(key, dev_id, summary=None):
    job = get(dev_id)
    missing = gate_report(job)
    if missing:
        raise ApiError("POSTFLIGHT_MISMATCH",
                       "закрыть нельзя: не пройдены обязательные гейты",
                       details={"missing": missing})
    phases = job["phases"]
    phases["COMPLETED"] = {"state": "pass", "by": auth.actor_of(key)["name"],
                           "at": datetime.utcnow().isoformat(), "evidence": "все гейты PASS"}
    # Completion truth must be generated from the completed row itself.
    # Building final_result from the pre-completion snapshot used to leave
    # REAL_RESULT.status/current_phase and NEXT_STEP stale (e.g. waiting /
    # DEPLOYMENT) even though the DB row was already COMPLETED. Also clear any
    # historical wait/block reason: a terminal completed job must not keep a
    # stale "waiting" explanation in owner UI.
    _set(job["id"], status="completed", current_phase="COMPLETED",
         phases_json=json.dumps(phases, ensure_ascii=False),
         wait_reason=None, blocked_reason=None)
    completed_job = get(job["id"])
    _set(job["id"],
         final_result_json=json.dumps(
             report(completed_job, summary), ensure_ascii=False
         )[:40000])
    event(job["id"], "COMPLETED", auth.actor_of(key)["name"], "completed", {})
    release(job["id"])
    schedule()
    return get(job["id"])


def report(job, summary=None):
    if isinstance(job, (int, str)):
        job = get(job)
    passed = [p for p, v in job["phases"].items() if v.get("state") == "pass"]
    failed = [p for p, v in job["phases"].items() if v.get("state") == "fail"]
    missing = gate_report(job)
    return {
        "WHAT_CHANGED": summary or job["goal"],
        "WHAT_WAS_TESTED": {"phases_passed": sorted(passed), "phases_failed": sorted(failed),
                            "tests": job["tests"]},
        "REAL_RESULT": {"status": job["status"], "current_phase": job["current_phase"],
                        "deployment": job["deployment_status"],
                        "blocked_reason": job["blocked_reason"]},
        "OPERATIONS": {o["name"]: o["status"] for o in (job.get("operations") or [])},
        "QA_RESULT": {"production_qa": job["qa_status"],
                      "acceptance": [{"text": c["text"], "done": c["done"]}
                                     for c in job["acceptance_criteria"]],
                      "gates_missing": missing},
        "FILES_CHANGED": job["files_changed"],
        "NEXT_STEP": ("задача закрыта" if not missing and job["status"] == "completed"
                      else ("разблокировать: " + job["blocked_reason"]) if job["blocked_reason"]
                      else f"следующая фаза {job['current_phase']}, "
                           f"осталось гейтов: {len(missing)}"),
    }


def listing(limit=50, status=None):
    sql, p = "SELECT id FROM ext_dev_jobs WHERE 1=1", {}
    if status:
        sql += " AND status=:s"; p["s"] = status
    sql += " ORDER BY id DESC LIMIT :l"; p["l"] = limit
    return [get(r["id"]) for r in db.rows(sql, **p)]


# ====================================================== параллельный диспетчер

def _raw(dev_id):
    return db.one("SELECT * FROM ext_dev_jobs WHERE id=:i", i=int(dev_id))


def business_goal_terminal_job_ids():
    """Non-terminal dev rows whose client/business result is already proved.

    They must not consume scheduler capacity or be resurrected merely because
    historical technical evidence gates are incomplete. This does NOT mark the
    dev job completed; completion still requires gate_report()/complete().
    """
    # BUSINESS_GOAL_TERMINAL_ANY_PROOF_V2: the terminal proof is durable.
    # A stale runtime can race after that proof and append a newer queued/blocked
    # order before the canonical generation converges. Looking only at MAX(id)
    # makes the proof disappear and lets scheduler/provider retries resurrect a
    # client whose fresh business truth is already DONE. Any canonical
    # business_goal_already_confirmed receipt fences the dev job; if production
    # truth later becomes incomplete, ensure_business_orders() owns the explicit
    # recovery decision rather than scheduler guessing from order chronology.
    rows = db.rows("""
        SELECT j.id
          FROM ext_dev_jobs j
         WHERE j.status NOT IN ('completed','cancelled')
           AND EXISTS (
               SELECT 1 FROM ext_a2a_orders o
                WHERE o.dev_job_id=j.id
                  AND o.blocked_reason='business_goal_already_confirmed'
           )
    """)
    return {int(r['id']) for r in rows}


def reconcile_legacy_locks(limit=500):
    """Persist conservative locks for active legacy jobs that only have scope.

    Runs independently from provider availability, so metadata self-heals even
    while coding capacity is temporarily zero. It does not assign or execute
    work; it only aligns scheduler locks with the already-declared job scope.
    """
    rows = db.rows("""SELECT id,current_phase,scope_json,locks_json
                      FROM ext_dev_jobs
                      WHERE status IN ('queued','planning','running','waiting',
                                       'review','testing','fixing','acceptance')
                      ORDER BY priority DESC,id ASC LIMIT :l""", l=int(limit))
    healed = []
    for r in rows:
        existing = [_norm_lock_path(x) for x in (_j(r.get("locks_json"), []) or [])
                    if _norm_lock_path(x)]
        if existing:
            continue
        derived = _scope_lock_paths(r)
        if not derived:
            continue
        encoded = json.dumps(derived, ensure_ascii=False)
        _set(r["id"], locks_json=encoded)
        event(r["id"], r.get("current_phase") or "PLANNING", "scheduler",
              "locks_self_healed_from_scope", {"locks": derived})
        healed.append({"dev_job_id": f"dev_{r['id']}", "locks": derived})
    return {"healed": healed, "count": len(healed)}


def reconcile_superseded_dependencies(limit=1000):
    """Replace dependencies on cancelled superseded jobs with their successor.

    A cancelled dependency normally means a hard stop. When cancellation is an
    explicit SUPERSEDED_BY transition, active jobs must follow the successor
    automatically or they wait forever on work that will never complete.
    """
    rows = db.rows("""SELECT id,current_phase,depends_on_json
                      FROM ext_dev_jobs
                      WHERE status IN ('queued','planning','running','waiting',
                                       'review','testing','fixing','acceptance')
                      ORDER BY id LIMIT :l""", l=int(limit))
    healed=[]
    for row in rows:
        deps=[int(x) for x in (_j(row.get("depends_on_json"), []) or [])]
        if not deps:
            continue
        changed=False
        out=[]
        replacements=[]
        for dep_id in deps:
            dep=db.one("""SELECT id,status,blocked_reason,wait_reason
                          FROM ext_dev_jobs WHERE id=:i""", i=int(dep_id))
            successor=None
            if dep and dep.get("status")=="cancelled":
                marker=(str(dep.get("blocked_reason") or "")+" "+str(dep.get("wait_reason") or ""))
                m=re.search(r"superseded(?:_by| by|_BY| BY)(?:_dev)?[_: ]*(?:dev_)?(\d+)", marker, re.I)
                if m:
                    successor=int(m.group(1))
            if successor and successor != dep_id:
                out.append(successor)
                replacements.append({"from":dep_id,"to":successor})
                changed=True
            else:
                out.append(dep_id)
        merged=sorted(set(out))
        if not changed or merged==deps:
            continue
        _set(row["id"],depends_on_json=json.dumps(merged,ensure_ascii=False),
             wait_reason="ждёт задачи: "+", ".join(f"dev_{x}" for x in merged[:5]))
        event(row["id"],row.get("current_phase") or "PLANNING","scheduler",
              "superseded_dependencies_reconciled",{"replacements":replacements,"depends_on":merged})
        healed.append({"dev_job_id":f"dev_{row['id']}","replacements":replacements,"depends_on":merged})
    return {"count":len(healed),"healed":healed}


def reconcile_dedup_dependencies(limit=1000):
    """Absorb dead dependencies explicitly merged into this same workstream.

    WORKSTREAM_DEDUP disables an old duplicate writer. A newer same-owner job
    must not wait forever for that intentionally disabled predecessor. We only
    absorb the dependency when the current job itself has one canonical owner,
    no unowned write paths, and the dedup marker names that exact owner.
    """
    from app.services.workstream_scope import classify_workstream_paths, load_registry

    registry=load_registry()
    rows=db.rows("""SELECT id,current_phase,scope_json,locks_json,depends_on_json
                    FROM ext_dev_jobs
                    WHERE status IN ('queued','planning','running','waiting',
                                     'review','testing','fixing','acceptance')
                    ORDER BY id LIMIT :l""", l=int(limit))
    healed=[]
    for row in rows:
        deps=[int(x) for x in (_j(row.get("depends_on_json"), []) or [])]
        if not deps:
            continue
        scope=_j(row.get("scope_json"), {}) or {}
        declared_owner=str(scope.get("workstream_id") or scope.get("workstream") or "").strip()
        locks=[_norm_lock_path(x) for x in (_j(row.get("locks_json"), []) or []) if _norm_lock_path(x)]
        classified=classify_workstream_paths(locks,registry)
        owners=classified.get("owners") or []
        if (
            len(owners)!=1
            or classified.get("ambiguous")
            or classified.get("unowned_paths")
        ):
            continue
        owner=owners[0]
        if declared_owner and declared_owner!=owner:
            continue

        kept=[]
        absorbed=[]
        for dep_id in deps:
            dep=db.one("""SELECT id,status,blocked_reason,wait_reason,scope_json
                          FROM ext_dev_jobs WHERE id=:i""", i=int(dep_id))
            if not dep or dep.get("status")!="blocked":
                kept.append(dep_id)
                continue
            marker=(str(dep.get("blocked_reason") or "")+" "+str(dep.get("wait_reason") or ""))
            match=re.search(r"WORKSTREAM_DEDUP:\s*merged_into_current_([a-zA-Z0-9_]+)_chat",marker)
            if not match or match.group(1)!=owner:
                kept.append(dep_id)
                continue
            dep_scope=_j(dep.get("scope_json"), {}) or {}
            dep_owner=str(dep_scope.get("workstream_id") or dep_scope.get("workstream") or "").strip()
            if dep_owner and dep_owner!=owner:
                kept.append(dep_id)
                continue
            absorbed.append(dep_id)

        if not absorbed:
            continue
        merged=sorted(set(kept))
        wait_reason=(
            "ждёт задачи: "+", ".join(f"dev_{x}" for x in merged[:5])
            if merged else
            "WORKSTREAM_DEDUP_DEPENDENCIES_ABSORBED: scheduler may resume"
        )
        _set(row["id"],depends_on_json=json.dumps(merged,ensure_ascii=False),wait_reason=wait_reason)
        event(row["id"],row.get("current_phase") or "PLANNING","scheduler",
              "dedup_dependencies_absorbed",{
                  "owner":owner,"absorbed":absorbed,"depends_on":merged,
              })
        healed.append({
            "dev_job_id":f"dev_{row['id']}","owner":owner,
            "absorbed":absorbed,"depends_on":merged,
        })
    return {"count":len(healed),"healed":healed}


def reconcile_legacy_scope_narrowing(limit=1000):
    """Self-heal only deterministic, explicitly proven legacy broad scopes."""
    rows = db.rows("""SELECT id,title,status,current_phase,scope_json,locks_json,blocked_reason
                      FROM ext_dev_jobs
                      WHERE status IN ('queued','waiting','blocked','planning','review','testing','fixing','acceptance')
                      ORDER BY id LIMIT :l""", l=int(limit))
    healed=[]
    try:
        from app.services.workstream_scope import classify_workstream_paths, load_registry
        registry = load_registry()
    except Exception:
        registry = None
    for row in rows:
        scope_obj = _j(row.get("scope_json"), {}) or {}
        locks = [_norm_lock_path(x) for x in (_j(row.get("locks_json"), []) or [])
                 if _norm_lock_path(x)]
        narrowed, new_locks, decision = _legacy_scope_narrowing(
            row.get("title"), scope_obj, locks
        )
        if not decision:
            continue
        if registry is not None:
            classified = classify_workstream_paths(new_locks, registry)
            if classified.get("owners") != [decision["workstream_id"]] or classified.get("ambiguous"):
                continue
        fields = {
            "scope_json": json.dumps(narrowed, ensure_ascii=False),
            "locks_json": json.dumps(new_locks, ensure_ascii=False),
        }
        reason = str(row.get("blocked_reason") or "")
        if row.get("status") == "blocked" and reason.startswith("WORKSTREAM_SCOPE_AMBIGUOUS:"):
            fields.update({
                "status": "waiting",
                "blocked_reason": None,
                "wait_reason": "WORKSTREAM_SCOPE_RECOVERED: deterministic legacy scope narrowed; scheduler may resume",
            })
        _set(row["id"], **fields)
        requeued_orders = _requeue_scope_recovered_orders(
            row["id"], narrowed, new_locks, decision["workstream_id"]
        )
        event(
            row["id"], row.get("current_phase") or "PLANNING", "scheduler",
            "legacy_scope_narrowed",
            {**decision, "requeued_orders": requeued_orders},
        )
        healed.append({
            "dev_job_id": f"dev_{row['id']}",
            **decision,
            "requeued_orders": requeued_orders,
        })
    return {"count": len(healed), "healed": healed}


def reconcile_blocked_parent_planning_read_only(limit=1000):
    """Remove obsolete broad write claims from blocked inspection-only parents.

    Safe conversion requires all of the following: parent is still PLANNING,
    it is blocked only by the broad workstream-scope quarantine, no unsettled
    mutation exists, no executor is running, and the latest PLANNING child is
    already an explicit read-only order blocked for a non-scope reason.

    An old APPLIED patch is historical once the latest read-only child has been
    updated after that patch was applied. Treating every APPLIED patch forever
    as "live" made old bootstrap work permanently pin broad write locks and
    forced the owner to act as a recovery operator. Proposed/approved patches,
    and applied patches newer than the current child truth, still fail closed.
    We preserve the child block and never requeue work here.
    """
    rows = db.rows(
        """SELECT id,title,status,current_phase,scope_json,locks_json,
                  blocked_reason
             FROM ext_dev_jobs
            WHERE status='blocked'
              AND current_phase='PLANNING'
              AND blocked_reason LIKE 'WORKSTREAM_SCOPE_AMBIGUOUS:%'
            ORDER BY priority DESC,id
            LIMIT :l""",
        l=int(limit),
    )
    healed = []
    skipped = []
    for row in rows:
        job_id = int(row["id"])
        running = db.one(
            "SELECT id FROM ext_a2a_orders WHERE dev_job_id=:j AND status='running' LIMIT 1",
            j=job_id,
        )
        if running:
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "running_order"})
            continue
        order = db.one(
            """SELECT id,phase,status,scope_json,blocked_reason,updated_at
                 FROM ext_a2a_orders
                WHERE dev_job_id=:j AND phase='PLANNING'
                ORDER BY id DESC LIMIT 1""",
            j=job_id,
        )
        live = db.one(
            """SELECT count(*)::int AS n FROM ext_dev_patches
                 WHERE dev_job_id=:j
                   AND (
                        status IN ('proposed','approved')
                        OR (
                            status='applied'
                            AND (
                                :order_updated IS NULL
                                OR COALESCE(applied_at,created_at) > :order_updated
                            )
                        )
                   )""",
            j=job_id,
            order_updated=(order or {}).get("updated_at"),
        ) or {}
        if int(live.get("n") or 0):
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "live_patch"})
            continue
        if not order or str(order.get("status") or "") != "blocked_infra":
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "planning_child_not_blocked"})
            continue
        order_scope = _j(order.get("scope_json"), {}) or {}
        if not isinstance(order_scope, dict) or order_scope.get("read_only") is not True:
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "planning_child_not_read_only"})
            continue
        child_reason = str(order.get("blocked_reason") or "").strip()
        if (
            not child_reason
            or child_reason.startswith("WORKSTREAM_SCOPE_AMBIGUOUS:")
            or child_reason.startswith("WORKSTREAM_ORDER_SCOPE_STALE:")
            or child_reason.startswith("INHERITED_PARENT_SCOPE_QUARANTINE:")
        ):
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "child_still_scope_blocked"})
            continue

        scope = _j(row.get("scope_json"), {}) or {}
        if not isinstance(scope, dict):
            scope = {}
        scope = dict(scope)
        old_paths = _scope_lock_paths(row)
        inspection = []
        for value in (order_scope.get("inspection_paths") or old_paths):
            norm = _norm_lock_path(value)
            if norm and norm not in inspection:
                inspection.append(norm)
        for key in ("paths", "files", "allowed_paths"):
            scope.pop(key, None)
        scope["inspection_paths"] = inspection
        scope["read_only"] = True
        scope["scope_mode"] = "planning_read_only"
        scope["scope_guard"] = "WORKSTREAM_PARENT_PLANNING_READ_ONLY_V1"
        parent_reason = ("PLANNING_READ_ONLY_CHILD_BLOCK: " + child_reason)[:500]
        _set(
            job_id,
            assigned_agent=None,
            scope_json=json.dumps(scope, ensure_ascii=False),
            locks_json="[]",
            blocked_reason=parent_reason,
            wait_reason=parent_reason,
        )
        event(
            job_id, "PLANNING", "scheduler",
            "parent_planning_write_scope_released",
            {
                "inspection_paths": inspection,
                "child_order_id": int(order["id"]),
                "child_blocked_reason": child_reason[:280],
            },
        )
        healed.append({
            "dev_job_id": f"dev_{job_id}",
            "inspection_paths": inspection,
            "child_order_id": int(order["id"]),
            "child_blocked_reason": child_reason[:280],
        })
    return {
        "count": len(healed),
        "healed": healed,
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def reconcile_readonly_parent_dependency_wait(limit=1000):
    """Replace an obsolete read-only PRODUCTION_CONFLICT with its real dependency wait.

    A read-only PLANNING child cannot write production. If the parent already has
    explicit live dependencies, keeping an old PRODUCTION_CONFLICT child forever
    makes the owner a recovery operator. We only retire an unclaimed blocked
    read-only child and let the normal dependency scheduler resume later.
    """
    rows = db.rows(
        """SELECT id,current_phase,scope_json,depends_on_json,blocked_reason
             FROM ext_dev_jobs
            WHERE status='blocked'
              AND current_phase='PLANNING'
              AND blocked_reason LIKE
                  'PLANNING_READ_ONLY_CHILD_BLOCK: PRODUCTION_CONFLICT:%'
            ORDER BY priority DESC,id
            LIMIT :l""",
        l=int(limit),
    )
    healed, skipped = [], []
    active_dep_states = {
        "queued","planning","running","waiting","review","testing",
        "fixing","acceptance","blocked",
    }
    for row in rows:
        job_id = int(row["id"])
        scope = _j(row.get("scope_json"), {}) or {}
        deps = [int(x) for x in (_j(row.get("depends_on_json"), []) or [])]
        if scope.get("read_only") is not True or not deps:
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "not_readonly_or_no_dependency"})
            continue
        order = db.one(
            """SELECT id,status,phase,scope_json,blocked_reason,claimed_by
                 FROM ext_a2a_orders
                WHERE dev_job_id=:j
                ORDER BY id DESC LIMIT 1""",
            j=job_id,
        )
        if (
            not order
            or str(order.get("status") or "") != "blocked_infra"
            or str(order.get("phase") or "").upper() != "PLANNING"
            or order.get("claimed_by")
        ):
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "child_not_safe_to_retire"})
            continue
        order_scope = _j(order.get("scope_json"), {}) or {}
        child_reason = str(order.get("blocked_reason") or "").strip()
        if (
            order_scope.get("read_only") is not True
            or not child_reason.startswith("PRODUCTION_CONFLICT:")
        ):
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "child_not_readonly_conflict"})
            continue

        dep_rows = []
        unsafe = False
        for dep_id in deps:
            dep = db.one(
                "SELECT id,status,title FROM ext_dev_jobs WHERE id=:i",
                i=dep_id,
            )
            if not dep or str(dep.get("status") or "") not in (active_dep_states | {"completed"}):
                unsafe = True
                break
            dep_rows.append(dep)
        if unsafe:
            skipped.append({"dev_job_id": f"dev_{job_id}", "reason": "dependency_not_safe"})
            continue

        unmet = [int(x["id"]) for x in dep_rows if x.get("status") != "completed"]
        wait_reason = (
            "ждёт задачи: " + ", ".join(f"dev_{x}" for x in unmet[:5])
            if unmet
            else "READONLY_PRODUCTION_CONFLICT_CLEARED: scheduler may resume"
        )
        db.q(
            """UPDATE ext_a2a_orders
                  SET status='cancelled',
                      blocked_reason=:reason,
                      claimed_by=NULL,
                      claimed_at=NULL,
                      updated_at=NOW()
                WHERE id=:o
                  AND status='blocked_infra'
                  AND claimed_by IS NULL""",
            o=int(order["id"]),
            reason=("superseded_by_dependency_wait" if unmet
                    else "obsolete_readonly_production_conflict"),
        )
        _set(
            job_id,
            status="waiting",
            assigned_agent=None,
            blocked_reason=None,
            wait_reason=wait_reason,
        )
        event(
            job_id, "PLANNING", "scheduler",
            "readonly_production_conflict_reconciled",
            {
                "retired_order_id": int(order["id"]),
                "dependencies": deps,
                "unmet_dependencies": unmet,
                "wait_reason": wait_reason,
            },
        )
        healed.append({
            "dev_job_id": f"dev_{job_id}",
            "retired_order_id": int(order["id"]),
            "dependencies": deps,
            "unmet_dependencies": unmet,
        })
    return {
        "count": len(healed),
        "healed": healed,
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def reconcile_scope_recovered_orders(limit=1000):
    """Finish legacy scope self-heal when parent was narrowed before its order.

    This is deliberately limited to jobs carrying our deterministic scope_split
    marker. It never touches provider/dependency/human blocks; the low-level
    helper filters to obsolete workstream-scope quarantine reasons only.
    """
    rows = db.rows(
        """SELECT id,current_phase,scope_json,locks_json
             FROM ext_dev_jobs
            WHERE status IN ('queued','waiting','planning','running',
                             'review','testing','fixing','acceptance')
            ORDER BY id LIMIT :l""",
        l=int(limit),
    )
    healed = []
    for row in rows:
        scope = _j(row.get("scope_json"), {}) or {}
        if not isinstance(scope, dict) or not scope.get("scope_split"):
            continue
        owner = str(scope.get("workstream_id") or "").strip()
        locks = [
            _norm_lock_path(x)
            for x in (_j(row.get("locks_json"), []) or [])
            if _norm_lock_path(x)
        ]
        if not owner or not locks:
            continue
        recovered = _requeue_scope_recovered_orders(
            row["id"], scope, locks, owner
        )
        if not recovered:
            continue
        event(
            row["id"],
            row.get("current_phase") or "PLANNING",
            "scheduler",
            "scope_recovered_orders_requeued",
            {"workstream_id": owner, "requeued_orders": recovered},
        )
        healed.append({
            "dev_job_id": f"dev_{row['id']}",
            "workstream_id": owner,
            "requeued_orders": recovered,
        })
    return {"count": len(healed), "healed": healed}


def development_lock_health(self_heal=True, limit=1000):
    """Health of dev-job ownership and hierarchical file locks.

    Waiting jobs may legitimately overlap because the scheduler serializes them.
    Only metadata gaps or simultaneous executing jobs with overlapping locks are
    unhealthy. Optional self-heal fills missing locks from explicit scope.
    """
    narrowed = reconcile_legacy_scope_narrowing(limit) if self_heal else {"count": 0, "healed": []}
    planning_parent_healed = reconcile_blocked_parent_planning_read_only(limit) if self_heal else {"count": 0, "healed": []}
    readonly_dependency_healed = reconcile_readonly_parent_dependency_wait(limit) if self_heal else {"count": 0, "healed": []}
    order_scope_healed = reconcile_scope_recovered_orders(limit) if self_heal else {"count": 0, "healed": []}
    healed = reconcile_legacy_locks(limit) if self_heal else {"count": 0, "healed": []}
    dep_healed = reconcile_superseded_dependencies(limit) if self_heal else {"count": 0, "healed": []}
    dedup_dep_healed = reconcile_dedup_dependencies(limit) if self_heal else {"count": 0, "healed": []}
    rows = db.rows("""SELECT id,title,status,current_phase,scope_json,locks_json,depends_on_json
                      FROM ext_dev_jobs
                      WHERE status IN ('queued','planning','running','waiting',
                                       'review','testing','fixing','acceptance')
                      ORDER BY id LIMIT :l""", l=int(limit))
    from . import a2a as _a2a
    # Actual execution means a worker owns a RUNNING order. Queued,
    # returned and review_requested orders are open lifecycle state, but no
    # executor is currently writing production for them.
    busy_ids = {int(r["dev_job_id"]) for r in db.rows(
        """SELECT DISTINCT dev_job_id FROM ext_a2a_orders
           WHERE dev_job_id IS NOT NULL
             AND status=:running
             AND claimed_by IS NOT NULL""",
        running=_a2a.RUNNING
    )}
    items = []
    metadata_gaps = []
    unscoped_jobs = []
    broken_dependencies = []
    for row in rows:
        locks = [_norm_lock_path(x) for x in (_j(row.get("locks_json"), []) or [])
                 if _norm_lock_path(x)]
        scope_obj = _j(row.get("scope_json"), {}) or {}
        derived = _scope_lock_paths(row)
        if derived and not locks:
            metadata_gaps.append({
                "dev_job_id": f"dev_{row['id']}",
                "title": row.get("title"),
                "scope_paths": derived,
            })
        explicit_read_only = scope_obj.get("read_only") is True
        planning_only_scope = scope_obj.get("scope_mode") == "planning_read_only"
        read_only_without_locks = (
            explicit_read_only
            and (
                not planning_only_scope
                or str(row.get("current_phase") or "").upper() == "PLANNING"
            )
        )
        if not derived and not locks and not read_only_without_locks:
            unscoped_jobs.append({
                "dev_job_id": f"dev_{row['id']}",
                "title": row.get("title"),
                "status": row.get("status"),
                "current_phase": row.get("current_phase"),
            })
        for dep_id in [int(x) for x in (_j(row.get("depends_on_json"), []) or [])]:
            dep = db.one("SELECT id,title,status,blocked_reason,wait_reason FROM ext_dev_jobs WHERE id=:i", i=dep_id)
            if dep is None or dep.get("status") in ("cancelled", "failed"):
                broken_dependencies.append({
                    "dev_job_id": f"dev_{row['id']}",
                    "title": row.get("title"),
                    "dependency": f"dev_{dep_id}",
                    "dependency_state": dep.get("status") if dep else "missing",
                    "dependency_title": dep.get("title") if dep else None,
                })
        items.append((row, locks))

    running_conflicts = []
    potential_overlap_pairs = 0
    for idx, (left, left_locks) in enumerate(items):
        if not left_locks:
            continue
        for right, right_locks in items[idx + 1:]:
            if not right_locks:
                continue
            clashes = _lock_conflicts(left_locks, right_locks)
            if not clashes:
                continue
            potential_overlap_pairs += 1
            if int(left["id"]) in busy_ids and int(right["id"]) in busy_ids:
                running_conflicts.append({
                    "left": f"dev_{left['id']}",
                    "left_title": left.get("title"),
                    "right": f"dev_{right['id']}",
                    "right_title": right.get("title"),
                    "locks": clashes[:10],
                })

    critical = bool(running_conflicts)
    degraded = bool(metadata_gaps or unscoped_jobs or broken_dependencies)
    state = "critical" if critical else ("degraded" if degraded else "ok")
    return {
        "ok": not critical and not degraded,
        "state": state,
        "active_jobs": len(rows),
        "executing_jobs": len(busy_ids),
        "metadata_gaps": metadata_gaps,
        "unscoped_jobs": unscoped_jobs,
        "broken_dependencies": broken_dependencies,
        "running_conflicts": running_conflicts,
        "potential_overlap_pairs": potential_overlap_pairs,
        "self_healed": int(healed.get("count") or 0),
        "legacy_scope_self_healed": int(narrowed.get("count") or 0),
        "legacy_scope_healed_jobs": narrowed.get("healed") or [],
        "planning_parent_self_healed": int(planning_parent_healed.get("count") or 0),
        "planning_parent_healed_jobs": planning_parent_healed.get("healed") or [],
        "readonly_dependency_self_healed": int(readonly_dependency_healed.get("count") or 0),
        "readonly_dependency_healed_jobs": readonly_dependency_healed.get("healed") or [],
        "scope_order_self_healed": int(order_scope_healed.get("count") or 0),
        "scope_order_healed_jobs": order_scope_healed.get("healed") or [],
        "dependency_self_healed": int(dep_healed.get("count") or 0),
        "dedup_dependency_self_healed": int(dedup_dep_healed.get("count") or 0),
        "dedup_dependency_healed_jobs": dedup_dep_healed.get("healed") or [],
        "owner_action_required": False,
        "self_heal": (
            "missing locks are restored from scope; stale read-only conflicts become dependency waits; superseded/dedup dependencies self-heal; unscoped jobs are quarantined; scheduler serializes hierarchical overlaps"
            if not critical else
            "new overlapping assignments are blocked; simultaneous executing conflict requires executor reconciliation"
        ),
    }


# PRODUCTION_CONVERGENCE_DRAIN_V1: production gets a quiet window before the
# scheduler admits the next source-mutating development phase. This is not a
# global development freeze: planning/QA/deploy keep moving, and explicit P0 or
# client-floor work may still pass. The gate is active only for a fresh global
# acceptance failure where all public/API health checks are green and no owner
# action is required — i.e. the system is serving clients but the current source
# generation has not yet converged to one accepted runtime.
_PRODUCTION_ACCEPTANCE_PATH = Path(__file__).resolve().parents[2] / "data" / "global_production_acceptance.json"
_PRODUCTION_MUTATING_PHASES = {"BACKEND", "FRONTEND", "INTEGRATION", "FIXING"}


def _live_backend_generation_current() -> dict:
    """Verify that the served backend generation still matches current bytes.

    ACCEPTANCE_LIVE_GENERATION_FENCE_V1: the persisted global acceptance can be
    PASS for a few minutes after another workstream lands new backend source.
    Admission must not rely on that stale PASS window. Reuse the canonical
    content certificate written only after a successful HA rolling deployment.
    """
    try:
        import production_contract_runner as _production_contract
        certified = _production_contract.BACKEND_GENERATION_CERT.read_text(
            encoding="utf-8"
        ).strip()
        current = _production_contract._backend_runtime_content_fingerprint()
        return {
            "current": bool(certified and certified == current),
            "certified_fingerprint": certified or None,
            "current_fingerprint": current,
            "error": None,
        }
    except Exception as exc:
        return {
            "current": False,
            "certified_fingerprint": None,
            "current_fingerprint": None,
            "error": type(exc).__name__,
        }


def _production_convergence_gate(now: float | None = None, max_age_seconds: int = 180) -> dict:
    stamp = float(time.time() if now is None else now)
    try:
        raw = json.loads(_PRODUCTION_ACCEPTANCE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"block_new_writes": False, "reason": "acceptance_state_unavailable", "error": type(exc).__name__}
    try:
        checked_at = float(raw.get("checked_at") or 0)
    except Exception:
        checked_at = 0.0
    age = max(0.0, stamp - checked_at) if checked_at > 0 else 10**9
    fresh = age <= max(30, int(max_age_seconds or 180))
    state = str(raw.get("state") or "")
    reason = str(raw.get("reason") or "")
    contract_status = str(raw.get("contract_status") or "")
    convergence_failure = (
        bool(raw.get("all_health_ok"))
        and not bool(raw.get("owner_action_required"))
        and (
            contract_status == "converging"
            or (state == "failed" and reason == "global_acceptance_failed_after_runtime_check")
        )
    )
    acceptance_pass = (
        state == "pass"
        and contract_status == "pass"
        and bool(raw.get("all_health_ok"))
        and not bool(raw.get("owner_action_required"))
    )
    live_generation = (
        _live_backend_generation_current()
        if acceptance_pass
        else {
            "current": None,
            "certified_fingerprint": None,
            "current_fingerprint": None,
            "error": None,
        }
    )
    live_drift = bool(acceptance_pass and live_generation.get("current") is False)
    block_new_writes = bool((fresh and convergence_failure) or live_drift)
    if live_drift:
        gate_reason = (
            "live_backend_generation_check_failed"
            if live_generation.get("error")
            else "live_backend_generation_drift"
        )
    elif fresh and convergence_failure:
        gate_reason = "production_convergence_drain"
    else:
        gate_reason = "open"
    return {
        "block_new_writes": block_new_writes,
        "reason": gate_reason,
        "age_seconds": round(age, 1),
        "contract_status": raw.get("contract_status"),
        "last_pass_at": raw.get("last_pass_at"),
        "self_heal_action": raw.get("self_heal_action"),
        "live_backend_generation_current": live_generation.get("current"),
        "live_backend_generation_error": live_generation.get("error"),
    }


def _explicit_p0_job(row: dict) -> bool:
    title = str((row or {}).get("title") or "").upper()
    return bool(re.search(r"(^|[^A-Z0-9])P0(?:[_:\-\s]|$)", title))


def _live_a2a_orders_by_job():
    """Newest live A2A order per job — runtime truth for scheduler/monitoring.

    ext_dev_jobs is the planning/control record, while ext_a2a_orders is the
    execution record. During claim/review/recovery those two rows can be
    updated on different ticks. The scheduler must therefore use the order as
    the source of truth for *what is actually happening now* and must not
    overwrite a live order's job back to WAITING just because capacity or
    dependency policy is re-evaluated on a later tick.
    """
    from . import a2a as _a2a
    states = list(_a2a.OPEN_STATES) + [
        _a2a.BLOCKED_INFRA,
        _a2a.BLOCKED_HUMAN,
    ]
    rows = db.rows(
        """SELECT DISTINCT ON (dev_job_id)
                  dev_job_id,id,status,phase,assigned_to,claimed_by,
                  blocked_reason,updated_at
             FROM ext_a2a_orders
            WHERE status = ANY(:st)
            ORDER BY dev_job_id,id DESC""",
        st=states,
    )
    return {int(r["dev_job_id"]): dict(r) for r in rows}


def _sync_job_from_live_a2a(row, order):
    """Mirror live A2A lifecycle into the human-facing dev job state.

    This is intentionally idempotent and only writes when a value changed, so
    the 30-second dispatcher loop does not churn updated_at.
    """
    from . import a2a as _a2a
    state = str(order.get("status") or "")
    oid = int(order.get("id") or 0)
    reason = str(order.get("blocked_reason") or "").strip()

    if state == _a2a.RUNNING:
        wanted = {
            "status": "running",
            "assigned_agent": order.get("assigned_to") or row.get("assigned_agent"),
            "wait_reason": None,
            "blocked_reason": None,
        }
    elif state == _a2a.REVIEW:
        wanted = {
            "status": "review",
            "assigned_agent": "claude-lead",
            "wait_reason": None,
            "blocked_reason": None,
        }
    elif state == _a2a.RETURNED:
        wanted = {
            "status": "fixing",
            "assigned_agent": order.get("assigned_to") or "gpt-executor",
            "wait_reason": None,
            "blocked_reason": None,
        }
    elif state == _a2a.QUEUED:
        # PROVIDER_WAIT_QUEUE_TRUTH_V2: a queued A2A order is not executable
        # when there is no free development provider. Mirroring it as ordinary
        # QUEUED made the owner believe work was progressing and also erased the
        # machine-readable provider wait reason written by the dispatcher.
        try:
            from . import aiprov as _aiprov
            provider_wait = not bool(_aiprov.development_candidates())
            provider_reason = _aiprov.development_wait_reason() if provider_wait else None
        except Exception:
            provider_wait = False
            provider_reason = None
        wanted = {
            "status": "waiting" if provider_wait else "queued",
            "assigned_agent": None,
            "wait_reason": (
                provider_reason
                if provider_wait else "наряд %s ждёт свободного исполнителя" % oid
            ),
            "blocked_reason": None,
        }
    elif state == _a2a.BLOCKED_INFRA:
        wanted = {
            "status": "waiting",
            "assigned_agent": None,
            "wait_reason": (
                "BLOCKED_INFRA: " + reason[:250]
                if reason else "BLOCKED_INFRA: автоматическое восстановление"
            ),
            "blocked_reason": None,
        }
    elif state == _a2a.BLOCKED_HUMAN:
        wanted = {
            "status": "blocked",
            "assigned_agent": None,
            "wait_reason": None,
            "blocked_reason": reason[:500] or "нужно внешнее решение",
        }
    else:
        return {}

    changed = {
        key: value
        for key, value in wanted.items()
        if row.get(key) != value
    }
    if changed:
        _set(row["id"], **changed)
    return changed


def _sync_all_live_a2a_job_states(live_a2a=None):
    """Self-heal active dev-job status from live A2A runtime truth.

    A live A2A order is the execution truth even when the parent dev-job row is
    stale BLOCKED from an older tick. BLOCKED_HUMAN remains blocked because
    _sync_job_from_live_a2a maps that exact order state back to blocked; a
    recovered QUEUED/RUNNING/REVIEW order must be allowed to clear stale parent
    blockage automatically. Only terminal dev jobs stay authoritative here.
    """
    from . import a2a as _a2a
    live_a2a = live_a2a or _live_a2a_orders_by_job()
    ids = sorted(int(x) for x in live_a2a)
    if not ids:
        return []

    rows = db.rows(
        """SELECT id,status,assigned_agent,wait_reason,blocked_reason
             FROM ext_dev_jobs
            WHERE id = ANY(:ids)
              AND status NOT IN ('completed','failed','cancelled')""",
        ids=ids,
    )
    changed = []
    for row in rows:
        order = live_a2a.get(int(row["id"]))
        if not order:
            continue
        # CONTROL_BLOCK_AUTHORITY_V1: a blocked parent is authoritative while
        # its child is still blocked. Earlier sync logic converted every
        # BLOCKED_INFRA child to a cosmetic WAITING parent, so the workstream
        # guard and dispatcher fought each other every tick and owner-facing
        # state oscillated. A genuinely recovered child (QUEUED/RUNNING/REVIEW/
        # RETURNED) still clears a stale blocked parent through the normal
        # mirror below.
        if (
            str(row.get("status") or "") == "blocked"
            and str(order.get("status") or "") in (
                _a2a.BLOCKED_INFRA, _a2a.BLOCKED_HUMAN,
            )
        ):
            continue
        delta = _sync_job_from_live_a2a(row, order)
        if delta:
            changed.append({
                "dev_job_id": "dev_%s" % row["id"],
                "order_id": int(order.get("id") or 0),
                "order_status": str(order.get("status") or ""),
                "changed": sorted(delta),
            })
    return changed


def reconcile_server_owned_phase_truth(live_a2a=None, limit=500):
    """Self-heal stale current_phase for server-owned autonomous jobs only.

    A2A is authoritative while a live order exists. Without a live order, the
    first unresolved required phase is the canonical continuation. Only the
    display/control phase is repaired; lifecycle status, locks and evidence are
    preserved.
    """
    live_a2a = _live_a2a_orders_by_job() if live_a2a is None else live_a2a
    rows = db.rows(
        """SELECT id,current_phase,status,scope_json,phases_json
             FROM ext_dev_jobs
            WHERE status IN ('queued','planning','running','waiting','review',
                             'testing','fixing','acceptance')
            ORDER BY id LIMIT :l""",
        l=max(1, min(int(limit), 5000)),
    )
    healed = []
    for row in rows:
        scope = _j(row.get("scope_json"), {}) or {}
        if not (
            scope.get("server_owned") is True
            and scope.get("autonomous_dev") is True
        ):
            continue
        phases = _j(row.get("phases_json"), {}) or {}
        expected = None
        source = None
        live = live_a2a.get(int(row["id"]))
        if live:
            live_phase = str(live.get("phase") or "").upper()
            if live_phase in PHASES and live_phase != "COMPLETED":
                expected = live_phase
                source = "live_a2a"
        if expected is None:
            for name in required_phases(scope):
                state = str((phases.get(name) or {}).get("state") or "pending")
                if state not in ("pass", "skipped"):
                    expected = name
                    source = "required_phases"
                    break
        current = str(row.get("current_phase") or "").upper()
        if not expected or expected == current:
            continue
        _set(row["id"], current_phase=expected)
        event(
            row["id"], expected, "scheduler", "phase_truth_reconciled",
            {
                "from": current,
                "to": expected,
                "source": source,
                "live_order_id": (int(live.get("id")) if live and live.get("id") else None),
            },
        )
        healed.append({
            "dev_job_id": "dev_%s" % row["id"],
            "from": current,
            "to": expected,
            "source": source,
        })
    return healed


def schedule(limit=50):
    """Раскладывает очередь по агентам.

    Правила: зависимости -> блокировки ресурсов -> ёмкость агента -> приоритет.
    Ничего не выполняет сам: назначает исполнителя и переводит в running или
    оставляет в waiting с человеческой причиной.
    """
    from . import agents, sched
    # Metadata repair and conflict detection are independent from execution
    # capacity. Fail closed if two workers somehow already own overlapping
    # trees: do not admit more work until executor reconciliation clears it.
    lock_health = development_lock_health(True, max(500, int(limit or 0)))
    production_gate = _production_convergence_gate()
    live_a2a = _live_a2a_orders_by_job()
    phase_truth_sync = reconcile_server_owned_phase_truth(
        live_a2a, max(500, int(limit or 0))
    )
    runtime_state_sync = _sync_all_live_a2a_job_states(live_a2a)
    unscoped_ids = {
        int(str(x.get("dev_job_id") or "dev_0").split("_")[-1])
        for x in (lock_health.get("unscoped_jobs") or [])
    }
    for jid in sorted(unscoped_ids):
        _set(
            jid,
            status="waiting",
            assigned_agent=None,
            wait_reason="UNSCOPED_DEV_JOB: explicit scope/locks required before execution",
        )
    if lock_health.get("running_conflicts"):
        return {
            "assigned": [],
            "waiting": [],
            "capacity_per_agent": MAX_PARALLEL_PER_AGENT,
            "capacity": {},
            "running_now": int(lock_health.get("executing_jobs") or 0),
            "under_pressure": True,
            "lock_gate": "RUNNING_LOCK_CONFLICT",
            "production_gate": production_gate,
            "runtime_state_sync": runtime_state_sync,
            "lock_health": {
                "state": lock_health.get("state"),
                "metadata_gaps": len(lock_health.get("metadata_gaps") or []),
                "unscoped_jobs": len(lock_health.get("unscoped_jobs") or []),
                "broken_dependencies": len(lock_health.get("broken_dependencies") or []),
                "running_conflicts": len(lock_health.get("running_conflicts") or []),
                "potential_overlap_pairs": int(lock_health.get("potential_overlap_pairs") or 0),
                "self_healed": int(lock_health.get("self_healed") or 0),
                "dependency_self_healed": int(lock_health.get("dependency_self_healed") or 0),
            },
        }
    # SCHEDULER_PROVIDER_GATE_V1: never convert WAITING_FREE_CODING_PROVIDER
    # jobs back to RUNNING merely because daemon agent names exist in the
    # registry. No free coding provider means zero executable development
    # capacity; preserve truthful WAITING state until dispatcher reprobe heals.
    try:
        from . import aiprov as _aiprov
        if not _aiprov.development_candidates():
            rows = db.rows("""SELECT id,depends_on_json FROM ext_dev_jobs
                               WHERE status IN ('queued','waiting','running','review','fixing')
                               ORDER BY priority DESC,id LIMIT :l""", l=limit)
            done_ids = {x["id"] for x in db.rows(
                "SELECT id FROM ext_dev_jobs WHERE status='completed'"
            )}
            waiting=[]
            live_running = 0
            for r in rows:
                live = live_a2a.get(int(r["id"]))
                if live:
                    _sync_job_from_live_a2a(r, live)
                    if str(live.get("status") or "") == "running":
                        live_running += 1
                    waiting.append({
                        "dev_job_id": f"dev_{r['id']}",
                        "reason": "live_a2a_" + str(live.get("status") or "unknown"),
                    })
                    continue
                if int(r["id"]) in unscoped_ids:
                    _set(
                        r["id"], status="waiting", assigned_agent=None,
                        wait_reason="UNSCOPED_DEV_JOB: explicit scope/locks required before execution",
                    )
                    waiting.append({"dev_job_id":f"dev_{r['id']}","reason":"unscoped"})
                    continue
                deps = _j(r.get("depends_on_json"), []) or []
                unmet = [int(d) for d in deps if int(d) not in done_ids]
                if unmet:
                    _set(
                        r["id"], status="waiting", assigned_agent=None,
                        wait_reason="ждёт задачи: " + ", ".join(f"dev_{d}" for d in unmet[:5]),
                    )
                    waiting.append({"dev_job_id":f"dev_{r['id']}","reason":"dependencies"})
                    continue
                _set(r['id'], status='waiting', assigned_agent=None,
                     wait_reason=_aiprov.development_wait_reason())
                waiting.append({"dev_job_id":f"dev_{r['id']}","reason":"provider"})
            return {"assigned":[],"waiting":waiting,"capacity_per_agent":MAX_PARALLEL_PER_AGENT,
                    "capacity":{},"running_now":live_running,"under_pressure":False,
                    "provider_gate":"WAITING_FREE_CODING_PROVIDER",
                    "production_gate":production_gate,
                    "runtime_state_sync":runtime_state_sync,
                    "lock_health":{
                        "state":lock_health.get("state"),
                        "metadata_gaps":len(lock_health.get("metadata_gaps") or []),
                        "unscoped_jobs":len(lock_health.get("unscoped_jobs") or []),
                        "broken_dependencies":len(lock_health.get("broken_dependencies") or []),
                        "running_conflicts":len(lock_health.get("running_conflicts") or []),
                        "potential_overlap_pairs":int(lock_health.get("potential_overlap_pairs") or 0),
                        "self_healed":int(lock_health.get("self_healed") or 0),
                        "dependency_self_healed":int(lock_health.get("dependency_self_healed") or 0),
                    }}
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1764)

    terminal_business = business_goal_terminal_job_ids()
    rows = db.rows("""SELECT * FROM ext_dev_jobs
                      WHERE status IN ('queued','waiting','running','review','fixing')
                      ORDER BY priority DESC, id ASC LIMIT :l""", l=limit)
    # BUSINESS_GOAL_TERMINAL_SCHEDULER_FENCE_V1: a proved client result is not
    # runnable development work. Keep technical lifecycle truthful, but remove
    # these rows from admission/capacity until missing evidence is reconciled.
    rows = [r for r in rows if int(r['id']) not in terminal_business]

    # Старение: задача, провисевшая в очереди, поднимается сама, иначе поток
    # срочного навсегда затирает обычную работу.
    rows = sorted(rows, key=lambda r: (-sched.effective_priority(
        {"priority": r.get("priority"), "created_at": r.get("created_at")}), r["id"]))
    by_id = {r["id"]: r for r in rows}
    done_ids = {r["id"] for r in db.rows(
        "SELECT id FROM ext_dev_jobs WHERE status='completed'")}

    # Runtime capacity comes from the A2A order, not from ext_dev_jobs.status.
    # The two records are updated on different ticks; using only the job flag
    # undercounted real RUNNING work, admitted too many jobs and later rewrote
    # genuinely running/reviewing jobs back to WAITING.
    from . import a2a as _a2a

    held = set()
    load = {}
    running_jobs = []
    idle_running = []
    for r in rows:
        jid = int(r["id"])
        live = live_a2a.get(jid)
        if live:
            _sync_job_from_live_a2a(r, live)

        if live and str(live.get("status") or "") == _a2a.RUNNING:
            current_locks = {
                _norm_lock_path(x)
                for x in (_j(r.get("locks_json"), []) or [])
                if _norm_lock_path(x)
            }
            held |= current_locks
            running_jobs.append({
                "locks": sorted(current_locks),
                "current_phase": r.get("current_phase"),
                "priority": r.get("priority"),
            })
            owner = live.get("assigned_to") or r.get("assigned_agent")
            if owner:
                load[owner] = load.get(owner, 0) + 1
            continue

        if r["status"] == "running" and not live:
            idle_running.append(jid)

    for jid in idle_running:
        _set(jid, status="queued", assigned_agent=None,
             wait_reason="нет открытого наряда — ждёт, пока лид поставит следующий")

    # Ёмкость считается по фактическим CPU, RAM и load этой машины, а не по
    # выдуманному числу воркеров: production важнее любой задачи из очереди.
    client_queue_rows = db.rows("""SELECT id FROM ext_dev_jobs
                                      WHERE status IN ('queued','waiting')
                                        AND priority >= :p""", p=sched.CLIENT_FLOOR)
    client_queue = sum(1 for r in client_queue_rows if int(r['id']) not in terminal_business)
    cap_state = sched.new_state(running=running_jobs, client_queue=client_queue)

    # Зомби-задачи: числятся в работе, но исполнитель молчит сутки. Пока их
    # не вернуть в очередь, они держат ёмкость и блокировки, и новая работа
    # не стартует никогда.
    try:
        sched.heartbeat_sweep()
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1837)

    from . import adapters as _ad
    usable = {a["agent"] for a in _ad.all_adapters() if a["enabled"] and a["real_execution"]}
    usable |= set(agents.DAEMONS)
    reg = {a["name"]: a for a in agents.registry() if a["name"] in usable}
    assigned, waiting = [], []

    for r in rows:
        jid = int(r["id"])
        # A live A2A order already has an owner and lifecycle. Re-running
        # dependency/capacity/production gates here is a second scheduler and
        # was the source of dev-job/A2A status divergence.
        if live_a2a.get(jid):
            continue
        if int(jid) in unscoped_ids:
            _set(
                jid,
                status="waiting",
                assigned_agent=None,
                wait_reason="UNSCOPED_DEV_JOB: explicit scope/locks required before execution",
            )
            waiting.append((jid, "unscoped"))
            continue
        deps = _j(r.get("depends_on_json"), [])
        unmet = [d for d in deps if d not in done_ids]
        if unmet:
            _set(jid, status="waiting", wait_reason="ждёт задачи: " +
                 ", ".join(f"dev_{d}" for d in unmet[:5]))
            waiting.append((jid, "dependencies"))
            continue

        # PRODUCTION_CONVERGENCE_DRAIN_V1: when the currently served source
        # generation is healthy but not yet globally accepted, do not admit a
        # fresh source mutation that would reset convergence again. Read-only
        # work, QA/acceptance/deployment phases and explicit P0/client-floor
        # emergencies continue normally.
        scope_obj = _j(r.get("scope_json"), {}) or {}
        phase = str(r.get("current_phase") or "").upper()
        try:
            priority = int(r.get("priority") or 0)
        except Exception:
            priority = 0
        emergency = bool(_explicit_p0_job(r) or priority >= sched.CLIENT_FLOOR)
        explicit_read_only = isinstance(scope_obj, dict) and scope_obj.get("read_only") is True
        if (production_gate.get("block_new_writes")
                and phase in _PRODUCTION_MUTATING_PHASES
                and not explicit_read_only
                and not emergency):
            _set(
                jid,
                status="waiting",
                assigned_agent=None,
                wait_reason=(
                    "PRODUCTION_CONVERGENCE_DRAIN: текущая версия обслуживает клиентов, "
                    "но ещё проходит общую приёмку; новые изменения исходников продолжатся "
                    "автоматически после стабилизации"
                ),
            )
            waiting.append((jid, "production_convergence"))
            continue

        locks = {_norm_lock_path(x) for x in (_j(r.get("locks_json"), []) or []) if _norm_lock_path(x)}
        clash = _lock_conflicts(locks, held)
        if clash:
            _set(jid, status="waiting",
                 wait_reason="ресурс занят другой задачей: " + ", ".join(clash[:5]))
            waiting.append((jid, "locks"))
            continue

        job_view = {"locks": sorted(locks), "current_phase": r.get("current_phase"),
                    "priority": r.get("priority")}
        verdict = sched.admission(job_view, cap_state)
        if not verdict["admit"]:
            _set(jid, status="waiting", wait_reason=verdict["reason"])
            waiting.append((jid, "capacity"))
            continue

        kind = PHASE_KIND.get(r["current_phase"], "code")
        chosen, why = agents.pick(kind, "auto", require_alive=True)
        if chosen and load.get(chosen["name"], 0) >= MAX_PARALLEL_PER_AGENT:
            other, why2 = agents.pick(kind, "auto", exclude=[chosen["name"]],
                                      require_alive=True)
            if other and load.get(other["name"], 0) < MAX_PARALLEL_PER_AGENT:
                chosen, why = other, why2
            else:
                chosen = None
        if chosen and chosen["name"] not in usable:
            chosen = None
        if not chosen:
            _set(jid, status="waiting",
                 wait_reason=(why or "")[:290] if why and "нет живого" in (why or "")
                 else (f"нет свободного агента под фазу {r['current_phase']}: "
                       f"либо все заняты (лимит {MAX_PARALLEL_PER_AGENT} на агента), "
                       f"либо адаптер выключен"))
            waiting.append((jid, "agents"))
            continue

        _set(jid, status="running", assigned_agent=chosen["name"], wait_reason=None)
        event(jid, r["current_phase"], chosen["name"], "assigned",
              {"why": why, "phase": r["current_phase"]})
        held |= locks
        sched.take(cap_state, job_view, verdict["weight"])

        # client_queue is calculated once before this scheduling pass.
        # After a queued/waiting client is actually assigned, keep the
        # in-memory admission state in sync; otherwise later infra jobs
        # incorrectly reserve the last slot for a client that already started.
        if sched.is_client(job_view) and cap_state.get("client_queue", 0) > 0:
            cap_state["client_queue"] -= 1

        load[chosen["name"]] = load.get(chosen["name"], 0) + 1
        assigned.append((jid, chosen["name"]))

    return {"assigned": [{"dev_job_id": f"dev_{i}", "agent": a} for i, a in assigned],
            "waiting": [{"dev_job_id": f"dev_{i}", "reason": w} for i, w in waiting],
            "capacity_per_agent": MAX_PARALLEL_PER_AGENT,
            "capacity": {k: v for k, v in cap_state["caps"].items()
                         if k.startswith("MAX")},
            "running_now": cap_state["total"],
            "under_pressure": cap_state["pressure"]["under_pressure"],
            "production_gate": production_gate,
            "runtime_state_sync": runtime_state_sync,
            "lock_health":{
                "state":lock_health.get("state"),
                "metadata_gaps":len(lock_health.get("metadata_gaps") or []),
                "unscoped_jobs":len(lock_health.get("unscoped_jobs") or []),
                "broken_dependencies":len(lock_health.get("broken_dependencies") or []),
                "running_conflicts":len(lock_health.get("running_conflicts") or []),
                "potential_overlap_pairs":int(lock_health.get("potential_overlap_pairs") or 0),
                "self_healed":int(lock_health.get("self_healed") or 0),
                "dependency_self_healed":int(lock_health.get("dependency_self_healed") or 0),
            }}


def _owner_queue_partition(rows, terminal_business):
    """Separate client/business DONE from actionable technical work.

    A dev row may intentionally stay technically non-terminal because old QA or
    acceptance evidence is incomplete, while fresh production truth already
    proves the client got the contracted result.  Such a row must remain
    auditable, but it must not look like an owner action item.
    """
    decorated, actionable, business_done = [], [], []
    terminal_business = {int(x) for x in (terminal_business or set())}
    for raw in rows:
        item = dict(raw)
        job_id = int(item["id"])
        technical_status = str(item.get("status") or "")
        is_business_done = job_id in terminal_business
        is_technical_terminal = technical_status in {
            "completed", "cancelled", "failed"
        }
        item["dev_job_id"] = f"dev_{job_id}"
        item["technical_status"] = technical_status
        item["business_result_confirmed"] = bool(is_business_done)
        if is_technical_terminal:
            item["owner_state"] = "technical_terminal"
            decorated.append(item)
            continue
        item["owner_state"] = (
            "business_result_confirmed" if is_business_done else "actionable"
        )
        decorated.append(item)
        (business_done if is_business_done else actionable).append(item)
    return decorated, actionable, business_done


def queue_view(limit=100):
    rows = db.rows("""SELECT id, title, status, priority, current_phase, assigned_agent,
                             wait_reason, batch_id, blocked_reason
                      FROM ext_dev_jobs ORDER BY
                        CASE status WHEN 'running' THEN 0 WHEN 'waiting' THEN 1
                                    WHEN 'queued' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END,
                        priority DESC, id LIMIT :l""", l=limit)
    counts = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) AS n FROM ext_dev_jobs GROUP BY status")}
    terminal_business = business_goal_terminal_job_ids()
    visible, actionable, business_done = _owner_queue_partition(
        rows, terminal_business
    )
    active_status_rows = db.rows(
        """SELECT id,status FROM ext_dev_jobs
           WHERE status NOT IN ('completed','cancelled','failed')"""
    )
    actionable_counts = {}
    for row in active_status_rows:
        if int(row["id"]) in terminal_business:
            continue
        status = row["status"]
        actionable_counts[status] = actionable_counts.get(status, 0) + 1
    per_agent = {r["assigned_agent"]: r["n"] for r in db.rows(
        """SELECT assigned_agent, COUNT(*) AS n FROM ext_dev_jobs
           WHERE status='running' AND assigned_agent IS NOT NULL GROUP BY assigned_agent""")}
    from . import sched
    return {
        "by_status": counts,
        "by_status_actionable": actionable_counts,
        "business_done_count": len(terminal_business),
        "running_per_agent": per_agent,
        "capacity_per_agent": MAX_PARALLEL_PER_AGENT,
        "scheduler": sched.report(),
        "jobs": visible,
        "actionable_jobs": actionable,
        "business_done_jobs": business_done,
        "owner_queue_rule": (
            "business_result_confirmed is shown separately from technical status; "
            "only actionable_jobs require continued work"
        ),
    }


def release(dev_id):
    """Освобождает агента и блокировки — вызывается при completed/blocked/cancelled."""
    _set(int(str(dev_id).replace("dev_", "")), assigned_agent=None)


def create_batch(key, request_id, items, batch_id=None):
    """«Вот 10 задач — делайте». Зависимости внутри пачки задаются индексами."""
    import uuid as _uuid
    if not isinstance(items, list) or not items:
        raise ApiError("MISSING_INPUT", "нужен непустой список задач")
    if len(items) > 50:
        raise ApiError("TOO_MANY_ITEMS", "за раз не больше 50 задач")
    bid = batch_id or ("batch_" + _uuid.uuid4().hex[:10])
    created, index_map = [], {}
    for i, it in enumerate(items):
        deps = []
        for d in (it.get("depends_on") or []):
            if isinstance(d, int) and d in index_map:
                deps.append(index_map[d])
            elif isinstance(d, str) and d.startswith("dev_"):
                deps.append(int(d[4:]))
        job = create(key, request_id, it.get("title"), it.get("goal"),
                     it.get("acceptance_criteria"), it.get("scope"),
                     priority=int(it.get("priority", 50)), depends_on=deps,
                     locks=it.get("locks"), batch_id=bid)
        index_map[i] = job["id"]
        created.append(job)
    return {"batch_id": bid, "created": len(created),
            "jobs": [{"dev_job_id": j["dev_job_id"], "title": j["title"],
                      "status": j["status"], "priority": j["priority"],
                      "depends_on": j["depends_on"], "locks": j["locks"]} for j in created],
            "schedule": schedule()}
