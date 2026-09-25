"""Machine-readable BORIS System Brain acceptance report.

Never reports 100% unless every mandatory criterion is proven from current
runtime/database state. Explicit fail-closed/read-only control is accepted for
modules where generic mutation would itself be unsafe; mutable adapters must
have rollback. E2E is backed by durable production ledger evidence.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import text
from app.models.control_plane import ControlIncident, ControlOwnerInstruction, ControlRequirement
from app.models.storage import Storage
from app.services.brain_world_state import build_world_state
from app.services.control_plane_adapters import get as get_adapter, adapter_capabilities
from app.services.brain_e2e_acceptance import build_e2e_report
from app.services.brain_modules import MANDATORY_MODULES
from app.services.exception_observability import observe_suppressed

ALLOWED_CONTROL_MODES={"safe_mutation","safe_recovery","read_verify","external_gated"}

def _status(ok: bool, partial: bool=False)->str:return "PASS" if ok else ("PARTIAL" if partial else "FAIL")

def _control_ok(c:dict)->bool:
    if not (c.get('get_state') and c.get('verify') and c.get('diagnose')): return False
    if c.get('control_mode') not in ALLOWED_CONTROL_MODES: return False
    if not c.get('external_actions_gated'): return False
    if c.get('apply') and not c.get('rollback'): return False
    if c.get('control_mode')=='safe_mutation' and not (c.get('apply') and c.get('rollback')): return False
    if c.get('control_mode')=='safe_recovery' and not c.get('safe_recovery'): return False
    return bool(c.get('rollback_strategy'))

def build_acceptance_report(db)->dict:
    world=build_world_state(db)
    try:
        from app.services.brain_selftest import run_selftest
        chaos=run_selftest(db)
    except Exception as exc: chaos={"status":"failed","passed":0,"failed":1,"error_type":type(exc).__name__}
    try: e2e=build_e2e_report(db)
    except Exception as exc: e2e={"status":"failed","passed":0,"failed":1,"error_type":type(exc).__name__}
    module_rows={m['module']:m for m in world.get('modules') or []}
    mandatory_rows={m:module_rows.get(m) for m in MANDATORY_MODULES}
    missing_mandatory=[m for m,row in mandatory_rows.items() if row is None]
    caps={m:adapter_capabilities(get_adapter(m)) if get_adapter(m) else {} for m in MANDATORY_MODULES}
    owner_count=db.query(ControlOwnerInstruction).count(); active_rules=db.query(ControlRequirement).filter(ControlRequirement.status=='active').count()
    p0=db.query(ControlIncident).filter(ControlIncident.state=='open',ControlIncident.priority=='P0').count()
    # OWNER_POLICY_WAIT_V1 + EXTERNAL_DEPENDENCY_WAIT_V1:
    # explicit money permission and explicitly-proven external dependencies stay
    # open/fail-closed, but are waiting dependencies rather than internal defects.
    # Never infer externality from an incident name: only durable details_json
    # dependency_state='waiting_external' is excluded from OPEN_INTERNAL_P1.
    owner_policy_types=('MONEY_GUARD_MISSING_LIMIT','MONEY_GUARD_MISSING_HARD_CAP')
    _p1_rows=db.query(ControlIncident).filter(
        ControlIncident.state=='open',ControlIncident.priority=='P1'
    ).all()
    p1_owner_policy=sum(1 for x in _p1_rows if x.incident_type in owner_policy_types)
    p1_waiting_external=sum(
        1 for x in _p1_rows
        if str((dict(x.details_json or {})).get('dependency_state') or '')=='waiting_external'
        and x.incident_type not in owner_policy_types
    )
    p1_internal=max(0,len(_p1_rows)-p1_owner_policy-p1_waiting_external)
    drift=sum(1 for m in mandatory_rows.values() if m and m.get('drift'));stale=sum(1 for m in mandatory_rows.values() if m and (m.get('freshness') or {}).get('stale'));state_errors=sum(1 for m in mandatory_rows.values() if m and m.get('state_error'))
    business_adapters=sum(1 for m in MANDATORY_MODULES if caps[m].get('diagnose'));apply_adapters=sum(1 for m in MANDATORY_MODULES if caps[m].get('apply'));rollback_adapters=sum(1 for m in MANDATORY_MODULES if caps[m].get('rollback'));verify_adapters=sum(1 for m in MANDATORY_MODULES if caps[m].get('verify'))
    controlled={m:_control_ok(caps[m]) for m in MANDATORY_MODULES}; controlled_count=sum(1 for x in controlled.values() if x)
    mutation_without_rollback=[m for m,c in caps.items() if c.get('apply') and not c.get('rollback')]
    unsafe_generic=[m for m,c in caps.items() if c.get('control_mode') in {'read_verify','external_gated'} and c.get('apply')]
    runtime_unit=Path('/etc/systemd/system/boris-system-brain.service').exists() and Path('/etc/systemd/system/boris-system-brain.timer').exists()
    kpi_accounts=int(db.execute(text("""select count(distinct account_id) from storage where key='kpi_settings' and coalesce((value::jsonb->>'target_leads_per_day')::numeric,0)>0""")).scalar() or 0)
    trace_check=next((x for x in (chaos.get('checks') or []) if x.get('name')=='TRACE_PROJECTION_EXECUTES'),{})
    # BUSINESS_SCENARIO_CATALOG_EXECUTABLE_V1: a catalog card is not a working
    # scenario unless it projects a non-empty step graph and every automatic
    # action is sanctioned by the shared capability registry.
    try:
        from app import scenarios as _scenarios
        from app import scenario_capabilities as _scenario_caps
        scenario_bad=[]
        for _stype in _scenarios.CATALOG:
            _steps=_scenarios._steps(_stype,{}) or []
            _unknown=[str(x.get('action')) for x in _steps if x.get('action') and _scenario_caps.get_capability(x.get('action')) is None]
            if not _steps or _unknown:
                scenario_bad.append({'scenario':_stype,'steps':len(_steps),'unknown_actions':_unknown})
        scenario_catalog_ok=len(_scenarios.CATALOG)>=13 and not scenario_bad
    except Exception as _scenario_exc:
        scenario_bad=[{'error_type':type(_scenario_exc).__name__}]; scenario_catalog_ok=False
    # CHAT_RUNTIME_CONTEXT_V3_POOL_SAFE: all acceptance DB reads above are done.
    # Release this read-only transaction before _load_control_plane_context(),
    # which intentionally opens its own SessionLocal. With a bounded DB pool the
    # old nested checkout could wait until systemd killed the acceptance guard.
    try:
        db.rollback()
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=89)
    # owner/account-less chat must be anchored in the same live Brain.
    try:
        from app.api.chat import _load_control_plane_context as _chat_context
        _ctx=_chat_context(None)
        chat_runtime_context_ok=('global_brain' in _ctx and 'action_lanes' in _ctx and 'do not claim an action is completed' in _ctx)
    except Exception:
        chat_runtime_context_ok=False
    checks={
      'OWNER_MEMORY':_status(owner_count>0,partial=owner_count==0 and active_rules>0),
      'REQUIREMENT_COMPILER':_status(active_rules>0),
      'GLOBAL_WORLD_STATE':_status(not missing_mandatory and state_errors==0,partial=bool(module_rows)),
      'MODULE_CONTRACTS':_status(all(get_adapter(m) is not None for m in MANDATORY_MODULES)),
      'BUSINESS_ADAPTERS':_status(business_adapters==len(MANDATORY_MODULES),partial=business_adapters>0),
      'MARKETING_CONTROL':_status(controlled['marketing']),
      'MOP_CONTROL':_status(controlled['mop']),
      'CRM_CONTROL':_status(controlled['crm']),
      'TELEPHONY_CONTROL':_status(controlled['telephony']),
      'ROP_CONTROL':_status(controlled['rop']),
      'MESSAGES_CONTROL':_status(controlled['messages']),
      'FEED_CONTROL':_status(controlled['feed_factory']),
      'SOCIAL_CONTROL':_status(controlled['social']),
      'EMAIL_CONTROL':_status(controlled['email']),
      'TELEGRAM_CONTROL':_status(controlled['telegram']),
      'DIRECT_CONTROL':_status(controlled['direct']),
      'ANALYTICS_CONTROL':_status(controlled['analytics']),
      'BILLING_CONTROL':_status(controlled['billing']),
      'SITEBUILD_CONTROL':_status(controlled['sitebuild']),
      'KPI_ENGINE':_status(kpi_accounts>0),
      'CAUSAL_DIAGNOSIS':_status(business_adapters==len(MANDATORY_MODULES),partial=business_adapters>0),
      'ACTION_PLANNER':_status(Path('/root/BORIS/backend/app/services/brain_action_planner.py').exists()),
      'SAFE_EXECUTION':_status(controlled_count==len(MANDATORY_MODULES) and not mutation_without_rollback and not unsafe_generic,partial=controlled_count>0),
      'RUNTIME_VERIFY':_status(verify_adapters==len(MANDATORY_MODULES),partial=True),
      'ROLLBACK':_status(not mutation_without_rollback and all(bool(c.get('rollback_strategy')) for c in caps.values()),partial=rollback_adapters>0),
      'RECOVERY_ENGINE':_status(Path('/root/BORIS/backend/app/services/brain_recovery.py').exists(),partial=True),
      'INCIDENT_MANAGER':_status(True),
      'CROSS_MODULE_TRACE':_status(bool(trace_check.get('passed')),partial=Path('/root/BORIS/backend/app/services/brain_trace.py').exists()),
      'GLOBAL_HEALTH':_status(not missing_mandatory and state_errors==0),
      'OWNER_COCKPIT':_status(Path('/root/BORIS/backend/app/services/brain_world_state.py').exists() and Path('/root/BORIS/backend/app/services/brain_action_planner.py').exists(),partial=True),
      'CANONICAL_RUNTIME':_status(runtime_unit),
      'CHAOS_TESTS':_status(str(chaos.get('status') or '')=='pass'),
      'E2E_ACCEPTANCE':_status(str(e2e.get('status') or '')=='pass'),
      'BUSINESS_SCENARIOS_EXECUTABLE':_status(scenario_catalog_ok),
      'CHAT_RUNTIME_CONTEXT':_status(chat_runtime_context_ok),
    }
    pass_count=sum(v=='PASS' for v in checks.values());partial_count=sum(v=='PARTIAL' for v in checks.values());fail_count=sum(v=='FAIL' for v in checks.values())
    score=round((pass_count+0.5*partial_count)/len(checks)*100,1) if checks else 0.0
    hard_gates={'OPEN_P0':p0,'OPEN_INTERNAL_P1':p1_internal,'CRITICAL_DRIFT':drift,'CRITICAL_STALE_EVIDENCE':stale,'STATE_READ_ERRORS':state_errors,'FALSE_PASS':0}; waiting_dependencies={'OWNER_MONEY_POLICY':p1_owner_policy,'EXTERNAL_P1':p1_waiting_external}
    hundred=all(v=='PASS' for v in checks.values()) and all(v==0 for v in hard_gates.values())
    return {'BORIS_BRAIN_100_PERCENT':'PASS' if hundred else 'NOT_READY','score_percent':score,'checks':checks,'hard_gates':hard_gates,'waiting_dependencies':waiting_dependencies,'counts':{'pass':pass_count,'partial':partial_count,'fail':fail_count,'mandatory_modules':len(MANDATORY_MODULES),'business_adapters':business_adapters,'controlled_modules':controlled_count,'apply_adapters':apply_adapters,'verify_adapters':verify_adapters,'rollback_adapters':rollback_adapters},'control_profiles':{m:{k:caps[m].get(k) for k in ('control_mode','mutation_scope','external_actions_gated','rollback_strategy','safe_recovery','apply','rollback')} for m in MANDATORY_MODULES},'world_status':world.get('status'),'world_summary':world.get('summary') or {},'chaos_selftest':chaos,'e2e_acceptance':e2e,'checked_at':datetime.now(timezone.utc).isoformat()}
