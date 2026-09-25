# -*- coding: utf-8 -*-
"""Canonical BORIS architecture registry validation.

The runtime source of truth is the existing Control Plane:
- control_requirements/control_requirement_versions = Rule Registry
- control_module_contracts = Module Contracts + dependency graph
- control_executions/control_execution_steps/control_evidence = Runtime Ledger

This module deliberately does not create a parallel registry.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import ast
from pathlib import Path

from app.models.control_plane import (
    ControlActionOwner,
    ControlActionOwnerVersion,
    ControlEvidence,
    ControlExecution,
    ControlModuleContract,
    ControlRequirement,
    ControlRequirementVersion,
)
from app.services.operation_contract import OperationState


ROOT = Path(__file__).resolve().parents[2]

def _canonical_entrypoint_exists(value: str) -> bool:
    module, sep, function = str(value or "").partition(":")
    if not sep or not module or not function:
        return False
    path = ROOT / (module.replace(".", "/") + ".py")
    if not path.exists():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except Exception:
        return False
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function for node in tree.body)


CONSTITUTION_KEYS = tuple(
    f"architecture.const.{n:02d}." + suffix
    for n, suffix in (
        (1, "single_source_truth"),
        (2, "single_action_owner"),
        (3, "error_observable"),
        (4, "idempotent_operations"),
        (5, "tenant_isolation"),
        (6, "versioned_rules"),
        (7, "evidence_required"),
        (8, "single_source_writer"),
        (9, "guardian_runtime_only"),
        (10, "regression_test_required"),
    )
)
CANONICAL_STATES = tuple(state.value for state in OperationState)
REQUIRED_MODULES = frozenset({
    "analytics", "billing", "crm", "direct", "email", "feed_factory", "marketing",
    "messages", "mop", "reactivation", "rop", "sitebuild", "social", "telegram",
    "telephony", "calltracking", "prospecting", "crowd_seo", "control_plane", "development",
})
REQUIRED_CONTRACT_FIELDS = frozenset({
    "contract_version", "writer", "source_of_truth", "input", "output", "states",
    "idempotency", "retry", "dependencies", "done_proof", "mandatory_tests", "tenant_isolation",
})
REQUIRED_ACTIONS = frozenset({
    "AVITO_BID_WRITE", "AVITO_BUDGET_WRITE", "AVITO_PROMOTION_WRITE", "AVITO_PUBLISH",
    "AVITO_UNPUBLISH", "MESSAGE_SEND", "EMAIL_SEND", "PHONE_PROVIDER_COMMAND",
    "PHONE_RECORDING_FETCH", "PHONE_PUSH_SEND", "CRM_DEAL_CREATE", "CRM_STATE_WRITE",
    "CRM_TASK_WRITE", "FEED_PUBLISH", "SOCIAL_PUBLISH", "SOCIAL_DELETE",
    "BILLING_CHARGE", "BILLING_USAGE_WRITE", "DEPLOY_RELEASE", "DB_SCHEMA_CHANGE",
})
REQUIRED_BUSINESS_RULE_FAMILY_COUNTS = {
    "marketing": 22,
    "messages": 16,
    "crm": 12,
    "telephony": 15,
    "email": 10,
    "feed_factory": 14,
    "social": 10,
    "prospecting": 10,
    "billing": 6,
}
REQUIRED_BUSINESS_RULE_COUNT = sum(REQUIRED_BUSINESS_RULE_FAMILY_COUNTS.values())
REQUIRED_RUNTIME_LEDGER_MODULES = frozenset({"marketing", "mop", "email", "telephony", "feed_factory", "social", "prospecting"})
REQUIRED_DEPENDENCY_CLOSURES = {
    "crm": {"crm", "mop", "rop", "telephony", "calltracking"},
    "messages": {"messages", "mop", "crm", "reactivation"},
    "marketing": {"marketing", "feed_factory"},
    "telephony": {"telephony", "crm", "calltracking", "mop", "rop"},
    "billing": {"billing", "telephony", "email", "marketing", "social"},
}


def _contract_doc(row) -> dict:
    if isinstance(row, dict):
        return dict(row)
    return dict(getattr(row, "contract_json", None) or {})


def validate_contract_documents(contracts: dict[str, dict]) -> dict:
    errors: list[dict] = []
    names = set(contracts)
    missing_modules = sorted(REQUIRED_MODULES - names)
    if missing_modules:
        errors.append({"code": "missing_module_contracts", "modules": missing_modules})

    for module, raw in sorted(contracts.items()):
        doc = _contract_doc(raw)
        missing = sorted(REQUIRED_CONTRACT_FIELDS - set(doc))
        if missing:
            errors.append({"code": "contract_fields_missing", "module": module, "fields": missing})
            continue
        if int(doc.get("contract_version") or 0) < 2:
            errors.append({"code": "contract_version_old", "module": module})
        if not str(doc.get("writer") or "").strip():
            errors.append({"code": "writer_missing", "module": module})
        if not str(doc.get("source_of_truth") or "").strip():
            errors.append({"code": "source_of_truth_missing", "module": module})
        states = tuple(doc.get("states") or ())
        if states != CANONICAL_STATES:
            errors.append({"code": "operation_states_mismatch", "module": module, "states": list(states)})
        dependencies = doc.get("dependencies") or []
        if not isinstance(dependencies, list):
            errors.append({"code": "dependencies_not_list", "module": module})
            dependencies = []
        unknown = sorted(set(str(x) for x in dependencies) - names)
        if unknown:
            errors.append({"code": "unknown_dependencies", "module": module, "dependencies": unknown})
        tests = doc.get("mandatory_tests") or []
        if not isinstance(tests, list) or not tests:
            errors.append({"code": "mandatory_tests_missing", "module": module})
        if not str(doc.get("done_proof") or "").strip():
            errors.append({"code": "done_proof_missing", "module": module})
    return {"ok": not errors, "errors": errors, "modules": len(contracts)}


def validate_action_owners(action_owners: dict[str, object], contracts: dict[str, dict]) -> dict:
    """Validate canonical runtime side-effect ownership without duplicating workstream file ownership."""
    errors: list[dict] = []
    names = set(action_owners)
    missing = sorted(REQUIRED_ACTIONS - names)
    if missing:
        errors.append({"code": "action_owners_missing", "actions": missing})
    for action_key, row in sorted(action_owners.items()):
        owner_module = str(getattr(row, "owner_module", "") or "")
        owner_workstream = str(getattr(row, "owner_workstream", "") or "")
        owner_service = str(getattr(row, "owner_service", "") or "")
        entrypoint = str(getattr(row, "canonical_entrypoint", "") or "")
        source_of_truth = str(getattr(row, "source_of_truth", "") or "")
        states = tuple(getattr(row, "terminal_states_json", None) or ())
        source = dict(getattr(row, "source_json", None) or {})
        if owner_module not in contracts:
            errors.append({"code": "action_owner_unknown_module", "action": action_key, "module": owner_module})
        if not owner_workstream:
            errors.append({"code": "action_owner_workstream_missing", "action": action_key})
        if not owner_service:
            errors.append({"code": "action_owner_service_missing", "action": action_key})
        if not entrypoint or ":" not in entrypoint:
            errors.append({"code": "action_owner_entrypoint_invalid", "action": action_key, "entrypoint": entrypoint})
        elif not _canonical_entrypoint_exists(entrypoint):
            errors.append({"code": "action_owner_entrypoint_missing", "action": action_key, "entrypoint": entrypoint})
        if not source_of_truth:
            errors.append({"code": "action_owner_source_missing", "action": action_key})
        if states != CANONICAL_STATES:
            errors.append({"code": "action_owner_states_mismatch", "action": action_key, "states": list(states)})
        if getattr(row, "external_side_effect", False):
            if not getattr(row, "idempotency_required", False):
                errors.append({"code": "action_owner_idempotency_not_required", "action": action_key})
            if not getattr(row, "runtime_ledger_required", False):
                errors.append({"code": "action_owner_ledger_not_required", "action": action_key})
            if not getattr(row, "proof_required", False):
                errors.append({"code": "action_owner_proof_not_required", "action": action_key})
        if getattr(row, "money_side_effect", False) and not getattr(row, "tenant_scope_required", False):
            errors.append({"code": "money_action_not_tenant_scoped", "action": action_key})
        if source.get("source_of_truth") != "control_plane":
            errors.append({"code": "action_owner_registry_source_mismatch", "action": action_key})
    return {"ok": not errors, "errors": errors, "actions": len(action_owners), "required_actions": len(REQUIRED_ACTIONS)}


def validate_business_rules(rows: list[object], contracts: dict[str, dict], action_owners: dict[str, object]) -> dict:
    errors: list[dict] = []
    family_counts = {name: 0 for name in REQUIRED_BUSINESS_RULE_FAMILY_COUNTS}
    seen_rule_ids: set[str] = set()
    seen_scopes: set[tuple[str, str, str]] = set()
    for row in rows:
        module = str(getattr(row, "module", "") or "")
        key = str(getattr(row, "key", "") or "")
        source = dict(getattr(row, "source_json", None) or {})
        desired = dict(getattr(row, "desired_state_json", None) or {})
        if source.get("catalog") != "architecture_business_rule_v1":
            continue
        if module in family_counts:
            family_counts[module] += 1
        rule_id = str(source.get("rule_id") or desired.get("rule_id") or "")
        operation = str(source.get("operation") or desired.get("operation") or "")
        tests = source.get("mandatory_tests") or []
        owner = str(source.get("owner") or "")
        scope = (module, str(getattr(row, "scope_type", "") or ""), str(getattr(row, "scope_id", "") or ""))
        if not rule_id:
            errors.append({"code": "business_rule_id_missing", "key": key})
        elif rule_id in seen_rule_ids:
            errors.append({"code": "business_rule_id_duplicate", "key": key, "rule_id": rule_id})
        seen_rule_ids.add(rule_id)
        logical_scope = (key, scope[1], scope[2])
        if logical_scope in seen_scopes:
            errors.append({"code": "business_rule_logical_scope_duplicate", "key": key, "scope": list(logical_scope[1:])})
        seen_scopes.add(logical_scope)
        if module not in contracts:
            errors.append({"code": "business_rule_unknown_module", "key": key, "module": module})
        elif owner != str(contracts[module].get("writer") or ""):
            errors.append({"code": "business_rule_owner_mismatch", "key": key, "owner": owner, "expected": contracts[module].get("writer")})
        if source.get("source_of_truth") != "control_plane":
            errors.append({"code": "business_rule_source_mismatch", "key": key})
        if not operation:
            errors.append({"code": "business_rule_operation_missing", "key": key})
        if tuple(desired.get("allowed_states") or ()) != CANONICAL_STATES:
            errors.append({"code": "business_rule_states_mismatch", "key": key})
        if not isinstance(tests, list) or not tests:
            errors.append({"code": "business_rule_tests_missing", "key": key})
        external = bool(source.get("external_side_effect"))
        money = bool(source.get("money_side_effect"))
        if external and operation not in action_owners:
            errors.append({"code": "business_rule_action_owner_missing", "key": key, "operation": operation})
        if money and not tests:
            errors.append({"code": "money_rule_tests_missing", "key": key})
        if str(source.get("tenant_scope") or "") != "account_required":
            errors.append({"code": "business_rule_tenant_scope_missing", "key": key})
    for family, required in REQUIRED_BUSINESS_RULE_FAMILY_COUNTS.items():
        actual = family_counts.get(family, 0)
        if actual < required:
            errors.append({"code": "business_rule_family_incomplete", "module": family, "actual": actual, "required": required})
    total = sum(family_counts.values())
    if total < REQUIRED_BUSINESS_RULE_COUNT:
        errors.append({"code": "business_rule_catalog_incomplete", "actual": total, "required": REQUIRED_BUSINESS_RULE_COUNT})
    return {"ok": not errors, "errors": errors, "rules": total, "required_rules": REQUIRED_BUSINESS_RULE_COUNT, "family_counts": family_counts}


def dependency_closure(contracts: dict[str, dict], changed_modules: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """Return all modules whose contracts directly or transitively depend on a change."""
    reverse: dict[str, set[str]] = {name: set() for name in contracts}
    for module, raw in contracts.items():
        for dependency in _contract_doc(raw).get("dependencies") or []:
            if dependency in reverse:
                reverse[dependency].add(module)
    seen = {m for m in changed_modules if m in contracts}
    queue = deque(sorted(seen))
    while queue:
        changed = queue.popleft()
        for dependent in sorted(reverse.get(changed, ())):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return sorted(seen)


def mandatory_tests_for_modules(contracts: dict[str, dict], modules: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    tests: set[str] = set()
    for module in dependency_closure(contracts, modules):
        tests.update(str(x) for x in (_contract_doc(contracts[module]).get("mandatory_tests") or []) if str(x).strip())
    return sorted(tests)


def validate_runtime_ledger_coverage(db, *, freshness_seconds: int = 7200) -> dict:
    """Require plan/fact obligations and fresh L5 evidence for every mandated module."""
    now = datetime.now(timezone.utc)
    requirements = db.query(ControlRequirement).filter(
        ControlRequirement.status == "active",
        ControlRequirement.source_json["catalog"].astext == "runtime_ledger_obligation_v1",
    ).all()
    by_module = {str(row.module): row for row in requirements}
    errors: list[dict] = []
    modules: dict[str, dict] = {}
    for module in sorted(REQUIRED_RUNTIME_LEDGER_MODULES):
        req = by_module.get(module)
        if req is None:
            errors.append({"code": "runtime_ledger_obligation_missing", "module": module})
            modules[module] = {"ok": False, "reason": "obligation_missing"}
            continue
        execution = db.query(ControlExecution).filter(
            ControlExecution.requirement_id == req.id,
        ).order_by(ControlExecution.created_at.desc()).first()
        if execution is None:
            errors.append({"code": "runtime_ledger_execution_missing", "module": module})
            modules[module] = {"ok": False, "reason": "execution_missing"}
            continue
        created = execution.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = int((now - created).total_seconds()) if created else 10**9
        evidence_count = db.query(ControlEvidence).filter(
            ControlEvidence.execution_id == execution.id,
            ControlEvidence.level.in_(("L4", "L5", "L6")),
            ControlEvidence.evidence_type == "runtime_plan_fact",
            ControlEvidence.passed.is_(True),
        ).count()
        actual = dict(execution.actual_state_json or {})
        plan = dict(execution.plan_json or {})
        required_plan = {"obligation_id", "module", "operation", "plan_date", "plan_quantity", "due_at", "owner"}
        required_fact = {"attempted_quantity", "succeeded_quantity", "failed_quantity", "waiting_quantity", "state", "reason"}
        missing_plan = sorted(required_plan - set(plan))
        missing_fact = sorted(required_fact - set(actual))
        ok = age <= freshness_seconds and evidence_count > 0 and not missing_plan and not missing_fact
        if age > freshness_seconds:
            errors.append({"code": "runtime_ledger_snapshot_stale", "module": module, "age_seconds": age})
        if evidence_count <= 0:
            errors.append({"code": "runtime_ledger_evidence_missing", "module": module})
        if missing_plan:
            errors.append({"code": "runtime_ledger_plan_incomplete", "module": module, "missing": missing_plan})
        if missing_fact:
            errors.append({"code": "runtime_ledger_fact_incomplete", "module": module, "missing": missing_fact})
        modules[module] = {
            "ok": ok,
            "execution_id": execution.id,
            "execution_status": execution.status,
            "age_seconds": age,
            "evidence_count": evidence_count,
            "fact_state": actual.get("state"),
        }
    return {
        "ok": not errors,
        "errors": errors,
        "modules": modules,
        "covered_modules": sum(1 for row in modules.values() if row.get("ok")),
        "required_modules": len(REQUIRED_RUNTIME_LEDGER_MODULES),
    }


def validate_control_architecture(db) -> dict:
    requirements = {
        row.key: row
        for row in db.query(ControlRequirement)
        .filter(ControlRequirement.key.in_(CONSTITUTION_KEYS), ControlRequirement.status == "active")
        .all()
    }
    errors: list[dict] = []
    missing_rules = sorted(set(CONSTITUTION_KEYS) - set(requirements))
    if missing_rules:
        errors.append({"code": "constitution_rules_missing", "keys": missing_rules})

    for key, row in requirements.items():
        source = dict(row.source_json or {})
        desired = dict(row.desired_state_json or {})
        if row.requirement_type != "architecture_rule":
            errors.append({"code": "wrong_rule_type", "key": key, "type": row.requirement_type})
        if source.get("source_of_truth") != "control_plane":
            errors.append({"code": "rule_source_of_truth_mismatch", "key": key})
        if not desired.get("enforced"):
            errors.append({"code": "rule_not_enforced", "key": key})
        history = db.query(ControlRequirementVersion).filter(
            ControlRequirementVersion.requirement_id == row.id,
            ControlRequirementVersion.version == row.version,
        ).count()
        if history != 1:
            errors.append({"code": "rule_version_history_missing", "key": key, "version": row.version, "count": history})

    contract_rows = db.query(ControlModuleContract).filter(ControlModuleContract.enabled.is_(True)).all()
    contracts = {row.module: dict(row.contract_json or {}) for row in contract_rows}
    contract_result = validate_contract_documents(contracts)
    errors.extend(contract_result["errors"])
    dependency_coverage = {}
    for changed_module, required_modules in REQUIRED_DEPENDENCY_CLOSURES.items():
        closure = set(dependency_closure(contracts, [changed_module]))
        missing_modules = sorted(required_modules - closure)
        tests = mandatory_tests_for_modules(contracts, [changed_module])
        dependency_coverage[changed_module] = {
            "closure": sorted(closure),
            "required": sorted(required_modules),
            "mandatory_tests": tests,
            "ok": not missing_modules and bool(tests),
        }
        if missing_modules:
            errors.append({"code": "dependency_closure_incomplete", "module": changed_module, "missing": missing_modules})
        if not tests:
            errors.append({"code": "dependency_mandatory_tests_missing", "module": changed_module})

    action_rows = db.query(ControlActionOwner).filter(ControlActionOwner.enabled.is_(True)).all()
    action_owners = {row.action_key: row for row in action_rows}
    action_result = validate_action_owners(action_owners, contracts)
    errors.extend(action_result["errors"])
    for action_key, row in action_owners.items():
        history = db.query(ControlActionOwnerVersion).filter(
            ControlActionOwnerVersion.action_key == action_key,
            ControlActionOwnerVersion.version == row.version,
        ).count()
        if history != 1:
            errors.append({"code": "action_owner_version_history_missing", "action": action_key, "version": row.version, "count": history})

    business_rows = db.query(ControlRequirement).filter(
        ControlRequirement.status == "active",
        ControlRequirement.requirement_type == "business_rule",
    ).all()
    business_result = validate_business_rules(business_rows, contracts, action_owners)
    errors.extend(business_result["errors"])
    for row in business_rows:
        source = dict(row.source_json or {})
        if source.get("catalog") != "architecture_business_rule_v1":
            continue
        history = db.query(ControlRequirementVersion).filter(
            ControlRequirementVersion.requirement_id == row.id,
            ControlRequirementVersion.version == row.version,
        ).count()
        if history != 1:
            errors.append({"code": "business_rule_version_history_missing", "key": row.key, "version": row.version, "count": history})

    runtime_ledger_result = validate_runtime_ledger_coverage(db)
    errors.extend(runtime_ledger_result["errors"])

    return {
        "ok": not errors,
        "errors": errors,
        "constitution_rules": len(requirements),
        "module_contracts": len(contracts),
        "action_owners": len(action_owners),
        "required_action_owners": len(REQUIRED_ACTIONS),
        "business_rules": business_result["rules"],
        "required_business_rules": business_result["required_rules"],
        "business_rule_family_counts": business_result["family_counts"],
        "dependency_graph_coverage": dependency_coverage,
        "runtime_ledger": {
            "requirements": "control_requirements",
            "executions": "control_executions",
            "steps": "control_execution_steps",
            "evidence": "control_evidence",
            "coverage_ok": runtime_ledger_result["ok"],
            "covered_modules": runtime_ledger_result["covered_modules"],
            "required_modules": runtime_ledger_result["required_modules"],
            "modules": runtime_ledger_result["modules"],
        },
    }
