"""Read-only durable E2E acceptance for BORIS System Brain.

The report proves production chains from persisted ledger/evidence. It never
performs paid/external actions and never turns a missing proof into PASS.
"""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import text
from app.models.control_plane import ControlEvidence, ControlExecution, ControlOwnerInstruction, ControlRequirement, ControlRequirementVersion, ControlRuleConflict
from app.services.control_plane_adapters import modules as adapter_modules, get as get_adapter, adapter_capabilities
from app.services.brain_modules import MANDATORY_MODULES


def _check(name: str, passed: bool, evidence=None, required=True):
    return {"name":name,"passed":bool(passed),"required":bool(required),"evidence":evidence or {}}


def build_e2e_report(db) -> dict:
    # BRAIN_E2E_DYNAMIC_MODULE_COUNT_V1
    checks=[]
    instructions=db.query(ControlOwnerInstruction).count()
    linked=db.query(ControlOwnerInstruction).filter(text("jsonb_array_length(linked_requirement_ids_json)>0")).count()
    checks.append(_check("OWNER_INTAKE_PERSISTED",instructions>0,{"instructions":instructions}))
    checks.append(_check("OWNER_INTAKE_LINKS_REQUIREMENT",linked>0,{"linked_instructions":linked,"instructions":instructions}))

    reqs=db.query(ControlRequirement).count(); vers=db.query(ControlRequirementVersion).count()
    req_without_version=int(db.execute(text("""select count(*) from control_requirements r where not exists(select 1 from control_requirement_versions v where v.requirement_id=r.id)""")).scalar() or 0)
    checks.append(_check("REQUIREMENT_VERSION_CHAIN",reqs>0 and req_without_version==0,{"requirements":reqs,"versions":vers,"without_version":req_without_version}))

    executions=db.query(ControlExecution).count(); passes=db.query(ControlExecution).filter(ControlExecution.status=='pass').count()
    pass_without_runtime=int(db.execute(text("""select count(*) from control_executions e where e.status='pass' and not exists(select 1 from control_evidence v where v.execution_id=e.id and v.passed=true and v.level in ('L4','L5','L6'))""")).scalar() or 0)
    checks.append(_check("NO_EVIDENCE_NO_PASS_RUNTIME",passes>0 and pass_without_runtime==0,{"executions":executions,"passes":passes,"pass_without_runtime":pass_without_runtime}))

    blocked=db.query(ControlExecution).filter(ControlExecution.status=='blocked').count()
    checks.append(_check("BLOCKED_IS_DURABLE_TERMINAL",blocked>0,{"blocked_executions":blocked}))

    critical_unverified=int(db.execute(text("""select count(*) from control_requirements r where r.status='active' and r.priority='critical' and (r.last_verified_at is null or r.last_verified_at<now()-interval '2 hours')""")).scalar() or 0)
    checks.append(_check("CRITICAL_RULES_HAVE_FRESH_VERIFICATION",critical_unverified==0,{"critical_unverified":critical_unverified}))

    conflicts=db.query(ControlRuleConflict).count(); open_conflicts=db.query(ControlRuleConflict).filter(ControlRuleConflict.state=='open').count()
    checks.append(_check("CONFLICT_LIFECYCLE_PROVEN",conflicts>0 and open_conflicts==0,{"historical_conflicts":conflicts,"open_conflicts":open_conflicts}))

    strategy_changes=int(db.execute(text("""select count(*) from (select execution_id,count(distinct strategy) c from control_execution_attempts group by execution_id having count(distinct strategy)>1) x""")).scalar() or 0)
    checks.append(_check("RETRY_CHANGES_STRATEGY",strategy_changes>0,{"durable_strategy_change_executions":strategy_changes}))

    dup_mop=int(db.execute(text("""select count(*) from (select account_id,avito_message_id,count(*) c from mop_drafts where avito_message_id is not null group by account_id,avito_message_id having count(*)>1) x""")).scalar() or 0)
    checks.append(_check("MOP_EXACTLY_ONCE_IDENTITY",dup_mop==0,{"duplicate_incoming_drafts":dup_mop}))

    profiles={m:adapter_capabilities(get_adapter(m)) for m in adapter_modules() if get_adapter(m)}
    unsafe=[]
    for m,c in profiles.items():
        if c.get('apply') and not c.get('rollback'):
            unsafe.append({"module":m,"reason":"mutable_without_rollback"})
        if c.get('control_mode') in {'external_gated','read_verify'} and c.get('apply'):
            unsafe.append({"module":m,"reason":"gated_module_exposes_generic_apply"})
        if not c.get('external_actions_gated'):
            unsafe.append({"module":m,"reason":"external_actions_not_gated"})
    checks.append(_check("NO_UNSAFE_GENERIC_WRITE_SURFACE",len(profiles)==len(MANDATORY_MODULES) and not unsafe,{"profiles":len(profiles),"unsafe":unsafe}))

    required=[x for x in checks if x['required']]
    passed=sum(1 for x in required if x['passed']); failed=len(required)-passed
    return {"status":"pass" if failed==0 else "failed","passed":passed,"failed":failed,"checks":checks,"checked_at":datetime.now(timezone.utc).isoformat()}
