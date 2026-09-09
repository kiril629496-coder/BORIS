"""BORIS Control Plane domain service.

Invariants:
- OWNER RULES MUST NOT LIVE ONLY IN CHAT HISTORY
- NO EVIDENCE = NO PASS
- desired state is reconciled against actual state
- KPI failure must produce diagnosis/action or explicit blocked state
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_

from app.models.control_plane import (
    ControlEvidence, ControlExecution, ControlIncident, ControlModuleContract,
    ControlRequirement, ControlRequirementVersion,
)
from app.services.reliability import get_trace_id, record_event

TERMINAL = {"pass", "blocked", "failed", "waiting_external", "waiting_owner", "degraded"}
EXECUTION_FLOW = {
    "received": {"parsed", "blocked", "failed"},
    "parsed": {"mapped_to_requirements", "blocked", "failed"},
    "mapped_to_requirements": {"planned", "blocked", "failed"},
    "planned": {"preflight", "blocked", "failed"},
    "preflight": {"executing", "blocked", "failed", "waiting_external", "waiting_owner"},
    "executing": {"technical_tested", "retrying", "blocked", "failed", "waiting_external"},
    "retrying": {"executing", "blocked", "failed"},
    "technical_tested": {"runtime_verified", "retrying", "blocked", "failed", "degraded"},
    "runtime_verified": {"business_verified", "pass", "degraded", "failed"},
    "business_verified": {"pass", "degraded", "failed"},
    "pass": {"degraded"},  # regression can revoke a previous PASS
    "degraded": {"preflight", "executing", "runtime_verified", "pass", "failed", "blocked"},
    "waiting_external": {"preflight", "executing", "failed", "blocked"},
    "waiting_owner": {"preflight", "blocked", "failed"},
    "blocked": {"preflight", "failed"},
    "failed": {"preflight"},
}

RULE_TYPES = {"business_rule", "kpi_rule", "operational_rule", "ui_rule", "automation_rule", "safety_rule", "integration_rule", "global_owner_rule", "decision", "fact"}
PRIORITIES = {"critical", "high", "normal", "low", "informational"}
SCOPE_TYPES = {"global", "company", "project", "account", "campaign", "module", "entity", "user", "workflow"}
SCOPE_RANK = {"global": 0, "company": 1, "project": 1, "module": 2, "user": 2, "workflow": 3, "account": 4, "campaign": 5, "entity": 6}

IMMUTABLE_INVARIANTS = (
    ("system.no_evidence_no_pass", "NO EVIDENCE = NO PASS"),
    ("system.reconcile_desired_actual", "DESIRED STATE MUST BE CONTINUOUSLY RECONCILED WITH ACTUAL STATE"),
    ("system.owner_rules_persist", "OWNER RULES MUST NOT LIVE ONLY IN CHAT HISTORY"),
    ("system.kpi_failure_action", "KPI FAILURE MUST TRIGGER DIAGNOSIS OR EXPLICIT BLOCKED STATUS"),
    ("system.health_requires_function", "A MODULE CANNOT CALL ITSELF HEALTHY ONLY BECAUSE ITS PROCESS IS RUNNING"),
    ("system.retry_strategy_change", "REPEATED FAILURE MUST CHANGE STRATEGY OR ESCALATE; NEVER LOOP FOREVER"),
    ("system.capture_before_execution", "EVERY OWNER INSTRUCTION MUST BE DURABLY CAPTURED BEFORE EXECUTION"),
    ("system.no_github_dependency", "BORIS PRODUCTION MUST NOT DEPEND ON GITHUB OR GIT-BASED DEPLOYMENT"),
)


def utcnow():
    return datetime.now(timezone.utc)


def _snapshot(row: ControlRequirement) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in {"created_at", "updated_at", "last_applied_at", "last_verified_at"}}


def seed_invariants(db) -> int:
    created = 0
    for key, text in IMMUTABLE_INVARIANTS:
        if db.query(ControlRequirement).filter(ControlRequirement.key == key).first():
            continue
        row = ControlRequirement(
            id=str(uuid.uuid4()), key=key, title=text, normalized_rule=text,
            requirement_type="global_owner_rule", scope_type="global", scope_id="*",
            module="system", priority="critical", status="active",
            desired_state_json={"enforced": True, "immutable": True},
            verification_json=[{"type": "system_invariant", "required": True}],
            source_json={"type": "system_seed", "immutable": True},
        )
        db.add(row); db.flush()
        db.add(ControlRequirementVersion(requirement_id=row.id, version=1, snapshot_json=_snapshot(row), change_reason="initial immutable invariant"))
        created += 1
    safety_key="system.marketing.absolute_autonomous_bid_cap"
    if not db.query(ControlRequirement).filter(ControlRequirement.key==safety_key).first():
        row=ControlRequirement(
            id=str(uuid.uuid4()),key=safety_key,title="ABSOLUTE AUTONOMOUS MARKETING BID CAP",
            normalized_rule="Autonomous marketing bid may never exceed the global safety cap stored in Control Plane",
            requirement_type="safety_rule",scope_type="global",scope_id="*",module="system",priority="critical",status="active",
            desired_state_json={"hard_max_bid_rub":150.0,"enforced":True,"immutable":True},
            verification_json=[{"type":"database_guard_and_runtime_readback","required":True}],
            source_json={"type":"system_seed","immutable":True,"source_of_truth":"control_plane"},
        )
        db.add(row);db.flush();db.add(ControlRequirementVersion(requirement_id=row.id,version=1,snapshot_json=_snapshot(row),change_reason="initial immutable money safety invariant"));created+=1
    return created


def classify_owner_instruction(text: str) -> dict:
    """Deterministic, no-paid-AI first-pass compiler for owner instructions."""
    raw = (text or "").strip()
    low = raw.lower()
    permanent_tokens = ("всегда", "каждый ", "никогда", "у всех", "для всех", "должен", "должна", "должно", "автоматически", "правило", "запомни",
                        "не останавливайся", "без меня", "не был снова оператором", "не быть оператором", "работай нонстоп")
    cancel_tokens = ("отмени", "отменить", "больше не", "удали правило", "отключи правило")
    correction_tokens = ("не так", "вместо", "исправь правило", "теперь", "изменить правило")
    intent = "temporary_task"
    if any(x in low for x in cancel_tokens): intent = "cancellation"
    elif any(x in low for x in correction_tokens): intent = "correction"
    elif any(x in low for x in permanent_tokens): intent = "permanent_rule"
    elif "если" in low and any(x in low for x in ("поднимай","повышай","создавай","пиши","активируй","включай","отключай","контролируй","делай","запускай")):
        intent = "permanent_rule"
    if "kpi" in low or "лид" in low or "cpl" in low or "план" in low and "факт" in low:
        rtype = "kpi_rule"
    elif "интерфейс" in low or "карточк" in low or "кнопк" in low:
        rtype = "ui_rule"
    elif "нельзя" in low or "запрет" in low or "лимит" in low or "не превыш" in low:
        rtype = "safety_rule"
    elif "crm" in low or "api" in low or "интеграц" in low or "звон" in low:
        rtype = "integration_rule"
    elif "автомат" in low or "каждый" in low:
        rtype = "automation_rule"
    else:
        rtype = "business_rule"
    scope_type, scope_id = "global", "*"
    m = re.search(r"(?:аккаунт(?:а|е|у)?|account)\s+([\w\-_.]+)", raw, flags=re.I)
    if m:
        scope_type, scope_id = "account", m.group(1)
    named_scope_hint = None
    if scope_type == "global" and not any(x in low for x in ("у всех", "для всех", "все аккаунт", "глобаль")):
        named = re.search(r"(?:^|[\s,])у\s+([а-яёa-z][а-яёa-z0-9_-]{3,})", low, flags=re.I)
        if named:
            named_scope_hint = named.group(1)
    modules = []
    for token, module in (("став", "marketing"), ("avito", "marketing"), ("cpl", "marketing"), ("цпл", "marketing"), ("автопилот", "marketing"), ("моп", "mop"), ("crm", "crm"), ("звон", "telephony"), ("роп", "rop"), ("соц", "social"), ("директ", "direct"), ("сообщ", "messages")):
        if token in low and module not in modules: modules.append(module)

    def _number(patterns):
        for pattern in patterns:
            hit=re.search(pattern,low,flags=re.I)
            if hit:
                try:return float(hit.group(1).replace(',','.'))
                except Exception:pass
        return None

    desired={}
    target=_number((r"(\d+(?:[.,]\d+)?)\s*лид\w*\s*(?:в|за)?\s*(?:день|сутк)", r"цель\s*(?:—|:|=)?\s*(\d+(?:[.,]\d+)?)\s*лид"))
    cpl=_number((r"(?:cpl|цпл)\s*(?:не\s*)?(?:выше|больше|дороже|макс\w*|до|=|был|была)?\s*(\d+(?:[.,]\d+)?)", r"стоимост\w*\s+лид\w*.{0,24}?(\d+(?:[.,]\d+)?)"))
    budget=_number((r"(?:дневн\w*\s+бюджет|бюджет\s+(?:в|на)\s+день).{0,24}?(\d+(?:[.,]\d+)?)",))
    bid=_number((r"(?:макс\w*\s+ставк\w*|ставк\w*\s+не\s+(?:выше|больше|дороже)).{0,24}?(\d+(?:[.,]\d+)?)",))
    if target is not None: desired["target_leads_per_day"]=target
    if cpl is not None: desired["max_cost_per_lead_rub"]=cpl
    if budget is not None: desired["daily_budget_limit_rub"]=budget
    if bid is not None: desired["hard_max_bid_rub"]=bid
    if desired and "marketing" not in modules: modules.append("marketing")
    constraints=[]
    if cpl is not None: constraints.append({"type":"max_cpl","value":cpl})
    if budget is not None: constraints.append({"type":"daily_budget_limit","value":budget})
    if bid is not None: constraints.append({"type":"hard_max_bid","value":bid})
    actions=[]
    if "marketing" in modules and (target is not None or "автопилот" in low or "став" in low):
        actions.append({"type":"diagnose_on_kpi_fail"})
    if "повыш" in low and "став" in low:
        actions.append({"type":"increase_bid_incrementally"})
    return {"intent": intent, "requirement_type": rtype, "scope_type": scope_type, "scope_id": scope_id, "named_scope_hint":named_scope_hint, "modules": modules or ["system"], "raw_text": raw, "desired_state":desired, "constraints":constraints, "actions":actions}


def create_requirement(db, *, key: str, title: str, normalized_rule: str, requirement_type: str,
                       scope_type: str = "global", scope_id: str = "*", module: str = "system",
                       priority: str = "normal", raw_owner_text: str | None = None,
                       desired_state: dict | None = None, conditions: list | None = None,
                       actions: list | None = None, constraints: list | None = None,
                       verification: list | None = None, dependencies: list | None = None,
                       source: dict | None = None, created_by: int | None = None) -> ControlRequirement:
    if requirement_type not in RULE_TYPES: raise ValueError("invalid requirement_type")
    if scope_type not in SCOPE_TYPES: raise ValueError("invalid scope_type")
    if priority not in PRIORITIES: raise ValueError("invalid priority")
    if db.query(ControlRequirement).filter(ControlRequirement.key == key).first(): raise ValueError("requirement key already exists")
    row = ControlRequirement(
        id=str(uuid.uuid4()), key=key[:160], title=title[:500], raw_owner_text=raw_owner_text,
        normalized_rule=normalized_rule, requirement_type=requirement_type, scope_type=scope_type,
        scope_id=str(scope_id or "*")[:255], module=str(module or "system")[:64], priority=priority,
        status="active", desired_state_json=desired_state or {}, conditions_json=conditions or [],
        actions_json=actions or [], constraints_json=constraints or [], verification_json=verification or [],
        dependencies_json=dependencies or [], source_json=source or {}, created_by=created_by,
    )
    db.add(row); db.flush()
    db.add(ControlRequirementVersion(requirement_id=row.id, version=1, snapshot_json=_snapshot(row), change_reason="created", changed_by=created_by))
    record_event(db, "control_plane", "requirement.created", title, severity="info", account_id=(row.scope_id if row.scope_type == "account" else None), details={"requirement_id": row.id, "key": row.key, "module": row.module})
    return row


def update_requirement(db, row: ControlRequirement, patch: dict, *, changed_by: int | None = None, reason: str = "updated") -> ControlRequirement:
    if bool((row.source_json or {}).get("immutable")):
        raise ValueError("immutable requirement")
    allowed = {"title", "normalized_rule", "requirement_type", "scope_type", "scope_id", "module", "priority", "status", "desired_state_json", "conditions_json", "actions_json", "constraints_json", "verification_json", "dependencies_json", "source_json", "superseded_by"}
    for k, v in patch.items():
        if k in allowed: setattr(row, k, v)
    row.version = int(row.version or 1) + 1
    row.updated_at = utcnow()
    db.flush()
    db.add(ControlRequirementVersion(requirement_id=row.id, version=row.version, snapshot_json=_snapshot(row), change_reason=reason[:2000], changed_by=changed_by))
    record_event(db, "control_plane", "requirement.updated", row.title, details={"requirement_id": row.id, "version": row.version})
    return row


def applicable_requirements(db, *, module: str, account_id: str | None = None) -> list[ControlRequirement]:
    q = db.query(ControlRequirement).filter(ControlRequirement.status == "active", or_(ControlRequirement.module == module, ControlRequirement.module == "system"))
    filters = [and_(ControlRequirement.scope_type == "global", ControlRequirement.scope_id == "*")]
    if account_id:
        filters.append(and_(ControlRequirement.scope_type == "account", ControlRequirement.scope_id == str(account_id)))
    filters.append(and_(ControlRequirement.scope_type == "module", ControlRequirement.scope_id.in_((module, "*"))))
    rows = q.filter(or_(*filters)).all()
    return sorted(rows, key=lambda r: (SCOPE_RANK.get(r.scope_type, 0), {"critical": 5, "high": 4, "normal": 3, "low": 2, "informational": 1}.get(r.priority, 0), int(r.version or 0)))


def effective_config(db, *, module: str, account_id: str | None = None) -> dict:
    rules = applicable_requirements(db, module=module, account_id=account_id)
    desired = {}
    provenance = {}
    for r in rules:
        source = dict(r.source_json or {})
        # Immutable system seeds describe control-plane invariants; they are not
        # domain configuration fields and must never create false module drift.
        if r.module == "system" and source.get("type") == "system_seed":
            continue
        for k, v in dict(r.desired_state_json or {}).items():
            desired[k] = v
            provenance[k] = {
                "requirement_id": r.id,
                "key": r.key,
                "version": r.version,
                "scope": f"{r.scope_type}:{r.scope_id}",
                "source": source,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
    return {"module": module, "account_id": account_id, "desired_state": desired, "provenance": provenance, "requirements": [serialize_requirement(r) for r in rules]}


def start_execution(db, requirement: ControlRequirement, *, account_id: str | None = None, plan: dict | None = None, idempotency_key: str | None = None) -> ControlExecution:
    correlation = get_trace_id()
    if not idempotency_key:
        base = f"{requirement.id}:{requirement.version}:{account_id or requirement.scope_id}:{json.dumps(plan or {}, sort_keys=True, ensure_ascii=False)}"
        idempotency_key = hashlib.sha256(base.encode("utf-8")).hexdigest()
    existing = db.query(ControlExecution).filter(ControlExecution.idempotency_key == idempotency_key).first()
    if existing: return existing
    row = ControlExecution(id=str(uuid.uuid4()), requirement_id=requirement.id, correlation_id=correlation, account_id=account_id, module=requirement.module, status="received", plan_json=plan or {}, idempotency_key=idempotency_key)
    db.add(row); db.flush()
    record_event(db, "control_plane", "execution.started", requirement.title, account_id=account_id, details={"execution_id": row.id, "requirement_id": requirement.id}, trace_id=correlation)
    return row


def transition_execution(db, execution: ControlExecution, new_status: str, *, actual_state: dict | None = None, reason: str | None = None, error_code: str | None = None) -> ControlExecution:
    old = str(execution.status or "received")
    if new_status not in EXECUTION_FLOW.get(old, set()):
        raise ValueError(f"invalid execution transition {old}->{new_status}")
    if new_status == "pass":
        passed = db.query(ControlEvidence).filter(ControlEvidence.execution_id == execution.id, ControlEvidence.passed.is_(True)).count()
        runtime = db.query(ControlEvidence).filter(ControlEvidence.execution_id == execution.id, ControlEvidence.passed.is_(True), ControlEvidence.level.in_(("L4", "L5", "L6"))).count()
        if passed == 0 or runtime == 0:
            raise ValueError("NO_EVIDENCE_NO_PASS")
    execution.status = new_status
    execution.updated_at = utcnow()
    if old == "received" and new_status != "received" and not execution.started_at: execution.started_at = utcnow()
    if actual_state is not None: execution.actual_state_json = actual_state
    if new_status == "blocked":
        execution.block_reason = reason or "blocked"
        execution.error_code = error_code or execution.error_code
        execution.error_message = reason or execution.error_message
    if new_status in {"failed", "degraded"}:
        execution.error_code = error_code or execution.error_code
        execution.error_message = reason or execution.error_message
    if new_status in TERMINAL: execution.finished_at = utcnow()
    if new_status == "pass":
        # Keep the execution ledger internally consistent: a terminal PASS cannot
        # leave its declared steps looking pending in the owner journal.
        from app.models.control_plane import ControlExecutionStep
        finished=utcnow()
        steps=db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id==execution.id).all()
        for step in steps:
            if str(step.status or "pending") in {"pending","running","received"}:
                step.status="verified"
                if not step.started_at: step.started_at=execution.started_at or finished
                step.finished_at=finished
                out=dict(step.output_json or {})
                out.setdefault("terminal_execution_status","pass")
                out.setdefault("verified_by","execution_terminal_evidence")
                step.output_json=out
    db.flush()
    if new_status == "pass":
        req = db.query(ControlRequirement).filter(ControlRequirement.id == execution.requirement_id).first()
        if req:
            req.last_applied_at = utcnow(); req.last_verified_at = utcnow()
    record_event(db, "control_plane", f"execution.{new_status}", reason or new_status, severity="critical" if new_status == "failed" else "info", account_id=execution.account_id, details={"execution_id": execution.id, "from": old, "to": new_status}, trace_id=execution.correlation_id)
    return execution


def add_evidence(db, execution: ControlExecution, *, level: str, evidence_type: str, source: str, passed: bool, payload: dict | None = None) -> ControlEvidence:
    if level not in {"L1", "L2", "L3", "L4", "L5", "L6"}: raise ValueError("invalid evidence level")
    row = ControlEvidence(execution_id=execution.id, requirement_id=execution.requirement_id, level=level, evidence_type=evidence_type[:48], source=source[:96], passed=bool(passed), payload_json=payload or {})
    db.add(row); db.flush()
    return row


def open_incident(db, *, incident_type: str, symptom: str, module: str = "system", account_id: str | None = None, requirement_id: str | None = None, execution_id: str | None = None, priority: str = "P2", details: dict | None = None) -> ControlIncident:
    row = ControlIncident(id=str(uuid.uuid4()), requirement_id=requirement_id, execution_id=execution_id, incident_type=incident_type[:64], priority=priority[:8], state="open", module=module[:64], account_id=account_id, symptom=symptom, root_cause="unknown", details_json=details or {})
    db.add(row); db.flush()
    record_event(db, module, incident_type, symptom, severity="critical" if priority in {"P0", "P1"} else "warning", account_id=account_id, details={"control_incident_id": row.id, **(details or {})})
    return row


def reconcile(db, *, module: str, account_id: str | None, actual_state: dict) -> dict:
    cfg = effective_config(db, module=module, account_id=account_id)
    desired = cfg["desired_state"]
    drift = {k: {"desired": v, "actual": actual_state.get(k)} for k, v in desired.items() if actual_state.get(k) != v}
    if drift:
        incident = open_incident(db, incident_type="CONFIG_DRIFT", symptom=f"Desired state differs from actual state for {len(drift)} field(s)", module=module, account_id=account_id, priority="P1", details={"drift": drift})
        return {"status": "degraded", "drift": drift, "incident_id": incident.id, **cfg}
    return {"status": "pass", "drift": {}, **cfg}


def serialize_requirement(r: ControlRequirement) -> dict:
    return {"id": r.id, "key": r.key, "title": r.title, "normalized_rule": r.normalized_rule, "type": r.requirement_type, "scope_type": r.scope_type, "scope_id": r.scope_id, "module": r.module, "priority": r.priority, "status": r.status, "version": r.version, "desired_state": r.desired_state_json or {}, "verification": r.verification_json or {}, "last_applied_at": r.last_applied_at.isoformat() if r.last_applied_at else None, "last_verified_at": r.last_verified_at.isoformat() if r.last_verified_at else None}


def _resolve_named_account(db, hint: str | None) -> tuple[str | None, list[dict]]:
    """Conservative owner-language account resolver. Exactly one match or none."""
    if not hint:
        return None, []
    from app.models.account import Account
    h=re.sub(r"[^а-яёa-z0-9_-]+","",str(hint).lower())
    if len(h)<4:
        return None, []
    stem=h[:4]
    candidates=[]
    for row in db.query(Account).all():
        name=str(row.name or "").lower()
        aid=str(row.account_id or "").lower()
        words=re.findall(r"[а-яёa-z0-9_-]+",name)
        if h==aid or h in words or any(len(w)>=4 and w[:4]==stem for w in words) or (len(h)>=5 and h in aid):
            candidates.append({"account_id":row.account_id,"name":row.name})
    unique={str(x["account_id"]):x for x in candidates if x.get("account_id")}
    vals=list(unique.values())
    return (vals[0]["account_id"] if len(vals)==1 else None), vals


def capture_owner_instruction(db, *, text: str, user_id: int | None = None, source: str = "command_center",
                              source_ref: str | None = None, account_id: str | None = None) -> dict:
    """Persist every owner instruction; materialize permanent rules automatically.

    This is intentionally deterministic and free of paid-model calls. A later AI
    enrichment may improve normalization, but the raw instruction is never lost.
    """
    from app.models.control_plane import ControlOwnerInstruction, ControlWorkspaceThread, ControlWorkspaceEvent
    try:
        from app.services.command_redact import redact_text
        safe_text = redact_text(text, 12000) or ""
    except Exception:
        safe_text = (text or "")[:12000]
    compiled = classify_owner_instruction(safe_text)
    scope_resolution_required = False
    scope_candidates = []
    if account_id and compiled.get("scope_type") == "global":
        compiled["scope_type"] = "account"
        compiled["scope_id"] = str(account_id)
    elif compiled.get("scope_type") == "global" and compiled.get("named_scope_hint"):
        resolved, scope_candidates = _resolve_named_account(db, compiled.get("named_scope_hint"))
        if resolved:
            compiled["scope_type"] = "account"
            compiled["scope_id"] = str(resolved)
            compiled["scope_resolution"] = {"status":"resolved","account_id":resolved,"candidates":scope_candidates}
        else:
            scope_resolution_required = True
            compiled["scope_resolution"] = {"status":"needs_resolution","hint":compiled.get("named_scope_hint"),"candidates":scope_candidates}
    identity = str(source_ref or "").strip()
    if identity:
        idem = hashlib.sha256(f"{source}:{identity}:{user_id or 0}".encode()).hexdigest()
    else:
        idem = hashlib.sha256(f"{source}:{user_id or 0}:{account_id or '*'}:{safe_text}".encode()).hexdigest()
    existing = db.query(ControlOwnerInstruction).filter(ControlOwnerInstruction.idempotency_key == idem).first()
    if existing:
        existing_thread = db.query(ControlWorkspaceThread).filter(
            ControlWorkspaceThread.external_chat_ref == f"instruction:{existing.id}"
        ).first()
        return {
            "instruction_id": existing.id,
            "intent": existing.intent,
            "status": existing.status,
            "requirement_ids": existing.linked_requirement_ids_json or [],
            "workspace_thread_id": existing_thread.id if existing_thread else None,
            "deduplicated": True,
        }
    row = ControlOwnerInstruction(
        id=str(uuid.uuid4()), idempotency_key=idem, user_id=user_id, source=str(source or "command_center")[:32],
        source_ref=str(source_ref or "")[:160] or None, account_id=str(account_id)[:255] if account_id else None,
        raw_text=safe_text, intent=compiled["intent"], requirement_type=compiled.get("requirement_type"),
        scope_type=compiled.get("scope_type") or "global", scope_id=compiled.get("scope_id") or "*",
        modules_json=compiled.get("modules") or ["system"], compiler_json=compiled, status="captured",
    )
    db.add(row); db.flush()
    workspace_thread_id = None
    # OWNER_TASK_TO_WORKSPACE_V2:
    # Every actionable owner task becomes a durable workspace thread even when
    # the same message also contains a permanent operating rule (for example
    # "continue non-stop"). Rules still materialize separately below.
    _task_tokens = (
        "сдел", "делай", "программ", "продолж", "доработ", "проверь",
        "собер", "собрать", "внедр", "добав", "почин", "исправ",
        "созда", "запусти", "настрой", "перенес", "реализ", "довед",
    )
    _actionable_task = (
        compiled["intent"] == "temporary_task"
        or any(token in safe_text.lower() for token in _task_tokens)
    )
    if _actionable_task and source != "workspace":
        resolved_account_id = account_id
        if not resolved_account_id and compiled.get("scope_type") == "account":
            resolved_account_id = compiled.get("scope_id")
        thread = ControlWorkspaceThread(
            id=str(uuid.uuid4()),
            thread_type="task",
            title=(safe_text.splitlines()[0].strip() or "Задача владельца")[:500],
            account_id=str(resolved_account_id)[:255] if resolved_account_id else None,
            external_chat_ref=f"instruction:{row.id}",
            status="active",
            summary=safe_text[:2000],
            current_state_json={
                "progress_percent": None,
                "progress_status": "captured",
                "current_step": "Задача зафиксирована",
                "next_step": "Проверить фактическое состояние и начать выполнение",
                "instruction_id": row.id,
                "memory_mode": "durable_workspace",
            },
            plan_json=[],
            source_json={
                "source": source,
                "source_ref": source_ref,
                "instruction_id": row.id,
                "auto_created": True,
            },
            created_by=user_id,
        )
        db.add(thread)
        db.flush()
        db.add(
            ControlWorkspaceEvent(
                thread_id=thread.id,
                event_type="task_captured",
                actor="system",
                text="Задача автоматически записана в постоянную память задач BORIS.",
                payload_json={"instruction_id": row.id, "source": source, "source_ref": source_ref},
                instruction_id=row.id,
            )
        )
        workspace_thread_id = thread.id
    requirement_ids = []
    if scope_resolution_required:
        row.status = "needs_resolution"
    elif compiled["intent"] == "permanent_rule":
        text_hash = hashlib.sha256(safe_text.encode("utf-8")).hexdigest()[:16]
        for module in compiled.get("modules") or ["system"]:
            key = f"owner.{text_hash}.{module}"[:160]
            req = db.query(ControlRequirement).filter(ControlRequirement.key == key).first()
            if req is None:
                req = create_requirement(
                    db, key=key, title=safe_text[:240], normalized_rule=safe_text,
                    requirement_type=compiled.get("requirement_type") or "business_rule",
                    scope_type=compiled.get("scope_type") or "global", scope_id=compiled.get("scope_id") or "*",
                    module=module, priority="high", raw_owner_text=safe_text,
                    desired_state=compiled.get("desired_state") or {},
                    actions=compiled.get("actions") or [], constraints=compiled.get("constraints") or [],
                    verification=[{"type": "runtime_required", "required": True}],
                    source={"type": "owner_instruction", "instruction_id": row.id, "source": source, "source_ref": source_ref, "allow_control_write": bool(compiled.get("desired_state"))},
                    created_by=user_id,
                )
            requirement_ids.append(req.id)
        row.linked_requirement_ids_json = requirement_ids
        row.status = "materialized"
    elif compiled["intent"] in {"correction", "cancellation"}:
        # Never guess which existing rule is targeted. Capture first, make the
        # unresolved change visible, and let conflict/resolution workflow bind it.
        row.status = "needs_resolution"
    db.flush()
    record_event(db, "control_plane", "owner_instruction.captured", safe_text[:500], severity="info", account_id=account_id,
                 details={"instruction_id": row.id, "intent": row.intent, "requirements": requirement_ids})
    return {
        "instruction_id": row.id,
        "intent": row.intent,
        "status": row.status,
        "requirement_ids": requirement_ids,
        "workspace_thread_id": workspace_thread_id,
        "deduplicated": False,
    }


def detect_rule_conflicts(db, *, module: str | None = None) -> list[dict]:
    """Detect contradictory desired-state assignments in overlapping scopes.

    Scope overlap is conservative: global overlaps everything; exact account/module
    scope pairs overlap their same target. More-specific rules may override a
    broader rule only when the broader requirement explicitly sets
    source_json.allow_override=true. Otherwise conflicting values are surfaced.
    """
    from app.models.control_plane import ControlRuleConflict
    q = db.query(ControlRequirement).filter(ControlRequirement.status == "active")
    if module:
        q = q.filter(or_(ControlRequirement.module == module, ControlRequirement.module == "system"))
    rows = q.all()
    found: list[dict] = []

    def overlap(a, b):
        if a.module != b.module and "system" not in {a.module, b.module}: return False
        if a.scope_type == "global" or b.scope_type == "global": return True
        if a.scope_type == b.scope_type and a.scope_id == b.scope_id: return True
        if a.scope_type == "module" and a.scope_id in {b.module, "*"}: return True
        if b.scope_type == "module" and b.scope_id in {a.module, "*"}: return True
        return False

    active_keys = set()
    for i, left in enumerate(rows):
        ld = dict(left.desired_state_json or {})
        if not ld: continue
        for right in rows[i+1:]:
            rd = dict(right.desired_state_json or {})
            if not rd or not overlap(left, right): continue
            for field in sorted(set(ld).intersection(rd)):
                if ld[field] == rd[field]: continue
                # Explicit override permission lets the more-specific scope win.
                lrank, rrank = SCOPE_RANK.get(left.scope_type,0), SCOPE_RANK.get(right.scope_type,0)
                broad = left if lrank < rrank else right if rrank < lrank else None
                if broad is not None and bool((broad.source_json or {}).get("allow_override")):
                    continue
                pair = sorted((str(left.id), str(right.id)))
                active_keys.add((pair[0], pair[1], field))
                existing = db.query(ControlRuleConflict).filter(
                    ControlRuleConflict.left_requirement_id == pair[0],
                    ControlRuleConflict.right_requirement_id == pair[1],
                    ControlRuleConflict.field == field,
                ).first()
                lv = ld[field] if str(left.id) == pair[0] else rd[field]
                rv = rd[field] if str(right.id) == pair[1] else ld[field]
                if existing is None:
                    existing = ControlRuleConflict(id=str(uuid.uuid4()), left_requirement_id=pair[0], right_requirement_id=pair[1], field=field, left_value_json={"value":lv}, right_value_json={"value":rv}, state="open")
                    db.add(existing); db.flush()
                else:
                    existing.left_value_json={"value":lv}; existing.right_value_json={"value":rv}; existing.state="open"; existing.resolved_at=None
                found.append({"id": existing.id, "left_requirement_id": pair[0], "right_requirement_id": pair[1], "field": field, "left": lv, "right": rv, "state":"open"})
    # Resolve conflicts that no longer exist after version/scope/value changes.
    open_rows = db.query(ControlRuleConflict).filter(ControlRuleConflict.state == "open").all()
    for row in open_rows:
        key=(str(row.left_requirement_id),str(row.right_requirement_id),str(row.field))
        if key not in active_keys:
            row.state="resolved"; row.resolution="rules no longer conflict"; row.resolved_at=utcnow()
    return found


def initialize_execution_steps(db, execution: ControlExecution) -> list:
    """Materialize a durable execution plan into ordered steps exactly once."""
    from app.models.control_plane import ControlExecutionStep
    existing=db.query(ControlExecutionStep).filter(ControlExecutionStep.execution_id==execution.id).order_by(ControlExecutionStep.step_no).all()
    if existing: return existing
    plan=dict(execution.plan_json or {})
    steps=plan.get("steps") if isinstance(plan.get("steps"),list) else []
    if not steps:
        steps=["preflight","apply","technical_test","runtime_verify","business_verify"]
    rows=[]
    for n,item in enumerate(steps,1):
        if isinstance(item,dict): name=str(item.get("name") or item.get("id") or f"step_{n}"); input_json=item
        else: name=str(item); input_json={}
        row=ControlExecutionStep(execution_id=execution.id,step_no=n,name=name[:160],status="pending",input_json=input_json)
        db.add(row); rows.append(row)
    db.flush(); return rows


def record_execution_attempt(db, execution: ControlExecution, *, step_id: int | None, strategy: str, status: str,
                             error_code: str | None = None, error_message: str | None = None, result: dict | None = None):
    from app.models.control_plane import ControlExecutionAttempt
    q=db.query(ControlExecutionAttempt).filter(ControlExecutionAttempt.execution_id==execution.id)
    if step_id is None: q=q.filter(ControlExecutionAttempt.step_id.is_(None))
    else: q=q.filter(ControlExecutionAttempt.step_id==step_id)
    number=q.count()+1
    row=ControlExecutionAttempt(execution_id=execution.id,step_id=step_id,attempt_no=number,strategy=str(strategy or "default")[:96],status=str(status or "started")[:32],error_code=error_code,error_message=error_message,result_json=result or {},finished_at=utcnow() if status in {"pass","failed","blocked"} else None)
    db.add(row); db.flush(); return row


def record_runtime_verification(db, requirement: ControlRequirement, *, account_id: str | None, actual_state: dict,
                                passed: bool, source: str = "control_plane_guardian", bucket: str | None = None) -> ControlExecution:
    """Persist bounded continuous-verification evidence.

    One verification execution per requirement/version/hour prevents evidence
    explosion while ensuring a current PASS is never just an in-memory claim.
    """
    bucket = bucket or utcnow().strftime("%Y%m%d%H")
    idem = f"verify:{requirement.id}:v{requirement.version}:{account_id or '*'}:{bucket}"
    ex = start_execution(db, requirement, account_id=account_id,
                         plan={"kind":"runtime_verification","bucket":bucket,"steps":["read_actual_state","compare_desired_state","record_evidence"]},
                         idempotency_key=idem)
    if ex.status in TERMINAL:
        return ex
    initialize_execution_steps(db, ex)
    path=("parsed","mapped_to_requirements","planned","preflight","executing","technical_tested")
    for st in path:
        if st in EXECUTION_FLOW.get(str(ex.status),set()): transition_execution(db,ex,st)
    add_evidence(db,ex,level="L5",evidence_type="actual_state_readback",source=source,passed=passed,payload={"actual_state":actual_state,"desired_state":requirement.desired_state_json or {},"requirement_version":requirement.version})
    if "runtime_verified" in EXECUTION_FLOW.get(str(ex.status),set()): transition_execution(db,ex,"runtime_verified",actual_state=actual_state)
    if passed:
        transition_execution(db,ex,"pass",actual_state=actual_state)
    else:
        transition_execution(db,ex,"degraded",actual_state=actual_state,reason="desired state differs from actual state",error_code="CONFIG_DRIFT")
    return ex


def execute_requirement_once(db, requirement: ControlRequirement, *, account_id: str | None = None,
                             idempotency_key: str | None = None) -> ControlExecution:
    """Bounded desired-state reconciliation through one registered module adapter.

    Dependency gates, step state, attempts and strategy changes are durable. No
    operation retries forever. Only an explicit adapter apply capability can write.
    """
    from app.services.control_plane_adapters import get as get_adapter, adapter_verify
    from app.models.control_plane import ControlExecutionStep

    target_account = account_id or (requirement.scope_id if requirement.scope_type == "account" else None)
    plan = {
        "kind": "reconcile_execution",
        "steps": ["dependency_check", "preflight", "read_actual_state", "apply_if_needed", "runtime_verify"],
        "requirement_version": int(requirement.version or 1),
    }
    ex = start_execution(db, requirement, account_id=target_account, plan=plan, idempotency_key=idempotency_key)
    steps = initialize_execution_steps(db, ex)
    if ex.status in TERMINAL:
        return ex
    step_by_name={str(x.name):x for x in steps}

    def mark(name: str, status: str, *, output=None, error_code=None, error_message=None):
        row=step_by_name.get(name)
        if not row: return
        row.status=status
        if status=="running" and not row.started_at: row.started_at=utcnow()
        if output is not None: row.output_json=output
        if error_code: row.error_code=error_code
        if error_message: row.error_message=error_message
        if status in {"pass","blocked","failed","skipped"}: row.finished_at=utcnow()
        db.flush()

    def advance(st):
        if st in EXECUTION_FLOW.get(str(ex.status), set()):
            transition_execution(db, ex, st)

    advance("parsed"); advance("mapped_to_requirements"); advance("planned")

    # Dependencies: strings may be requirement UUIDs or keys. Dicts may mark an
    # external dependency, in which case waiting_external is a first-class state.
    mark("dependency_check","running")
    unmet=[]
    for dep in list(requirement.dependencies_json or []):
        external=False; token=None
        if isinstance(dep,dict):
            external=str(dep.get("type") or "").lower()=="external"
            token=dep.get("requirement_id") or dep.get("key")
            if external and not token:
                if dep.get("ready") is not True: unmet.append({"dependency":dep,"external":True})
                continue
        else: token=str(dep or "").strip()
        if not token: continue
        q=db.query(ControlRequirement).filter(or_(ControlRequirement.id==str(token),ControlRequirement.key==str(token))).first()
        if not q:
            unmet.append({"dependency":token,"reason":"requirement_not_found","external":external}); continue
        latest=db.query(ControlExecution).filter(ControlExecution.requirement_id==q.id).order_by(ControlExecution.created_at.desc()).first()
        runtime_evidence=0
        if latest:
            runtime_evidence=db.query(ControlEvidence).filter(ControlEvidence.execution_id==latest.id,ControlEvidence.passed.is_(True),ControlEvidence.level.in_(("L4","L5","L6"))).count()
        if not latest or latest.status!="pass" or runtime_evidence<=0:
            unmet.append({"dependency":str(q.id),"key":q.key,"latest_status":latest.status if latest else None,"runtime_evidence":runtime_evidence,"external":external})
    if unmet:
        is_external=any(x.get("external") for x in unmet)
        mark("dependency_check","blocked",output={"unmet":unmet},error_code="DEPENDENCY_NOT_READY")
        advance("preflight")
        transition_execution(db,ex,"waiting_external" if is_external else "blocked",reason="Execution dependencies are not verified",error_code="DEPENDENCY_NOT_READY")
        return ex
    mark("dependency_check","pass",output={"dependencies":len(requirement.dependencies_json or [])})

    advance("preflight")
    adapter = get_adapter(requirement.module)
    if adapter is None:
        mark("preflight","blocked",error_code="MODULE_ADAPTER_MISSING")
        record_execution_attempt(db, ex, step_id=step_by_name.get("preflight").id if step_by_name.get("preflight") else None, strategy="adapter_lookup", status="blocked", error_code="MODULE_ADAPTER_MISSING", error_message="No registered module adapter")
        transition_execution(db, ex, "blocked", reason="No registered module adapter", error_code="MODULE_ADAPTER_MISSING")
        return ex

    desired = dict(requirement.desired_state_json or {})
    if not desired:
        mark("preflight","blocked",error_code="DESIRED_STATE_EMPTY")
        record_execution_attempt(db, ex, step_id=step_by_name.get("preflight").id if step_by_name.get("preflight") else None, strategy="desired_state", status="blocked", error_code="DESIRED_STATE_EMPTY", error_message="Natural-language rule is stored but not yet compiled into executable desired state")
        transition_execution(db, ex, "blocked", reason="Rule has no executable desired state", error_code="DESIRED_STATE_EMPTY")
        return ex

    mark("preflight","running")
    if adapter.preflight:
        try:
            pre = adapter.preflight(db, target_account, requirement) or {"ok": True}
        except Exception as exc:
            pre = {"ok": False, "reason": f"preflight_error:{type(exc).__name__}"}
        if pre.get("ok") is False:
            reason = str(pre.get("reason") or "adapter preflight blocked")
            mark("preflight","blocked",output=pre,error_code="PREFLIGHT_BLOCKED",error_message=reason)
            record_execution_attempt(db, ex, step_id=step_by_name.get("preflight").id if step_by_name.get("preflight") else None, strategy="preflight", status="blocked", error_code="PREFLIGHT_BLOCKED", error_message=reason, result=pre)
            transition_execution(db, ex, "blocked", reason=reason, error_code="PREFLIGHT_BLOCKED")
            return ex
    mark("preflight","pass")

    advance("executing")
    mark("read_actual_state","running")
    try:
        actual_before = adapter.get_state(db, target_account) or {}
    except Exception as exc:
        mark("read_actual_state","failed",error_code="STATE_READ_FAILED",error_message=type(exc).__name__)
        transition_execution(db,ex,"failed",reason="Runtime state read failed",error_code="STATE_READ_FAILED")
        return ex
    mark("read_actual_state","pass",output={"actual_state":actual_before})
    drift = {k: {"desired": v, "actual": actual_before.get(k)} for k, v in desired.items() if actual_before.get(k) != v}
    apply_result=None
    if drift:
        if adapter.apply is None:
            add_evidence(db, ex, level="L5", evidence_type="actual_state_readback", source=f"adapter:{requirement.module}", passed=False, payload={"actual_state": actual_before, "desired_state": desired, "drift": drift})
            mark("apply_if_needed","blocked",output={"drift":drift},error_code="APPLY_NOT_IMPLEMENTED")
            record_execution_attempt(db, ex, step_id=step_by_name.get("apply_if_needed").id if step_by_name.get("apply_if_needed") else None, strategy="apply", status="blocked", error_code="APPLY_NOT_IMPLEMENTED", error_message="Module adapter is verify-only", result={"drift": drift})
            transition_execution(db, ex, "blocked", actual_state=actual_before, reason="Module adapter is verify-only; desired state differs from actual state", error_code="APPLY_NOT_IMPLEMENTED")
            return ex
        mark("apply_if_needed","running")
        strategies=("compare_and_set","refresh_actual_then_compare_and_set")
        apply_error=None
        for idx,strategy in enumerate(strategies,1):
            ex.attempt=idx; db.flush()
            try:
                apply_result=adapter.apply(db,target_account,requirement,drift) or {}
                record_execution_attempt(db,ex,step_id=step_by_name.get("apply_if_needed").id if step_by_name.get("apply_if_needed") else None,strategy=strategy,status="pass",result=apply_result)
                apply_error=None; break
            except RuntimeError as exc:
                apply_error=exc
                retryable=str(exc).startswith("concurrent_state_changed:")
                record_execution_attempt(db,ex,step_id=step_by_name.get("apply_if_needed").id if step_by_name.get("apply_if_needed") else None,strategy=strategy,status="failed",error_code="CONCURRENT_STATE_CHANGED" if retryable else "APPLY_FAILED",error_message=str(exc)[:500])
                if not retryable or idx>=len(strategies): break
                transition_execution(db,ex,"retrying",reason="Concurrent state changed; refreshing actual state",error_code="CONCURRENT_STATE_CHANGED")
                transition_execution(db,ex,"executing")
                refreshed=adapter.get_state(db,target_account) or {}
                drift={k:{"desired":v,"actual":refreshed.get(k)} for k,v in desired.items() if refreshed.get(k)!=v}
                if not drift:
                    apply_error=None; apply_result={"before":{},"after":{},"changed_fields":[]}; break
            except Exception as exc:
                apply_error=exc
                record_execution_attempt(db,ex,step_id=step_by_name.get("apply_if_needed").id if step_by_name.get("apply_if_needed") else None,strategy=strategy,status="failed",error_code="APPLY_FAILED",error_message=type(exc).__name__)
                break
        if apply_error is not None:
            mark("apply_if_needed","failed",error_code="APPLY_FAILED",error_message=str(apply_error)[:500])
            transition_execution(db, ex, "failed", reason=f"adapter apply failed: {type(apply_error).__name__}", error_code="APPLY_FAILED")
            return ex
        mark("apply_if_needed","pass",output={"result":apply_result or {}})
    else:
        mark("apply_if_needed","skipped",output={"reason":"already_matches_desired"})

    advance("technical_tested")
    mark("runtime_verify","running")
    verification = adapter_verify(db, adapter, target_account, requirement, desired=desired)
    actual_after = dict(verification.get("actual_state") or {})
    remaining = dict(verification.get("remaining_drift") or {})
    verified = bool(verification.get("passed")) and not remaining
    add_evidence(db, ex, level="L5", evidence_type="actual_state_readback", source=f"adapter:{requirement.module}", passed=verified,
                 payload={"actual_state": actual_after, "desired_state": desired, "remaining_drift": remaining,"attempt":int(ex.attempt or 0),"verification":verification})
    advance("runtime_verified")
    if not verified:
        rollback_result = None
        if apply_result is not None and adapter.rollback is not None:
            try:
                rollback_result = adapter.rollback(db, target_account, requirement, apply_result) or {}
                record_execution_attempt(db, ex, step_id=step_by_name.get("runtime_verify").id if step_by_name.get("runtime_verify") else None, strategy="rollback_after_failed_verify", status="pass", result=rollback_result)
                add_evidence(db, ex, level="L5", evidence_type="rollback_readback", source=f"adapter:{requirement.module}", passed=True, payload={"rollback":rollback_result})
            except Exception as rollback_exc:
                record_execution_attempt(db, ex, step_id=step_by_name.get("runtime_verify").id if step_by_name.get("runtime_verify") else None, strategy="rollback_after_failed_verify", status="failed", error_code="ROLLBACK_FAILED", error_message=type(rollback_exc).__name__)
                open_incident(db, incident_type="CONTROL_ROLLBACK_FAILED", symptom="Control Plane write verification failed and rollback could not be confirmed", module=requirement.module, account_id=target_account, requirement_id=requirement.id, execution_id=ex.id, priority="P0", details={"remaining_drift":remaining,"error_type":type(rollback_exc).__name__})
        mark("runtime_verify","failed",output={"remaining_drift":remaining,"rollback":rollback_result},error_code="CONFIG_DRIFT")
        transition_execution(db, ex, "degraded", actual_state=actual_after, reason="Runtime readback still differs from desired state" + ("; rolled back" if rollback_result is not None else ""), error_code="CONFIG_DRIFT")
    else:
        mark("runtime_verify","pass",output={"actual_state":actual_after})
        transition_execution(db, ex, "pass", actual_state=actual_after)
    return ex



def sync_marketing_baseline_from_actual(db, account_id: str, *, reason: str, source_ref: str | None = None, fields: dict | None = None) -> ControlRequirement | None:
    """Version the imported per-account marketing baseline after a canonical domain write.

    This compatibility bridge prevents old/UI writers from bypassing Control Plane.
    It updates only the imported production baseline, never owner-created rules.
    """
    key=f"production.marketing.autopilot.{account_id}"[:160]
    row=db.query(ControlRequirement).filter(ControlRequirement.key==key).first()
    if row is None:
        return None
    src=dict(row.source_json or {})
    if src.get("type")!="legacy_production_import":
        return row
    desired=dict(row.desired_state_json or {})
    changed=False
    for k,v in dict(fields or {}).items():
        if k in {"autopilot_mode","target_leads_per_day","daily_budget_limit_rub","max_cost_per_lead_rub","hard_max_bid_rub"} and v is not None and desired.get(k)!=v:
            desired[k]=v;changed=True
    if not changed:
        return row
    src.update({"authoritative_after_import":True,"last_sync_source":"canonical_domain_write","last_sync_ref":source_ref})
    update_requirement(db,row,{"desired_state_json":desired,"source_json":src},reason=reason)
    return row

def resolve_owner_instruction(db, *, instruction_id: str, target_requirement_id: str, action: str,
                              patch: dict | None = None, changed_by: int | None = None, reason: str = "owner instruction resolution") -> dict:
    """Bind an ambiguous correction/cancellation to one canonical requirement.

    No fuzzy guessing is allowed here. The caller names the exact requirement;
    cancellation is versioned and history-preserving; correction requires an
    explicit structured patch before the instruction can be considered resolved.
    """
    from app.models.control_plane import ControlOwnerInstruction
    ins = db.query(ControlOwnerInstruction).filter(ControlOwnerInstruction.id == instruction_id).first()
    if not ins:
        raise ValueError("instruction_not_found")
    req = db.query(ControlRequirement).filter(ControlRequirement.id == target_requirement_id).first()
    if not req:
        raise ValueError("requirement_not_found")
    if bool((req.source_json or {}).get("immutable")):
        raise ValueError("immutable_requirement")
    action = str(action or "").lower().strip()
    if action == "cancel":
        update_requirement(db, req, {"status": "cancelled"}, changed_by=changed_by, reason=reason or "owner cancellation")
    elif action == "correct":
        if not patch:
            raise ValueError("structured_patch_required")
        update_requirement(db, req, patch, changed_by=changed_by, reason=reason or "owner correction")
    elif action == "supersede":
        if not patch or not patch.get("superseded_by"):
            raise ValueError("superseded_by_required")
        update_requirement(db, req, {"status": "superseded", "superseded_by": patch["superseded_by"]}, changed_by=changed_by, reason=reason or "owner supersede")
    else:
        raise ValueError("invalid_resolution_action")
    linked = list(ins.linked_requirement_ids_json or [])
    if req.id not in linked:
        linked.append(req.id)
    ins.linked_requirement_ids_json = linked
    ins.status = "resolved"
    comp = dict(ins.compiler_json or {})
    comp["resolution"] = {"action": action, "requirement_id": req.id, "resolved_at": utcnow().isoformat(), "reason": reason}
    ins.compiler_json = comp
    db.flush()
    record_event(db, "control_plane", "owner_instruction.resolved", f"{action}: {req.title}", severity="info",
                 account_id=(req.scope_id if req.scope_type == "account" else None),
                 details={"instruction_id": ins.id, "requirement_id": req.id, "action": action, "version": req.version})
    return {"instruction_id": ins.id, "status": ins.status, "action": action, "requirement": serialize_requirement(req)}
