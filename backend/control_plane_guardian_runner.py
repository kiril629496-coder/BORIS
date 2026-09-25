#!/usr/bin/env python3
"""Periodic reconciliation/diagnostic tick for BORIS Control Plane.

This runner is fail-closed: it never invents PASS and never performs paid domain
mutations. It seeds canonical invariants/contracts, verifies registered adapters,
and creates deduplicated incidents for drift/stale/dead rules.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/BORIS/backend")
from app.db.session import SessionLocal
from app.models.control_plane import ControlExecution, ControlIncident, ControlModuleContract, ControlRequirement
from app.services.control_plane import effective_config, open_incident, seed_invariants, detect_rule_conflicts, record_runtime_verification, execute_requirement_once, start_execution, add_evidence, transition_execution, REGISTRY_ONLY_REQUIREMENT_TYPES
from app.services.control_plane_adapters import get as get_adapter, modules as adapter_modules, adapter_diagnose, adapter_capabilities
from app.services.reliability import heartbeat, feature_enabled, json_safe
from app.services.brain_recovery import run_safe_recovery

CONTRACTS = {
    "marketing": {"input": "KPI + Avito/account state", "state": "autopilot_settings + kpi_settings + virtual_marketer_runtime", "action": "domain-owned KPI controller", "output": "proposal/apply result", "post_condition": "actual bid/state confirmed", "evidence": "runtime + external state", "errors": "blocked/deferred/failed"},
    "mop": {"input": "inbound dialogue", "state": "dialogue + CRM context", "action": "qualify/reply", "output": "message/task", "post_condition": "reply/task exists", "evidence": "message id / CRM id", "errors": "blocked/deferred/failed"},
    "crm": {"input": "lead/call/message event", "state": "contact/deal/task", "action": "create/update", "output": "CRM entity", "post_condition": "entity readable", "evidence": "entity id", "errors": "blocked/deferred/failed"},
    "telephony": {"input": "call event", "state": "call/session", "action": "ingest/qualify", "output": "call + CRM side effects", "post_condition": "call attached", "evidence": "call id / CRM id", "errors": "blocked/deferred/failed"},
    "messages": {"input": "inbound/outbound message", "state": "thread", "action": "reply/queue", "output": "delivered message", "post_condition": "delivery/read state", "evidence": "provider message id", "errors": "blocked/deferred/failed"},
    "rop": {"input": "calls/messages/KPI", "state": "audit window", "action": "audit/report", "output": "score/recommendation", "post_condition": "report persisted", "evidence": "report id", "errors": "blocked/deferred/failed"},
    "feed_factory": {"input": "catalog + requirements", "state": "campaign items", "action": "build/validate/export", "output": "feed", "post_condition": "validation/pass or explicit block", "evidence": "artifact + validation", "errors": "blocked/deferred/failed"},
    "social": {"input": "content plan", "state": "post queue", "action": "publish", "output": "provider post", "post_condition": "post readable", "evidence": "post id/url", "errors": "blocked/deferred/failed"},
    "email": {"input": "lead + template", "state": "email queue", "action": "send", "output": "provider receipt", "post_condition": "sent/delivery state", "evidence": "message id", "errors": "blocked/deferred/failed"},
    "telegram": {"input": "lead/dialogue", "state": "dialogue queue", "action": "send/follow-up", "output": "Telegram message", "post_condition": "message id exists", "evidence": "message id", "errors": "blocked/deferred/failed"},
    "analytics": {"input": "events/stats", "state": "aggregation window", "action": "aggregate", "output": "metrics", "post_condition": "fresh metrics", "evidence": "snapshot timestamp", "errors": "blocked/deferred/failed"},
    "direct": {"input": "campaign/KPI", "state": "Direct entities", "action": "configure/optimize", "output": "campaign state", "post_condition": "API actual state matches", "evidence": "Direct API readback", "errors": "blocked/deferred/failed"},
    "billing": {"input": "tariff/payment", "state": "billing ledger", "action": "entitle/charge", "output": "entitlement/receipt", "post_condition": "ledger readable", "evidence": "ledger id", "errors": "blocked/deferred/failed"},
    "sitebuild": {"input": "site spec", "state": "build state", "action": "build/publish", "output": "site artifact", "post_condition": "site reachable", "evidence": "build/url", "errors": "blocked/deferred/failed"},
}


def _json_safe(value):
    """Return JSON-column-safe evidence without losing diagnostic values."""
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _incident_once(db, incident_type: str, module: str, account_id: str | None, symptom: str, *, priority="P2", requirement_id=None, details=None):
    details = _json_safe(details)
    q=db.query(ControlIncident).filter(ControlIncident.state=="open", ControlIncident.incident_type==incident_type, ControlIncident.module==module)
    if account_id is None: q=q.filter(ControlIncident.account_id.is_(None))
    else: q=q.filter(ControlIncident.account_id==account_id)
    if requirement_id: q=q.filter(ControlIncident.requirement_id==requirement_id)
    row=q.first()
    if row:
        row.symptom=symptom
        if details is not None:
            row.details_json=json_safe(details)
        elif not row.details_json:
            row.details_json={}
        row.updated_at=datetime.now(timezone.utc)
        return row,False
    return open_incident(db,incident_type=incident_type,symptom=symptom,module=module,account_id=account_id,requirement_id=requirement_id,priority=priority,details=details),True


def seed_contracts(db):
    """Read-only contract registry check.

    CONTROL_PLANE_CONTRACTS_MIGRATION_OWNED_V1: Module Contracts are canonical
    Control Plane configuration and may only be changed by a versioned migration
    or an explicit Control Plane rule update. The guardian must never rewrite
    contract_json/capabilities_json from an in-process legacy dictionary.
    """
    from app.services.architecture_registry import REQUIRED_MODULES
    existing={str(row[0]) for row in db.query(ControlModuleContract.module).filter(ControlModuleContract.enabled.is_(True)).all()}
    missing=sorted(set(REQUIRED_MODULES)-existing)
    if missing:
        _incident_once(
            db,"MODULE_CONTRACT_MISSING","control_plane",None,
            "Canonical Module Contracts are missing; versioned migration/control-plane repair required",
            priority="P1",details={"missing":missing,"guardian_wrote_contracts":False},
        )
    return 0


def tick(db):
    now=datetime.now(timezone.utc)
    inv=seed_invariants(db); contracts=seed_contracts(db); db.flush()
    try:
        recovery=run_safe_recovery(db)
    except Exception as recovery_exc:
        recovery={"status":"degraded","actions":[],"changed_actions":0,"error_type":type(recovery_exc).__name__}
        _incident_once(db,"BRAIN_RECOVERY_FAILED","control_plane",None,"Safe recovery engine failed before reconciliation",priority="P1",details=recovery)
    conflicts=detect_rule_conflicts(db)
    conflict_requirement_ids=set()
    for _c in conflicts:
        conflict_requirement_ids.update((str(_c.get("left_requirement_id") or ""), str(_c.get("right_requirement_id") or "")))
    created=0; checked=0; drifted=0; auto_remediated=0; auto_blocked=0
    for conflict in conflicts:
        _,new_inc=_incident_once(db,"RULE_CONFLICT","control_plane",None,f"Two active rules disagree on {conflict['field']}",priority="P1",details=conflict)
        created+=int(new_inc)
    active_conflict_ids={str(x.get("id") or "") for x in conflicts}
    for inc in db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="RULE_CONFLICT").all():
        cid=str((inc.details_json or {}).get("id") or "")
        if not conflicts or cid not in active_conflict_ids:
            inc.state="resolved";inc.resolved_at=now;inc.updated_at=now;inc.remediation="Rule conflict no longer exists after current conflict scan"
    # SYSTEM_INVARIANT_RUNTIME_VERIFICATION_V1: critical system rules have their
    # own real checks; they are not compared to unrelated module JSON.
    from sqlalchemy import text as _sql_cp
    from pathlib import Path as _PathCP
    system_rules=db.query(ControlRequirement).filter(ControlRequirement.status=="active",ControlRequirement.module=="system").all()
    for req in system_rules:
        passed=False; payload={}; level="L5"
        key=str(req.key)
        if key=="system.no_evidence_no_pass":
            bad=db.execute(_sql_cp("""select count(*) from control_executions e where e.status='pass' and not exists(
              select 1 from control_evidence v where v.execution_id=e.id and v.passed=true and v.level in ('L4','L5','L6'))""")).scalar() or 0
            passed=int(bad)==0;payload={"pass_without_runtime_evidence":int(bad)}
        elif key=="system.reconcile_desired_actual":
            bad=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="CONFIG_DRIFT").count()
            passed=int(bad)==0;payload={"open_config_drift":int(bad)}
        elif key=="system.owner_rules_persist":
            exists=db.execute(_sql_cp("select to_regclass('public.control_owner_instructions') is not null")).scalar()
            passed=bool(exists);payload={"owner_intake_table":bool(exists)}
        elif key=="system.capture_before_execution":
            src=_PathCP('/root/BORIS/backend/app/api/command.py').read_text(encoding='utf-8')
            passed='OWNER_CONTROL_PLANE_CAPTURE_BEFORE_EXECUTION_V2' in src and 'control_plane_capture_failed' in src;payload={"capture_before_execution_marker":passed};level="L4"
        elif key=="system.kpi_failure_action":
            bad=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="KPI_FAILED_BUT_NO_ACTION").count()
            passed=int(bad)==0;payload={"kpi_failed_without_action":int(bad)}
        elif key=="system.health_requires_function":
            bad=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="MODULE_STALLED").count()
            passed=int(bad)==0;payload={"stalled_modules":int(bad)}
        elif key=="system.retry_strategy_change":
            src=_PathCP('/root/BORIS/backend/app/services/control_plane.py').read_text(encoding='utf-8')
            passed=('refresh_actual_then_compare_and_set' in src and 'strategies=("compare_and_set","refresh_actual_then_compare_and_set")' in src)
            payload={"bounded_strategy_change_present":passed};level="L4"
        elif key=="system.no_github_dependency":
            import subprocess as _sp_cp
            en=_sp_cp.run(["systemctl","is-enabled","boris-gitdeploy.service"],capture_output=True,text=True,timeout=5)
            ac=_sp_cp.run(["systemctl","is-active","boris-gitdeploy.service"],capture_output=True,text=True,timeout=5)
            enabled=(en.stdout or "").strip(); active=(ac.stdout or "").strip()
            passed=enabled in {"disabled","masked","not-found"} and active in {"inactive","failed","unknown"}
            payload={"gitdeploy_enabled":enabled,"gitdeploy_active":active,"github_required":False};level="L5"
        elif (
            key.startswith("owner.")
            and str((req.source_json or {}).get("source_ref") or "")=="owner-rule:single-writer-workstreams:v1"
        ):
            # OWNER_SINGLE_WRITER_RUNTIME_VERIFY_V2:
            # This owner rule has no desired_state fields by design, so generic
            # desired-vs-actual reconciliation cannot verify it. Reuse the
            # canonical dev/workstream lock health in strictly read-only mode.
            # Waiting/review jobs may intentionally claim overlapping hierarchical
            # locks: the scheduler serializes them. The invariant fails only when
            # canonical lock health is unhealthy or two EXECUTING writers overlap.
            # Never self-heal or reassign work from an invariant check.
            from app.ext_api import dev as _dev_owner_scope
            _lock_health=_dev_owner_scope.development_lock_health(self_heal=False,limit=1000)
            _running_conflicts=list(_lock_health.get("running_conflicts") or [])
            _potential_pairs=int(_lock_health.get("potential_overlap_pairs") or 0)
            passed=bool(
                _lock_health.get("ok")
                and str(_lock_health.get("state") or "")=="ok"
                and not _running_conflicts
            )
            payload={
                "single_writer_policy":"single_writer_by_workstream",
                "active_jobs":int(_lock_health.get("active_jobs") or 0),
                "executing_jobs":int(_lock_health.get("executing_jobs") or 0),
                "running_conflicts":_running_conflicts[:20],
                "potential_overlap_pairs":_potential_pairs,
                "metadata_gaps":list(_lock_health.get("metadata_gaps") or [])[:20],
                "unscoped_jobs":list(_lock_health.get("unscoped_jobs") or [])[:20],
                "owner_action_required":bool(_lock_health.get("owner_action_required")),
                "self_heal_invoked":False,
                "owner_operator_required":False,
            }
            level="L5"
        elif key.startswith("owner.") and bool((req.desired_state_json or {}).get("autonomous_continuation")):
            import subprocess as _sp_owner
            from app.models.reliability import ReliabilityHeartbeat as _HB_OWNER
            timer=_sp_owner.run(["systemctl","is-active","boris-system-brain.timer"],capture_output=True,text=True,timeout=5)
            timer_active=(timer.stdout or "").strip()=="active"
            hb=db.query(_HB_OWNER).filter(_HB_OWNER.module=="control_plane",_HB_OWNER.worker_id=="guardian").order_by(_HB_OWNER.last_seen_at.desc()).first()
            age=None
            if hb and hb.last_seen_at:
                seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc)
                age=max(0,int((now-seen).total_seconds()))
            heartbeat_fresh=age is not None and age<=600 and str(getattr(hb,"state","") or "ok") not in {"error","failed","critical"}
            safe_recovery_present=_PathCP('/root/BORIS/backend/app/services/brain_recovery.py').exists()
            passed=timer_active and heartbeat_fresh and safe_recovery_present
            payload={"system_brain_timer_active":timer_active,"guardian_heartbeat_age_sec":age,"guardian_heartbeat_fresh":heartbeat_fresh,"safe_recovery_present":safe_recovery_present,"owner_operator_required":False,"escalate_only_when_blocked":True};level="L5"
        elif key=="system.marketing.absolute_autonomous_bid_cap":
            cap=float((req.desired_state_json or {}).get("hard_max_bid_rub") or 0)
            fn1=str(db.execute(_sql_cp("select pg_get_functiondef('boris_guard_money_mandate_v1'::regproc)")).scalar() or '')
            fn2=str(db.execute(_sql_cp("select pg_get_functiondef('boris_reconcile_money_from_kpi_v1'::regproc)")).scalar() or '')
            over=db.execute(_sql_cp("""select count(*) from money_mandates where status='active' and revoked_at is null
              and 'cpx.raise_bid'=any(allowed_operations) and coalesce(max_bid_rub,999999)>:cap"""),{"cap":cap}).scalar() or 0
            passed=cap>0 and 'CONTROL_PLANE_SAFETY_CAP' in fn1 and 'CONTROL_PLANE_SAFETY_CAP' in fn2 and int(over)==0
            payload={"control_plane_cap":cap,"db_guard_reads_registry":'CONTROL_PLANE_SAFETY_CAP' in fn1,"kpi_reconcile_reads_registry":'CONTROL_PLANE_SAFETY_CAP' in fn2,"active_mandates_over_cap":int(over)}
        else:
            continue
        bucket=now.strftime('%Y%m%d%H')
        ex=start_execution(db,req,plan={"kind":"system_invariant_verification","key":key},idempotency_key=f"system-verify:{req.id}:v{req.version}:{bucket}")
        if ex.status not in {"pass","failed","degraded","blocked","cancelled"}:
            for st in ("parsed","mapped_to_requirements","planned","preflight","executing","technical_tested"):
                try: transition_execution(db,ex,st)
                except ValueError: pass
            add_evidence(db,ex,level=level,evidence_type="system_invariant_check",source="control_plane_guardian",passed=passed,payload=payload)
            try: transition_execution(db,ex,"runtime_verified",actual_state=payload)
            except ValueError: pass
            transition_execution(db,ex,"pass" if passed else "degraded",actual_state=payload,reason=None if passed else "system invariant verification failed",error_code=None if passed else "SYSTEM_INVARIANT_FAILED")
        if passed:
            req.last_verified_at=now
            for inc in db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="SYSTEM_INVARIANT_FAILED",ControlIncident.requirement_id==req.id).all():
                inc.state="resolved";inc.resolved_at=now;inc.updated_at=now;inc.remediation="System invariant verification PASS"
        else:
            _,new=_incident_once(db,"SYSTEM_INVARIANT_FAILED","control_plane",None,f"System invariant failed: {req.title}",priority="P1",requirement_id=req.id,details=payload);created+=int(new)

    # Only requirements with explicit desired state are reconciled. System seeds
    # describe invariants, they are verified by dedicated code/tests rather than
    # compared against unrelated module state.
    rules=db.query(ControlRequirement).filter(
        ControlRequirement.status=="active",
        ControlRequirement.module!="system",
        ~ControlRequirement.requirement_type.in_(("architecture_rule","business_rule")),
    ).all()
    for req in rules:
        account_id=req.scope_id if req.scope_type=="account" else None
        adapter=get_adapter(req.module)
        if not adapter:
            _,new=_incident_once(db,"MODULE_ADAPTER_MISSING",req.module,account_id,"Active rule has no runtime adapter",priority="P1",requirement_id=req.id); created+=int(new); continue
        if not req.desired_state_json: continue
        checked+=1
        contract=db.query(ControlModuleContract).filter(ControlModuleContract.module==req.module).first()
        stale_seconds=max(60,int((contract.verification_stale_seconds if contract else 3600) or 3600))
        _last=req.last_verified_at
        if _last is not None and _last.tzinfo is None: _last=_last.replace(tzinfo=timezone.utc)
        stale=(not _last) or ((now-_last).total_seconds()>stale_seconds)
        if stale and req.priority in {"critical","high"}:
            _,new=_incident_once(db,"STALE_VERIFICATION",req.module,account_id,"Critical/high rule has no fresh runtime verification",priority="P1",requirement_id=req.id,details={"last_verified_at":_last.isoformat() if _last else None,"stale_seconds":stale_seconds}); created+=int(new)
        try:
            actual=adapter.get_state(db,account_id) or {}
        except Exception as state_exc:
            _,new=_incident_once(db,"MODULE_STATE_READ_FAILED",req.module,account_id,"Runtime state could not be read",priority="P1",requirement_id=req.id,details={"error_type":type(state_exc).__name__}); created+=int(new)
            continue
        # STATE_READ_INCIDENT_AUTORESOLVE_V1: successful deterministic readback
        # is direct proof that the previous read-failure incident is no longer
        # current. Resolve it even if a separate config drift still exists.
        _rows=db.query(ControlIncident).filter(
            ControlIncident.state=="open",
            ControlIncident.incident_type=="MODULE_STATE_READ_FAILED",
            ControlIncident.requirement_id==req.id,
        ).all()
        for _inc in _rows:
            _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
            _inc.remediation="Fresh runtime state read succeeded"
        desired=dict(req.desired_state_json or {})
        # MARKETING_EXPIRED_RULE_RUNTIME_SCOPE_V1: historical desired state is not executable after paid period ends.
        if req.module == "marketing" and account_id:
            from app.services.control_plane_adapters_ext import marketing_service_entitlement
            _scope_entitlement=marketing_service_entitlement(db,str(account_id),now=now)
            if str(_scope_entitlement.get("state") or "") != "active":
                req.last_verified_at=now
                record_runtime_verification(db,req,account_id=account_id,actual_state={**actual,"service_entitlement":_scope_entitlement,"execution_scope":"inactive_paid_period"},passed=True)
                _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.requirement_id==req.id,ControlIncident.incident_type.in_(("CONFIG_DRIFT","STALE_VERIFICATION","MODULE_STATE_READ_FAILED","KPI_FAILED_BUT_NO_ACTION","MONEY_GUARD_MISSING_LIMIT","MONEY_GUARD_MISSING_HARD_CAP"))).all()
                for _inc in _rows:
                    _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                    _inc.remediation="No explicit active marketing paid period; runtime is correctly fail-closed and historical desired state is non-executable"
                continue
        # MARKETING_MONEY_GUARD_INVARIANT_V1: autonomous KPI mode may exist with
        # zero/missing budget or hard bid cap, but money execution must then be
        # explicitly BLOCKED rather than silently pretending the autopilot works.
        if req.module == "marketing" and account_id:
            # MONEY_GUARD_PAID_PERIOD_SCOPE_V1: stale KPI/autopilot config is not
            # proof that a marketing service period is currently active.
            from app.services.control_plane_adapters_ext import marketing_service_entitlement
            _marketing_entitlement=marketing_service_entitlement(db,str(account_id),now=now)
            _marketing_paid=_marketing_entitlement.get("state")=="active"
            target=float(actual.get("target_leads_per_day") or 0)
            mode=str(actual.get("autopilot_mode") or "")
            money_auto=_marketing_paid and target>0 and mode in {"goal_auto","always_auto"}
            for _itype,_field,_label in (("MONEY_GUARD_MISSING_LIMIT","daily_budget_limit_rub","дневной лимит бюджета"),("MONEY_GUARD_MISSING_HARD_CAP","hard_max_bid_rub","жёсткий предел ставки")):
                value=float(actual.get(_field) or 0)
                if money_auto and value<=0:
                    _,new=_incident_once(db,_itype,"marketing",account_id,f"Активный KPI-автопилот заблокирован: не задан {_label}",priority="P1",requirement_id=req.id,details={"field":_field,"value":value,"autopilot_mode":mode,"target_leads_per_day":target,"service_entitlement":_marketing_entitlement}); created+=int(new)
                else:
                    _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type==_itype,ControlIncident.requirement_id==req.id).all()
                    for _inc in _rows:
                        _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                        _inc.remediation=(f"{_field} is now configured" if _marketing_paid else "No explicit active marketing paid period; stale autopilot config is not an owner obligation")
        # KPI_FAILED_BUT_NO_ACTION_V1: a miss is acceptable only when the runtime
        # produced a concrete diagnostic/wait state or a planned/applied action.
        if req.module == "marketing" and account_id:
            target=float(actual.get("target_leads_per_day") or 0)
            actual_leads=float(actual.get("actual_leads") or 0)
            runtime_planned=int(actual.get("runtime_planned") or 0)
            runtime_applied=int(actual.get("runtime_applied") or 0)
            runtime_mode=str(actual.get("runtime_mode") or "").strip()
            if target>0 and actual_leads<target and runtime_planned<=0 and runtime_applied<=0 and not runtime_mode:
                diagnostic_code=None; diagnostic_reason=None
                if float(actual.get("daily_budget_limit_rub") or 0)<=0:
                    diagnostic_code="KPI_BLOCKED_MONEY_GUARD";diagnostic_reason="KPI отстаёт, но денежные действия запрещены: дневной бюджет не задан"
                elif int(actual.get("items_count") or 0)<=0 or int(actual.get("active_items") or 0)<=0:
                    diagnostic_code="KPI_BLOCKED_NO_ACTIVE_ITEMS";diagnostic_reason="KPI отстаёт, но нет активных объявлений для безопасного действия"
                if diagnostic_code:
                    bucket=now.strftime("%Y%m%d%H")
                    dex=start_execution(db,req,account_id=account_id,plan={"kind":"kpi_failure_diagnosis","reason":diagnostic_code},idempotency_key=f"kpi-diagnosis:{req.id}:{bucket}")
                    if dex.status not in {"pass","blocked","failed","degraded","waiting_external","cancelled"}:
                        for st in ("parsed","mapped_to_requirements","planned","preflight"):
                            try: transition_execution(db,dex,st)
                            except ValueError: pass
                        add_evidence(db,dex,level="L5",evidence_type="kpi_failure_diagnosis",source="control_plane_guardian",passed=True,payload={"target":target,"actual":actual_leads,"daily_budget_limit_rub":actual.get("daily_budget_limit_rub"),"items_count":actual.get("items_count"),"active_items":actual.get("active_items"),"diagnostic_code":diagnostic_code})
                        transition_execution(db,dex,"blocked",actual_state=actual,reason=diagnostic_reason,error_code=diagnostic_code)
                    _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="KPI_FAILED_BUT_NO_ACTION",ControlIncident.requirement_id==req.id).all()
                    for _inc in _rows:
                        _inc.state="resolved";_inc.resolved_at=now;_inc.updated_at=now;_inc.remediation=diagnostic_reason
                else:
                    _,new=_incident_once(db,"KPI_FAILED_BUT_NO_ACTION","marketing",account_id,
                        f"KPI не выполнен ({actual_leads:g}/{target:g}), но нет ни действия, ни сохранённой диагностики",
                        priority="P1",requirement_id=req.id,details={"target":target,"actual":actual_leads,"runtime_last_run_at":actual.get("runtime_last_run_at"),"items_count":actual.get("items_count"),"active_items":actual.get("active_items")}); created+=int(new)
            else:
                _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="KPI_FAILED_BUT_NO_ACTION",ControlIncident.requirement_id==req.id).all()
                for _inc in _rows:
                    _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now; _inc.remediation="Marketing runtime produced action/diagnosis or KPI recovered"
        drift={k:{"desired":v,"actual":actual.get(k)} for k,v in desired.items() if actual.get(k)!=v}
        if drift:
            drifted+=1
            source=dict(req.source_json or {})
            may_write=bool(source.get("allow_control_write")) and str(req.id) not in conflict_requirement_ids and feature_enabled(db,"control_plane_auto_remediation",account_id=account_id,default=False)
            repaired=False
            repair_execution=None
            if may_write:
                bucket=str(int(now.timestamp())//300)
                repair_execution=execute_requirement_once(db,req,account_id=account_id,idempotency_key=f"guardian-remediate:{req.id}:v{req.version}:{account_id or '*'}:{bucket}")
                if str(repair_execution.status)=="pass":
                    actual=adapter.get_state(db,account_id) or {}
                    drift={k:{"desired":v,"actual":actual.get(k)} for k,v in desired.items() if actual.get(k)!=v}
                    repaired=not bool(drift)
                    if repaired:
                        auto_remediated+=1
                        incidents=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="CONFIG_DRIFT",ControlIncident.requirement_id==req.id).all()
                        for inc in incidents:
                            inc.state="resolved"; inc.resolved_at=now; inc.updated_at=now; inc.remediation="Control Plane auto-remediation confirmed by runtime readback"
                elif str(repair_execution.status) in {"blocked","failed","degraded"}:
                    auto_blocked+=1
            if not repaired:
                record_runtime_verification(db,req,account_id=account_id,actual_state=actual,passed=False)
                details={"drift":drift,"auto_remediation_allowed":may_write}
                if repair_execution is not None:
                    details.update({"repair_execution_id":str(repair_execution.id),"repair_status":repair_execution.status,"repair_error_code":repair_execution.error_code})
                _,new=_incident_once(db,"CONFIG_DRIFT",req.module,account_id,f"Desired state differs from actual state for {len(drift)} field(s)",priority="P1" if req.priority in {"critical","high"} else "P2",requirement_id=req.id,details=details); created+=int(new)
        else:
            record_runtime_verification(db,req,account_id=account_id,actual_state=actual,passed=True)
            req.last_verified_at=now
            # Auto-resolution is proof-gated: only a fresh successful readback closes these incidents.
            incidents=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type.in_(("CONFIG_DRIFT","STALE_VERIFICATION","MODULE_STATE_READ_FAILED")),ControlIncident.requirement_id==req.id).all()
            for inc in incidents:
                inc.state="resolved"; inc.resolved_at=now; inc.updated_at=now; inc.remediation="Fresh runtime verification PASS"
    # NO_SILENT_EXECUTIONS_V1: non-terminal executions cannot disappear forever.
    stale_exec_cutoff=now-timedelta(minutes=20)
    stale_statuses=("received","parsed","mapped_to_requirements","planned","preflight","executing","retrying","technical_tested","runtime_verified","business_verified")
    stale_execs=db.query(ControlExecution).filter(ControlExecution.status.in_(stale_statuses),ControlExecution.updated_at<stale_exec_cutoff).limit(500).all()
    for ex in stale_execs:
        req=db.query(ControlRequirement).filter(ControlRequirement.id==ex.requirement_id).first()
        try:
            transition_execution(db,ex,"failed",reason="Execution exceeded 20 minute no-progress deadline",error_code="STALE_EXECUTION")
        except ValueError:
            ex.status="failed"; ex.error_code="STALE_EXECUTION"; ex.error_message="Execution exceeded 20 minute no-progress deadline"; ex.finished_at=now; ex.updated_at=now
        _,new=_incident_once(db,"STALE_EXECUTION",ex.module,ex.account_id,"Execution stopped making progress and was terminalized",priority="P1" if req and req.priority in {"critical","high"} else "P2",requirement_id=ex.requirement_id,details={"execution_id":str(ex.id),"previous_updated_at":ex.updated_at.isoformat() if ex.updated_at else None}); created+=int(new)

    # MODULE_ACTIVITY_HEALTH_V1: enabled/registered is not health. An active-rule
    # module needs fresh worker activity or a fresh successful Control Plane execution.
    from app.models.reliability import ReliabilityHeartbeat
    heartbeat_aliases={
        "marketing": (("runtime","new_ad_bid_watchdog"),),
        "messages": (("messenger","poller"),),
        "mop": (("messenger","poller"),),
        "social": (("runtime","social_posting_scheduler"),),
    }
    for contract in db.query(ControlModuleContract).filter(ControlModuleContract.enabled.is_(True)).all():
        active_count=db.query(ControlRequirement).filter(
            ControlRequirement.status=="active",
            ControlRequirement.module==contract.module,
            ~ControlRequirement.requirement_type.in_(tuple(REGISTRY_ONLY_REQUIREMENT_TYPES)),
        ).count()
        if active_count<=0:
            # Registry/catalog metadata alone is not runtime work. An older
            # MODULE_STALLED opened while catalog rows were counted as executable
            # must close once the module has no executable obligation.
            _rows=db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="MODULE_STALLED",
                ControlIncident.module==contract.module,
            ).all()
            for _inc in _rows:
                _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                _inc.remediation="Registry-only requirements are not executable runtime work"
            continue
        stale_sec=max(300,int(contract.verification_stale_seconds or 3600))
        fresh_activity=False
        for hb_module,hb_worker in heartbeat_aliases.get(contract.module,((contract.module,None),)):
            q=db.query(ReliabilityHeartbeat).filter(ReliabilityHeartbeat.module==hb_module)
            if hb_worker: q=q.filter(ReliabilityHeartbeat.worker_id==hb_worker)
            hb=q.order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
            if hb and hb.last_seen_at:
                seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc)
                if (now-seen).total_seconds()<=stale_sec and str(hb.state or "ok") not in {"error","failed","critical","stale"}:
                    fresh_activity=True; break
        if not fresh_activity:
            # MODULE_ACTIVITY_REQUIREMENT_EVIDENCE_V3: bounded hourly ledgers may
            # intentionally reuse one terminal execution within the hour while
            # still performing a fresh adapter read/diagnosis on every Brain tick.
            # last_verified_at is the durable readback freshness receipt.
            last_requirement=db.query(ControlRequirement).filter(
                ControlRequirement.status=="active",
                ControlRequirement.module==contract.module,
                ~ControlRequirement.requirement_type.in_(tuple(REGISTRY_ONLY_REQUIREMENT_TYPES)),
                ControlRequirement.last_verified_at.isnot(None),
            ).order_by(ControlRequirement.last_verified_at.desc()).first()
            if last_requirement and last_requirement.last_verified_at:
                ts=last_requirement.last_verified_at if last_requirement.last_verified_at.tzinfo else last_requirement.last_verified_at.replace(tzinfo=timezone.utc)
                fresh_activity=(now-ts).total_seconds()<=stale_sec
        if not fresh_activity:
            # MODULE_ACTIVITY_EXECUTION_EVIDENCE_V2: activity is also proved by a
            # fresh verified terminal execution, not only by a green business
            # outcome. DEGRADED/BLOCKED/WAITING remain real execution evidence.
            last_verified=db.query(ControlExecution).filter(
                ControlExecution.module==contract.module,
                ControlExecution.status.in_(("pass","degraded","blocked","waiting_external","waiting_owner")),
            ).order_by(ControlExecution.finished_at.desc()).first()
            if last_verified and last_verified.finished_at:
                ts=last_verified.finished_at if last_verified.finished_at.tzinfo else last_verified.finished_at.replace(tzinfo=timezone.utc)
                fresh_activity=(now-ts).total_seconds()<=stale_sec
        if not fresh_activity:
            _,new=_incident_once(db,"MODULE_STALLED",contract.module,None,f"Модуль имеет {active_count} активных правил, но нет свежей подтверждённой активности",priority="P1",details={"active_rules":active_count,"stale_seconds":stale_sec}); created+=int(new)
        else:
            _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="MODULE_STALLED",ControlIncident.module==contract.module).all()
            for _inc in _rows:
                _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now; _inc.remediation="Fresh module activity or verified execution observed"

    # MODULE_ACTIVITY_INVARIANT_POST_RECONCILE_V1: system.health_requires_function
    # is evaluated earlier in the tick, before MODULE_ACTIVITY_HEALTH can close a
    # historical MODULE_STALLED incident. Reconcile the same invariant after the
    # authoritative activity scan so one tick cannot finish with a false P1.
    _stalled_now=db.query(ControlIncident).filter(
        ControlIncident.state=="open",
        ControlIncident.incident_type=="MODULE_STALLED",
    ).count()
    if int(_stalled_now)==0:
        _health_req=db.query(ControlRequirement).filter(ControlRequirement.key=="system.health_requires_function").first()
        if _health_req is not None:
            _health_ex=db.query(ControlExecution).filter(
                ControlExecution.requirement_id==_health_req.id,
            ).order_by(ControlExecution.created_at.desc()).first()
            if _health_ex is not None and str(_health_ex.status or "")=="degraded":
                add_evidence(
                    db,_health_ex,level="L5",evidence_type="module_activity_post_reconcile",
                    source="control_plane_guardian",passed=True,
                    payload={"stalled_modules":0,"post_module_activity_reconcile":True},
                )
                transition_execution(db,_health_ex,"pass",actual_state={"stalled_modules":0,"post_module_activity_reconcile":True})
            _health_req.last_verified_at=now
            for _inc in db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="SYSTEM_INVARIANT_FAILED",
                ControlIncident.requirement_id==_health_req.id,
            ).all():
                _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                _inc.remediation="Post-reconcile module activity scan has zero stalled executable modules"

    # BUSINESS_STATE_HEALTH_V1: heartbeat/process health is not business health.
    # Adapters with deterministic diagnosis create one deduplicated module incident.
    # No mutation is attempted here; domain recovery remains policy-gated.
    business_diagnosed=0
    business_degraded=0
    for module_name in adapter_modules():
        adapter=get_adapter(module_name)
        if not adapter or adapter.diagnose is None:
            continue
        business_diagnosed+=1
        try:
            business_actual=adapter.get_state(db,None) or {}
            diagnosis=adapter_diagnose(db,adapter,None,actual_state=business_actual) or {}
            # BUSINESS_READ_INCIDENT_AUTORESOLVE_V1: only a successful read AND
            # deterministic diagnosis proves the previous adapter-read failure
            # is gone. Close stale incident here before health classification.
            _rows=db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="BUSINESS_STATE_READ_FAILED",
                ControlIncident.module==module_name,
            ).all()
            for _inc in _rows:
                _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                _inc.remediation="Fresh business-state read and deterministic diagnosis succeeded"
            d_health=str(diagnosis.get("health") or "pass").lower()
            # BUSINESS_DEPENDENCY_WAIT_SEMANTICS_V1: an explicit owner/external
            # dependency is not an internal module degradation. Account-scoped
            # policy incidents remain canonical WAITING evidence while safe work continues.
            # BUSINESS_EXPECTED_DEPENDENCY_SEMANTICS_V2: explicit waiting_owner
            # or waiting_external from any adapter is dependency truth, not an
            # internal runtime outage.
            dependency_wait = str(diagnosis.get("dependency_state") or "").startswith("waiting_")
            if dependency_wait:
                _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="BUSINESS_STATE_DEGRADED",ControlIncident.module==module_name).all()
                for _inc in _rows:
                    _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                    _inc.remediation="Expected business dependency classified as WAITING; internal runtime remains healthy"
            elif d_health in {"degraded","blocked","critical"}:
                business_degraded+=1
                priority="P1" if d_health in {"blocked","critical"} else "P2"
                classification=str(diagnosis.get("classification") or "BUSINESS_STATE_DEGRADED")
                inc,new=_incident_once(db,"BUSINESS_STATE_DEGRADED",module_name,None,
                    f"{classification}: business post-condition is not healthy",priority=priority,
                    details={"diagnosis":diagnosis,"actual_state":business_actual})
                inc.root_cause=classification
                inc.remediation=str(diagnosis.get("safe_action") or "diagnosis_required")
                created+=int(new)
            else:
                _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="BUSINESS_STATE_DEGRADED",ControlIncident.module==module_name).all()
                for _inc in _rows:
                    _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                    _inc.remediation="Deterministic business-state diagnosis returned PASS"
        except Exception as diag_exc:
            inc,new=_incident_once(db,"BUSINESS_STATE_READ_FAILED",module_name,None,
                "Business state adapter could not produce a deterministic diagnosis",priority="P1",
                details={"error_type":type(diag_exc).__name__})
            inc.root_cause=type(diag_exc).__name__
            created+=int(new)

    # Active owner-created rules that never produced any execution are visible,
    # but allow a short intake/configuration grace period.
    cutoff=now-timedelta(minutes=15)
    dead=db.query(ControlRequirement).filter(ControlRequirement.status=="active",ControlRequirement.created_at<cutoff).all()
    dead_count=0
    for req in dead:
        if req.requirement_type in REGISTRY_ONLY_REQUIREMENT_TYPES:
            # REGISTRY_NEVER_EXECUTED_INCIDENT_RECONCILE_V1: registry metadata is
            # verified by architecture evidence, not by a domain executor. Close
            # historical incidents created before that separation was enforced.
            _rows=db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="ACTIVE_BUT_NEVER_EXECUTED",
                ControlIncident.requirement_id==req.id,
            ).all()
            for _inc in _rows:
                _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                _inc.remediation="Registry-only requirement verified through canonical architecture registry"
            continue
        if (req.source_json or {}).get("type")=="system_seed":
            continue
        exists=db.query(ControlExecution.id).filter(ControlExecution.requirement_id==req.id).first()
        if req.last_verified_at is not None or exists:
            # ACTIVE_RULE_FIRST_EXECUTION_AUTORESOLVE_V1:
            # The incident is historical as soon as the rule has either a
            # runtime verification timestamp or any durable execution. Keep the
            # audit row, but never leave a stale owner-facing "never executed"
            # warning after proof appears.
            _rows=db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="ACTIVE_BUT_NEVER_EXECUTED",
                ControlIncident.requirement_id==req.id,
            ).all()
            for _inc in _rows:
                _inc.state="resolved"
                _inc.resolved_at=now
                _inc.updated_at=now
                _inc.remediation="Rule now has durable execution/runtime verification evidence"
            continue
        dead_count+=1
        _,new=_incident_once(db,"ACTIVE_BUT_NEVER_EXECUTED",req.module,req.scope_id if req.scope_type=="account" else None,"Active requirement has never executed",priority="P1" if req.priority=="critical" else "P2",requirement_id=req.id); created+=int(new)
    # MARKETER_FLEET_ABORT_AUTORESOLVE_V1:
    # Keep the incident open until a later canonical marketer service run
    # completes successfully. Never resolve only because time passed.
    try:
        import subprocess as _sp_marketer_reconcile
        _show=_sp_marketer_reconcile.run(
            ["systemctl","show","boris-ai-marketer.service",
             "-p","Result","-p","ExecMainStatus","-p","ExecMainStartTimestamp"],
            capture_output=True,text=True,timeout=8,check=False,
        )
        _props={}
        for _line in (_show.stdout or "").splitlines():
            if "=" in _line:
                _k,_v=_line.split("=",1); _props[_k]=_v
        _latest_start=None
        _raw_start=str(_props.get("ExecMainStartTimestamp") or "").strip()
        if _raw_start:
            try:
                _parsed=datetime.strptime(_raw_start,"%a %Y-%m-%d %H:%M:%S %Z")
                _latest_start=_parsed.replace(tzinfo=timezone.utc)
            except Exception:
                _latest_start=None
        _success=(str(_props.get("Result") or "")=="success"
                  and str(_props.get("ExecMainStatus") or "")=="0"
                  and _latest_start is not None)
        if _success:
            for _inc in db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="MARKETER_FLEET_ABORT",
            ).all():
                _opened=_inc.opened_at
                if _opened and _opened.tzinfo is None:
                    _opened=_opened.replace(tzinfo=timezone.utc)
                if _opened and _latest_start > _opened:
                    _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                    _inc.remediation="A later canonical boris-ai-marketer.service cycle completed successfully (RC0)"
                    _d=dict(_inc.details_json or {})
                    _d.update({"resolved_by":"later_canonical_marketer_success",
                               "successful_cycle_started_at":_latest_start.isoformat(),
                               "service_result":"success","exec_main_status":0})
                    _inc.details_json=_d
    except Exception:
        pass

    # CLIENT_SUPERVISOR_FLEET_WIRE_V1: every System Brain cycle now also
    # verifies the same paid-period-aware per-client composite shown to owner.
    # Read-only: no publish/send/spend side effects. A read/composition failure is
    # an internal P1; inactive/expired clients are deliberately not incidents.
    from app.services.client_supervisor import build_fleet_state
    fleet=build_fleet_state(db)
    fleet_bad=int((fleet.get("counts") or {}).get("read_error") or 0)+int((fleet.get("counts") or {}).get("needs_self_heal") or 0)
    if fleet_bad:
        _,new=_incident_once(db,"CLIENT_SUPERVISOR_FLEET_UNHEALTHY","control_plane",None,
            "Client supervisor found internal client-contour failures",priority="P1",
            details={"counts":fleet.get("counts"),"checked_at":fleet.get("checked_at")}); created+=int(new)
    else:
        _rows=db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="CLIENT_SUPERVISOR_FLEET_UNHEALTHY").all()
        for _inc in _rows:
            _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now; _inc.remediation="Fresh fleet projection has no internal self-heal/read failures"

    # RUNTIME_LEDGER_PLAN_FACT_V1: a running timer is not completion proof.
    # Materialize one bounded hourly plan/attempt/fact snapshot for every module
    # required by the architecture audit, reusing the canonical Control Plane
    # execution/attempt/evidence tables. This is read-only with respect to domain
    # providers: adapters only read actual state here.
    from app.services.runtime_obligation_ledger import record_required_snapshots
    runtime_ledger=record_required_snapshots(db,bucket=now.strftime('%Y%m%d%H'))
    if runtime_ledger.get("errors"):
        _,new=_incident_once(
            db,"RUNTIME_LEDGER_SNAPSHOT_FAILED","control_plane",None,
            "One or more required modules could not materialize plan/fact evidence",
            priority="P1",details={"errors":runtime_ledger.get("errors"),"required_modules":runtime_ledger.get("required_modules")},
        )
        created+=int(new)
    else:
        for _inc in db.query(ControlIncident).filter(ControlIncident.state=="open",ControlIncident.incident_type=="RUNTIME_LEDGER_SNAPSHOT_FAILED").all():
            _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now; _inc.remediation="All required Runtime Ledger snapshots materialized successfully"

    # MODULE_ACTIVITY_POST_RUNTIME_LEDGER_V1: hourly Runtime Ledger evidence is
    # materialized after the first activity scan. Reconcile historical/stale
    # MODULE_STALLED incidents once more against the fresh terminal execution so
    # a single Brain tick cannot finish red after proving the module just ran.
    for _inc in db.query(ControlIncident).filter(
        ControlIncident.state=="open",
        ControlIncident.incident_type=="MODULE_STALLED",
    ).all():
        _contract=db.query(ControlModuleContract).filter(ControlModuleContract.module==_inc.module).first()
        _stale_sec=max(300,int((_contract.verification_stale_seconds if _contract else 3600) or 3600))
        _fresh_req=db.query(ControlRequirement).filter(
            ControlRequirement.status=="active",
            ControlRequirement.module==_inc.module,
            ~ControlRequirement.requirement_type.in_(tuple(REGISTRY_ONLY_REQUIREMENT_TYPES)),
            ControlRequirement.last_verified_at.isnot(None),
        ).order_by(ControlRequirement.last_verified_at.desc()).first()
        _fresh=False
        if _fresh_req is not None and _fresh_req.last_verified_at is not None:
            _ts=_fresh_req.last_verified_at if _fresh_req.last_verified_at.tzinfo else _fresh_req.last_verified_at.replace(tzinfo=timezone.utc)
            _fresh=(now-_ts).total_seconds()<=_stale_sec
        if not _fresh:
            _latest=db.query(ControlExecution).filter(
                ControlExecution.module==_inc.module,
                ControlExecution.status.in_(("pass","degraded","blocked","waiting_external","waiting_owner")),
            ).order_by(ControlExecution.finished_at.desc()).first()
            if _latest is not None and _latest.finished_at is not None:
                _ts=_latest.finished_at if _latest.finished_at.tzinfo else _latest.finished_at.replace(tzinfo=timezone.utc)
                _fresh=(now-_ts).total_seconds()<=_stale_sec
        if _fresh:
            _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
            _inc.remediation="Fresh runtime verification observed after Runtime Ledger materialization"

    # MODULE_ACTIVITY_INVARIANT_FINAL_RECONCILE_V1: the post-ledger pass above may
    # be the first point in this tick where activity freshness becomes provable.
    # Close the system invariant only after the final authoritative stall count.
    _final_stalled=db.query(ControlIncident).filter(
        ControlIncident.state=="open",
        ControlIncident.incident_type=="MODULE_STALLED",
    ).count()
    if int(_final_stalled)==0:
        _health_req=db.query(ControlRequirement).filter(ControlRequirement.key=="system.health_requires_function").first()
        if _health_req is not None:
            _health_ex=db.query(ControlExecution).filter(
                ControlExecution.requirement_id==_health_req.id,
            ).order_by(ControlExecution.created_at.desc()).first()
            if _health_ex is not None and str(_health_ex.status or "")=="degraded":
                add_evidence(
                    db,_health_ex,level="L5",evidence_type="module_activity_final_reconcile",
                    source="control_plane_guardian",passed=True,
                    payload={"stalled_modules":0,"post_runtime_ledger":True},
                )
                transition_execution(db,_health_ex,"pass",actual_state={"stalled_modules":0,"post_runtime_ledger":True})
            _health_req.last_verified_at=now
            for _health_inc in db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.incident_type=="SYSTEM_INVARIANT_FAILED",
                ControlIncident.requirement_id==_health_req.id,
            ).all():
                _health_inc.state="resolved"; _health_inc.resolved_at=now; _health_inc.updated_at=now
                _health_inc.remediation="Final post-ledger activity scan has zero stalled executable modules"

    # ARCH_REGISTRY_RUNTIME_EVIDENCE_V1: registry-only rules are architecture
    # metadata, not domain desired-state fields. Verify them through the single
    # canonical architecture registry after Runtime Ledger snapshots exist, then
    # persist L5 proof per rule and close historical false drift/stale incidents.
    from app.services.architecture_registry import validate_control_architecture
    try:
        architecture_validation=validate_control_architecture(db)
    except Exception as architecture_exc:
        architecture_validation={"ok":False,"errors":[{"code":"architecture_validation_exception","error_type":type(architecture_exc).__name__}]}
    if architecture_validation.get("ok"):
        registry_rules=db.query(ControlRequirement).filter(
            ControlRequirement.status=="active",
            ControlRequirement.requirement_type.in_(tuple(REGISTRY_ONLY_REQUIREMENT_TYPES)),
        ).all()
        for _req in registry_rules:
            _actual=dict(_req.desired_state_json or {})
            _actual["registry_validation"]="pass"
            _actual["source_of_truth"]="control_plane_architecture_registry"
            record_runtime_verification(
                db,_req,account_id=None,actual_state=_actual,passed=True,
                source="architecture_registry",
            )
            _req.last_verified_at=now
            for _inc in db.query(ControlIncident).filter(
                ControlIncident.state=="open",
                ControlIncident.requirement_id==_req.id,
                ControlIncident.incident_type.in_(("CONFIG_DRIFT","STALE_VERIFICATION","MODULE_STATE_READ_FAILED")),
            ).all():
                _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
                _inc.remediation="Registry metadata verified by canonical architecture registry; not domain-state drift"
        for _inc in db.query(ControlIncident).filter(
            ControlIncident.state=="open",
            ControlIncident.incident_type=="ARCHITECTURE_REGISTRY_INVALID",
        ).all():
            _inc.state="resolved"; _inc.resolved_at=now; _inc.updated_at=now
            _inc.remediation="Canonical architecture registry validation PASS"
    else:
        _,new=_incident_once(
            db,"ARCHITECTURE_REGISTRY_INVALID","control_plane",None,
            "Canonical architecture registry or Runtime Ledger validation failed",
            priority="P1",details={"errors":list(architecture_validation.get("errors") or [])[:100]},
        )
        created+=int(new)

    db.flush()
    # GUARDIAN_EXTERNAL_P1_WAIT_V1: keep P0 hard, but an explicitly evidenced
    # waiting_external P1 is a dependency, not an internal defect. This mirrors
    # brain_acceptance and never infers externality from the incident name.
    owner_policy_types=("MONEY_GUARD_MISSING_LIMIT","MONEY_GUARD_MISSING_HARD_CAP")
    p0_internal=db.query(ControlIncident).filter(
        ControlIncident.state=="open",ControlIncident.priority=="P0"
    ).count()
    _p1_rows=db.query(ControlIncident).filter(
        ControlIncident.state=="open",ControlIncident.priority=="P1"
    ).all()
    waiting_owner_policy=sum(1 for x in _p1_rows if x.incident_type in owner_policy_types)
    waiting_external_p1=sum(
        1 for x in _p1_rows
        if str((dict(x.details_json or {})).get("dependency_state") or "")=="waiting_external"
        and x.incident_type not in owner_policy_types
    )
    p1_internal=max(0,len(_p1_rows)-waiting_owner_policy-waiting_external_p1)
    p01_internal=int(p0_internal)+int(p1_internal)
    db.commit()
    return {"status":"degraded" if (drifted or conflicts or p01_internal or business_degraded or recovery.get("status")!="pass" or runtime_ledger.get("errors")) else "pass","invariants_seeded":inv,"contracts_seeded":contracts,"adapter_modules":adapter_modules(),"rules_checked":checked,"drifted":drifted,"rule_conflicts":len(conflicts),"dead_rules":dead_count,"open_p0_p1":p01_internal,"waiting_owner_policy":waiting_owner_policy,"waiting_external_p1":waiting_external_p1,"incidents_created":created,"auto_remediated":auto_remediated,"auto_blocked":auto_blocked,"business_modules_diagnosed":business_diagnosed,"business_modules_degraded":business_degraded,"client_supervisor": {"status":fleet.get("status"),"counts":fleet.get("counts"),"checked_at":fleet.get("checked_at")},"runtime_ledger":runtime_ledger,"safe_recovery":recovery,"checked_at":now.isoformat()}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--json',action='store_true'); args=ap.parse_args()
    heartbeat('control_plane','guardian',details={'phase':'start'},min_interval_seconds=1)
    db=SessionLocal()
    try: result=tick(db)
    except Exception as exc:
        db.rollback(); heartbeat('control_plane','guardian',state='error',details={'error_type':type(exc).__name__},min_interval_seconds=1); raise
    finally: db.close()
    heartbeat('control_plane','guardian',details=result,min_interval_seconds=1)
    print(json.dumps(result,ensure_ascii=False) if args.json else result)
    return 0

if __name__=='__main__': raise SystemExit(main())
