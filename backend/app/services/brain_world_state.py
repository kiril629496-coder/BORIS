"""Canonical BORIS System Brain world-state projection.

Read-only projection over requirements, adapters, incidents and executions.
It is deliberately deterministic: factual state comes from DB/runtime adapters,
never from an LLM. This is the single owner-facing snapshot used by Brain APIs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json

from app.models.control_plane import ControlExecution, ControlIncident, ControlModuleContract, ControlRequirement
from app.models.storage import Storage
from app.services.control_plane import effective_config, REGISTRY_ONLY_REQUIREMENT_TYPES
from app.services.control_plane_adapters import get as get_adapter, adapter_capabilities, adapter_diagnose


# CLIENT_WORLD_ACCOUNT_SCOPE_V1: these adapters are global infrastructure
# surfaces today: their state cannot be attributed to one client account.
# Never project a global email/Telegram/sitebuild incident into every selected
# client. Global owner cockpit still sees them when account_id is None.
GLOBAL_ONLY_MODULES = {"email", "telegram", "sitebuild"}


def _iso(value):
    return value.isoformat() if value is not None else None


def _scope_mop_diagnosis(actual: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Do not let a managed human handoff hide a separate provider outage."""
    waiting=int(actual.get("waiting_external") or 0)
    if waiting>0 and str(diagnosis.get("classification") or "")=="MOP_HUMAN_ESCALATION_MANAGED":
        return {
            "classification":"MOP_WAITING_AI_PROVIDER",
            "health":"waiting_external",
            "dependency_state":"waiting_external",
            "confidence":1.0,
            "evidence_for":[{
                "waiting_external":waiting,
                "managed_human_required":int(actual.get("managed_human_required") or 0),
            }],
            "safe_action":"resume_when_provider_available",
            "owner_action_required":False,
        }
    return diagnosis


def _scope_marketing_diagnosis(db, account_id: str, actual: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Align owner-facing Brain truth with actual money guards and wallet funding."""
    target=float(actual.get("target_leads_per_day") or 0)
    leads=float(actual.get("actual_leads") or 0)
    mode=str(actual.get("autopilot_mode") or "")
    reason=str(actual.get("runtime_money_block_reason") or "")
    runtime_mode=str(actual.get("runtime_mode") or "")
    owner_confirmed=bool(actual.get("daily_budget_owner_confirmed"))
    money_owner_reasons={
        "daily_budget_owner_provenance_missing",
        "daily_budget_owner_authority_missing",
        "daily_budget_missing_unconfirmed",
        "budget_missing_unconfirmed_existing_spend",
    }
    money_policy_wait=bool(
        target>0 and leads<target and mode in {"goal_auto","always_auto"}
        and (reason in money_owner_reasons or ("WAITING_OWNER:" in runtime_mode and not owner_confirmed))
    )

    funding={}
    row=(db.query(Storage)
         .filter(Storage.account_id==str(account_id),Storage.key=="guardian_balance_funding_health")
         .order_by(Storage.id.desc()).first())
    if row:
        try: funding=json.loads(row.value or "{}")
        except Exception: funding={}
    funding_wait=bool(
        target>0 and leads<target
        and funding.get("owner_action_required")
        and str(funding.get("reason") or "")=="avito_balance_below_one_red_cpl"
    )

    if money_policy_wait or funding_wait:
        owner_actions=[]
        if money_policy_wait:
            text=runtime_mode.removeprefix("WAITING_OWNER:").strip() if runtime_mode.startswith("WAITING_OWNER:") else ""
            owner_actions.append(text or "Подтвердите денежный лимит в настройках аккаунта.")
        if funding_wait:
            try: real=float(funding.get("real_balance_rub") or 0)
            except Exception: real=0.0
            try: need=float(funding.get("max_cpl_rub") or actual.get("max_cost_per_lead_rub") or 0)
            except Exception: need=0.0
            owner_actions.append(
                f"Пополните реальный баланс Avito минимум до {need:.0f} ₽; сейчас {real:.0f} ₽."
                if need>0 else
                f"Пополните реальный баланс Avito; сейчас {real:.0f} ₽."
            )
        return {
            "classification":"KPI_WAITING_OWNER_DEPENDENCY",
            "health":"degraded",
            "dependency_state":"waiting_owner",
            "confidence":1.0,
            "evidence_for":[{
                "target":target,
                "actual":leads,
                "daily_budget_limit_rub":actual.get("daily_budget_limit_rub"),
                "daily_budget_policy_state":actual.get("daily_budget_policy_state"),
                "daily_budget_policy_source":actual.get("daily_budget_policy_source"),
                "daily_budget_owner_confirmed":owner_confirmed,
                "runtime_money_block_reason":reason,
                "real_balance_rub":funding.get("real_balance_rub"),
                "funding_required":funding_wait,
                "funding_resume_condition":funding.get("resume_condition"),
            }],
            "safe_action":"surface_owner_policy_and_continue_nonmoney_recovery",
            "owner_action_required":True,
            "owner_action_text":" ".join(x for x in owner_actions if x).strip(),
        }
    return diagnosis


def _scope_social_diagnosis(db, account_id: str, actual: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Prevent global social heartbeat/SLA details from leaking into another client."""
    rows=(db.query(Storage).filter(Storage.account_id==str(account_id),Storage.key=="posting_projects").all())
    active_names=set(); project_count=0
    for row in rows:
        try: data=json.loads(row.value or "[]")
        except Exception: data=[]
        if not isinstance(data,list):
            continue
        project_count+=sum(1 for item in data if isinstance(item,dict))
        for item in data:
            if not isinstance(item,dict) or str(item.get("mode") or "auto")=="off":
                continue
            name=str(item.get("name") or item.get("title") or item.get("project_name") or "").strip()
            if name: active_names.add(name)
    if not active_names:
        if project_count>0:
            return {"classification":"SOCIAL_CONFIGURED_BUT_DISABLED","health":"pass","confidence":1.0,"evidence_for":[{"projects":project_count}],"safe_action":None,"owner_action_required":False}
        # SOCIAL_PAID_PROJECT_GAP_OWNER_ACTION_V1: compatibility marker retained;
        # V2 below intentionally removes owner-as-operator and uses external wait + auto-resume.
        # SOCIAL_PAID_PROJECT_GAP_EXTERNAL_WAIT_V2: paid Social stays visible
        # before destination onboarding is complete, but the owner is not a
        # runtime operator. BORIS prepares a durable scaffold and auto-resumes
        # when real client/platform destinations appear.
        subrow=(db.query(Storage).filter(Storage.account_id==str(account_id),Storage.key=="posting_subscription")
                .order_by(Storage.id.desc()).first())
        try: subscription=json.loads(subrow.value or "{}") if subrow else {}
        except Exception: subscription={}
        from app.services.social_entitlement import normalize_posting_subscription, posting_subscription_active
        subscription,_=normalize_posting_subscription(subscription)
        if posting_subscription_active(subscription):
            scaffold_row=(db.query(Storage).filter(
                Storage.account_id==str(account_id),
                Storage.key=="posting_onboarding_scaffold_v1",
            ).order_by(Storage.id.desc()).first())
            try: scaffold=json.loads(scaffold_row.value or "{}") if scaffold_row else {}
            except Exception: scaffold={}
            platforms=str(subscription.get("platforms") or "both").lower()
            required=list((scaffold or {}).get("required_external_fields") or [])
            if not required:
                if platforms in {"tg","both"}: required.append("channel_tg")
                if platforms in {"vk","both"}: required.append("vk_owner_id")
            missing=list((scaffold or {}).get("missing_external_fields") or required)
            return {
                "classification":"SOCIAL_SUBSCRIPTION_PROJECT_MISSING",
                "health":"waiting_external",
                "dependency_state":"waiting_external",
                "confidence":1.0,
                "evidence_for":[{
                    "plan":subscription.get("plan"),
                    "platforms":subscription.get("platforms"),
                    "paid_at":subscription.get("paid_at"),
                    "period_days":subscription.get("period_days"),
                    "required_external_fields":required,
                    "missing_external_fields":missing,
                    "onboarding_scaffold":"posting_onboarding_scaffold_v1",
                    "auto_resume":"materialize_project_when_destinations_present",
                }],
                "safe_action":"wait_for_external_destinations_then_auto_resume",
                "owner_action_required":False,
            }
        return {"classification":"SOCIAL_NOT_CONFIGURED_FOR_ACCOUNT","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None,"owner_action_required":False}
    if str(diagnosis.get("classification") or "")=="SOCIAL_DELIVERY_SLA_BREACH":
        evidence=[x for x in (diagnosis.get("evidence_for") or []) if str((x or {}).get("project") or "").strip() in active_names]
        if not evidence:
            return {"classification":"SOCIAL_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None,"owner_action_required":False}
        diagnosis=dict(diagnosis);diagnosis["evidence_for"]=evidence
    return diagnosis


def build_module_state(db, module: str, account_id: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    adapter = get_adapter(module)
    contract = db.query(ControlModuleContract).filter(ControlModuleContract.module == module).first()
    rules_q = db.query(ControlRequirement).filter(ControlRequirement.status == "active", ControlRequirement.module == module)
    if account_id:
        rules_q = rules_q.filter(
            (ControlRequirement.scope_type == "global") |
            ((ControlRequirement.scope_type == "account") & (ControlRequirement.scope_id == str(account_id))) |
            ((ControlRequirement.scope_type == "module") & (ControlRequirement.scope_id.in_((module, "*"))))
        )
    rules = rules_q.all()
    # GLOBAL_PROJECTION_SCOPE_V1: a global cockpit snapshot has no single
    # account actual-state to compare with account-scoped desired rules. Those
    # rules are verified in their own account projections/guardian executions.
    # Do not merge 15 account configurations into one fictional module state.
    projection_rules = rules if account_id else [r for r in rules if r.scope_type != "account"]
    # REGISTRY_METADATA_NOT_BUSINESS_STATE_V1: architecture/business catalog rows
    # describe operation contracts and ownership. They are validated by the
    # architecture registry and must never be compared with domain business JSON
    # such as {queued, sent_24h, calls_24h}. Only executable runtime rules own
    # desired-vs-actual reconciliation/freshness in this projection.
    runtime_projection_rules = [
        r for r in projection_rules
        if str(r.requirement_type or "") not in REGISTRY_ONLY_REQUIREMENT_TYPES
    ]
    incidents_q = db.query(ControlIncident).filter(ControlIncident.state == "open", ControlIncident.module == module)
    if account_id:
        incidents_q = incidents_q.filter((ControlIncident.account_id == str(account_id)) | (ControlIncident.account_id.is_(None)))
    incidents = incidents_q.order_by(ControlIncident.priority.asc(), ControlIncident.opened_at.asc()).all()
    latest_q = db.query(ControlExecution).filter(ControlExecution.module == module)
    if account_id:
        latest_q = latest_q.filter(ControlExecution.account_id == str(account_id))
    latest = latest_q.order_by(ControlExecution.created_at.desc()).first()

    actual = {}
    state_error = None
    if adapter:
        try:
            # Isolate every adapter read. A broken domain query must not poison
            # the transaction and blind the rest of the global Brain snapshot.
            with db.begin_nested():
                actual = adapter.get_state(db, account_id) or {}
        except Exception as exc:
            state_error = type(exc).__name__
            actual = {}
    desired = effective_config(db, module=module, account_id=account_id) if adapter else {"desired_state": {}, "provenance": {}, "requirements": []}
    # SYSTEM_SCOPE_ISOLATION_V1: owner/system operating invariants are verified by
    # the System Brain itself. They must not be projected into every domain
    # adapter (CRM, MOP, Analytics...) as if each module had fields such as
    # autonomous_continuation. Only requirements actually owned by this module
    # participate in desired-vs-actual drift.
    domain_desired = {}
    domain_provenance = {}
    for req in runtime_projection_rules:
        if str(req.module or "") != module:
            continue
        for key, value in dict(req.desired_state_json or {}).items():
            domain_desired[key] = value
            domain_provenance[key] = str(req.id)
    desired = {**desired, "desired_state": domain_desired, "provenance": domain_provenance}
    drift = {k: {"desired": v, "actual": actual.get(k)} for k, v in domain_desired.items() if actual.get(k) != v}
    if adapter and not state_error:
        try:
            with db.begin_nested():
                diagnosis = adapter_diagnose(db, adapter, account_id, actual_state=actual)
        except Exception as exc:
            diagnosis = {"classification": "BUSINESS_DIAGNOSIS_FAILED", "health": "critical", "confidence": 1.0, "error_type": type(exc).__name__}
            state_error = state_error or type(exc).__name__
    else:
        diagnosis = {"classification": "MODULE_STATE_UNAVAILABLE" if adapter else "MODULE_ADAPTER_MISSING", "health": "critical" if adapter else "blocked", "confidence": 1.0}
    if account_id and module=="mop" and not state_error:
        diagnosis=_scope_mop_diagnosis(actual,dict(diagnosis or {}))
    if str((diagnosis or {}).get("health") or "").lower()=="waiting_external":
        diagnosis=dict(diagnosis or {})
        if not diagnosis.get("dependency_state"):
            diagnosis["dependency_state"]="waiting_external"
        # WAITING_EXTERNAL_IS_NOT_SELF_HEAL_V1: a passive "resume when provider
        # returns" marker is not an internal repair action. Preserve the external
        # blocker in the plan, but do not make Client Supervisor report it as a
        # broken contour that BORIS should somehow self-heal locally.
        if str(diagnosis.get("safe_action") or "")=="resume_when_provider_available":
            diagnosis["safe_action"]=None
    if account_id and module=="marketing" and not state_error:
        diagnosis=_scope_marketing_diagnosis(db,str(account_id),actual,dict(diagnosis or {}))
    if account_id and module=="social" and not state_error:
        diagnosis=_scope_social_diagnosis(db,str(account_id),actual,dict(diagnosis or {}))

    stale_seconds = max(60, int((contract.verification_stale_seconds if contract else 3600) or 3600))
    freshest = max((r.last_verified_at for r in runtime_projection_rules if r.last_verified_at), default=None)
    if freshest is not None and freshest.tzinfo is None:
        freshest = freshest.replace(tzinfo=timezone.utc)
    stale = bool(runtime_projection_rules) and (freshest is None or (now - freshest).total_seconds() > stale_seconds)
    p0 = sum(1 for x in incidents if x.priority == "P0")
    p1 = sum(1 for x in incidents if x.priority == "P1")
    diagnosis_health = str((diagnosis or {}).get("health") or "pass").lower()
    if state_error or p0 or diagnosis_health == "critical":
        health = "critical"
    elif adapter is None or diagnosis_health == "blocked":
        health = "blocked"
    # WAITING_HUMAN_WORLD_HEALTH_V1: a deterministic diagnosis may prove that
    # runtime is healthy but a real business obligation is waiting on the
    # manager/MOP work queue. It is not PASS: action planner must see it and
    # surface the safe action (for example overdue CRM tasks) without pretending
    # the human action already happened.
    elif p1 or drift or stale or diagnosis_health in {"degraded", "waiting_human", "waiting_external"}:
        # WAITING_EXTERNAL_WORLD_TRUTH_V1: a provider-side dependency is not a
        # healthy PASS merely because BORIS cannot repair it locally. Keep the
        # module visibly degraded while self-heal/reprobe continues.
        health = "degraded"
    else:
        health = "pass"
    return {
        "module": module,
        "account_id": account_id,
        "health": health,
        "desired_state": desired.get("desired_state") or {},
        "provenance": desired.get("provenance") or {},
        "actual_state": actual,
        "drift": drift,
        "freshness": {"last_verified_at": _iso(freshest), "stale": stale, "ttl_seconds": stale_seconds},
        "capabilities": adapter_capabilities(adapter) if adapter else {},
        "diagnosis": diagnosis,
        "incidents": [{"id": x.id, "type": x.incident_type, "priority": x.priority, "symptom": x.symptom, "opened_at": _iso(x.opened_at)} for x in incidents],
        "latest_execution": None if latest is None else {"id": latest.id, "status": latest.status, "error_code": latest.error_code, "created_at": _iso(latest.created_at), "finished_at": _iso(latest.finished_at)},
        "state_error": state_error,
    }


def build_world_state(db, account_id: str | None = None) -> dict[str, Any]:
    # BRAIN_WORLD_REGISTRY_RECONCILE_V1: the adapter registry is the executable
    # module truth. A newly added mandatory adapter must appear in the world state
    # immediately even if its DB contract row has not yet been materialized. Keep
    # enabled DB-only contracts too, so disabling/deleting an adapter cannot hide
    # an existing controlled module.
    from app.services.control_plane_adapters import modules as adapter_modules
    contracts = db.query(ControlModuleContract).filter(ControlModuleContract.enabled.is_(True)).order_by(ControlModuleContract.module.asc()).all()
    module_names = sorted(set(adapter_modules()) | {str(c.module) for c in contracts})
    if account_id:
        module_names = [m for m in module_names if m not in GLOBAL_ONLY_MODULES]
    modules = [build_module_state(db, module, account_id=account_id) for module in module_names]
    # BRAIN_EXPIRED_MARKETING_PORTFOLIO_TRUTH_V1
    if account_id is None:
        from app.services.control_plane_adapters_ext import marketing_service_entitlement
        for m in modules:
            if m.get("module") != "marketing": continue
            actual=m.get("actual_state") or {}; portfolio=actual.get("portfolio_accounts")
            if not isinstance(portfolio,list): continue
            current=[]; expired=[]
            for state in portfolio:
                aid=str((state or {}).get("account_id") or ""); ent=marketing_service_entitlement(db,aid) if aid else {"state":"unknown"}
                row=dict(state or {}); row["service_entitlement"]=ent
                (expired if ent.get("state")=="expired" else current).append(row)
            actual["portfolio_accounts"]=current; actual["portfolio_count"]=len(current); actual["expired_accounts_excluded"]=[{"account_id":x.get("account_id"),"service_entitlement":x.get("service_entitlement")} for x in expired]; actual["expired_accounts_excluded_count"]=len(expired)
            m["diagnosis"]=adapter_diagnose(db,get_adapter("marketing"),None,actual_state=actual)
    p0 = sum(sum(1 for x in m["incidents"] if x["priority"] == "P0") for m in modules)
    p1 = sum(sum(1 for x in m["incidents"] if x["priority"] == "P1") for m in modules)
    critical_drift = sum(1 for m in modules if m["drift"])
    stale = sum(1 for m in modules if m["freshness"]["stale"])
    if p0 or any(m["health"] == "critical" for m in modules):
        status = "critical"
    elif p1 or critical_drift or stale or any(m["health"] in {"degraded", "blocked"} for m in modules):
        status = "degraded"
    else:
        status = "pass"
    return {
        "status": status,
        "account_id": account_id,
        "summary": {
            "modules": len(modules),
            "pass": sum(1 for m in modules if m["health"] == "pass"),
            "degraded": sum(1 for m in modules if m["health"] == "degraded"),
            "critical": sum(1 for m in modules if m["health"] == "critical"),
            "blocked": sum(1 for m in modules if m["health"] == "blocked"),
            "open_p0": p0,
            "open_p1": p1,
            "modules_with_drift": critical_drift,
            "stale_modules": stale,
        },
        "modules": modules,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
