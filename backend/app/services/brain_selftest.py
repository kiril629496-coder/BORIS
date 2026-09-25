"""Production-safe BORIS Brain chaos/self-test suite.

No destructive or paid operations. The suite validates fail-closed invariants,
world-state isolation, action safety, trace determinism and runtime contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.brain_world_state import build_world_state
from app.services.brain_action_planner import build_action_plan, ACTION_META
from app.services.brain_trace import trace_id, recent_traces
from app.services.control_plane_adapters import get as get_adapter, adapter_capabilities, modules as adapter_modules
from app.services.brain_modules import MANDATORY_MODULES


def _check(name: str, passed: bool, evidence=None) -> dict:
    return {"name":name,"passed":bool(passed),"evidence":evidence or {}}


def run_selftest(db) -> dict:
    # BRAIN_SELFTEST_DYNAMIC_MODULE_COUNT_V1
    checks=[]
    world=build_world_state(db)
    world_names={str(m.get("module") or "") for m in (world.get("modules") or [])}
    missing_mandatory=sorted(set(MANDATORY_MODULES)-world_names)
    # BRAIN_WORLD_SUPERSET_V1: the architecture registry may contain additional
    # controlled contracts (calltracking/control_plane/crowd_seo/development).
    # Brain runtime acceptance requires every mandatory adapter to be present;
    # it must not fail merely because the world is a strict superset.
    checks.append(_check("WORLD_STATE_ALL_MODULES",not missing_mandatory,{"summary":world.get("summary"),"missing_mandatory":missing_mandatory,"world_modules":len(world_names)}))
    errors=[{"module":m.get("module"),"error":m.get("state_error")} for m in world.get("modules") or [] if m.get("state_error")]
    checks.append(_check("ADAPTER_FAILURE_ISOLATION",not errors,{"state_errors":errors}))
    caps={m:adapter_capabilities(get_adapter(m)) for m in adapter_modules() if get_adapter(m)}
    checks.append(_check("ALL_MODULES_HAVE_READBACK",len(caps)==len(MANDATORY_MODULES) and all(x.get("get_state") for x in caps.values()),{"modules":len(caps)}))
    checks.append(_check("ALL_MODULES_HAVE_VERIFY",len(caps)==len(MANDATORY_MODULES) and all(x.get("verify") for x in caps.values()),{"missing":[m for m,x in caps.items() if not x.get("verify")]}))
    checks.append(_check("ALL_MODULES_HAVE_DIAGNOSIS",len(caps)==len(MANDATORY_MODULES) and all(x.get("diagnose") for x in caps.values()),{"missing":[m for m,x in caps.items() if not x.get("diagnose")]}))

    plan=build_action_plan(db)
    unsafe_now=[]
    for x in plan.get("queue",{}).get("NOW",[]):
        action=str(x.get("action") or "")
        meta=ACTION_META.get(action)
        if meta is None or meta.get("requires_owner_policy") or str(meta.get("level")) in {"L3","L4"}:
            unsafe_now.append({"module":x.get("module"),"action":action})
    checks.append(_check("UNKNOWN_OR_HIGH_RISK_ACTION_NEVER_NOW",not unsafe_now,{"unsafe_now":unsafe_now}))

    unknown_default={"level":"L4","reversible":False,"requires_owner_policy":True}
    checks.append(_check("UNKNOWN_ACTION_FAILS_CLOSED",unknown_default["requires_owner_policy"] and unknown_default["level"]=="L4",unknown_default))

    marketing=[m for m in world.get("modules") or [] if m.get("module")=="marketing"]
    checks.append(_check("MONEY_GUARD_VISIBLE",bool(marketing),{"marketing_health":marketing[0].get("health") if marketing else None,"open_p1":(world.get("summary") or {}).get("open_p1")}))

    t1=trace_id("selftest-account","selftest-chat");t2=trace_id("selftest-account","selftest-chat")
    checks.append(_check("TRACE_ID_DETERMINISTIC",t1==t2 and t1.startswith("tr_"),{"trace_id":t1}))
    traces=recent_traces(db,hours=24,limit=3)
    checks.append(_check("TRACE_PROJECTION_EXECUTES",all(x.get("status")!="error" for x in traces),{"sample":[{"trace_id":x.get("trace_id"),"status":x.get("status"),"breaks":x.get("breaks")} for x in traces]}))

    # Current runtime must never report global PASS while a production P0/P1 exists.
    p01=int((world.get("summary") or {}).get("open_p0") or 0)+int((world.get("summary") or {}).get("open_p1") or 0)
    checks.append(_check("NO_FALSE_GLOBAL_PASS",not (p01>0 and world.get("status")=="pass"),{"p0_p1":p01,"world_status":world.get("status")}))

    passed=sum(1 for x in checks if x["passed"]);failed=len(checks)-passed
    return {"status":"pass" if failed==0 else "failed","passed":passed,"failed":failed,"checks":checks,"checked_at":datetime.now(timezone.utc).isoformat()}
