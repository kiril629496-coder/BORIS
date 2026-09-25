# -*- coding: utf-8 -*-
"""Контур Claude Lead ⇄ Orchestrator ⇄ GPT Executor.

Один источник истины на задачу — наряд (order). Лид кладёт EXECUTOR_ORDER,
исполнитель забирает его сам, работает и возвращает REVIEW_REQUEST. Лид
отвечает ACCEPT или RETURN_WITH_FIXES, и во втором случае наряд сам
возвращается исполнителю со следующей итерацией. Человека в этой петле нет.

Владельцу уходит ровно два исхода: DONE или настоящий BLOCKED_HUMAN.
Нехватка инструмента — это BLOCKED_INFRA, отдельный статус, и он остаётся
внутри системы.
"""
import hashlib, json, re, time, unicodedata
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from . import db, auth, dev
from .errors import ApiError

# Статусы наряда. Их немного намеренно: каждый означает, чей сейчас ход.
QUEUED = "queued"                 # лид положил, исполнитель ещё не забрал
RUNNING = "running"               # исполнитель работает
REVIEW = "review_requested"       # ход лида
RETURNED = "returned"             # лид вернул с замечаниями, ход исполнителя
ACCEPTED = "accepted"             # лид принял
FAILED = "failed"
BLOCKED_HUMAN = "blocked_human"
BLOCKED_INFRA = "blocked_infra"
CANCELLED = "cancelled"

OPEN_STATES = (QUEUED, RUNNING, REVIEW, RETURNED)
CLAIMABLE = (QUEUED, RETURNED)

DEFAULT_MAX_ITERATIONS = 6

# BLOCKED_HUMAN is reserved for actions that technically require an external
# owner/admin decision. Code/test/provider/deploy/rollback/worker failures are
# internal recovery states and must never be surfaced as human blockers.
OWNER_ONLY_BLOCKER_MARKERS = (
    "owner approval", "owner-only", "admin approval", "admin permission",
    "credential required", "credentials required", "external credential",
    "secret required", "secrets required", "legal decision", "financial decision",
    "paid action approval", "destructive db", "destructive data",
    "auth permission", "security permission", "disable safety",
    "systemd configuration", "nginx configuration", "firewall", "ssh security",
    "требуется решение владельца", "нужен владелец", "нужен admin",
    "нужен credential", "нужны credentials", "нужен секрет", "нужны секреты",
    "юридическое решение", "финансовое решение", "платное действие",
)


def is_owner_only_blocker(reason):
    low = str(reason or "").strip().lower()
    return bool(low) and any(marker in low for marker in OWNER_ONLY_BLOCKER_MARKERS)


_WORKSPACE_ACCESS_BLOCKER_MARKERS = (
    "доступ к кодовой базе",
    "доступа к кодовой базе",
    "доступ к исходному коду",
    "доступ к файловой системе проекта",
    "доступ к файлам проекта",
    "доступ к файлам системы boris",
    "отсутствуют файлы проекта",
    "рабочая директория",
    "рабочей директории",
    "проектная директория",
)


def _workspace_access_blocker_healed(order_id, reason):
    """Narrow self-heal for old read-only PLANNING workspace-access blocks."""
    low = str(reason or "").strip().lower()
    if not low or not any(marker in low for marker in _WORKSPACE_ACCESS_BLOCKER_MARKERS):
        return False
    if "missing from the environment" in low or "не существует" in low:
        return False
    try:
        order = get(int(order_id), with_messages=False)
        scope = order.get("scope") or {}
        if (
            str(order.get("phase") or "").upper() != "PLANNING"
            or scope.get("read_only") is not True
            or not any(scope.get(k) for k in ("inspection_paths", "read_only_paths", "paths"))
        ):
            return False
        from . import executor as _executor
        return bool(_executor._gemini_context_candidates(order))
    except Exception:
        return False


_FILESYSTEM_SCOPE_BLOCKER_MARKERS = (
    "доступ к репозиторию",
    "доступа к репозиторию",
    "доступ к исходному коду",
    "доступа к исходному коду",
    "рабочая область не содержит файлов проекта",
    "рабочей области не содержит файлов проекта",
    "repository source code",
    "missing from the environment",
)


def _filesystem_scope_blocker_healed(order_id, reason):
    """Recover old false "repo/workspace missing" blocks from current facts.

    This is deliberately fail-closed. Every concrete path declared by the
    blocked order must exist inside /root/BORIS (or /root/BORIS/backend for
    backend-relative paths), and the current bounded workspace materializer
    must be able to snapshot source files. A genuinely missing file therefore
    remains blocked; an old sandbox/access error heals without owner action.
    """
    low = str(reason or "").strip().lower()
    if not low or not any(x in low for x in _FILESYSTEM_SCOPE_BLOCKER_MARKERS):
        return False
    try:
        order = get(int(order_id), with_messages=False)
        scope = order.get("scope") or {}
        raw_paths = []
        for key in (
            "inspection_paths", "read_only_paths", "paths", "files",
            "allowed_paths",
        ):
            value = scope.get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                raw_paths.extend(value)
        if not raw_paths:
            return False

        root = Path("/root/BORIS").resolve()
        backend = (root / "backend").resolve()
        proven = []
        for raw in raw_paths:
            value = str(raw or "").strip().replace("\\", "/")
            if not value or any(ch in value for ch in "*?[]"):
                return False
            p = Path(value)
            candidates = []
            if p.is_absolute():
                try:
                    resolved = p.resolve()
                except Exception:
                    return False
                if resolved != root and root not in resolved.parents:
                    return False
                candidates.append(resolved)
            else:
                rel = value.lstrip("/")
                candidates.append((root / rel).resolve())
                if not rel.startswith("backend/"):
                    candidates.append((backend / rel).resolve())
            existing = [
                c for c in candidates
                if (c == root or root in c.parents) and c.exists()
            ]
            if not existing:
                return False
            proven.append(str(existing[0]))

        if not proven:
            return False
        from . import executor as _executor
        _work, ctx = _executor._prepare_gemini_workspace(order)
        return bool((ctx or {}).get("source_snapshot_files"))
    except Exception:
        return False


def _context_truncation_blocker_healed(order_id, reason):
    """Self-heal old bounded-context truncation only after materialization works."""
    low = str(reason or "").strip().lower()
    if not low.startswith("autonomous_context_truncated:"):
        return False
    try:
        order = get(int(order_id), with_messages=False)
        from . import executor as _executor
        _work, ctx = _executor._prepare_gemini_workspace(order)
        return bool((ctx or {}).get("source_snapshot_files"))
    except Exception:
        return False


def _loop_guard_root_cause_recovery(order_id, reason):
    """Prepare one bounded root-cause retry after the normal iteration guard.

    The iteration guard is valuable: repeating the same failed review loop is
    wasteful. But leaving the order BLOCKED_INFRA forever turns the owner into
    the recovery operator. We therefore allow exactly one automatic recovery
    mode on the same durable order. It resets only the iteration counter and
    changes the goal so the next executor must diagnose the first broken fact
    before editing anything. A second exhausted root-cause cycle stays blocked
    and is not auto-replayed indefinitely.
    """
    low = str(reason or "").strip().lower()
    if not low.startswith("исчерпан лимит итераций"):
        return None
    try:
        order = get(int(order_id), with_messages=False)
        scope = dict(order.get("scope") or {})
        attempts = int(scope.get("_loop_guard_root_cause_recoveries") or 0)
        if attempts >= 1:
            return None
        scope["_loop_guard_root_cause_recoveries"] = attempts + 1
        scope["_loop_guard_recovery_mode"] = "root_cause_once"
        previous_goal = str(order.get("goal") or "").strip()
        last_review = order.get("last_review") or {}
        fixes = last_review.get("fixes") or []
        recovery_goal = (
            "АВТОВОССТАНОВЛЕНИЕ ПОСЛЕ ЗАЦИКЛИВАНИЯ. "
            "Не повторяй предыдущую попытку. Сначала найди первопричину: "
            "какой первый проверяемый факт или критерий остаётся неверным, "
            "почему прошлые исправления не меняли этот факт, и какой минимальный "
            "фикс устраняет именно причину. Затем внеси этот фикс и заново докажи "
            "результат тестом/runtime/evidence. Если причина внешняя и действительно "
            "неустранима без владельца — зафиксируй точный внешний blocker. "
            f"Исходная цель: {previous_goal[:5000]}"
        )
        if fixes:
            recovery_goal += " Последние замечания review: " + str(fixes)[:1800]
        return {
            "scope_json": _dump(scope),
            "goal": recovery_goal[:8000],
            "iteration": 0,
            "blocked_reason": None,
            "claimed_by": None,
            "claimed_at": None,
        }
    except Exception:
        return None


def _read_only_production_conflict_healed(order_id, reason):
    """Recover a stale read-only PLANNING conflict without owner clicks.

    A read-only planning order owns no write locks, so an old production-writer
    conflict must not remain a permanent manual blocker. If the parent has
    explicit dependencies, however, we wait until all of them are completed so
    planning cannot race ahead of the canonical workstream it depends on.
    """
    if not str(reason or "").strip().upper().startswith("PRODUCTION_CONFLICT:"):
        return False
    try:
        order = get(int(order_id), with_messages=False)
        scope = order.get("scope") or {}
        if not (
            str(order.get("phase") or "").upper() == "PLANNING"
            and scope.get("read_only") is True
            and str(scope.get("scope_mode") or "") == "planning_read_only"
        ):
            return False
        job = dev.get(order.get("dev_job_id"))
        parent_scope = job.get("scope") or {}
        if parent_scope.get("read_only") is not True or list(job.get("locks") or []):
            return False
        dependencies = list(job.get("depends_on") or [])
        for dep in dependencies:
            if str(dev.get(dep).get("status") or "") != "completed":
                return False
        return True
    except Exception:
        return False


def recover_internal_blockers(limit=100, actor="dispatcher"):
    """Recover stale/misclassified blockers without owner intervention.

    Historical non-owner BLOCKED_HUMAN rows are requeued. Provider-unavailable
    BLOCKED_INFRA rows are retryable only after provider recovery. A normal
    iteration-loop guard gets one bounded root-cause recovery pass instead of
    requiring the owner to press a reset button. True owner-only blockers are
    untouched.
    """
    rows = db.rows("""SELECT id, status, blocked_reason,
                            last_review_json, last_evidence_json
                       FROM ext_a2a_orders
                      WHERE status = ANY(:st) ORDER BY id LIMIT :l""",
                  st=[BLOCKED_HUMAN, BLOCKED_INFRA], l=int(limit))
    recovered = []
    for row in rows:
        reason = str(row.get("blocked_reason") or "")
        human_misclassified = (row["status"] == BLOCKED_HUMAN and
                               not is_owner_only_blocker(reason))
        # Provider failures are retryable only when at least one free
        # development provider is currently usable. Otherwise requeueing on
        # every dispatcher tick creates an endless queued->blocked loop and
        # burns provider calls without making progress.
        provider_retry = False
        low_reason = reason.lower()
        provider_block = (
            low_reason.startswith("provider_unavailable:")
            or low_reason.startswith("all free development providers unavailable")
        )
        if row["status"] == BLOCKED_INFRA and provider_block:
            try:
                from . import aiprov as _aiprov
                provider_retry = bool(_aiprov.development_candidates())
            except Exception:
                provider_retry = False
        last_review = _j(row.get("last_review_json"), {}) or {}
        last_evidence = _j(row.get("last_evidence_json"), {}) or {}
        review_provider_retry = bool(
            provider_retry
            and low_reason.startswith("provider_unavailable:")
            and str(last_review.get("verdict") or "").upper() == "BLOCKED_INFRA"
            and isinstance(last_evidence, dict)
            and bool(last_evidence.get("evidence") or last_evidence.get("summary"))
        )
        # Historical August blockers claimed that proposePatch did not exist.
        # It exists in the current gateway; keeping those rows blocked forever
        # would make the owner manually revive work after infrastructure healed.
        tool_healed = False
        if row["status"] == BLOCKED_INFRA and "proposepatch" in reason.lower():
            try:
                from . import repo as _repo
                tool_healed = callable(getattr(_repo, "propose", None))
            except Exception:
                tool_healed = False
        # Old FORBIDDEN_SCOPE/worktree blockers predate the current bounded
        # server-owned workspace + repo.propose path. Only revive them when
        # that safe patch capability exists; this avoids permanent manual
        # resurrection of infrastructure problems that BORIS can now handle.
        scope_healed = False
        low_reason = reason.lower()
        if row["status"] == BLOCKED_INFRA and (
                "forbidden_scope" in low_reason or "worktree" in low_reason):
            try:
                from . import repo as _repo
                scope_healed = callable(getattr(_repo, "propose", None))
            except Exception:
                scope_healed = False
        workspace_healed = (
            row["status"] == BLOCKED_INFRA
            and _workspace_access_blocker_healed(row["id"], reason)
        )
        filesystem_healed = (
            row["status"] == BLOCKED_INFRA
            and _filesystem_scope_blocker_healed(row["id"], reason)
        )
        context_healed = (
            row["status"] == BLOCKED_INFRA
            and _context_truncation_blocker_healed(row["id"], reason)
        )
        read_only_conflict_healed = (
            row["status"] == BLOCKED_INFRA
            and _read_only_production_conflict_healed(row["id"], reason)
        )
        loop_recovery = (
            _loop_guard_root_cause_recovery(row["id"], reason)
            if row["status"] == BLOCKED_INFRA else None
        )
        if loop_recovery:
            _set(row["id"], status=QUEUED, **loop_recovery)
            message(row["id"], "AUTO_ROOT_CAUSE_RECOVERY", actor, {
                "from": row["status"],
                "why": reason[:500],
                "mode": "root_cause_once",
                "owner_action_required": False,
            })
            recovered.append(int(row["id"]))
            continue
        if not (human_misclassified or provider_retry or tool_healed
                or scope_healed or workspace_healed or filesystem_healed
                or context_healed or read_only_conflict_healed):
            continue
        recovered_status = REVIEW if review_provider_retry else QUEUED
        _set(row["id"], status=recovered_status, claimed_by=None, claimed_at=None,
             blocked_reason=None)
        message(
            row["id"],
            "AUTO_RECOVERY_TO_REVIEW" if review_provider_retry else "AUTO_RECOVERY",
            actor,
            {
                "from": row["status"],
                "to": recovered_status,
                "why": reason[:500],
                "evidence_preserved": bool(review_provider_retry),
            },
        )
        recovered.append(int(row["id"]))
    return recovered

# Имена исполнителей приёмочных прогонов. Боевые службы их наряды не трогают:
# иначе демон принимает работу теста раньше самого теста.
TEST_AGENTS = ("e2e-executor", "selftest-executor")


def _j(x, default=None):
    if x in (None, ""):
        return default
    if isinstance(x, (dict, list)):
        return x
    try:
        return json.loads(x)
    except Exception:
        return default


def _dump(x):
    return json.dumps(x if x is not None else None, ensure_ascii=False)[:200000]


def _criterion_text(value):
    """Exact deterministic criterion text normalization, never fuzzy matching."""
    if isinstance(value, dict):
        value = value.get("text") or value.get("criterion")
    raw = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(raw.split()).strip()


def _criterion_id(dev_job_id, text):
    """Stable identity derived only from parent job + exact normalized text."""
    norm = _criterion_text(text)
    if not norm:
        return None
    raw_id = str(dev_job_id or "")
    if raw_id.startswith("dev_"):
        raw_id = raw_id[4:]
    digest = hashlib.sha256((raw_id + "\0" + norm).encode("utf-8")).hexdigest()
    return "ac_" + digest[:24]


def _parent_acceptance_links(job, acceptance):
    """Map order criteria to parent criteria only when the exact text is unique.

    Duplicate parent/order texts are intentionally excluded: ambiguity fails closed.
    """
    parent = job.get("acceptance_criteria") or []
    order_items = list(acceptance or [])
    parent_texts = [_criterion_text(c) for c in parent]
    order_texts = [_criterion_text(c) for c in order_items]
    parent_counts = {t: parent_texts.count(t) for t in set(parent_texts) if t}
    order_counts = {t: order_texts.count(t) for t in set(order_texts) if t}
    links = []
    for text in order_texts:
        if not text:
            continue
        if parent_counts.get(text) != 1 or order_counts.get(text) != 1:
            continue
        links.append({
            "criterion_id": _criterion_id(job.get("id") or job.get("dev_job_id"), text),
            "text": text,
        })
    return links


def _validated_parent_links(order, job):
    """Validate persisted links against current order+parent; legacy falls back exact-only."""
    parent = job.get("acceptance_criteria") or []
    order_items = order.get("acceptance_criteria") or []
    parent_texts = [_criterion_text(c) for c in parent]
    order_texts = [_criterion_text(c) for c in order_items]
    parent_counts = {t: parent_texts.count(t) for t in set(parent_texts) if t}
    order_counts = {t: order_texts.count(t) for t in set(order_texts) if t}

    persisted = ((order.get("scope") or {}).get("_parent_acceptance_links") or [])
    persisted_by_text = {}
    for item in persisted:
        if not isinstance(item, dict):
            continue
        text = _criterion_text(item.get("text"))
        cid = str(item.get("criterion_id") or "")
        expected = _criterion_id(job.get("id") or job.get("dev_job_id"), text)
        if (text and cid and cid == expected and
                parent_counts.get(text) == 1 and order_counts.get(text) == 1):
            persisted_by_text[text] = cid

    out = {}
    for idx, criterion in enumerate(parent):
        text = _criterion_text(criterion)
        if not text or parent_counts.get(text) != 1 or order_counts.get(text) != 1:
            continue
        cid = persisted_by_text.get(text)
        if not cid:
            # Legacy order: exact unique text is the only safe fallback.
            cid = _criterion_id(job.get("id") or job.get("dev_job_id"), text)
        out[text] = {"criterion_id": cid, "index": idx, "text": text}
    return out


def reconcile_parent_acceptance(key, order_id, emit_message=True):
    """Copy accepted PASS evidence to the exact parent criterion, fail closed.

    No fuzzy text, no positional/index guessing, no overall evidence fallback.
    A criterion closes only with its own non-empty evidence after ACCEPT.
    """
    order = get(order_id, with_messages=False)
    result = {
        "order_id": int(order_id),
        "status": order.get("status"),
        "closed_indices": [],
        "matched_indices": [],
        "criterion_ids": [],
        "skipped": [],
    }
    if order.get("status") != ACCEPTED:
        result["skipped"].append("order_not_accepted")
        return result

    job = dev.get(order["dev_job_id"])
    links = _validated_parent_links(order, job)
    ev = dict(order.get("last_evidence") or {})
    criteria = list(ev.get("acceptance_criteria") or [])
    enriched = []
    changed_evidence = False

    for raw in criteria:
        c = dict(raw) if isinstance(raw, dict) else {}
        text = _criterion_text(c.get("criterion"))
        status = str(c.get("status") or "").upper()
        evidence = str(c.get("evidence") or "").strip()
        link = links.get(text)

        if status != "PASS":
            result["skipped"].append({"criterion": text, "reason": "not_pass"})
            enriched.append(c)
            continue
        if not evidence:
            result["skipped"].append({"criterion": text, "reason": "missing_evidence"})
            enriched.append(c)
            continue
        if not link:
            result["skipped"].append({"criterion": text, "reason": "no_exact_unique_parent_link"})
            enriched.append(c)
            continue

        supplied_id = str(c.get("criterion_id") or "")
        if supplied_id and supplied_id != link["criterion_id"]:
            result["skipped"].append({"criterion": text, "reason": "criterion_id_mismatch"})
            enriched.append(c)
            continue

        if c.get("criterion_id") != link["criterion_id"]:
            c["criterion_id"] = link["criterion_id"]
            changed_evidence = True
        enriched.append(c)
        result["matched_indices"].append(link["index"])
        result["criterion_ids"].append(link["criterion_id"])

        current = (dev.get(order["dev_job_id"]).get("acceptance_criteria") or [])[link["index"]]
        if isinstance(current, dict) and current.get("done"):
            continue
        dev.mark_criteria(key, order["dev_job_id"], link["index"], True, evidence)
        result["closed_indices"].append(link["index"])

    if changed_evidence:
        ev["acceptance_criteria"] = enriched
        _set(order_id, last_evidence_json=_dump(ev))

    if emit_message and result["closed_indices"]:
        message(order_id, "PARENT_ACCEPTANCE_RECONCILED",
                auth.actor_of(key)["name"], {
                    "closed_indices": result["closed_indices"],
                    "criterion_ids": result["criterion_ids"],
                    "mapping": "exact_unique_text_hash_v1",
                })
    return result


# ------------------------------------------------------------------ наряды

# Агенты, которых невозможно разбудить программно: Custom GPT в ChatGPT и
# чат Claude. Наряд, отданный такому, тихо висит сутками и держит ёмкость —
# именно это однажды и остановило всю работу на ночь.
NON_AUTONOMOUS = ("chatgpt", "claude", "chat-gpt", "claude-chat")


def assert_autonomous(agent):
    name = (agent or "").strip().lower()
    if name in NON_AUTONOMOUS:
        raise ApiError("VALIDATION_ERROR",
                       "агенту «%s» нельзя поручить автономный наряд: его нельзя "
                       "разбудить без человека. Исполнители автономного контура — "
                       "серверные службы (gpt-executor, claude-lead)" % agent)
    return name


def create_order(key, dev_job_id, phase, goal, scope=None, acceptance=None,
                 forbidden=None, required_qa=None, required_evidence=None,
                 assigned_to="gpt-executor", max_iterations=None, priority=50):
    """EXECUTOR_ORDER. Кладёт лид; доставка — забота оркестратора, не человека."""
    assert_autonomous(assigned_to)
    job = dev.get(dev_job_id)
    # BUSINESS_GOAL_TERMINAL_CREATE_FENCE_V1: exactly-once belongs at the
    # shared order creation boundary, not only in individual producers. Once a
    # durable business_goal_already_confirmed receipt exists, recovery/lead/
    # client bootstrap must not resurrect executor work for that dev job.
    # If fresh business truth later regresses, clients.ensure_business_orders()
    # is the single owner allowed to make an explicit recovery decision.
    # BUSINESS_GOAL_TERMINAL_CREATE_FENCE_V2_FAIL_CLOSED: this is a safety
    # boundary, not a diagnostic convenience.  Swallowing errors here allowed
    # a stale producer to recreate paid/free development work after the client
    # result was already durably proved.  Use the durable receipt directly so
    # the fence is independent of helper/runtime generation and fail closed if
    # that receipt exists.  A later business regression is reopened only by
    # clients.ensure_business_orders(), which owns fresh business truth.
    terminal = db.one("""SELECT id FROM ext_a2a_orders
                          WHERE dev_job_id=:d
                            AND blocked_reason='business_goal_already_confirmed'
                          ORDER BY id DESC LIMIT 1""", d=job["id"])
    if terminal:
        return get(terminal["id"])
    # DEV_JOB_TERMINAL_CREATE_FENCE_V1: cancelled/failed/completed jobs must
    # never be resurrected by lead/recovery/bootstrap. If the job already has
    # history, return its latest order idempotently; otherwise fail closed.
    if job.get("status") in ("cancelled", "failed", "completed"):
        previous = db.one("""SELECT id,status FROM ext_a2a_orders
                             WHERE dev_job_id=:d ORDER BY id DESC LIMIT 1""",
                          d=job["id"])
        if previous:
            if previous.get("status") in OPEN_STATES:
                _set(previous["id"], status=CANCELLED, claimed_by=None, claimed_at=None,
                     blocked_reason="terminal_dev_job_%s" % job.get("status"))
                message(previous["id"], "RESET", "scheduler", {
                    "why": "terminal dev_job cannot own an open A2A order",
                    "dev_job_status": job.get("status"),
                })
            return get(previous["id"])
        raise ApiError(
            "VALIDATION_ERROR",
            "нельзя создавать новый A2A order для terminal dev_job: %s" % job.get("status"),
        )
    if not goal or not str(goal).strip():
        raise ApiError("MISSING_INPUT", "у наряда должна быть цель")
    if not acceptance:
        raise ApiError("MISSING_INPUT",
                       "наряд без критериев приёмки принять нельзя — по чему тогда review")
    # One live order per development job. Multiple autonomous producers may
    # converge on the same requirement (MASTER coverage, repair, recovery).
    # Reuse the existing live order instead of creating a duplicate that the
    # watchdog has to repair later.
    live = db.one("""SELECT id FROM ext_a2a_orders
                     WHERE dev_job_id=:d
                       AND status IN ('queued','running','review_requested','returned',
                                      'blocked_human','blocked_infra')
                     ORDER BY id DESC LIMIT 1""", d=job["id"])
    if live:
        return get(live["id"])
    scope_payload = dict(scope or {}) if isinstance(scope, dict) else {}
    parent_links = _parent_acceptance_links(job, list(acceptance))
    if parent_links:
        scope_payload["_parent_acceptance_links"] = parent_links
    try:
        row = db.one("""INSERT INTO ext_a2a_orders
                          (dev_job_id, phase, goal, scope_json, acceptance_json, forbidden_json,
                           required_qa_json, required_evidence_json, created_by, assigned_to,
                           status, iteration, max_iterations, priority)
                        VALUES (:d,:p,:g,:sc,:ac,:fb,:qa,:ev,:cb,:at,:st,0,:mx,:pr)
                        RETURNING id""",
                     d=job["id"], p=(phase or job["current_phase"]), g=str(goal)[:8000],
                     sc=_dump(scope_payload), ac=_dump(list(acceptance)),
                     fb=_dump(list(forbidden or [])), qa=_dump(list(required_qa or [])),
                     ev=_dump(list(required_evidence or [])),
                     cb=auth.actor_of(key)["name"], at=assigned_to, st=QUEUED,
                     mx=int(max_iterations or DEFAULT_MAX_ITERATIONS), pr=int(priority))
    except IntegrityError:
        # A concurrent autonomous producer may win between the preflight SELECT
        # and INSERT. The DB partial unique index is the final exactly-once fence.
        live = db.one("""SELECT id FROM ext_a2a_orders
                         WHERE dev_job_id=:d
                           AND status IN ('queued','running','review_requested','returned',
                                          'blocked_human','blocked_infra')
                         ORDER BY id DESC LIMIT 1""", d=job["id"])
        if not live:
            raise
        return get(live["id"])
    oid = row["id"]
    message(oid, "EXECUTOR_ORDER", auth.actor_of(key)["name"],
            {"goal": goal, "phase": phase or job["current_phase"],
             "acceptance_criteria": list(acceptance),
             "scope": scope_payload, "forbidden_changes": list(forbidden or []),
             "required_qa": list(required_qa or []),
             "required_evidence": list(required_evidence or [])})
    dev.event(job["id"], phase or job["current_phase"], auth.actor_of(key)["name"],
              "order_created", {"order_id": oid, "assigned_to": assigned_to})
    return get(oid)


def message(order_id, kind, author, body):
    db.q("""INSERT INTO ext_a2a_messages (order_id, kind, author, body_json)
            VALUES (:o,:k,:a,:b)""",
         o=int(order_id), k=kind, a=author, b=_dump(body))


def reconcile_terminal_dev_orders(limit=500):
    """Cancel open A2A orders whose development job is already terminal.

    This is the recovery-side complement to create_order's terminal fence: a
    stale producer may have created an order before the job was cancelled.
    Cleanup is autonomous and does not resurrect work or require the owner.
    """
    terminal_child_states = list(OPEN_STATES) + [BLOCKED_HUMAN, BLOCKED_INFRA]
    rows = db.rows("""SELECT o.id AS order_id,o.status AS order_status,
                             j.id AS dev_job_id,j.status AS job_status
                      FROM ext_a2a_orders o
                      JOIN ext_dev_jobs j ON j.id=o.dev_job_id
                      WHERE j.status IN ('cancelled','failed','completed')
                        AND o.status = ANY(:st)
                      ORDER BY o.id LIMIT :l""",
                   st=terminal_child_states, l=int(limit))
    healed=[]
    for row in rows:
        _set(row["order_id"], status=CANCELLED, claimed_by=None, claimed_at=None,
             blocked_reason="terminal_dev_job_%s" % row["job_status"])
        message(row["order_id"], "RESET", "scheduler", {
            "why": "terminal dev_job cannot own an open A2A order",
            "dev_job_id": "dev_%s" % row["dev_job_id"],
            "dev_job_status": row["job_status"],
        })
        healed.append({
            "order_id": row["order_id"],
            "dev_job_id": "dev_%s" % row["dev_job_id"],
            "from": row["order_status"],
            "to": CANCELLED,
            "dev_job_status": row["job_status"],
        })
    return {"count": len(healed), "healed": healed}


def get(order_id, with_messages=True):
    r = db.one("SELECT * FROM ext_a2a_orders WHERE id=:i", i=int(order_id))
    if not r:
        raise ApiError("JOB_NOT_FOUND", "наряд не найден")
    out = {
        "order_id": r["id"], "dev_job_id": f"dev_{r['dev_job_id']}",
        "phase": r["phase"], "goal": r["goal"],
        "scope": _j(r["scope_json"], {}),
        "acceptance_criteria": _j(r["acceptance_json"], []),
        "forbidden_changes": _j(r["forbidden_json"], []),
        "required_qa": _j(r["required_qa_json"], []),
        "required_evidence": _j(r["required_evidence_json"], []),
        "created_by": r["created_by"], "assigned_to": r["assigned_to"],
        "status": r["status"], "iteration": r["iteration"],
        "max_iterations": r["max_iterations"],
        "last_review": _j(r.get("last_review_json")),
        "last_evidence": _j(r.get("last_evidence_json")),
        "blocked_reason": r.get("blocked_reason"),
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
    }
    if with_messages:
        out["messages"] = [
            {"id": m["id"], "kind": m["kind"], "author": m["author"],
             "body": _j(m["body_json"], {}), "at": m["created_at"]}
            for m in db.rows("SELECT * FROM ext_a2a_messages WHERE order_id=:o ORDER BY id",
                             o=int(order_id))]
    return out


def _set(order_id, **f):
    sets = ", ".join(f"{k}=:{k}" for k in f)
    db.q(f"UPDATE ext_a2a_orders SET {sets}, updated_at=NOW() WHERE id=:__i",
         __i=int(order_id), **f)


def listing(status=None, dev_job_id=None, assigned_to=None, limit=50,
            exclude_assigned=None):
    sql, prm = "SELECT id FROM ext_a2a_orders WHERE 1=1", {}
    if status:
        sql += " AND status=:s"; prm["s"] = status
    if assigned_to:
        sql += " AND assigned_to=:a"; prm["a"] = assigned_to
    elif exclude_assigned:
        sql += " AND assigned_to <> ALL(:ex)"; prm["ex"] = list(exclude_assigned)
    if dev_job_id:
        sql += " AND dev_job_id=:d"; prm["d"] = dev.get(dev_job_id)["id"]
    sql += " ORDER BY priority DESC, id LIMIT :l"; prm["l"] = int(limit)
    return [get(r["id"], with_messages=False) for r in db.rows(sql, **prm)]


# --------------------------------------------------------------- исполнитель

# Имя пула, на который выписываются наряды. Воркеров в пуле может быть
# восемь, но наряд адресован пулу — иначе пришлось бы гадать, какой из
# процессов сейчас свободен.
POOL = "gpt-executor"

# Historical/public name of the systemd executor service. The daemon process
# identifies its A2A pool as gpt-executor, while callers may address the same
# pool by the service name boris-executor. Keep the stored assigned_to value
# intact, but treat these two names as one autonomous executor pool when
# claiming work. No other aliases are accepted.
EXECUTOR_POOL_ALIASES = {
    "gpt-executor": ("gpt-executor", "boris-executor"),
    "boris-executor": ("boris-executor", "gpt-executor"),
}


def _assignment_names(agent):
    name = str(agent or "").strip().lower()
    # systemd pool workers may identify themselves by the concrete unit name
    # (boris-executor-2..8 / gpt-executor-2..8).  Orders are addressed to the
    # shared pool, not to one process, so canonicalize only these numeric worker
    # suffixes before applying the narrow pool alias map.
    m = re.fullmatch(r"(gpt-executor|boris-executor)(?:-\d+)?", name)
    pool = m.group(1) if m else name
    return EXECUTOR_POOL_ALIASES.get(pool, (pool,))


def _assignment_matches(assigned, agent):
    assigned_name = str(assigned or "").strip().lower()
    return assigned_name in _assignment_names(agent)


def claim(agent, limit=1, worker=None):
    """Исполнитель сам берёт следующий наряд.

    SKIP LOCKED — чтобы двое воркеров не взяли один и тот же: строка
    блокируется в момент выборки, и второй просто её не увидит. Наряд
    адресован пулу, а забирает его конкретный воркер: его имя пишется в
    claimed_by, и по нему видно, кто именно ведёт задачу.
    """
    who = worker or agent
    recover_internal_blockers(actor=who)
    rows = db.rows("""SELECT id FROM ext_a2a_orders
                      WHERE assigned_to = ANY(:agents) AND status = ANY(:st)
                      ORDER BY priority DESC, id
                      LIMIT :l FOR UPDATE SKIP LOCKED""",
                   agents=list(_assignment_names(agent)),
                   st=list(CLAIMABLE), l=int(limit))
    out = []
    for r in rows:
        _set(r["id"], status=RUNNING, claimed_by=who[:40],
             claimed_at=_now_sql())
        message(r["id"], "CLAIMED", who, {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                          "pool": agent})
        out.append(get(r["id"]))
    return out


def _now_sql():
    import datetime
    return datetime.datetime.utcnow()


def heartbeat_claim(order_id, worker):
    """Refresh the lease of exactly one RUNNING order owned by `worker`.

    The order's immutable claimed_at remains the start time; updated_at is the
    live lease timestamp. A worker can only refresh its own claim, so a stale
    process cannot keep another worker's order alive after requeue/reclaim.
    """
    oid = int(order_id)
    who = str(worker or "").strip()
    if not who:
        return False
    row = db.one(
        """UPDATE ext_a2a_orders
              SET updated_at=NOW()
            WHERE id=:oid
              AND status=:run
              AND claimed_by=:worker
        RETURNING id""",
        oid=oid, run=RUNNING, worker=who,
    )
    return bool(row)


def release_stale_claims(minutes=3):
    """Requeue stale RUNNING orders, including orphan rows without a worker.

    `claimed_at` is only the claim start time. Fresh work is proved by
    heartbeat_claim() updating updated_at while the executor is inside long
    blocking provider/tool calls. A RUNNING row with claimed_by=NULL can never
    receive such a heartbeat, so after the same lease timeout it is an orphan
    and must return to the queue automatically.
    """
    rows = db.rows("""SELECT id, claimed_by FROM ext_a2a_orders
                       WHERE status = :run
                         AND updated_at < NOW() - (:m || ' minutes')::interval""",
                   run=RUNNING, m=int(minutes))
    freed = []
    for r in rows:
        _set(r["id"], status=QUEUED, claimed_by=None, claimed_at=None)
        previous = r.get("claimed_by") or "без назначенного воркера"
        message(r["id"], "RESET", "dispatcher",
                {"why": "просроченный RUNNING-наряд (%s) возвращён в очередь" % previous})
        freed.append({"order_id": r["id"], "was": r.get("claimed_by"),
                      "orphan": not bool(r.get("claimed_by"))})
    return freed



def claim_order(order_id, agent, worker=None):
    """Atomically claim exactly one order by id.

    Unlike claim(), this function never scans or consumes another order
    from the shared queue. It is intended for deterministic orchestration
    and controlled recovery flows.
    """
    oid = int(order_id)
    agent = str(agent or "").strip()
    worker = str(worker or agent or "").strip()

    if not agent:
        raise ApiError(
            "MISSING_INPUT",
            "claim_order: agent обязателен",
        )

    recover_internal_blockers(actor=worker or agent)
    current = get(oid, with_messages=False)

    # Targeted claim must never treat an already-running order as a successful
    # claim: a second caller could then execute the same order concurrently.
    if current["status"] == RUNNING:
        raise ApiError(
            "JOB_NOT_RETRYABLE",
            "наряд уже выполняется; повторный targeted claim запрещён",
        )

    if current["status"] not in CLAIMABLE:
        raise ApiError(
            "JOB_NOT_RETRYABLE",
            f"наряд в статусе «{current['status']}» нельзя забрать в работу",
        )

    assigned = str(current.get("assigned_to") or "").strip()

    if assigned and not _assignment_matches(assigned, agent):
        raise ApiError(
            "FORBIDDEN_SCOPE",
            f"наряд назначен «{assigned}», а забрать пытается «{agent}»",
        )

    row = db.one(
        """
        UPDATE ext_a2a_orders
           SET status=:running,
               claimed_by=:worker,
               claimed_at=NOW(),
               updated_at=NOW()
         WHERE id=:oid
           AND status = ANY(:claimable)
           AND (
                assigned_to IS NULL
                OR assigned_to=''
                OR assigned_to = ANY(:agents)
           )
        RETURNING id
        """,
        running=RUNNING,
        worker=worker,
        oid=oid,
        claimable=list(CLAIMABLE),
        agents=list(_assignment_names(agent)),
    )

    if not row:
        now = get(oid, with_messages=False)

        raise ApiError(
            "JOB_NOT_RETRYABLE",
            "claim_order: наряд изменился конкурентно, уже выполняется или назначен другому исполнителю",
        )

    message(
        oid,
        "CLAIMED",
        worker or agent,
        {
            "pool": agent,
            "mode": "targeted",
        },
    )

    return get(oid)


RECOVERABLE_EXECUTOR_ERRORS = (
    "MISSING_INPUT",
    "VALIDATION_ERROR",
    "POSTFLIGHT_UNVERIFIED",
)


def recover_stranded_executor_orders(agent, actor=None):
    """Requeue pre-reload RUNNING orders stranded by recoverable executor errors.

    Patch-level recovery must be safe on every daemon restart: only orders owned
    by this executor pool are considered, and only when the latest A2A message
    is EXECUTOR_ERROR carrying a known recoverable code. The stored
    assigned_to value is never rewritten.
    """
    names = list(_assignment_names(agent))
    rows = db.rows(
        """
        SELECT o.id,
               m.body_json
          FROM ext_a2a_orders o
          JOIN LATERAL (
              SELECT kind, body_json
                FROM ext_a2a_messages
               WHERE order_id=o.id
               ORDER BY id DESC
               LIMIT 1
          ) m ON TRUE
         WHERE o.status=:running
           AND o.assigned_to = ANY(:agents)
           AND m.kind='EXECUTOR_ERROR'
         ORDER BY o.id
        """,
        running=RUNNING,
        agents=names,
    )
    recovered = []
    for row in rows:
        body = _j(row.get("body_json"), {}) or {}
        code = str(body.get("code") or "").upper()
        if code not in RECOVERABLE_EXECUTOR_ERRORS:
            continue
        release_order(
            row["id"],
            reason=f"startup recovery after {code}: pre-reload executor error",
            actor=actor or agent,
        )
        recovered.append(int(row["id"]))
    return recovered


def release_order(order_id, reason, actor="owner"):
    """Return exactly one RUNNING order to QUEUED.

    Used for deterministic recovery when a specific order was claimed
    accidentally or its worker must release it.
    """
    oid = int(order_id)
    reason = str(reason or "").strip()
    actor = str(actor or "owner").strip()

    if not reason:
        raise ApiError(
            "MISSING_INPUT",
            "release_order: нужна причина возврата",
        )

    current = get(oid, with_messages=False)

    if current["status"] == QUEUED:
        return get(oid)

    if current["status"] != RUNNING:
        raise ApiError(
            "JOB_NOT_RETRYABLE",
            f"наряд в статусе «{current['status']}» нельзя вернуть из RUNNING",
        )

    row = db.one(
        """
        UPDATE ext_a2a_orders
           SET status=:queued,
               claimed_by=NULL,
               claimed_at=NULL,
               updated_at=NOW()
         WHERE id=:oid
           AND status=:running
        RETURNING id
        """,
        queued=QUEUED,
        oid=oid,
        running=RUNNING,
    )

    if not row:
        now = get(oid, with_messages=False)

        if now["status"] == QUEUED:
            return get(oid)

        raise ApiError(
            "JOB_NOT_RETRYABLE",
            "release_order: наряд изменился конкурентно",
        )

    message(
        oid,
        "RESET",
        actor,
        {
            "why": reason[:1000],
            "mode": "targeted",
        },
    )

    return get(oid)


def checkpoint(order_id, author, data=None):
    """Persist resumable executor state on the existing A2A order.

    Schema-free by design: CHECKPOINT uses ext_a2a_messages, so replacement
    workers resume the same order instead of creating a duplicate order.
    """
    o = get(order_id, with_messages=False)
    # BLOCKED_INFRA is resumable by design. Saving progress while an external
    # dependency is unavailable must not require reopening the order or losing
    # evidence. Terminal/owner-decision states remain closed to checkpoints.
    if o["status"] not in OPEN_STATES and o["status"] != BLOCKED_INFRA:
        raise ApiError("JOB_NOT_RETRYABLE",
                       f"checkpoint: наряд в статусе «{o['status']}» уже не открыт")
    payload = dict(data or {})
    payload.setdefault("iteration", o.get("iteration"))
    payload.setdefault("status", o.get("status"))
    message(order_id, "CHECKPOINT", author, payload)
    return payload


_TRANSIENT_CHECKPOINT_REASONS = frozenset((
    "provider_failover",
    "all_free_providers_unavailable",
))


def _checkpoint_body(row):
    if not row:
        return None
    try:
        body = json.loads(row.get("body_json") or "{}")
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"value": body}
    body["_author"] = row.get("author")
    body["_created_at"] = row.get("created_at")
    return body


def last_checkpoint(order_id):
    """Newest raw durable checkpoint for audit/debug, or None."""
    row = db.one("""SELECT body_json,author,created_at
                    FROM ext_a2a_messages
                    WHERE order_id=:o AND kind='CHECKPOINT'
                    ORDER BY id DESC LIMIT 1""", o=int(order_id))
    return _checkpoint_body(row)


def resume_checkpoint(order_id, limit=100):
    """Newest semantic progress checkpoint, preserving latest infra state.

    Provider failover/quota checkpoints are operational diagnostics, not task
    progress. Returning one of them as the sole resume state makes a replacement
    worker forget already-proven work and rediscover the same task. Scan a
    bounded history, return the newest non-transient checkpoint, and attach the
    newest transient checkpoint separately for current provider context.
    """
    rows = db.rows("""SELECT body_json,author,created_at
                     FROM ext_a2a_messages
                     WHERE order_id=:o AND kind='CHECKPOINT'
                     ORDER BY id DESC LIMIT :l""",
                   o=int(order_id), l=max(1, min(int(limit), 500)))
    latest_transient = None
    for row in rows:
        body = _checkpoint_body(row)
        if not body:
            continue
        reason = str(body.get("reason") or "").strip().lower()
        if reason in _TRANSIENT_CHECKPOINT_REASONS:
            if latest_transient is None:
                latest_transient = body
            continue
        if latest_transient is not None:
            body["_latest_transient"] = latest_transient
        return body
    return latest_transient


def _evidence_missing(value):
    text = str(value or "").strip()
    if not text:
        return True
    upper = text.upper()
    return upper in ("NOT_RUN", "NOT CHECKED", "NOT_CHECKED", "N/A", "NONE") or upper.startswith("NOT RUN")


def _accept_evidence_gaps(order):
    """Fail-closed evidence contract, activated only by this order's requirements."""
    ev = order.get("last_evidence") or {}
    required = " | ".join(
        [str(x) for x in (order.get("required_qa") or [])] +
        [str(x) for x in (order.get("required_evidence") or [])]
    ).lower()
    gaps = []

    if _evidence_missing(ev.get("evidence")):
        gaps.append("EVIDENCE")

    need_files = ("patch" in required or "files changed" in required)
    need_backup = "backup" in required
    need_verify = "verif" in required
    need_build = ("compile" in required or "build" in required or "import" in required)
    need_tests = "test" in required
    need_runtime = "runtime" in required
    need_e2e = "e2e" in required

    scope = order.get("scope") or {}
    read_only_planning = bool(
        str(order.get("phase") or "").upper() == "PLANNING"
        and scope.get("read_only") is True
        and str(scope.get("scope_mode") or "") == "planning_read_only"
    )
    # Historical PLANNING orders may still carry generic mutation evidence
    # ("applied patch id", backup/verify) even after scope reconciliation made
    # the order explicitly read-only. Requiring a patch here is a deadlock:
    # the order is forbidden to write but ACCEPT demands a write. The canonical
    # scope wins over stale template text. Conversely, claiming changed files
    # from a read-only planning order is itself an acceptance failure.
    if read_only_planning:
        need_files = False
        need_backup = False
        need_verify = False

    files = list(ev.get("files_changed") or [])
    if read_only_planning and files:
        gaps.append("READ_ONLY_SCOPE_WRITE")
    reviewed_server_owned = bool(
        not read_only_planning
        and scope.get("server_owned") is True
        and scope.get("autonomous_dev") is True
    )
    if need_files and not files:
        gaps.append("PATCH_APPLIED" if not reviewed_server_owned else "PATCH_PROPOSED")

    # REVIEWED_SERVER_OWNED_TWO_PHASE_V1:
    # A server-owned patch must first be accepted by the independent reviewer,
    # then the deploy daemon applies it with backup/compile/tests/runtime QA.
    # Requiring an *applied* patch before ACCEPT created a circular deadlock:
    # deploy required ACCEPT, while ACCEPT required deploy. For this one guarded
    # lifecycle we require a real proposed/approved/applied patch pre-ACCEPT and
    # defer apply/backup/verify/runtime gates to reviewed_order_apply_state().
    if reviewed_server_owned and files:
        try:
            from . import repo as _repo
            patches = _repo.list_patches(order["dev_job_id"], limit=200)
        except Exception:
            patches = []
        matching = []
        for patch in patches:
            if str(patch.get("status") or "") not in {"proposed","approved","applied"}:
                continue
            proven = set(patch.get("files") or [])
            if set(files).issubset(proven):
                matching.append(patch)
        if not matching:
            gaps.append("PATCH_PROPOSED")
    elif (not read_only_planning) and (files or need_backup or need_verify):
        try:
            from . import repo as _repo
            patches = _repo.list_patches(order["dev_job_id"], status="applied", limit=200)
        except Exception:
            patches = []
        matching = []
        for patch in patches:
            proven = set(patch.get("files") or [])
            if files and not set(files).issubset(proven):
                continue
            matching.append(patch)
        if not matching:
            gaps.append("PATCH_APPLIED")
        else:
            if need_backup and not any(str(x.get("backup_dir") or "").strip() for x in matching):
                gaps.append("BACKUP")
            if need_verify and not any(str(x.get("verify_output") or "").strip() for x in matching):
                gaps.append("VERIFY")

    post_apply_qa_deferred = reviewed_server_owned and bool(files)
    if need_build and not post_apply_qa_deferred and _evidence_missing(ev.get("build")):
        gaps.append("COMPILE")
    if need_tests and not post_apply_qa_deferred and _evidence_missing(ev.get("tests")):
        gaps.append("TESTS")
    if need_runtime and not post_apply_qa_deferred and _evidence_missing(ev.get("runtime")):
        gaps.append("RUNTIME_QA")
    if need_e2e and not post_apply_qa_deferred and _evidence_missing(ev.get("e2e")):
        gaps.append("E2E")

    return list(dict.fromkeys(gaps))


def _normalize_review_blockers(value):
    """Canonical REVIEW_REQUEST blockers shape: always list[str]."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ApiError(
                "VALIDATION_ERROR",
                "blockers должен быть строкой или списком строк",
            )
        return list(value)
    raise ApiError(
        "VALIDATION_ERROR",
        "blockers должен быть строкой или списком строк",
    )


def submit_review(order_id, author, summary, files_changed=None, build=None, tests=None,
                  runtime=None, criteria=None, evidence=None, blockers=None, safety=None,
                  e2e=None, execution=None):
    """REVIEW_REQUEST. Исполнитель отчитывается фактами, а не обещаниями."""
    o = get(order_id, with_messages=False)
    if o["status"] not in (RUNNING, RETURNED):
        raise ApiError("JOB_NOT_RETRYABLE",
                       f"наряд в статусе «{o['status']}», отчёт сейчас не принимается")
    if not evidence:
        raise ApiError("MISSING_INPUT",
                       "REVIEW_REQUEST без evidence не принимается: нужен вывод команд, "
                       "сборки или проверок, а не пересказ")
    payload = {"summary": summary, "files_changed": list(files_changed or []),
               "build": build, "tests": tests, "runtime": runtime, "e2e": e2e,
               "acceptance_criteria": criteria or [], "evidence": evidence,
               "blockers": _normalize_review_blockers(blockers), "safety": safety,
               "execution": dict(execution or {}),
               "iteration": o["iteration"]}
    _set(order_id, status=REVIEW, claimed_by=None, claimed_at=None,
         last_evidence_json=_dump(payload))
    message(order_id, "REVIEW_REQUEST", author, payload)
    return get(order_id)


# ----------------------------------------------------------------- ревьюер

def review(key, order_id, verdict, note=None, fixes=None, blocked_reason=None):
    """ACCEPT · RETURN_WITH_FIXES · BLOCKED_HUMAN · BLOCKED_INFRA."""
    o = get(order_id, with_messages=False)
    actor = auth.actor_of(key)["name"]
    v = (verdict or "").upper()
    if o["status"] != REVIEW and v != "BLOCKED_HUMAN":
        raise ApiError("JOB_NOT_RETRYABLE",
                       f"по наряду в статусе «{o['status']}» решение сейчас не принимается")

    if v == "ACCEPT":
        missing = _accept_evidence_gaps(o)
        if missing:
            message(order_id, "ACCEPT_EVIDENCE_REJECTED", actor, {
                "missing": missing,
                "required_qa": o.get("required_qa") or [],
                "required_evidence": o.get("required_evidence") or [],
            })
            raise ApiError(
                "ACCEPTANCE_NOT_PASSED",
                "ACCEPT отклонён сервером: не хватает фактических доказательств: "
                + ", ".join(missing),
            )
        _set(order_id, status=ACCEPTED, claimed_by=None, claimed_at=None,
             last_review_json=_dump({"verdict": v, "note": note}))
        message(order_id, "ACCEPT", actor, {"note": note})
        try:
            reconcile_parent_acceptance(key, order_id)
        except Exception as exc:
            # ACCEPT itself remains valid; parent stays open (fail-closed) and
            # recovery/lead may retry deterministic reconciliation later.
            message(order_id, "PARENT_ACCEPTANCE_RECONCILE_FAILED", actor, {
                "error_type": type(exc).__name__,
                "why": str(exc)[:500],
            })
        return get(order_id)

    if v == "RETURN_WITH_FIXES":
        if not fixes:
            raise ApiError("MISSING_INPUT",
                           "возврат без списка замечаний бесполезен: нужен failed criterion, "
                           "в чём проблема и что именно исправить")
        it = int(o["iteration"]) + 1
        if it >= int(o["max_iterations"]):
            _set(order_id, status=BLOCKED_INFRA, iteration=it,
                 claimed_by=None, claimed_at=None,
                 blocked_reason=f"исчерпан лимит итераций ({o['max_iterations']}) — "
                                "нужен разбор первопричины, а не ещё один круг",
                 last_review_json=_dump({"verdict": v, "fixes": fixes}))
            message(order_id, "LOOP_GUARD", actor,
                    {"iteration": it, "fixes": fixes,
                     "note": "цикл остановлен, требуется root-cause review"})
            return get(order_id)
        _set(order_id, status=RETURNED, iteration=it,
             claimed_by=None, claimed_at=None,
             last_review_json=_dump({"verdict": v, "note": note, "fixes": fixes}))
        message(order_id, "RETURN_WITH_FIXES", actor,
                {"note": note, "fixes": fixes, "iteration": it})
        return get(order_id)

    if v in ("BLOCKED_HUMAN", "BLOCKED_INFRA"):
        if not blocked_reason:
            raise ApiError("MISSING_INPUT", "нужна причина блокировки одним предложением")
        if v == "BLOCKED_HUMAN" and not is_owner_only_blocker(blocked_reason):
            _set(order_id, status=QUEUED, claimed_by=None, claimed_at=None,
                 blocked_reason=None,
                 last_review_json=_dump({"verdict": "AUTO_RECOVER", "reason": blocked_reason}))
            message(order_id, "AUTO_RECOVERY", actor,
                    {"from": "BLOCKED_HUMAN", "why": str(blocked_reason)[:500]})
            return get(order_id)
        _set(order_id, status=(BLOCKED_HUMAN if v == "BLOCKED_HUMAN" else BLOCKED_INFRA),
             claimed_by=None, claimed_at=None,
             blocked_reason=str(blocked_reason)[:500],
             last_review_json=_dump({"verdict": v, "reason": blocked_reason}))
        message(order_id, v, actor, {"reason": blocked_reason})
        return get(order_id)

    raise ApiError("VALIDATION_ERROR",
                   "verdict: ACCEPT | RETURN_WITH_FIXES | BLOCKED_HUMAN | BLOCKED_INFRA")


# -------------------------------------------------------------- что показать

def board(limit=50):
    """То, что можно показывать владельцу: только исходы, без внутренней переписки."""
    rows = listing(limit=limit)
    done = [o for o in rows if o["status"] == ACCEPTED]
    human = [o for o in rows if o["status"] == BLOCKED_HUMAN]
    infra = [o for o in rows if o["status"] == BLOCKED_INFRA]
    working = [o for o in rows if o["status"] in OPEN_STATES]
    return {
        "DONE": [{"order": o["order_id"], "task": o["dev_job_id"], "phase": o["phase"],
                  "goal": o["goal"], "iterations": o["iteration"]} for o in done],
        "BLOCKED_HUMAN": [{"order": o["order_id"], "task": o["dev_job_id"],
                           "why": o["blocked_reason"],
                           "proven": (o.get("last_evidence") or {}).get("summary")}
                          for o in human],
        "BLOCKED_INFRA": [{"order": o["order_id"], "why": o["blocked_reason"]}
                          for o in infra],
        "in_progress": [{"order": o["order_id"], "task": o["dev_job_id"],
                         "phase": o["phase"], "status": o["status"],
                         "iteration": f"{o['iteration']}/{o['max_iterations']}"}
                        for o in working],
        "counts": {"done": len(done), "blocked_human": len(human),
                   "blocked_infra": len(infra), "in_progress": len(working)},
    }


def release_nonrunning_claims(limit=500, actor="dispatcher"):
    """Clear stale worker ownership from orders that are no longer RUNNING.

    claimed_by/claimed_at are lease metadata, not historical authorship. Once an
    order leaves RUNNING (review, queued, returned, accepted or blocked), keeping
    the old worker claim can create false busy/dead-assignment signals. This
    cleanup changes no lifecycle status and is safe/idempotent.
    """
    rows = db.rows("""SELECT id,status,claimed_by FROM ext_a2a_orders
                     WHERE status <> :running AND claimed_by IS NOT NULL
                     ORDER BY id LIMIT :l""",
                  running=RUNNING, l=int(limit))
    released = []
    for row in rows:
        _set(row["id"], claimed_by=None, claimed_at=None)
        message(row["id"], "CLAIM_RELEASED", actor, {
            "from_status": row.get("status"),
            "worker": row.get("claimed_by"),
            "why": "order is not RUNNING",
        })
        released.append(int(row["id"]))
    return released
