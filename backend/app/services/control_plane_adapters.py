"""Runtime adapters for BORIS Control Plane.

Adapters deliberately separate read/verify from mutation. The default contracts
are read-only until a domain supplies an explicit safe apply function.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from app.models.storage import Storage


@dataclass(frozen=True)
class Adapter:
    module: str
    get_state: Callable
    preflight: Callable | None = None
    apply: Callable | None = None
    verify: Callable | None = None
    rollback: Callable | None = None
    diagnose: Callable | None = None
    capabilities: Callable | None = None


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    _REGISTRY[adapter.module] = adapter


def get(module: str) -> Adapter | None:
    return _REGISTRY.get(str(module or ""))


def modules() -> list[str]:
    return sorted(_REGISTRY)


def _storage_json(db, account_id: str, key: str) -> dict:
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).order_by(Storage.id.desc()).first()
    if not row: return {}
    try: return json.loads(row.value or "{}")
    except Exception: return {}


def marketing_state(db, account_id: str | None) -> dict:
    # BUSINESS_KPI_PORTFOLIO_HEALTH_V1: System Brain is invoked unscoped, so an
    # unscoped marketing adapter must summarize real account KPI health instead
    # of returning a meaningless green `unscoped` placeholder.
    if not account_id:
        account_ids = [str(x[0]) for x in db.query(Storage.account_id).filter(Storage.key == "kpi_settings").distinct().all() if x and x[0]]
        portfolio = []
        excluded_periods = []
        for aid in account_ids:
            if aid.startswith("qa") or aid.startswith("user:"):
                continue
            # MARKETING_PORTFOLIO_CANONICAL_ENTITLEMENT_V2: the paid KPI
            # portfolio is an execution/obligation view, therefore it must use
            # the same canonical current service-period truth as planner,
            # mandate/DB money guards and get_goal_auto_accounts. Unknown is
            # fail-closed and remains visible in Client Supervisor as
            # unknown_period; it must not create a budget obligation.
            from app.services.control_plane_adapters_ext import marketing_service_entitlement
            entitlement = marketing_service_entitlement(db, aid)
            if str(entitlement.get("state") or "") != "active":
                excluded_periods.append({
                    "account_id": aid,
                    "state": entitlement.get("state"),
                    "source": entitlement.get("source"),
                    "until": entitlement.get("until"),
                })
                continue
            state = marketing_state(db, aid)
            state["service_entitlement"] = entitlement
            target = float(state.get("target_leads_per_day") or 0)
            mode = str(state.get("autopilot_mode") or "")
            if target > 0 and mode in {"goal_auto", "always_auto"}:
                portfolio.append(state | {"account_id": aid})
        return {
            "account_id": None,
            "state": "portfolio",
            "portfolio_accounts": portfolio,
            "portfolio_count": len(portfolio),
            "target_leads_total": sum(float(x.get("target_leads_per_day") or 0) for x in portfolio),
            "actual_leads_total": sum(float(x.get("actual_leads") or 0) for x in portfolio),
            "inactive_or_unknown_accounts_excluded": excluded_periods,
            "inactive_or_unknown_accounts_excluded_count": len(excluded_periods),
        }
    autopilot = _storage_json(db, account_id, "autopilot_settings")
    kpi = _storage_json(db, account_id, "kpi_settings")
    runtime = _storage_json(db, account_id, "virtual_marketer_runtime")
    health = _storage_json(db, account_id, "virtual_marketer_account_health")

    # CONTROL_PLANE_DAILY_BUDGET_PROVENANCE_V1:
    # legacy kpi_settings may contain numeric 0 simply because no daily money
    # authority was ever confirmed. Do not present that absence as an explicit
    # owner decision. Canonical ControlRequirement evidence decides whether a
    # daily budget is configured or still missing/unconfirmed.
    _budget_policy={
        "state":"unknown",
        "source":None,
        "owner_confirmed":False,
        "requirement_id":None,
        "requirement_source_type":None,
    }
    try:
        from app.models.control_plane import ControlRequirement as _BudgetRequirement
        _budget_req=(db.query(_BudgetRequirement)
            .filter(_BudgetRequirement.key==f"production.marketing.autopilot.{account_id}")
            .first())
        _budget_value=kpi.get("daily_budget_limit_rub")
        try: _budget_num=float(_budget_value) if _budget_value is not None else None
        except Exception: _budget_num=None
        if _budget_req is not None:
            _desired=dict(_budget_req.desired_state_json or {})
            _source=dict(_budget_req.source_json or {})
            _constraints=list(_budget_req.constraints_json or [])
            _has_desired_budget="daily_budget_limit_rub" in _desired
            _desired_budget=_desired.get("daily_budget_limit_rub")
            _required_missing=any(
                isinstance(x,dict)
                and str(x.get("type") or "")=="daily_budget_required"
                and x.get("value") is None
                for x in _constraints
            )
            _owner_evidence=bool(
                str(getattr(_budget_req,"raw_owner_text","") or "").strip()
                or str(_source.get("type") or "") in {"owner_instruction","owner_rule","explicit_owner"}
            )
            _budget_policy.update({
                "requirement_id":str(_budget_req.id),
                "requirement_source_type":_source.get("type"),
                "owner_confirmed":_owner_evidence,
            })
            if _has_desired_budget:
                _budget_policy.update({
                    "state":"configured" if float(_desired_budget or 0)>0 else "explicit_nonpositive",
                    "source":"control_requirement",
                })
            elif _required_missing:
                _budget_policy.update({"state":"missing_unconfirmed","source":"control_requirement"})
            elif _budget_num is not None and _budget_num>0:
                _budget_policy.update({"state":"configured","source":"kpi_settings"})
            else:
                _budget_policy.update({"state":"missing_or_zero_unproven","source":"legacy_kpi_settings"})
        elif _budget_num is not None and _budget_num>0:
            _budget_policy.update({"state":"configured","source":"kpi_settings"})
        else:
            _budget_policy.update({"state":"missing_or_zero_unproven","source":"legacy_kpi_settings"})
    except Exception:
        _budget_policy.update({"state":"unknown","source":"provenance_read_failed"})
    # CONTROL_PLANE_MARKETER_HEALTH_LATEST_EVIDENCE_V1: Command Center must not
    # expose a sticky deferred/incident flag after a later successful marketer
    # cycle. Reconcile the projection from the newest authoritative runtime in
    # exactly the same direction as System Guardian; do not mutate source rows.
    from datetime import datetime, timezone
    def _runtime_dt(value):
        try:
            parsed=datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
            # MARKETER_HEALTH_TIMEZONE_NORMALIZATION_V1: legacy/storage runtime
            # timestamps can be naive while newer evidence is timezone-aware.
            # They represent UTC operational timestamps; normalize before compare.
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except Exception:
            return None
    _ht = _runtime_dt(health.get("checked_at"))
    _rt = _runtime_dt(runtime.get("finished_at") or runtime.get("last_run_at"))
    if _rt and (_ht is None or _rt > _ht) and str(runtime.get("status") or "").lower() in {"ok", "pass", "healthy"}:
        health = {
            "state": "ok",
            "checked_at": _rt.isoformat(),
            "stage": "virtual_marketer_runtime",
            "reason": str(runtime.get("mode") or "latest virtual marketer cycle completed successfully")[:500],
            "reconciled_from_runtime": True,
        }
    # CONTROL_PLANE_MARKETING_DAY_TRUTH_V1: Avito daily stats and money/KPI
    # controls use the Moscow business day. UTC is still yesterday between
    # 00:00 and 02:59 MSK, so using datetime.now().date() here exposed stale
    # plan/fact during the first three hours of every business day.
    from app.services.marketing_clock import marketing_today_iso
    daily = _storage_json(db, account_id, "daily_stats:" + marketing_today_iso())
    items = daily.get("items") if isinstance(daily.get("items"), list) else []
    # CONTROL_PLANE_PROVIDER_STATS_LAG_TRUTH_V1: the snapshot key/date is the
    # Moscow business day, while Avito item statistics can legitimately lag one
    # day. Never label yesterday's contacts as today's KPI fact. Preserve the
    # observed value for diagnostics, but expose actual_leads only when provider
    # stats_date matches the current business date.
    _business_date = str(daily.get("date") or marketing_today_iso())[:10]
    _provider_stats_date = str(daily.get("stats_date") or daily.get("date") or "")[:10]
    _actual_leads_observed = sum(float(x.get("contacts") or 0) for x in items if isinstance(x,dict))
    _stats_current = bool(_provider_stats_date and _provider_stats_date == _business_date)
    actual_leads = _actual_leads_observed if _stats_current else None

    # Owner-facing runtime text must reflect money-policy provenance. Preserve the
    # raw writer output for audit, but when the canonical rule says the daily
    # budget is missing/unconfirmed, do not describe legacy numeric 0 as an
    # explicit BORIS/owner budget.
    _runtime_mode_raw=str(runtime.get("mode") or "")
    _runtime_mode_projected=_runtime_mode_raw
    _runtime_reason_raw=str(runtime.get("money_block_reason") or "")
    _runtime_reason_projected=_runtime_reason_raw
    if _budget_policy.get("state")=="missing_unconfirmed" and _runtime_reason_raw=="budget_zero_existing_spend":
        _money=dict(runtime.get("money_block_evidence") or {})
        try: _spent=float(_money.get("spent_today_rub") or 0)
        except Exception: _spent=0.0
        try: _presence=float(_money.get("presence_rub") or 0)
        except Exception: _presence=0.0
        _spent_note=(f" Avito уже списал {_spent:.0f} ₽ сегодня"
                     + (f" (из них действующее размещение {_presence:.0f} ₽)." if _presence>0 else ".")
                     if _spent>0 else "")
        _runtime_mode_projected=(
            "WAITING_OWNER: Дневной бюджет BORIS не подтверждён."
            + _spent_note
            + " BORIS не запускает новые повышения ставок или расширение размещения "
              "до появления подтверждённого дневного лимита. Существующее платное "
              "размещение не отключается автоматически без отдельного решения."
        )
        _runtime_reason_projected="budget_missing_unconfirmed_existing_spend"
    return {
        "autopilot_mode": autopilot.get("mode"),
        "target_leads_per_day": kpi.get("target_leads_per_day"),
        "daily_budget_limit_rub": kpi.get("daily_budget_limit_rub"),
        "daily_budget_policy_state": _budget_policy.get("state"),
        "daily_budget_policy_source": _budget_policy.get("source"),
        "daily_budget_owner_confirmed": bool(_budget_policy.get("owner_confirmed")),
        "daily_budget_requirement_id": _budget_policy.get("requirement_id"),
        "daily_budget_requirement_source_type": _budget_policy.get("requirement_source_type"),
        "max_cost_per_lead_rub": kpi.get("max_cost_per_lead_rub"),
        "hard_max_bid_rub": kpi.get("hard_max_bid_rub"),
        "bid_autopilot": bool(kpi.get("bid_autopilot")),
        "placement_package_content_only": bool(
            kpi.get("placement_package_content_only")
            or kpi.get("bid_autopilot") is False
        ),
        "allowed_optimization_scope": [
            str(x) for x in (kpi.get("allowed_optimization_scope") or [])
            if str(x).strip()
        ],
        "runtime_status": runtime.get("status"),
        "runtime_applied": runtime.get("applied"),
        "runtime_failed": runtime.get("failed"),
        "runtime_planned": runtime.get("planned"),
        "runtime_mode": _runtime_mode_projected,
        "runtime_mode_raw": _runtime_mode_raw,
        "runtime_money_block_reason": _runtime_reason_projected,
        "runtime_money_block_reason_raw": _runtime_reason_raw,
        "runtime_last_run_at": runtime.get("last_run_at") or runtime.get("finished_at"),
        "actual_leads": actual_leads,
        "actual_leads_observed": _actual_leads_observed,
        "business_date": _business_date,
        "provider_stats_current": _stats_current,
        "provider_stats_lagging": bool(daily) and not _stats_current,
        "items_count": int(daily.get("items_count") or len(items) or 0),
        "active_items": sum(1 for x in items if isinstance(x,dict) and str(x.get("status") or "").lower()=="active"),
        "inventory_complete": bool((daily.get("completeness") or {}).get("inventory_complete")) if isinstance(daily.get("completeness"),dict) else False,
        "stats_date": daily.get("stats_date") or daily.get("date"),
        "health_state": health.get("state"),
        "health_checked_at": health.get("checked_at"),
    }



def _marketing_preflight(db, account_id: str | None, requirement) -> dict:
    from app.models.account import Account
    if not account_id:
        return {"ok": False, "reason": "account_scope_required"}
    if db.query(Account).filter(Account.account_id == str(account_id)).first() is None:
        return {"ok": False, "reason": "account_not_found"}
    desired = dict(getattr(requirement, "desired_state_json", {}) or {})
    allowed = {"autopilot_mode", "target_leads_per_day", "daily_budget_limit_rub", "max_cost_per_lead_rub", "hard_max_bid_rub"}
    unsupported = sorted(set(desired) - allowed)
    if unsupported:
        return {"ok": False, "reason": "unsupported_marketing_fields", "fields": unsupported}
    source = dict(getattr(requirement, "source_json", {}) or {})
    if not bool(source.get("allow_control_write")):
        return {"ok": False, "reason": "control_write_not_authorized"}
    from app.services.reliability import module_blocked
    blocked, reason = module_blocked(db, "marketing", str(account_id))
    if blocked:
        return {"ok": False, "reason": "kill_switch:" + str(reason or "marketing blocked")}
    mode = desired.get("autopilot_mode")
    if mode is not None and mode not in {"always_ask", "always_auto", "goal_auto", "dates"}:
        return {"ok": False, "reason": "invalid_autopilot_mode"}
    for key in ("target_leads_per_day", "daily_budget_limit_rub", "max_cost_per_lead_rub", "hard_max_bid_rub"):
        if key in desired:
            try: value=float(desired[key])
            except Exception: return {"ok": False, "reason": f"invalid_numeric:{key}"}
            if value <= 0:
                return {"ok": False, "reason": f"non_positive_value:{key}"}
    return {"ok": True}


def _locked_storage_json(db, account_id: str, key: str):
    row=(db.query(Storage).filter(Storage.account_id==account_id, Storage.key==key)
         .order_by(Storage.id.desc()).with_for_update().first())
    if not row: return None, {}
    try: value=json.loads(row.value or "{}")
    except Exception: value={}
    return row, value


def _marketing_apply(db, account_id: str | None, requirement, drift: dict) -> dict:
    if not account_id: raise RuntimeError("account_scope_required")
    desired=dict(requirement.desired_state_json or {})
    ap_row, ap_before=_locked_storage_json(db, str(account_id), "autopilot_settings")
    kpi_row, kpi_before=_locked_storage_json(db, str(account_id), "kpi_settings")
    current={
        "autopilot_mode": ap_before.get("mode"),
        "target_leads_per_day": kpi_before.get("target_leads_per_day"),
        "daily_budget_limit_rub": kpi_before.get("daily_budget_limit_rub"),
        "max_cost_per_lead_rub": kpi_before.get("max_cost_per_lead_rub"),
        "hard_max_bid_rub": kpi_before.get("hard_max_bid_rub"),
    }
    # Compare-and-set: never overwrite a state that changed after planning/readback.
    for field, meta in (drift or {}).items():
        if current.get(field) != (meta or {}).get("actual"):
            raise RuntimeError(f"concurrent_state_changed:{field}")
    ap_after=dict(ap_before); kpi_after=dict(kpi_before)
    if "autopilot_mode" in desired: ap_after["mode"]=desired["autopilot_mode"]
    for key in ("target_leads_per_day","daily_budget_limit_rub","max_cost_per_lead_rub","hard_max_bid_rub"):
        if key in desired:
            kpi_after[key]=float(desired[key])
    if "max_cost_per_lead_rub" in desired:
        kpi_after["max_cost_per_lead_source"]="explicit_control_plane_owner_rule"
    def save(row,key,value):
        raw=json.dumps(value,ensure_ascii=False)
        if row: row.value=raw
        else: db.add(Storage(account_id=str(account_id),key=key,value=raw))
    save(ap_row,"autopilot_settings",ap_after)
    save(kpi_row,"kpi_settings",kpi_after)
    db.flush()
    return {
        "before":{"autopilot_settings":ap_before,"kpi_settings":kpi_before},
        "after":{"autopilot_settings":ap_after,"kpi_settings":kpi_after},
        "changed_fields":sorted(drift or {}),
    }


def _marketing_rollback(db, account_id: str | None, requirement, apply_result: dict) -> dict:
    if not account_id: raise RuntimeError("account_scope_required")
    before=dict((apply_result or {}).get("before") or {})
    expected=dict((apply_result or {}).get("after") or {})
    restored=[]
    for key in ("autopilot_settings","kpi_settings"):
        row, current=_locked_storage_json(db,str(account_id),key)
        expected_value=dict(expected.get(key) or {})
        # Never rollback over a newer concurrent owner/UI change.
        if current != expected_value:
            raise RuntimeError(f"rollback_concurrent_state_changed:{key}")
        old=dict(before.get(key) or {})
        raw=json.dumps(old,ensure_ascii=False)
        if row: row.value=raw
        else: db.add(Storage(account_id=str(account_id),key=key,value=raw))
        restored.append(key)
    db.flush()
    return {"restored":restored}

def _generic_state(db, account_id: str | None, module: str) -> dict:
    # Generic adapters expose durable module heartbeat/state without pretending
    # they can mutate a domain they do not own.
    from app.models.reliability import ReliabilityHeartbeat
    q = db.query(ReliabilityHeartbeat).filter(ReliabilityHeartbeat.module == module)
    if account_id: q = q.filter(ReliabilityHeartbeat.account_id.in_((str(account_id), "*")))
    rows = q.order_by(ReliabilityHeartbeat.last_seen_at.desc()).limit(20).all()
    return {"workers": [{"worker_id": r.worker_id, "state": r.state, "account_id": r.account_id, "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None, "details": r.details_json or {}} for r in rows]}


def _marketing_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    # BUSINESS_KPI_PORTFOLIO_DIAGNOSIS_V1: when System Brain asks for marketing
    # health without an account scope, diagnose every autonomous KPI account.
    portfolio = actual.get("portfolio_accounts") if isinstance(actual, dict) else None
    if isinstance(portfolio, list):
        blocked = []
        inventory_blocked = []
        off_target = []
        for state in portfolio:
            if not isinstance(state, dict):
                continue
            aid = str(state.get("account_id") or "")
            target_i = float(state.get("target_leads_per_day") or 0)
            leads_i = float(state.get("actual_leads") or 0)
            budget_i = float(state.get("daily_budget_limit_rub") or 0)
            cap_i = float(state.get("hard_max_bid_rub") or 0)
            health_i = str(state.get("health_state") or "").lower()
            if target_i <= 0:
                continue
            # Reaching today's KPI is healthy even if the owner configured no
            # further spend. A missing budget/cap is a blocker only while the
            # target is still unmet.
            if leads_i < target_i and (budget_i <= 0 or cap_i <= 0):
                blocked.append({
                    "account_id": aid, "target": target_i, "actual": leads_i,
                    "daily_budget_limit_rub": budget_i,
                    "daily_budget_policy_state": state.get("daily_budget_policy_state"),
                    "daily_budget_policy_source": state.get("daily_budget_policy_source"),
                    "daily_budget_owner_confirmed": bool(state.get("daily_budget_owner_confirmed")),
                    "hard_max_bid_rub": cap_i, "health_state": health_i,
                })
            elif leads_i < target_i and int(state.get("active_items") or 0) <= 0:
                inventory_blocked.append({"account_id": aid, "target": target_i, "actual": leads_i, "active_items": int(state.get("active_items") or 0), "health_state": health_i})
            elif leads_i < target_i:
                off_target.append({"account_id": aid, "target": target_i, "actual": leads_i, "runtime_mode": state.get("runtime_mode"), "health_state": health_i})
        if blocked or inventory_blocked:
            # PORTFOLIO_DEPENDENCY_WAIT_V1: explicit missing money permission and
            # zero inventory are business dependencies, not evidence that the
            # marketing runtime itself is broken. Keep them visible as waiting /
            # blocked account states while all safe non-money recovery continues.
            # Business health remains degraded until the KPI dependency is
            # actually resolved. The classification/evidence distinguishes an
            # owner/external dependency from an internal runtime fault; the
            # guardian must not turn the whole portfolio green merely because
            # the worker process itself is healthy.
            return {"classification": "PORTFOLIO_KPI_WAITING_DEPENDENCY", "health": "degraded", "dependency_state": "waiting_owner" if blocked else "waiting_external", "confidence": 1.0, "evidence_for": (blocked + inventory_blocked)[:20], "money_policy_blocked": blocked[:20], "inventory_blocked": inventory_blocked[:20], "also_off_target": off_target[:20], "safe_action": "surface_owner_policy_and_continue_nonmoney_recovery", "owner_action_required": bool(blocked)}
        if off_target:
            return {"classification": "PORTFOLIO_KPI_OFF_TARGET", "health": "degraded", "confidence": 1.0, "evidence_for": off_target[:20], "safe_action": "continue_account_scoped_kpi_recovery", "owner_action_required": False}
        return {"classification": "PORTFOLIO_KPI_HEALTHY", "health": "pass", "confidence": 1.0, "evidence_for": [{"accounts": len(portfolio)}], "safe_action": None, "owner_action_required": False}

    target = float(actual.get("target_leads_per_day") or 0)
    leads = float(actual.get("actual_leads") or 0)
    mode = str(actual.get("autopilot_mode") or "")
    budget = float(actual.get("daily_budget_limit_rub") or 0)
    cap = float(actual.get("hard_max_bid_rub") or 0)
    planned = int(actual.get("runtime_planned") or 0)
    applied = int(actual.get("runtime_applied") or 0)
    runtime_mode = str(actual.get("runtime_mode") or "").strip()
    content_only = bool(
        actual.get("placement_package_content_only")
        or actual.get("bid_autopilot") is False
    )
    if target > 0 and leads < target and mode in {"goal_auto", "always_auto"} and content_only:
        return {
            "classification": "KPI_CONTENT_ONLY_OFF_TARGET",
            "health": "degraded",
            "confidence": 1.0,
            "evidence_for": [{
                "target": target,
                "actual": leads,
                "bid_autopilot": bool(actual.get("bid_autopilot")),
                "allowed_optimization_scope": actual.get("allowed_optimization_scope") or [],
            }],
            "safe_action": "continue_account_scoped_kpi_recovery",
            "owner_action_required": False,
        }
    # ACCOUNT_MONEY_DEPENDENCY_WAIT_V1: missing owner money authority is a
    # business dependency, not a runtime crash. Paid actions stay blocked by the
    # CPX provenance/bid guards, while read-only and non-money recovery must
    # continue (content diagnosis, title/photo hypotheses, CRM/reactivation).
    if target > 0 and leads < target and mode in {"goal_auto", "always_auto"} and budget <= 0:
        return {
            "classification": "KPI_WAITING_MONEY_POLICY",
            "health": "degraded",
            "dependency_state": "waiting_owner",
            "confidence": 1.0,
            "evidence_for": [{
                "daily_budget_limit_rub": budget,
                "daily_budget_policy_state": actual.get("daily_budget_policy_state"),
                "daily_budget_policy_source": actual.get("daily_budget_policy_source"),
                "daily_budget_owner_confirmed": bool(actual.get("daily_budget_owner_confirmed")),
                "autopilot_mode": mode,
            }],
            "safe_action": "surface_owner_policy_and_continue_nonmoney_recovery",
            "owner_action_required": True,
        }
    if target > 0 and leads < target and mode in {"goal_auto", "always_auto"} and cap <= 0:
        return {
            "classification": "KPI_WAITING_MONEY_POLICY",
            "health": "degraded",
            "dependency_state": "waiting_owner",
            "confidence": 1.0,
            "evidence_for": [{"hard_max_bid_rub": cap, "autopilot_mode": mode}],
            "safe_action": "surface_owner_policy_and_continue_nonmoney_recovery",
            "owner_action_required": True,
        }
    if target > 0 and leads < target and planned <= 0 and applied <= 0 and not runtime_mode:
        return {"classification": "KPI_MISS_WITHOUT_ACTION", "health": "degraded", "confidence": 1.0, "evidence_for": [{"target": target, "actual": leads, "runtime_planned": planned, "runtime_applied": applied}], "safe_action": "run_marketing_diagnosis", "owner_action_required": False}
    if target > 0 and leads < target:
        return {"classification": "KPI_OFF_TARGET", "health": "degraded", "confidence": 1.0, "evidence_for": [{"target": target, "actual": leads}], "safe_action": "observe_existing_plan", "owner_action_required": False}
    return {"classification": "ON_TARGET_OR_NO_KPI", "health": "pass", "confidence": 1.0, "evidence_for": [{"target": target, "actual": leads}], "safe_action": None, "owner_action_required": False}


def _mop_state(db, account_id: str | None) -> dict:
    from datetime import datetime, timezone, timedelta
    from app.models.mop_draft import MopDraft
    from sqlalchemy import text as _mop_sql
    now = datetime.now(timezone.utc); since = now - timedelta(hours=24)
    q = db.query(MopDraft)
    if account_id: q = q.filter(MopDraft.account_id == str(account_id))
    drafts_24h = q.filter(MopDraft.created_at >= since).count()
    sent_24h = q.filter(MopDraft.sent_at >= since).count()
    send_failed = q.filter(MopDraft.status == "send_failed", MopDraft.created_at >= since).count()
    send_failed_all = q.filter(MopDraft.status == "send_failed").count()
    unresolved_send_failed = int(db.execute(_mop_sql("""
      SELECT count(*) FROM mop_drafts d
       WHERE (:account_id='' OR d.account_id=:account_id)
         AND d.status='send_failed' AND d.created_at>=now()-interval '24 hours'
         AND NOT EXISTS (SELECT 1 FROM messenger_messages m
          WHERE m.account_id=d.account_id AND m.avito_chat_id=d.avito_chat_id
            AND lower(m.direction) LIKE 'out%'
            AND m.avito_created_at > COALESCE((SELECT i.avito_created_at FROM messenger_messages i
              WHERE i.account_id=d.account_id AND i.avito_message_id=d.avito_message_id LIMIT 1),0))
    """), {"account_id":str(account_id or "")}).scalar() or 0)
    unresolved_ready_rows = db.execute(_mop_sql("""
      SELECT d.account_id,d.id,d.created_at FROM mop_drafts d
       WHERE (:account_id='' OR d.account_id=:account_id) AND d.status='draft_ready'
         AND EXISTS (SELECT 1 FROM messenger_messages m WHERE m.account_id=d.account_id AND m.avito_chat_id=d.avito_chat_id
           AND lower(m.direction) LIKE 'in%'
           AND m.avito_created_at=(SELECT max(m2.avito_created_at) FROM messenger_messages m2 WHERE m2.account_id=d.account_id AND m2.avito_chat_id=d.avito_chat_id))
    """), {"account_id":str(account_id or "")}).mappings().all()
    auto_send_ready=[]
    auto_send_policy_unset=[]
    entitlement_wait_ready=[]
    for rr in unresolved_ready_rows:
        aid=str(rr['account_id'])
        cfg=_storage_json(db,aid,"mop_crm_sales_settings")
        try:
            from app.api.messenger import get_manager_balance as _mop_balance_for_ready
            _entitlement_active=bool((_mop_balance_for_ready(aid) or {}).get("active"))
        except Exception:
            _entitlement_active=False
        if not _entitlement_active:
            entitlement_wait_ready.append(rr)
            continue
        # MOP_AUTOSEND_POLICY_EXPLICIT_V1: missing auto_send is not the same
        # thing as an explicit manual-approval choice. The settings API records
        # auto_send explicitly; legacy rows that lack the key must stay visible
        # as a policy gap instead of silently looking healthy forever.
        if "auto_send" not in cfg:
            auto_send_policy_unset.append(rr)
        elif bool(cfg.get("auto_send",False)):
            auto_send_ready.append(rr)
    oldest_auto_age=None
    if auto_send_ready:
        created=min(x['created_at'] for x in auto_send_ready if x.get('created_at'))
        if created:
            created=created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            oldest_auto_age=max(0,int((now-created).total_seconds()))
    human_required = q.filter(MopDraft.status == "human_required").count()
    waiting_external = q.filter(MopDraft.status == "waiting_external").count()
    ready = q.filter(MopDraft.status == "draft_ready").count()
    stale_analyzing = q.filter(MopDraft.status == "analyzing", MopDraft.updated_at < now - timedelta(minutes=30)).count()
    # MOP_HUMAN_HANDOFF_MANAGER_OWNERSHIP_V1: human_required stays visible, but
    # it is not owner work when the canonical CRM deal has a responsible manager
    # and BORIS has created the dedicated open handoff task.
    unresolved_human_rows=db.execute(_mop_sql("""
      SELECT d.id,d.account_id,d.created_at,d.avito_chat_id,
             cd.id AS deal_id,
             COALESCE(cd.responsible_user_id,cd.owner_user_id) AS manager_user_id,
             mt.id AS manager_task_id,mt.due_at AS manager_task_due_at
        FROM mop_drafts d
        LEFT JOIN LATERAL (
          SELECT x.id,x.responsible_user_id,x.owner_user_id
            FROM boris_crm_deals x
           WHERE x.avito_account_id=d.account_id AND x.avito_chat_id=d.avito_chat_id
             AND x.status='open'
           ORDER BY x.id DESC LIMIT 1
        ) cd ON true
        LEFT JOIN LATERAL (
          SELECT t.id,t.due_at
            FROM boris_crm_tasks t
           WHERE t.deal_id=cd.id AND t.status='open' AND t.source='mop_handoff_recovery'
           ORDER BY t.id DESC LIMIT 1
        ) mt ON true
       WHERE (:account_id='' OR d.account_id=:account_id) AND d.status='human_required'
         AND EXISTS (SELECT 1 FROM messenger_messages m WHERE m.account_id=d.account_id AND m.avito_chat_id=d.avito_chat_id
           AND lower(m.direction) LIKE 'in%'
           AND m.avito_created_at=(SELECT max(m2.avito_created_at) FROM messenger_messages m2 WHERE m2.account_id=d.account_id AND m2.avito_chat_id=d.avito_chat_id))
    """), {"account_id":str(account_id or "")}).mappings().all()
    managed_human=[x for x in unresolved_human_rows if x.get('manager_user_id') and x.get('manager_task_id')]
    manager_task_missing=[x for x in unresolved_human_rows if x.get('manager_user_id') and not x.get('manager_task_id')]
    owner_required_human=[x for x in unresolved_human_rows if not x.get('manager_user_id')]
    managed_human_examples=[{
        "draft_id":int(x.get("id") or 0),
        "deal_id":int(x.get("deal_id") or 0),
        "task_id":int(x.get("manager_task_id") or 0),
        "manager_user_id":int(x.get("manager_user_id") or 0),
        "due_at":x.get("manager_task_due_at"),
    } for x in managed_human[:10]]
    return {"drafts_24h":drafts_24h,"sent_24h":sent_24h,"send_failed":send_failed,"send_failed_all":send_failed_all,
            "unresolved_send_failed":unresolved_send_failed,"human_required":human_required,"draft_ready":ready,
            "unresolved_human_required":len(unresolved_human_rows),
            "managed_human_required":len(managed_human),
            "manager_task_missing_human_required":len(manager_task_missing),
            "owner_required_human_required":len(owner_required_human),
            "managed_human_examples":managed_human_examples,
            "waiting_external":waiting_external,
            "unresolved_draft_ready":len(unresolved_ready_rows),"auto_send_unresolved_ready":len(auto_send_ready),
            "auto_send_policy_unset_ready":len(auto_send_policy_unset),
            "auto_send_policy_unset_examples":[{"account_id":str(x.get("account_id") or ""),"draft_id":int(x.get("id") or 0)} for x in auto_send_policy_unset[:10]],
            "entitlement_wait_ready":len(entitlement_wait_ready),
            "entitlement_wait_ready_examples":[{"account_id":str(x.get("account_id") or ""),"draft_id":int(x.get("id") or 0)} for x in entitlement_wait_ready[:10]],
            "oldest_auto_send_unresolved_age_sec":oldest_auto_age,"stale_analyzing":stale_analyzing}

def _mop_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    if int(actual.get("unresolved_send_failed") or 0)>0:
        # Exact Avito 403/chat-access failures are external and non-retryable.
        # Never keep them as an internal P1 and never blind-retry the send.
        _failed=[]
        try:
            _failed=db.execute(_mop_sql("""
              SELECT id,account_id,avito_chat_id,send_error FROM mop_drafts
               WHERE (:a='' OR account_id=:a) AND status='send_failed'
            """), {"a":str(account_id or "")}).mappings().all()
        except Exception:
            _failed=[]
        _external=[x for x in _failed if ('403' in str(x.get('send_error') or '') and 'чат' in str(x.get('send_error') or '').lower())]
        if _failed and len(_external)==len(_failed):
            return {"classification":"MOP_AVITO_CHAT_ACCESS_EXTERNAL","health":"waiting_external","confidence":1.0,
                    "evidence_for":[{"blocked_chats":len(_external)}],"safe_action":"wait_for_avito_chat_access",
                    "owner_action_required":False,"dependency_state":"waiting_external","blind_retry":False}
        return {"classification":"MOP_SEND_FAILURES","health":"degraded","confidence":1.0,"evidence_for":[{"unresolved_send_failed":actual.get("unresolved_send_failed")}],"safe_action":"classify_failure_then_retry_if_retryable"}
    if int(actual.get("stale_analyzing") or 0)>0:
        return {"classification":"MOP_ANALYSIS_STALLED","health":"degraded","confidence":1.0,"evidence_for":[{"stale_analyzing":actual.get("stale_analyzing")}],"safe_action":"recover_stale_analyzing"}
    if int(actual.get("auto_send_unresolved_ready") or 0)>0:
        # MOP_AUTOSEND_TRANSPORT_402_WAIT_V1: fresh exact-chat 402 evidence
        # means external transport wait, not an internal unsent-backlog failure.
        # Diagnosis is read-only; the minute inbox contour owns re-probing.
        try:
            from app.reactivation_avito_health import effective_capability, chat_transport_blocked
            from sqlalchemy import text as _text
            _rows=db.execute(_text("""
              SELECT d.account_id,d.avito_chat_id,d.id FROM mop_drafts d
               WHERE (:a='' OR d.account_id=:a) AND d.status='draft_ready'
                 AND EXISTS (SELECT 1 FROM storage s WHERE s.account_id=d.account_id
                   AND s.key='mop_crm_sales_settings'
                   AND COALESCE((s.value::jsonb->>'auto_send')::boolean,false)=true)
                 AND EXISTS (SELECT 1 FROM messenger_messages m WHERE m.account_id=d.account_id
                   AND m.avito_chat_id=d.avito_chat_id AND lower(m.direction) LIKE 'in%'
                   AND m.avito_created_at=(SELECT max(m2.avito_created_at) FROM messenger_messages m2
                     WHERE m2.account_id=d.account_id AND m2.avito_chat_id=d.avito_chat_id))
            """),{"a":str(account_id or '')}).mappings().all()
            _blocked=[]
            for _r in _rows:
                _cap=effective_capability(db,str(_r['account_id'])) or {}
                if str(_cap.get('classification') or '') in ('avito_messenger_history_partial_402','avito_messenger_subscription_required') and chat_transport_blocked(db,str(_r['account_id']),str(_r['avito_chat_id']),max_age_sec=180):
                    _blocked.append({'account_id':str(_r['account_id']),'draft_id':int(_r['id']),'dependency':'avito_messenger_402'})
            if _blocked and len(_blocked)>=int(actual.get("auto_send_unresolved_ready") or 0):
                return {"classification":"MOP_AUTOSEND_WAITING_AVITO_TRANSPORT","health":"waiting_external","dependency_state":"waiting_external","confidence":1.0,"evidence_for":_blocked[:10],"safe_action":"resume_when_avito_transport_available","owner_action_required":False}
        except Exception:
            pass
        return {"classification":"MOP_AUTOSEND_BACKLOG","health":"degraded","confidence":1.0,"evidence_for":[{"auto_send_unresolved_ready":actual.get("auto_send_unresolved_ready"),"oldest_age_sec":actual.get("oldest_auto_send_unresolved_age_sec")}],"safe_action":"send_ready_autopilot_drafts"}
    if int(actual.get("owner_required_human_required") or 0)>0:
        return {"classification":"MOP_HUMAN_ESCALATION_OWNER_REQUIRED","health":"waiting_owner","dependency_state":"waiting_owner","confidence":1.0,
                "evidence_for":[{"owner_required_human_required":actual.get("owner_required_human_required")}],
                "safe_action":None,"owner_action_required":True}
    if int(actual.get("manager_task_missing_human_required") or 0)>0:
        return {"classification":"MOP_HUMAN_ESCALATION_NEEDS_MANAGER_TASK","health":"degraded","confidence":1.0,
                "evidence_for":[{"manager_task_missing_human_required":actual.get("manager_task_missing_human_required")}],
                "safe_action":"ensure_manager_handoff_task","owner_action_required":False}
    if int(actual.get("managed_human_required") or 0)>0:
        return {"classification":"MOP_HUMAN_ESCALATION_MANAGED","health":"pass","confidence":1.0,
                "evidence_for":actual.get("managed_human_examples") or [{"managed_human_required":actual.get("managed_human_required")}],
                "safe_action":None,"owner_action_required":False}
    if int(actual.get("entitlement_wait_ready") or 0)>0:
        return {"classification":"MOP_ENTITLEMENT_WAIT","health":"waiting_owner","dependency_state":"waiting_owner","confidence":1.0,
                "evidence_for":actual.get("entitlement_wait_ready_examples") or [{"entitlement_wait_ready":actual.get("entitlement_wait_ready")}],
                "safe_action":None,"owner_action_required":True}
    if int(actual.get("auto_send_policy_unset_ready") or 0)>0:
        return {"classification":"MOP_AUTOSEND_POLICY_UNSET","health":"waiting_owner","dependency_state":"waiting_owner","confidence":1.0,
                "evidence_for":actual.get("auto_send_policy_unset_examples") or [{"auto_send_policy_unset_ready":actual.get("auto_send_policy_unset_ready")}],
                "safe_action":None,"owner_action_required":True}
    if int(actual.get("waiting_external") or 0)>0:
        return {"classification":"MOP_WAITING_AI_PROVIDER","health":"waiting_external","confidence":1.0,"evidence_for":[{"waiting_external":actual.get("waiting_external")}],"safe_action":"resume_when_provider_available","owner_action_required":False}
    return {"classification":"MOP_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[{"manual_draft_queue":int(actual.get("unresolved_draft_ready") or 0)}],"safe_action":None}

def _messages_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    params={"account_id":str(account_id or "")}
    scope="(:account_id='' OR account_id=:account_id)"
    inbound_24h=int(db.execute(text(f"SELECT count(*) FROM messenger_messages WHERE {scope} AND direction='in' AND stored_at>=now()-interval '24 hours'"),params).scalar() or 0)
    outbound_24h=int(db.execute(text(f"SELECT count(*) FROM messenger_messages WHERE {scope} AND direction='out' AND stored_at>=now()-interval '24 hours'"),params).scalar() or 0)
    last_rows=db.execute(text(f"""SELECT DISTINCT ON(account_id,avito_chat_id) account_id,avito_chat_id,item_id,item_owner_id,direction,avito_message_id,avito_created_at,content_type,text
      FROM messenger_messages WHERE {scope} AND COALESCE(msg_type,'')<>'system'
      ORDER BY account_id,avito_chat_id,avito_created_at DESC"""),params).mappings().all()
    # MESSAGE_SELLER_SIDE_SCOPE_PARITY_V1: the canonical MOP poll ignores chats
    # where this BORIS account is the buyer (the Avito item belongs to another
    # user). Supervisor must apply the same authority boundary or a reply from a
    # seller/contact-center is falsely diagnosed as an unanswered customer lead.
    # This is local deterministic evidence; no provider/API call is needed.
    _account_avito_users={str(r[0]):str(r[1]) for r in db.execute(text(
        "SELECT account_id,avito_user_id FROM accounts WHERE avito_user_id IS NOT NULL"
    )).all() if r and r[0] and r[1]}
    cutoff=int((__import__('datetime').datetime.now(__import__('datetime').timezone.utc)-__import__('datetime').timedelta(hours=24)).timestamp())
    # MESSAGE_OWNER_SCOPE_SEMANTICS_V1: an explicitly disabled MOP account is
    # outside BORIS automatic-reply authority. Its customer message must remain
    # visible in Inbox, but it is not an internal delivery failure and System
    # Brain must never silently re-enable an owner-disabled sales agent.
    total=actionable=scope_excluded=owner_disabled=enabled_unbound=entitlement_wait=non_dialogue_events=buyer_side=0
    excluded=[]; owner_disabled_examples=[]; enabled_unbound_examples=[]; entitlement_wait_examples=[]; non_dialogue_examples=[]; buyer_side_examples=[]
    for r in last_rows:
        if not str(r.get('direction') or '').lower().startswith('in') or int(r.get('avito_created_at') or 0)<cutoff:
            continue
        # MESSAGE_NON_DIALOGUE_EVENT_SCOPE_V1: Avito appCall is an event, not a
        # customer text turn. The canonical MOP intake intentionally skips appCall
        # in _real_last_incoming(); Messages supervision must use the same truth or
        # it creates a permanent UNANSWERED_DIALOGS incident that cannot be
        # dispatched. Do not suppress media that MOP can actually process (image,
        # voice, file, video); only the proven non-dialogue appCall class is closed.
        _ctype=str(r.get('content_type') or '').lower()
        if _ctype == 'appcall' and not str(r.get('text') or '').strip():
            non_dialogue_events+=1
            non_dialogue_examples.append({"account_id":str(r.get('account_id') or ''),"chat_id":r.get('avito_chat_id'),"item_id":str(r.get('item_id') or ''),"message_id":r.get('avito_message_id'),"content_type":_ctype})
            continue
        aid=str(r.get('account_id') or '')
        _our_avito_user=str(_account_avito_users.get(aid) or '')
        _item_owner=str(r.get('item_owner_id') or '')
        if _our_avito_user and _item_owner and _our_avito_user != _item_owner:
            buyer_side+=1
            buyer_side_examples.append({"account_id":aid,"chat_id":r.get('avito_chat_id'),"item_id":str(r.get('item_id') or ''),"message_id":r.get('avito_message_id'),"account_avito_user_id":_our_avito_user,"item_owner_id":_item_owner})
            continue
        total+=1
        cfg=_storage_json(db,aid,'mop_crm_sales_settings')
        owner_enabled=bool(cfg.get('mop_enabled',cfg.get('enabled',True)))
        if not owner_enabled:
            owner_disabled+=1
            owner_disabled_examples.append({"account_id":aid,"chat_id":r.get('avito_chat_id'),"item_id":str(r.get('item_id') or ''),"message_id":r.get('avito_message_id'),"disabled_reason":str(cfg.get('disabled_reason') or 'owner_disabled')[:120]})
            continue
        bound=bool(db.execute(text("SELECT 1 FROM ai_bindings WHERE product='mop' AND account_id=:a LIMIT 1"),{"a":aid}).first())
        if not bound:
            enabled_unbound+=1
            enabled_unbound_examples.append({"account_id":aid,"chat_id":r.get('avito_chat_id'),"item_id":str(r.get('item_id') or ''),"message_id":r.get('avito_message_id')})
            continue
        # MESSAGE_MOP_ENTITLEMENT_SCOPE_V1: binding != paid runtime entitlement.
        try:
            from app.api.messenger import get_manager_balance as _mop_balance
            entitlement_active=bool((_mop_balance(aid) or {}).get('active'))
        except Exception:
            entitlement_active=False
        if not entitlement_active:
            entitlement_wait+=1
            entitlement_wait_examples.append({"account_id":aid,"chat_id":r.get('avito_chat_id'),"item_id":str(r.get('item_id') or ''),"message_id":r.get('avito_message_id')})
            continue
        wl=_storage_json(db,aid,'messenger_item_whitelist').get('item_ids') or []
        wl={str(x) for x in wl if str(x)}
        item=str(r.get('item_id') or '')
        if wl and item not in wl:
            scope_excluded+=1
            excluded.append({"account_id":aid,"chat_id":r.get('avito_chat_id'),"item_id":item,"message_id":r.get('avito_message_id')})
        else:
            # MESSAGE_TO_MOP_DISPATCH_TRUTH_V1: once the exact incoming message
            # has a canonical mop_draft, Messages has successfully dispatched it.
            # Draft/send/autosend health belongs to the MOP adapter; counting the
            # same draft here as an unanswered delivery failure created false
            # double incidents for normal manual-approval and in-flight states.
            dispatched=bool(db.execute(text("SELECT 1 FROM mop_drafts WHERE account_id=:a AND avito_message_id=:m LIMIT 1"),
                                       {"a":aid,"m":str(r.get('avito_message_id') or '')}).first())
            if dispatched:
                continue
            actionable+=1
    return {"inbound_24h":inbound_24h,"outbound_24h":outbound_24h,"unanswered_dialogs":total,
            "actionable_unanswered_dialogs":actionable,"scope_excluded_unanswered":scope_excluded,
            "scope_excluded_examples":excluded[:10],"owner_disabled_unanswered":owner_disabled,
            "owner_disabled_examples":owner_disabled_examples[:10],"enabled_unbound_unanswered":enabled_unbound,
            "enabled_unbound_examples":enabled_unbound_examples[:10],"entitlement_wait_unanswered":entitlement_wait,
            "entitlement_wait_examples":entitlement_wait_examples[:10],"non_dialogue_events":non_dialogue_events,
            "non_dialogue_examples":non_dialogue_examples[:10],"buyer_side_unanswered":buyer_side,
            "buyer_side_examples":buyer_side_examples[:10]}


def _messages_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    pending=int(actual.get('actionable_unanswered_dialogs') or 0)
    if pending>0:
        return {"classification":"UNANSWERED_DIALOGS","health":"degraded","confidence":1.0,"evidence_for":[{"actionable_unanswered_dialogs":pending}],"safe_action":"dispatch_to_mop"}
    entitlement_wait=int(actual.get('entitlement_wait_unanswered') or 0)
    if pending<=0 and entitlement_wait>0:
        return {"classification":"UNANSWERED_MOP_ENTITLEMENT_WAIT","health":"waiting_owner","dependency_state":"waiting_owner","confidence":1.0,"evidence_for":actual.get('entitlement_wait_examples') or [{"entitlement_wait_unanswered":entitlement_wait}],"safe_action":None,"owner_action_required":True}
    unbound=int(actual.get('enabled_unbound_unanswered') or 0)
    if unbound>0:
        return {"classification":"MOP_BINDING_MISSING_FOR_ENABLED_ACCOUNT","health":"degraded","confidence":1.0,"evidence_for":actual.get('enabled_unbound_examples') or [{"enabled_unbound_unanswered":unbound}],"safe_action":"repair_mop_binding","owner_action_required":False}
    disabled=int(actual.get('owner_disabled_unanswered') or 0)
    if disabled>0:
        return {"classification":"UNANSWERED_OWNER_DISABLED_MOP","health":"waiting_owner","dependency_state":"waiting_owner","confidence":1.0,"evidence_for":actual.get('owner_disabled_examples') or [{"owner_disabled_unanswered":disabled}],"safe_action":None,"owner_action_required":False}
    excluded=int(actual.get('scope_excluded_unanswered') or 0)
    if excluded>0:
        return {"classification":"UNANSWERED_OUTSIDE_CONFIGURED_SCOPE","health":"pass","confidence":1.0,"evidence_for":[{"scope_excluded_unanswered":excluded}],"safe_action":None,"owner_action_required":False}
    return {"classification":"MESSAGE_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}

def _crm_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    params = {"account_id": str(account_id or "")}
    scope = "(:account_id='' OR d.avito_account_id=:account_id)"
    deals = int(db.execute(text(f"SELECT count(*) FROM boris_crm_deals d WHERE {scope}"), params).scalar() or 0)
    deals_today = int(db.execute(text(f"SELECT count(*) FROM boris_crm_deals d WHERE {scope} AND d.created_at>=date_trunc('day',now())"), params).scalar() or 0)
    missing_contact = int(db.execute(text(f"SELECT count(*) FROM boris_crm_deals d WHERE {scope} AND d.contact_id IS NULL"), params).scalar() or 0)
    open_no_next = int(db.execute(text(f"SELECT count(*) FROM boris_crm_deals d WHERE {scope} AND d.status='open' AND d.next_action_at IS NULL"), params).scalar() or 0)
    # CRM_NEXT_ACTION_DIAGNOSIS_LIVE_UNANSWERED_V1: diagnosis must use the same
    # provider-time truth as recovery. A deal imported today from old history, or
    # a recent chat already answered, is not missing autonomous work.
    recent_rows=db.execute(text(f"""SELECT d.id,d.avito_account_id,d.avito_chat_id
      FROM boris_crm_deals d WHERE {scope} AND d.status='open' AND d.next_action_at IS NULL
       AND d.created_at>=now()-interval '24 hours'
       AND (d.avito_chat_id IS NULL OR (
         EXISTS (SELECT 1 FROM messenger_messages mi WHERE mi.account_id=d.avito_account_id AND mi.avito_chat_id=d.avito_chat_id
                  AND mi.direction='in' AND to_timestamp(mi.avito_created_at)>=now()-interval '24 hours')
         -- CRM_NEXT_ACTION_DIAGNOSIS_MOP_SCOPE_PARITY_V1
         AND NOT EXISTS (SELECT 1 FROM messenger_messages ml WHERE ml.account_id=d.avito_account_id AND ml.avito_chat_id=d.avito_chat_id
                  AND ml.direction='in' AND COALESCE(ml.msg_type,'')<>'system'
                  AND ml.avito_created_at=(SELECT max(mx.avito_created_at) FROM messenger_messages mx WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in' AND COALESCE(mx.msg_type,'')<>'system')
                  AND LOWER(COALESCE(ml.content_type,''))='appcall' AND COALESCE(ml.text,'')='')
         AND NOT EXISTS (SELECT 1 FROM storage sw WHERE sw.account_id=d.avito_account_id AND sw.key='messenger_item_whitelist'
                  AND jsonb_typeof(sw.value::jsonb->'item_ids')='array' AND jsonb_array_length(sw.value::jsonb->'item_ids')>0
                  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(sw.value::jsonb->'item_ids') w(item)
                    WHERE w.item=(SELECT COALESCE(mx.item_id,'') FROM messenger_messages mx WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in' ORDER BY mx.avito_created_at DESC LIMIT 1)))
         -- CRM_DIAG_MOP_OWNED_OBLIGATION_V1: a chat already represented
         -- by a canonical MOP waiting/provider or unset-autosend dependency is
         -- not a missing CRM next action. Keep diagnosis aligned with recovery.
         AND NOT EXISTS (SELECT 1 FROM mop_drafts md
                  WHERE md.account_id=d.avito_account_id AND md.avito_chat_id=d.avito_chat_id
                    AND (md.status='waiting_external' OR (md.status='draft_ready' AND NOT EXISTS (
                      SELECT 1 FROM storage ms WHERE ms.account_id=d.avito_account_id AND ms.key='mop_crm_sales_settings'
                        AND (ms.value::jsonb ? 'auto_send')))))
         AND NOT EXISTS (SELECT 1 FROM messenger_messages mo WHERE mo.account_id=d.avito_account_id AND mo.avito_chat_id=d.avito_chat_id
                  AND mo.direction='out' AND to_timestamp(mo.avito_created_at) >
                    (SELECT max(to_timestamp(mx.avito_created_at)) FROM messenger_messages mx WHERE mx.account_id=d.avito_account_id AND mx.avito_chat_id=d.avito_chat_id AND mx.direction='in'))
       )) ORDER BY d.id DESC LIMIT 100"""),params).mappings().all()
    recent_actionable=[]; recent_owner_disabled=[]
    for rr in recent_rows:
        aid=str(rr.get('avito_account_id') or '')
        cfg=_storage_json(db,aid,'mop_crm_sales_settings') if aid else {}
        owner_enabled=bool(cfg.get('mop_enabled',cfg.get('enabled',True)))
        evidence={"deal_id":rr.get('id'),"account_id":aid,"chat_id":rr.get('avito_chat_id')}
        if aid and not owner_enabled:
            evidence['disabled_reason']=str(cfg.get('disabled_reason') or 'owner_disabled')[:120]
            recent_owner_disabled.append(evidence)
            continue
        # CRM_NEXT_ACTION_MOP_SCOPE_PARITY_V3: diagnosis must use the exact
        # same persisted MOP authority rules as safe recovery. Otherwise a
        # provider-only appCall, an item outside the account whitelist, or a
        # chat already owned by MOP is diagnosed as broken while recovery
        # correctly refuses a duplicate human task. Scope exclusions are
        # healthy non-actionable evidence, not a self-heal incident.
        if aid and rr.get('avito_chat_id'):
            _last=db.execute(text("""SELECT content_type,text,item_id FROM messenger_messages
              WHERE account_id=:a AND avito_chat_id=:c AND direction='in' AND COALESCE(msg_type,'')<>'system'
              ORDER BY avito_created_at DESC LIMIT 1"""),{'a':aid,'c':rr['avito_chat_id']}).mappings().first()
            if _last and str(_last.get('content_type') or '').lower()=='appcall' and not str(_last.get('text') or '').strip():
                continue
            raw_wl=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='messenger_item_whitelist' ORDER BY id DESC LIMIT 1"),{'a':aid}).scalar()
            try:
                _wl_cfg=json.loads(raw_wl) if isinstance(raw_wl,str) else (raw_wl or {})
            except Exception:
                _wl_cfg={}
            _wl={str(x) for x in ((_wl_cfg or {}).get('item_ids') or []) if str(x)} if isinstance(_wl_cfg,dict) else set()
            if _last and _wl and str(_last.get('item_id') or '') not in _wl:
                continue
            _mop_owned=db.execute(text("""SELECT 1 FROM mop_drafts md
              WHERE md.account_id=:a AND md.avito_chat_id=:c
                AND (
                  md.status='waiting_external'
                  OR (md.status='draft_ready' AND NOT EXISTS (
                    SELECT 1 FROM storage ms
                     WHERE ms.account_id=:a AND ms.key='mop_crm_sales_settings'
                       AND (ms.value::jsonb ? 'auto_send')
                  ))
                )
              LIMIT 1"""),{'a':aid,'c':rr['avito_chat_id']}).first()
            if _mop_owned:
                continue
        recent_actionable.append(evidence)
    recent_no_next=len(recent_actionable)
    overdue_tasks = int(db.execute(text(f"SELECT count(*) FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id WHERE {scope} AND d.status='open' AND t.status='open' AND t.due_at IS NOT NULL AND t.due_at<now()"), params).scalar() or 0)
    return {"deals": deals, "deals_today": deals_today, "deals_missing_contact": missing_contact,
            "open_deals_without_next_action": open_no_next,"recent_open_deals_without_next_action":recent_no_next,
            "recent_missing_next_action_examples":recent_actionable[:10],
            "recent_owner_disabled_without_next_action":len(recent_owner_disabled),
            "recent_owner_disabled_examples":recent_owner_disabled[:10],"overdue_tasks": overdue_tasks}


def _crm_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    if int(actual.get("deals_missing_contact") or 0) > 0:
        return {"classification": "CRM_ORPHAN_DEALS", "health": "degraded", "confidence": 1.0, "evidence_for": [{"deals_missing_contact": actual.get("deals_missing_contact")}], "safe_action": "repair_crm_relationships"}
    if int(actual.get("recent_open_deals_without_next_action") or 0) > 0:
        return {"classification": "CRM_NEXT_ACTION_MISSING", "health": "degraded", "confidence": 1.0,
                "evidence_for": actual.get("recent_missing_next_action_examples") or [{"recent_open_deals_without_next_action": actual.get("recent_open_deals_without_next_action"), "all_open_without_next_action": actual.get("open_deals_without_next_action")}],
                "safe_action": "create_or_repair_next_action"}
    # CRM_OWNER_DISABLED_SOURCE_WAIT_V1: an inquiry can still be persisted into
    # CRM while the account MOP is explicitly owner-disabled. Do not fabricate a
    # sales follow-up or mark CRM runtime broken; preserve it as a visible owner
    # dependency until the owner re-enables that sales agent or handles it manually.
    if int(actual.get("recent_owner_disabled_without_next_action") or 0) > 0:
        return {"classification":"CRM_NEXT_ACTION_WAITING_OWNER_DISABLED_MOP","health":"waiting_owner","dependency_state":"waiting_owner","confidence":1.0,
                "evidence_for":actual.get("recent_owner_disabled_examples") or [],"safe_action":None,"owner_action_required":False}
    if int(actual.get("overdue_tasks") or 0) > 0:
        return {"classification": "CRM_OVERDUE_HUMAN_TASKS", "health": "waiting_human", "confidence": 1.0, "evidence_for": [{"overdue_tasks": actual.get("overdue_tasks")}], "safe_action": "surface_overdue_tasks", "owner_action_required": False}
    return {"classification": "CRM_FLOW_OK", "health": "pass", "confidence": 1.0, "evidence_for": [], "safe_action": None}


def _telephony_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    params={"account_id":str(account_id or "")}
    scope="(:account_id='' OR account_id=:account_id)"
    real_call_scope=f"{scope} AND COALESCE(source,'')<>'qa' AND COALESCE(account_id,'') NOT LIKE '__qa_%'"
    calls_24h=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND created_at>=now()-interval '24 hours'"),params).scalar() or 0)
    missed_24h=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND created_at>=now()-interval '24 hours' AND answered_at IS NULL"),params).scalar() or 0)
    # TELEPHONY_QA_CALL_EXCLUSION_V1: synthetic runtime probes are not customer calls.
    no_crm=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND created_at>=now()-interval '24 hours' AND crm_deal_id IS NULL"),params).scalar() or 0)
    rec_pending=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND created_at>=now()-interval '24 hours' AND COALESCE(recording_status,'') NOT IN ('ready','not_applicable','unavailable')"),params).scalar() or 0)
    tr_pending=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND created_at>=now()-interval '24 hours' AND answered_at IS NOT NULL AND COALESCE(transcription_status,'') NOT IN ('ready','done','not_applicable','unavailable')"),params).scalar() or 0)
    ai_pending=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND created_at>=now()-interval '24 hours' AND answered_at IS NOT NULL AND COALESCE(ai_analysis_status,'') NOT IN ('ready','done','not_applicable','unavailable')"),params).scalar() or 0)
    callback_due=int(db.execute(text(f"SELECT count(*) FROM telephony_calls WHERE {real_call_scope} AND callback_due_at IS NOT NULL AND callback_due_at<now() AND COALESCE(callback_status,'') NOT IN ('done','completed')"),params).scalar() or 0)
    callback_without_task=int(db.execute(text("""SELECT count(*) FROM telephony_calls c WHERE (:account_id='' OR c.account_id=:account_id) AND c.callback_status='required' AND c.callback_due_at<now() AND c.crm_contact_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM boris_crm_tasks t WHERE t.description=('boris_callback_overdue:'||c.id) AND t.status NOT IN ('done','completed','cancelled'))"""),params).scalar() or 0)
    stale_commands=int(db.execute(text("""SELECT count(*) FROM telephony_commands WHERE (:account_id='' OR account_id=:account_id) AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]') AND ((status='processing' AND updated_at<now()-interval '2 minutes') OR (status IN ('queued','waiting_provider') AND attempts<5 AND (next_attempt_at IS NULL OR next_attempt_at<now()-interval '2 minutes')))"""),params).scalar() or 0)
    stale_recordings=int(db.execute(text("""SELECT count(*) FROM telephony_recordings WHERE (:account_id='' OR account_id=:account_id) AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]') AND ((status='downloading' AND updated_at<now()-interval '10 minutes') OR (transcript_status='processing' AND updated_at<now()-interval '15 minutes'))"""),params).scalar() or 0)
    stale_devices=int(db.execute(text("""SELECT count(*) FROM telephony_devices WHERE (:account_id='' OR account_id=:account_id) AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]') AND revoked_at IS NULL AND presence NOT IN ('offline','dnd') AND (last_seen_at IS NULL OR last_seen_at<now()-interval '90 seconds')"""),params).scalar() or 0)
    expired_targets=int(db.execute(text("""SELECT count(*) FROM telephony_call_targets WHERE (:account_id='' OR account_id=:account_id) AND NOT (lower(account_id) ~ '^__.*qa' OR lower(account_id) ~ '^qa[-_]') AND status='ringing' AND expires_at IS NOT NULL AND expires_at<now()"""),params).scalar() or 0)
    provider_unhealthy=int(db.execute(text("""SELECT count(*) FROM telephony_provider_configs p WHERE (:account_id='' OR p.account_id=:account_id) AND NOT (lower(p.account_id) ~ '^__.*qa' OR lower(p.account_id) ~ '^qa[-_]') AND p.credentials_enc IS NOT NULL AND p.credentials_enc<>'' AND (p.status<>'connected' OR COALESCE(p.last_health_status,'')<>'ok' OR p.last_health_at IS NULL OR p.last_health_at<now()-interval '20 minutes') AND EXISTS (SELECT 1 FROM telephony_entitlements e WHERE e.account_id=p.account_id AND e.enabled=true AND e.paid_until>now())"""),params).scalar() or 0)
    return {"calls_24h":calls_24h,"missed_24h":missed_24h,"calls_without_crm_deal_24h":no_crm,"recording_pending_24h":rec_pending,"transcription_pending_24h":tr_pending,"analysis_pending_24h":ai_pending,"overdue_callbacks":callback_due,"overdue_callbacks_without_task":callback_without_task,"overdue_callbacks_waiting_human":max(0,callback_due-callback_without_task),"stale_runtime_commands":stale_commands,"stale_recording_leases":stale_recordings,"stale_online_devices":stale_devices,"expired_ring_targets":expired_targets,"unhealthy_provider_configs":provider_unhealthy}


def _telephony_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    runtime_stale=sum(int(actual.get(k) or 0) for k in ("stale_runtime_commands","stale_recording_leases","stale_online_devices","expired_ring_targets"))
    if runtime_stale>0:
        return {"classification":"TELEPHONY_RUNTIME_STALLED","health":"degraded","confidence":1.0,"evidence_for":[{"stale_runtime_commands":actual.get("stale_runtime_commands"),"stale_recording_leases":actual.get("stale_recording_leases"),"stale_online_devices":actual.get("stale_online_devices"),"expired_ring_targets":actual.get("expired_ring_targets")}],"safe_action":"telephony_autonomy_guardian"}
    if int(actual.get("unhealthy_provider_configs") or 0)>0:
        return {"classification":"TELEPHONY_PROVIDER_UNHEALTHY","health":"degraded","confidence":1.0,"evidence_for":[{"unhealthy_provider_configs":actual.get("unhealthy_provider_configs")}],"safe_action":"verify_provider_connection"}
    if int(actual.get("calls_without_crm_deal_24h") or 0)>0:
        return {"classification":"TELEPHONY_CRM_LINK_MISSING","health":"degraded","confidence":1.0,"evidence_for":[{"calls_without_crm_deal_24h":actual.get("calls_without_crm_deal_24h")}],"safe_action":"run_calltracking_crm_sync"}
    if int(actual.get("overdue_callbacks_without_task") or 0)>0:
        return {"classification":"MISSED_CALLBACK_TASK_MISSING","health":"degraded","confidence":1.0,"evidence_for":[{"overdue_callbacks_without_task":actual.get("overdue_callbacks_without_task")}],"safe_action":"ensure_callback_task"}
    if int(actual.get("transcription_pending_24h") or 0)>0 or int(actual.get("analysis_pending_24h") or 0)>0:
        return {"classification":"CALL_ANALYSIS_PIPELINE_LAG","health":"degraded","confidence":1.0,"evidence_for":[{"transcription_pending_24h":actual.get("transcription_pending_24h"),"analysis_pending_24h":actual.get("analysis_pending_24h")}],"safe_action":"retry_analysis_pipeline"}
    if int(actual.get("overdue_callbacks_waiting_human") or 0)>0:
        return {"classification":"MISSED_CALLBACK_WAITING_MANAGER","health":"waiting_human","confidence":1.0,"evidence_for":[{"overdue_callbacks_waiting_human":actual.get("overdue_callbacks_waiting_human")}],"safe_action":None,"owner_action_required":False}
    return {"classification":"TELEPHONY_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}


def _rop_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text, bindparam
    from app.models.reliability import ReliabilityHeartbeat
    from datetime import datetime, timezone
    from app.api.calltracking import get_rop_minutes

    # ROP_ENTITLEMENT_SCOPED_HEALTH_V2:
    # The production runner only analyzes accounts explicitly bound to ROP and
    # carrying an active minute package. Health must use the identical service
    # scope. Expired/manual accounts are not backlog and must never trigger AI.
    bound_accounts=[str(x) for x in db.execute(text(
        "SELECT DISTINCT account_id FROM ai_bindings "
        "WHERE product='rop' AND account_id IS NOT NULL ORDER BY account_id"
    )).scalars().all()]
    if account_id:
        requested=str(account_id)
        scoped_bound=[requested] if requested in bound_accounts else []
    else:
        scoped_bound=list(bound_accounts)

    active_accounts=[]
    entitlement_errors=[]
    for aid in scoped_bound:
        try:
            bal=get_rop_minutes(aid) or {}
            if bool(bal.get("active")):
                active_accounts.append(aid)
        except Exception as exc:
            entitlement_errors.append({"account_id":aid,"error_type":type(exc).__name__})

    entitled=len(active_accounts)
    analyzed_24h=quality_24h=telephony_answered_24h=0
    avito_answered_24h=avito_missing_analysis_24h=oldest_pending_age_sec=0

    if active_accounts:
        p={"rop_accounts":active_accounts}
        analyzed_stmt=text("""
            SELECT count(*) FROM call_analysis c
             WHERE c.account_id IN :rop_accounts
               AND c.created_at>=now()-interval '24 hours'
        """).bindparams(bindparam("rop_accounts",expanding=True))
        analyzed_24h=int(db.execute(analyzed_stmt,p).scalar() or 0)

        quality_stmt=text("""
            SELECT count(*) FROM telephony_call_quality q
             WHERE q.account_id IN :rop_accounts
               AND q.created_at>=now()-interval '24 hours'
        """).bindparams(bindparam("rop_accounts",expanding=True))
        quality_24h=int(db.execute(quality_stmt,p).scalar() or 0)

        tel_stmt=text("""
            SELECT count(*) FROM telephony_calls t
             WHERE t.account_id IN :rop_accounts
               AND t.created_at>=now()-interval '24 hours'
               AND t.answered_at IS NOT NULL
        """).bindparams(bindparam("rop_accounts",expanding=True))
        telephony_answered_24h=int(db.execute(tel_stmt,p).scalar() or 0)

        # ROP_AVITO_CALLTRACKING_TRUTH_V1: Avito Calltracking is a canonical
        # ROP input, but only inside the paid service scope above.
        avito_stmt=text("""
            SELECT count(*) FROM boris_crm_activities a
             WHERE a.source='avito_calltracking'
               AND a.channel='avito_calltracking'
               AND a.created_at>=now()-interval '24 hours'
               AND COALESCE(a.metadata_json->>'account_id','') IN :rop_accounts
               AND CASE WHEN COALESCE(a.metadata_json->>'talk_duration','') ~ '^[0-9]+$'
                        THEN (a.metadata_json->>'talk_duration')::int ELSE 0 END > 0
        """).bindparams(bindparam("rop_accounts",expanding=True))
        avito_answered_24h=int(db.execute(avito_stmt,p).scalar() or 0)

        pending_stmt=text("""
            SELECT count(*) FROM boris_crm_activities a
             WHERE a.source='avito_calltracking'
               AND a.channel='avito_calltracking'
               AND a.created_at>=now()-interval '24 hours'
               AND COALESCE(a.metadata_json->>'account_id','') IN :rop_accounts
               AND CASE WHEN COALESCE(a.metadata_json->>'talk_duration','') ~ '^[0-9]+$'
                        THEN (a.metadata_json->>'talk_duration')::int ELSE 0 END > 0
               AND NOT EXISTS (
                   SELECT 1 FROM call_analysis ca
                    WHERE ca.account_id=COALESCE(a.metadata_json->>'account_id','')
                      AND ca.call_id::text=COALESCE(a.metadata_json->>'call_id','')
               )
        """).bindparams(bindparam("rop_accounts",expanding=True))
        avito_missing_analysis_24h=int(db.execute(pending_stmt,p).scalar() or 0)

        age_stmt=text("""
            SELECT COALESCE(EXTRACT(EPOCH FROM (now()-MIN(a.created_at)))::int,0)
              FROM boris_crm_activities a
             WHERE a.source='avito_calltracking'
               AND a.channel='avito_calltracking'
               AND a.created_at>=now()-interval '24 hours'
               AND COALESCE(a.metadata_json->>'account_id','') IN :rop_accounts
               AND CASE WHEN COALESCE(a.metadata_json->>'talk_duration','') ~ '^[0-9]+$'
                        THEN (a.metadata_json->>'talk_duration')::int ELSE 0 END > 0
               AND NOT EXISTS (
                   SELECT 1 FROM call_analysis ca
                    WHERE ca.account_id=COALESCE(a.metadata_json->>'account_id','')
                      AND ca.call_id::text=COALESCE(a.metadata_json->>'call_id','')
               )
        """).bindparams(bindparam("rop_accounts",expanding=True))
        oldest_pending_age_sec=int(db.execute(age_stmt,p).scalar() or 0)

    hb=db.query(ReliabilityHeartbeat).filter(
        ReliabilityHeartbeat.module=='runtime',
        ReliabilityHeartbeat.worker_id=='rop_auto_scheduler',
    ).order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    scheduler_age=None
    if hb and hb.last_seen_at:
        seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc)
        scheduler_age=int((datetime.now(timezone.utc)-seen).total_seconds())

    missing_quality=max(0,telephony_answered_24h-quality_24h)
    return {
        "answered_calls_24h":telephony_answered_24h+avito_answered_24h,
        "telephony_answered_calls_24h":telephony_answered_24h,
        "avito_answered_calls_24h":avito_answered_24h,
        "avito_calls_pending_analysis_24h":avito_missing_analysis_24h,
        "oldest_pending_analysis_age_sec":int(oldest_pending_age_sec or 0),
        "call_analysis_24h":analyzed_24h,
        "quality_audits_24h":quality_24h,
        "calls_without_quality_audit_24h":missing_quality,
        "rop_bound_accounts":len(scoped_bound),
        "active_rop_accounts":int(entitled),
        "inactive_rop_accounts":max(0,len(scoped_bound)-int(entitled)),
        "active_rop_account_ids":list(active_accounts),
        "entitlement_errors":entitlement_errors,
        "scheduler_age_sec":scheduler_age,
        "scheduler_state":hb.state if hb else None,
        "scheduler_details":hb.details_json if hb else {},
    }


def _rop_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    entitled=int(actual.get("active_rop_accounts") or 0)
    bound=int(actual.get("rop_bound_accounts") or 0)
    if entitled<=0:
        return {
            "classification":"ROP_NOT_ENTITLED" if bound else "ROP_NOT_CONFIGURED",
            "health":"pass","confidence":1.0,
            "evidence_for":[{
                "rop_bound_accounts":bound,
                "active_rop_accounts":entitled,
                "inactive_rop_accounts":actual.get("inactive_rop_accounts"),
            }],
            "safe_action":None,
            "owner_action_required":False,
        }
    age=actual.get("scheduler_age_sec")
    state=str(actual.get("scheduler_state") or "")
    if age is None or int(age)>1800:
        return {"classification":"ROP_AUTO_SCHEDULER_STALLED","health":"degraded","confidence":1.0,"evidence_for":[actual],"safe_action":"restart_rop_auto_scheduler"}
    if state in {"degraded","error","failed","critical","stale"}:
        return {"classification":"ROP_AUTO_SCHEDULER_DEGRADED","health":"degraded","confidence":1.0,"evidence_for":[actual],"safe_action":"restart_rop_auto_scheduler"}
    pending=int(actual.get("avito_calls_pending_analysis_24h") or 0)
    if pending>0 and int(actual.get("oldest_pending_analysis_age_sec") or 0)>1200:
        return {"classification":"ROP_AVITO_CALL_ANALYSIS_BACKLOG","health":"degraded","confidence":1.0,"evidence_for":[{"pending":pending,"oldest_age_sec":actual.get("oldest_pending_analysis_age_sec")}],"safe_action":"restart_rop_auto_scheduler","owner_action_required":False}
    if pending>0:
        return {"classification":"ROP_AVITO_CALL_ANALYSIS_WAITING_SCHEDULER","health":"waiting_internal","confidence":1.0,"evidence_for":[{"pending":pending,"oldest_age_sec":actual.get("oldest_pending_analysis_age_sec")}],"safe_action":None,"owner_action_required":False}
    miss=int(actual.get("calls_without_quality_audit_24h") or 0)
    if miss>0:
        return {"classification":"ROP_CALL_AUDIT_GAP","health":"degraded","confidence":1.0,"evidence_for":[{"calls_without_quality_audit_24h":miss,"answered_calls_24h":actual.get("answered_calls_24h")}],"safe_action":"run_missing_call_audits"}
    return {"classification":"ROP_AUDIT_OK","health":"pass","confidence":1.0,"evidence_for":[actual],"safe_action":None}


def _direct_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    params={"account_id":str(account_id or "")}
    scope="(:account_id='' OR account_id=:account_id)"
    connected=int(db.execute(text(f"SELECT count(*) FROM direct_connections WHERE {scope} AND status IN ('connected','active','ok')"),params).scalar() or 0)
    connections=int(db.execute(text(f"SELECT count(*) FROM direct_connections WHERE {scope}"),params).scalar() or 0)
    entities=int(db.execute(text(f"SELECT count(*) FROM direct_entities WHERE {scope}"),params).scalar() or 0)
    stale_entities=int(db.execute(text(f"SELECT count(*) FROM direct_entities WHERE {scope} AND (synced_at IS NULL OR synced_at<now()-interval '24 hours')"),params).scalar() or 0)
    stats_today=int(db.execute(text(f"SELECT count(*) FROM direct_stats_daily WHERE {scope} AND day=current_date::text"),params).scalar() or 0)
    return {"connections":connections,"connected_connections":connected,"entities":entities,"stale_entities":stale_entities,"stats_rows_today":stats_today}


def _direct_diagnose(db, account_id: str | None, requirement, actual: dict) -> dict:
    if int(actual.get("connections") or 0)>0 and int(actual.get("connected_connections") or 0)==0:
        return {"classification":"DIRECT_NOT_CONFIGURED","health":"pass","confidence":1.0,"evidence_for":[{"connections":actual.get("connections"),"connected":actual.get("connected_connections")}],"safe_action":None,"owner_action_required":False}
    if int(actual.get("stale_entities") or 0)>0:
        return {"classification":"DIRECT_STATE_STALE","health":"degraded","confidence":1.0,"evidence_for":[{"stale_entities":actual.get("stale_entities")}],"safe_action":"sync_direct_state"}
    return {"classification":"DIRECT_STATE_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}



def _feed_factory_state(db, account_id: str | None) -> dict:
    from app.models.background_job import BackgroundJob
    from datetime import datetime,timezone,timedelta
    now=datetime.now(timezone.utc)
    q=db.query(BackgroundJob).filter(BackgroundJob.kind.in_(("campaign_prepare","campaign_enhance","campaign_deliver")))
    if account_id:q=q.filter(BackgroundJob.account_id==str(account_id))
    recent=q.filter(BackgroundJob.status.in_(("failed","partial")),BackgroundJob.finished_at>=now-timedelta(hours=24)).all()
    waiting_external=0; retryable_failures=0; recovering_failures=0; examples=[]
    # Owner-facing queue truth distinguishes historical/recovered failures from
    # failures that still require work. Keep every event in DB; only the current
    # alarm counter is resolved by durable recovery evidence.
    recovered_failed=0; recovering_failed=0; waiting_external_failed=0; unresolved_failed=0

    def _campaign_id(job):
        p=dict(job.payload_json or {}); r=dict(job.result_json or {})
        try:return int(p.get("campaign_id") or r.get("campaign_id") or 0)
        except Exception:return 0

    def _superseded_campaign_recovered(job):
        # FEED_SUPERSEDED_RECOVERY_TRUTH_V3: a failed superseded repair job is
        # historical only when its durable resolution points to a replacement
        # campaign that is itself completed and has exact published identity
        # evidence. This closes the race where an obsolete repair job executes
        # after a stronger replacement campaign already became canonical.
        cid=_campaign_id(job)
        if not cid or str(job.kind or '')!='campaign_deliver':
            return False
        try:
            from app.models.campaign import Campaign
            from app.models.campaign_item import CampaignItem
            row=db.query(Campaign).filter(Campaign.id==cid).first()
            if not row or str(row.status or '')!='superseded':
                return False
            settings=row.settings_json if isinstance(row.settings_json,dict) else json.loads(row.settings_json or '{}')
            res=(settings or {}).get('resolution') or {}
            # Legacy recovered shape remains valid.
            expected=int(res.get('new_ads_expected') or 0); active=int(res.get('new_ads_active_verified') or 0)
            if str(res.get('status') or '')=='recovered' and expected>0 and active>=expected and res.get('owner_action_required') is False:
                return True
            # Exact replacement shape used by targeted Avito 2014 media repair.
            if str(res.get('status') or '')!='superseded':
                return False
            replacement_id=int(res.get('replacement_campaign_id') or 0)
            replacement_feed_id=str(res.get('replacement_feed_identity') or '').strip()
            if replacement_id<=0 or not replacement_feed_id:
                return False
            replacement=db.query(Campaign).filter(Campaign.id==replacement_id).first()
            if replacement is None or str(replacement.status or '')!='completed':
                return False
            rsettings=replacement.settings_json if isinstance(replacement.settings_json,dict) else json.loads(replacement.settings_json or '{}')
            ff=dict((rsettings or {}).get('feed_factory') or {})
            activation=dict(ff.get('owner_activation') or {})
            if str(activation.get('status') or '')!='completed' or int(activation.get('errors') or 0)!=0 or int(activation.get('blocked') or 0)!=0:
                return False
            items=db.query(CampaignItem).filter(CampaignItem.campaign_id==replacement_id,CampaignItem.account_id==job.account_id).all()
            exact=[x for x in items if str(getattr(x,'feed_identity','') or '')==replacement_feed_id and str(getattr(x,'status','') or '')=='published' and str(getattr(x,'avito_item_id','') or '').strip()]
            return len(exact)==1
        except Exception:
            return False

    def _newer_recovery(job):
        # FEED_FAILED_SUPERSEDED_BY_RECOVERY_V1: an older failed delivery is
        # historical evidence, not a current outage, when a newer durable watcher
        # for the same campaign is already queued/running. Never hide a failure
        # unless the replacement job is explicit and still non-terminal.
        cid=_campaign_id(job)
        if not cid or str(job.kind or "")!="campaign_deliver":
            return None
        newer=(db.query(BackgroundJob)
               .filter(BackgroundJob.kind=="campaign_deliver",
                       BackgroundJob.account_id==job.account_id,
                       BackgroundJob.id>job.id,
                       BackgroundJob.status.in_(("queued","running","validating","awaiting_confirmation","partial")))
               .order_by(BackgroundJob.id.desc()).limit(30).all())
        for candidate in newer:
            if _campaign_id(candidate)!=cid:
                continue
            cp=dict(candidate.payload_json or {}); cr=dict(candidate.result_json or {})
            stage=str(cp.get("stage") or cr.get("stage") or "")
            if stage in {"awaiting_scheduled_url_result","publication_watch","publication_processing","publication_pending_review"}:
                return candidate
        return None

    def _durable_failure_recovered(job):
        """FEED_FAILED_DURABLE_ROLLBACK_PROOF_V1

        Keep the failed job as history but stop diagnosing a current outage after an
        exact, verified local rollback restored the previously published feed. This
        is deliberately narrow: explicit rollback evidence, no active delivery for
        the failed campaign, failed identities absent from canonical feed, and all
        restored published identities present.
        """
        cid=_campaign_id(job)
        if not cid or str(job.kind or '')!='campaign_deliver':
            return False
        from app.models.campaign import Campaign
        from app.models.campaign_item import CampaignItem
        campaign=db.query(Campaign).filter(Campaign.id==cid).first()
        if campaign is None:
            return False
        try:
            settings=json.loads(campaign.settings_json or '{}') if isinstance(campaign.settings_json,str) else dict(campaign.settings_json or {})
        except Exception:
            settings={}
        rb=dict(settings.get('rollback_after_duplicate_failure') or {})
        if str(rb.get('state') or '')!='restored_previous_published_identities' or bool(rb.get('external_action')):
            return False
        restored_cid=int(rb.get('restored_campaign_id') or 0)
        if restored_cid<=0:
            return False
        active_same=(db.query(BackgroundJob).filter(
            BackgroundJob.kind=='campaign_deliver',BackgroundJob.account_id==job.account_id,
            BackgroundJob.status.in_(('queued','running','validating'))).all())
        if any(_campaign_id(x)==cid for x in active_same):
            return False
        row=db.query(Storage).filter(Storage.account_id==job.account_id,Storage.key=='feed_items').order_by(Storage.id.desc()).first()
        if not row:
            return False
        try:
            feed=json.loads(row.value or '[]') if isinstance(row.value,str) else list(row.value or [])
        except Exception:
            return False
        feed_ids={str((x or {}).get('id') or '') for x in feed if isinstance(x,dict)}
        failed_ids={str(x[0]) for x in db.query(CampaignItem.feed_identity).filter(CampaignItem.campaign_id==cid,CampaignItem.feed_identity.isnot(None)).all() if x and x[0]}
        restored_ids={str(x[0]) for x in db.query(CampaignItem.feed_identity).filter(CampaignItem.campaign_id==restored_cid,CampaignItem.status=='published',CampaignItem.feed_identity.isnot(None)).all() if x and x[0]}
        expected=int(rb.get('restored_items') or 0)
        return bool(restored_ids and (not expected or len(restored_ids)==expected) and restored_ids.issubset(feed_ids) and not (failed_ids & feed_ids))

    def _superseded_resolution_recovered(job):
        # FEED_FAILED_SUPERSEDED_RESOLUTION_PROOF_V1:
        # historical only through an explicit durable replacement link and
        # item-level Avito success evidence; never infer recovery from timing.
        cid=_campaign_id(job)
        if not cid or str(job.kind or '')!='campaign_deliver':
            return None
        from app.models.campaign import Campaign
        from app.models.campaign_item import CampaignItem
        source=db.query(Campaign).filter(Campaign.id==cid).first()
        if source is None or str(source.status or '').lower()!='superseded':
            return None
        try:
            source_settings=json.loads(source.settings_json or '{}') if isinstance(source.settings_json,str) else dict(source.settings_json or {})
        except Exception:
            source_settings={}
        source_ff=dict(source_settings.get('feed_factory') or {})
        resolution=dict(source_settings.get('resolution') or source_ff.get('resolution') or {})
        replacement_id=int(resolution.get('replacement_campaign_id') or 0)
        if str(resolution.get('status') or '').lower()!='superseded' or replacement_id<=0:
            return None
        replacement=db.query(Campaign).filter(Campaign.id==replacement_id).first()
        if replacement is None or str(replacement.status or '').lower()!='completed':
            return None
        try:
            rs=json.loads(replacement.settings_json or '{}') if isinstance(replacement.settings_json,str) else dict(replacement.settings_json or {})
        except Exception:
            rs={}
        replacement_state=dict(rs.get('replacement') or {})
        ff=dict(rs.get('feed_factory') or {})
        activation=dict(ff.get('owner_activation') or {})
        receipt=dict(ff.get('publication_receipt') or {})
        expected=int(rs.get('expected_items') or 0)
        if expected<=0 or str(replacement_state.get('state') or '').lower()!='completed':
            return None
        if str(activation.get('status') or '').lower()!='completed':
            return None
        if int(activation.get('published') or 0)!=expected or int(activation.get('errors') or 0) or int(activation.get('blocked') or 0) or int(activation.get('unresolved') or 0):
            return None
        if str(receipt.get('source') or '')!='avito_autoload_item_report' or str(receipt.get('report_section') or '')!='success_added' or str(receipt.get('avito_status') or '').lower()!='active':
            return None
        receipt_avito_id=str(receipt.get('avito_item_id') or '').strip()
        receipt_feed_id=str(receipt.get('feed_identity') or '').strip()
        if not int(receipt.get('upload_id') or 0) or not receipt_avito_id or not receipt_feed_id:
            return None
        rows=db.query(CampaignItem).filter(
            CampaignItem.campaign_id==replacement_id,
            CampaignItem.account_id==job.account_id,
        ).all()
        if len(rows)!=expected:
            return None
        ids=[str(getattr(x,'avito_item_id','') or '').strip() for x in rows]
        feeds=[str(getattr(x,'feed_identity','') or '').strip() for x in rows]
        if any(not x for x in ids) or len(set(ids))!=expected or any(not x for x in feeds) or len(set(feeds))!=expected:
            return None
        if receipt_avito_id not in ids or receipt_feed_id not in feeds:
            return None
        return {
            'replacement_campaign_id':replacement_id,
            'replacement_campaign_item_id':int(replacement_state.get('new_campaign_item_id') or 0) or None,
            'replacement_feed_identity':receipt_feed_id,
            'replacement_avito_item_id':receipt_avito_id,
            'replacement_upload_id':int(receipt.get('upload_id') or 0),
            'published_items':expected,
        }

    def _terminal_failure_replaced(job):
        """FEED_FAILED_REPLACED_BY_COMPLETED_CAMPAIGN_V2

        A failed delivery becomes historical only through an explicit durable
        replacement link AND immutable terminal delivery evidence. CampaignItem
        status is mutable after publication (watchdogs/cleanup may move it), so it
        must not override a completed 0-error Avito delivery receipt. Every item
        still needs a real Avito id and the exact delivery scope must be proven.
        """
        cid=_campaign_id(job)
        if not cid or str(job.kind or '')!='campaign_deliver':
            return None
        from app.models.campaign import Campaign
        from app.models.campaign_item import CampaignItem

        def _settings(row):
            try:
                return json.loads(row.settings_json or '{}') if isinstance(row.settings_json,str) else dict(row.settings_json or {})
            except Exception:
                return {}

        def _proven_delivery(row, settings, account_id):
            if row is None:
                return None
            ff=dict(settings.get('feed_factory') or {})
            activation=dict(ff.get('owner_activation') or {})
            report=dict(activation.get('report') or {})
            scope=dict(report.get('scope') or {})
            current_status=str(row.status or '').lower()
            activation_status=str(activation.get('status') or '').lower()
            reconciled_partial=False
            if current_status!='completed' or activation_status!='completed':
                reconciliation=dict(activation.get('publication_truth_reconciliation') or {})
                watch=dict(ff.get('post_publish_watch') or {})
                try:
                    missing_count=int(watch.get('missing_feed_ids_count') or 0)
                except Exception:
                    missing_count=-1
                reconciled_partial=(
                    current_status=='partial'
                    and activation_status=='partial'
                    and str(reconciliation.get('reason') or '')=='canonical_identity_provider_rejection'
                    and reconciliation.get('owner_action_required') is False
                    and reconciliation.get('safe_republish') is False
                    and str(watch.get('diagnostic') or '')=='healthy'
                    and watch.get('owner_action_required') is False
                    and missing_count==0
                )
                if not reconciled_partial:
                    return None
            try:
                expected=int(scope.get('expected_total') or report.get('total') or activation.get('published') or 0)
                found=int(scope.get('found') if scope.get('found') is not None else expected)
                active=int(scope.get('active') if scope.get('active') is not None else expected)
                successful=int(report.get('successful') if report.get('successful') is not None else expected)
                report_errors=int(report.get('error') or 0)
                scope_errors=int(scope.get('errors') or 0)
                blocked=int(report.get('blocked') or 0)
            except Exception:
                return None
            if expected<=0:
                return None
            if report.get('terminal') is not True or report.get('full_success') is not True or report.get('failed') is True:
                return None
            if scope and (scope.get('terminal') is not True or scope.get('full_success') is not True or scope.get('failed') is True):
                return None
            if report_errors or scope_errors or blocked or found!=expected or active!=expected or successful!=expected:
                return None
            if list(scope.get('missing') or []) or list(scope.get('unresolved') or []) or list(report.get('item_errors') or []):
                return None
            rows=db.query(CampaignItem).filter(
                CampaignItem.campaign_id==row.id,
                CampaignItem.account_id==account_id,
            ).all()
            if len(rows)!=expected:
                return None
            avito_ids=[str(getattr(r,'avito_item_id','') or '').strip() for r in rows]
            if any(not value for value in avito_ids) or len(set(avito_ids))!=expected:
                return None
            return {'rows':rows,'expected':expected,'upload_id':report.get('upload_id') or activation.get('upload_id')}

        campaign=db.query(Campaign).filter(Campaign.id==cid).first()
        if campaign is None:
            return None
        settings=_settings(campaign)
        terminal=dict(settings.get('terminal_failure') or {})
        replacement_id=int(terminal.get('replacement_campaign_id') or 0)
        if replacement_id<=0:
            return None

        replacement=db.query(Campaign).filter(Campaign.id==replacement_id).first()
        replacement_settings=_settings(replacement) if replacement is not None else {}
        replacement_proof=_proven_delivery(replacement,replacement_settings,job.account_id)
        if replacement_proof is None:
            return None

        pilot_id=int(replacement_settings.get('pilot_campaign_id') or 0)
        pilot_proof=None
        if pilot_id>0:
            pilot=db.query(Campaign).filter(Campaign.id==pilot_id).first()
            pilot_settings=_settings(pilot) if pilot is not None else {}
            if int(pilot_settings.get('scale_after_success_campaign_id') or 0)!=replacement_id:
                return None
            pilot_proof=_proven_delivery(pilot,pilot_settings,job.account_id)
            if pilot_proof is None:
                return None

        cleanup=dict(replacement_settings.get('replacement_cleanup') or {})
        published_items=int(replacement_proof['expected'])+int((pilot_proof or {}).get('expected') or 0)
        return {
            'replacement_campaign_id':replacement_id,
            'pilot_campaign_id':pilot_id or None,
            'published_items':published_items,
            'replacement_upload_id':replacement_proof.get('upload_id'),
            'pilot_upload_id':(pilot_proof or {}).get('upload_id'),
            'replacement_cleanup_status':str(cleanup.get('status') or '') or None,
        }

    for job in recent:
        payload=json.dumps(job.result_json or {},ensure_ascii=False).lower()
        result=job.result_json or {}
        report=result.get('report') if isinstance(result,dict) else {}
        terminal_partial=(
            str(job.kind or '')=='campaign_deliver'
            and str(job.status or '')=='partial'
            and str(result.get('stage') or '')=='publication_partial'
            and (bool((report or {}).get('terminal')) or bool(((report or {}).get('scope') or {}).get('terminal')))
        )
        pending_external=(
            str(job.kind or '')=='campaign_deliver'
            and str(job.status or '')=='partial'
            and str(result.get('stage') or '') in {'publication_partial','publication_pending_review'}
            and (str((report or {}).get('status') or '').lower() in {'pending','processing'} or (report or {}).get('terminal') is False)
        )
        terminal_external=terminal_partial or pending_external or any(x in payload for x in ('error_fee_hard_limit','закончились размещения','лимит размещен','quota','waiting_avito_confirmation'))
        recovery=_newer_recovery(job)
        recovered=_durable_failure_recovered(job) or _superseded_campaign_recovered(job)
        superseded_recovery=_superseded_resolution_recovered(job)
        replacement=_terminal_failure_replaced(job)
        if superseded_recovery is not None:
            if str(job.status or '')=='failed': recovered_failed+=1
            examples.append({"job_id":job.id,"account_id":job.account_id,"status":job.status,
                             "class":"recovered_by_superseded_replacement",**superseded_recovery})
        elif replacement is not None:
            if str(job.status or '')=='failed': recovered_failed+=1
            examples.append({"job_id":job.id,"account_id":job.account_id,"status":job.status,
                             "class":"recovered_by_completed_replacement",**replacement})
        elif recovered:
            if str(job.status or '')=='failed': recovered_failed+=1
            examples.append({"job_id":job.id,"account_id":job.account_id,"status":job.status,"class":"recovered_by_durable_rollback"})
        elif recovery is not None:
            recovering_failures+=1
            waiting_external+=1
            if str(job.status or '')=='failed': recovering_failed+=1
            rp=dict(recovery.payload_json or {}); rr=dict(recovery.result_json or {})
            examples.append({"job_id":job.id,"account_id":job.account_id,"status":job.status,
                             "class":"recovery_in_progress","recovery_job_id":recovery.id,
                             "recovery_status":recovery.status,
                             "recovery_stage":str(rp.get("stage") or rr.get("stage") or "")})
        elif terminal_external:
            waiting_external+=1
            if str(job.status or '')=='failed': waiting_external_failed+=1
            examples.append({"job_id":job.id,"account_id":job.account_id,"status":job.status,"class":"terminal_partial" if terminal_partial else ("pending_external" if pending_external else "waiting_external")})
        else:
            retryable_failures+=1
            if str(job.status or '')=='failed': unresolved_failed+=1
            examples.append({"job_id":job.id,"account_id":job.account_id,"status":job.status,"class":"failure"})
    return {
        "queued":q.filter(BackgroundJob.status=='queued').count(),
        "running":q.filter(BackgroundJob.status.in_(("running","validating"))).count(),
        "stale_running":q.filter(BackgroundJob.status.in_(("running","validating")),BackgroundJob.started_at<now-timedelta(minutes=30)).count(),
        "failed_or_partial_24h":len(recent),
        "recovered_failed_24h":recovered_failed,
        "recovering_failed_24h":recovering_failed,
        "waiting_external_failed_24h":waiting_external_failed,
        "unresolved_failed_24h":unresolved_failed,
        "waiting_external_24h":waiting_external,
        "recovering_failures_24h":recovering_failures,
        "retryable_failures_24h":retryable_failures,
        "examples":examples[:10],
    }

def _feed_factory_diagnose(db, account_id, requirement, a):
    if int(a.get('stale_running') or 0)>0:return {"classification":"FEED_JOB_STALLED","health":"degraded","confidence":1.0,"evidence_for":[{"stale_running":a['stale_running']}],"safe_action":"recover_stale_feed_jobs"}
    if int(a.get('retryable_failures_24h') or 0)>0:return {"classification":"FEED_JOB_FAILURES","health":"degraded","confidence":1.0,"evidence_for":[{"retryable_failures_24h":a['retryable_failures_24h']}],"safe_action":"inspect_feed_failures"}
    if int(a.get('waiting_external_24h') or 0)>0:return {"classification":"FEED_WAITING_EXTERNAL_LIMIT","health":"waiting_external","confidence":1.0,"evidence_for":[{"waiting_external_24h":a['waiting_external_24h']}],"safe_action":None,"owner_action_required":False}
    return {"classification":"FEED_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}

def _social_state(db, account_id: str | None) -> dict:
    from app.models.reliability import ReliabilityHeartbeat
    from datetime import datetime,timezone
    q=db.query(Storage).filter(Storage.key=='posting_projects')
    if account_id:q=q.filter(Storage.account_id==str(account_id))
    projects=0;active=0
    for row in q.all():
        try:data=json.loads(row.value or '[]')
        except Exception:data=[]
        if isinstance(data,list):
            projects+=len(data);active+=sum(1 for x in data if isinstance(x,dict) and str(x.get('mode') or 'auto')!='off')
    hb=db.query(ReliabilityHeartbeat).filter(ReliabilityHeartbeat.module=='runtime',ReliabilityHeartbeat.worker_id=='social_posting_scheduler').order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    age=None
    if hb and hb.last_seen_at:
        seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc);age=int((datetime.now(timezone.utc)-seen).total_seconds())
    return {"projects":projects,"active_projects":active,"scheduler_age_sec":age,"scheduler_state":hb.state if hb else None,"scheduler_details":(hb.details_json or {}) if hb else {}}

def _social_diagnose(db, account_id, requirement, a):
    # SOCIAL_HEARTBEAT_ZERO_AGE_TRUTH_V1: age=0 is the freshest possible heartbeat,
    # not a missing value. Using `age or 999999` converted integer zero to stale
    # and created false SOCIAL_SCHEDULER_STALLED incidents immediately after a beat.
    _age=a.get('scheduler_age_sec')
    _age_stale=(_age is None or int(_age)>180)
    if int(a.get('active_projects') or 0)>0 and _age_stale:return {"classification":"SOCIAL_SCHEDULER_STALLED","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"restart_social_scheduler"}
    _details=(a.get('scheduler_details') or {}) if isinstance(a,dict) else {}
    _cleanup=_details.get('social_vk_cleanup') or _details.get('ocean_vk_cleanup') or {}
    # CAPTCHA/backoff is an external VK dependency wait on a bounded cleanup
    # operation, not a scheduler failure. The heartbeat proves the loop itself
    # is alive and contract_ok=True; keep the dependency visible without
    # restarting a healthy scheduler or asking the owner to operate it.
    if str(_cleanup.get('status') or '') in {'captcha','backoff'}:
        return {
            "classification":"SOCIAL_EXTERNAL_CAPTCHA_WAIT",
            "health":"waiting_external",
            "dependency_state":"waiting_external",
            "confidence":1.0,
            "evidence_for":[a],
            "safe_action":None,
            "owner_action_required":False,
            "retry_after_seconds":int(_cleanup.get('retry_after_seconds') or _cleanup.get('next_retry_in_seconds') or 0),
            "pending_count":int(_cleanup.get('pending_count') or 0),
        }
    # SOCIAL_PRE_SLOT_EXTERNAL_WAIT_V1: when the scheduler is alive and every
    # current static pre-slot blocker is explicitly marked waiting_external, the
    # runtime is not internally degraded. Keep the dependency visible and never
    # restart or bypass the fail-closed VK requirement.
    _pre=_details.get('pre_slot_readiness') or {}
    _pre_blocked=[x for x in (_pre.get('blocked') or []) if isinstance(x,dict)]
    _pre_external=[x for x in _pre_blocked if str(x.get('dependency_state') or '')=='waiting_external']
    _scheduler_blocked_count=int(_details.get('blocked_count') or 0)
    if (
        int(a.get('active_projects') or 0)>0
        and str(a.get('scheduler_state') or '') != 'ok'
        and _pre_blocked
        and len(_pre_external)==len(_pre_blocked)
        and _scheduler_blocked_count==len(_pre_blocked)
    ):
        return {
            "classification":"SOCIAL_PRE_SLOT_WAITING_EXTERNAL",
            "health":"waiting_external",
            "dependency_state":"waiting_external",
            "confidence":1.0,
            "evidence_for":_pre_external[:10],
            "safe_action":None,
            "owner_action_required":False,
            "client_dependencies_count":int(_details.get("client_dependencies_count") or 0),
        }
    if int(a.get('active_projects') or 0)>0 and str(a.get('scheduler_state') or '') != 'ok':
        if bool(_details.get("client_dependencies_waiting")) and bool(_details.get("contract_ok")):
            return {"classification":"SOCIAL_FLOW_OK_WITH_CLIENT_DEPENDENCIES","health":"pass","confidence":1.0,"evidence_for":[{"client_dependencies_count":int(_details.get("client_dependencies_count") or 0)}],"safe_action":None,"owner_action_required":False}
        return {"classification":"SOCIAL_SCHEDULER_DEGRADED","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"inspect_social_contract_blockers"}
    # A delivered slot can still violate the promised delivery SLA. This is not
    # a broken scheduler and must not trigger a restart, but it is also not
    # SOCIAL_FLOW_OK: keep the factual quality incident visible until a clean
    # service window proves recovery.
    _sla=[]
    for _name in ("ocean_daily_delivery","dushi_daily_delivery"):
        _v=_details.get(_name) or {}
        if int(_v.get("sla_breach_count") or 0)>0:
            _sla.append({"project":_v.get("project_name") or _name,"sla_breach_count":int(_v.get("sla_breach_count") or 0),"late_due":int(_v.get("late_due") or 0),"missing_due":int(_v.get("missing_due") or 0),"status":_v.get("status")})
    if _sla:
        return {"classification":"SOCIAL_DELIVERY_SLA_BREACH","health":"degraded","confidence":1.0,"evidence_for":_sla,"safe_action":"inspect_social_delivery_sla","owner_action_required":False}
    return {"classification":"SOCIAL_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}


def _reactivation_state(db, account_id: str | None) -> dict:
    from app.models.reliability import ReliabilityHeartbeat
    from datetime import datetime, timezone
    from sqlalchemy import text
    p={"a":str(account_id or "")}; scope="(:a='' OR account_id=:a)"
    cand=dict(db.execute(text(f"select status,count(*) from reactivation_candidates where {scope} group by status"),p).all())
    msgs=dict(db.execute(text(f"select status,count(*) from reactivation_messages where {scope} group by status"),p).all())
    stale=int(db.execute(text(f"select count(*) from reactivation_candidates where {scope} and status in ('candidate','needs_review','approved','scheduled','cooldown') and watch_since<now()-interval '45 days'"),p).scalar() or 0)
    hb=db.query(ReliabilityHeartbeat).filter(ReliabilityHeartbeat.module=='runtime',ReliabilityHeartbeat.worker_id=='reactivation_scheduler').order_by(ReliabilityHeartbeat.last_seen_at.desc()).first()
    age=None
    if hb and hb.last_seen_at:
        seen=hb.last_seen_at if hb.last_seen_at.tzinfo else hb.last_seen_at.replace(tzinfo=timezone.utc); age=int((datetime.now(timezone.utc)-seen).total_seconds())
    settings=None
    mop_active=None
    avito_capability=None
    if account_id:
        row=db.execute(text("select enabled,mode,daily_limit,max_attempts_per_dialog,cooldown_days,disabled_reason from reactivation_settings where account_id=:a"),p).mappings().first(); settings=dict(row) if row else {"enabled":True,"mode":"recommend"}
        try:
            from app.api.messenger import get_manager_balance
            mop_active=bool((get_manager_balance(str(account_id)) or {}).get("active"))
        except Exception:
            mop_active=False
        try:
            from app.reactivation_avito_health import effective_capability
            avito_capability=effective_capability(db, str(account_id)) or None
        except Exception:
            avito_capability=None
    hb_details=hb.details_json if hb else {}
    # Portfolio capability truth comes from latest account-scoped probe storage,
    # not only from an older scheduler heartbeat. A recovered/partially blocked
    # account must not stay falsely waiting_external until the next timer tick.
    capability_waiting_external_accounts=int((hb_details or {}).get('capability_waiting_external_accounts') or 0)
    capability_degraded_accounts=int((hb_details or {}).get('capability_degraded_accounts') or 0)
    capability_partial_external_accounts=0
    if not account_id:
        try:
            from app.reactivation_avito_health import effective_capability
            _caps=[]
            _cap_accounts=[str(r[0]) for r in db.execute(text("select distinct account_id from storage where key in ('reactivation_avito_capability_v1','reactivation_inbox_capability_v1') and account_id is not null")).all()]
            for _cap_account in _cap_accounts:
                try: _caps.append(effective_capability(db, _cap_account) or {})
                except Exception: pass
            capability_waiting_external_accounts=sum(1 for _c in _caps if str(_c.get('state') or '')=='waiting_external')
            capability_partial_external_accounts=sum(1 for _c in _caps if str(_c.get('classification') or '')=='avito_messenger_history_partial_402')
            capability_degraded_accounts=sum(1 for _c in _caps if str(_c.get('state') or '')=='degraded' and str(_c.get('classification') or '')!='avito_messenger_history_partial_402')
        except Exception:
            pass
    transport_unavailable=int(db.execute(text(f"select count(*) from reactivation_messages where {scope} and coalesce(error_code,'')='transport_unavailable'"),p).scalar() or 0)
    # REACTIVATION_AMBIGUOUS_DELIVERY_AGE_V1: delivery_unknown is intentionally
    # fail-closed. The scheduler repeatedly performs READ -> exact match -> mark_sent
    # and never re-POSTs. Once that safe reconciliation has run and an ambiguity is
    # old, it is an external unresolved dependency, not an internally degraded loop.
    unknown_oldest_age_sec=int(db.execute(text(f"""
        select coalesce(extract(epoch from (now()-min(updated_at)))::int,0)
          from reactivation_messages where {scope} and status='delivery_unknown'
    """),p).scalar() or 0)
    return {"account_id":account_id,"settings":settings,"mop_active":mop_active,"avito_capability":avito_capability,"candidates":cand,"messages":msgs,"active_candidates":sum(int(cand.get(x) or 0) for x in ('candidate','needs_review','approved','scheduled','cooldown')),"ready_messages":int(msgs.get('ready') or 0),"sent_messages":int(msgs.get('sent') or 0),"blocked_messages":int(msgs.get('blocked') or 0),"delivery_unknown_messages":int(msgs.get('delivery_unknown') or 0),"delivery_unknown_oldest_age_sec":unknown_oldest_age_sec,"transport_unavailable_messages":transport_unavailable,"replied_candidates":int(cand.get('replied') or 0),"stale_candidates_45d":stale,"scheduler_age_sec":age,"scheduler_state":hb.state if hb else None,"scheduler_details":hb_details,"capability_waiting_external_accounts":capability_waiting_external_accounts,"capability_partial_external_accounts":capability_partial_external_accounts,"capability_degraded_accounts":capability_degraded_accounts}

def _reactivation_diagnose(db, account_id, requirement, a):
    st=a.get('settings') or {}
    if account_id and st.get('enabled') is False:return {"classification":"REACTIVATION_INTENTIONALLY_DISABLED","health":"pass","confidence":1.0,"evidence_for":[a],"safe_action":None}
    if account_id and a.get('mop_active') is False:return {"classification":"REACTIVATION_NOT_ENTITLED_OR_MOP_INACTIVE","health":"pass","confidence":1.0,"evidence_for":[{"mop_active":False}],"safe_action":None,"owner_action_required":False}
    cap=a.get('avito_capability') or {}
    # REACTIVATION_PARTIAL_402_EXTERNAL_TRUTH_V1: a mixed history sample may
    # be labelled partial_402 while its concrete error classes prove the same
    # provider subscription dependency. Retrying cannot self-heal that.
    _cap_class=str(cap.get('classification') or '')
    _cap_errors={str(x) for x in (cap.get('history_error_classes') or [])}
    if account_id and _cap_class=='avito_messenger_subscription_required':
        _persist=cap.get("persistence") or {}
        return {"classification":"REACTIVATION_AVITO_MESSENGER_SUBSCRIPTION_REQUIRED","health":"waiting_external","dependency_state":"waiting_external","confidence":1.0,"evidence_for":[cap],"safe_action":None,"owner_action_required":bool(_persist.get("persistent") and _persist.get("owner_action_required")),"auto_reprobe":True,"persistent_external":bool(_persist.get("persistent"))}
    # Mixed 200/402 history is chat-scoped: healthy dialogs must continue to
    # sync/reconcile while only the blocked dialogs wait for Avito access.
    # Do not turn a partial provider restriction into an account-wide stop.
    if account_id and _cap_class=='avito_messenger_history_partial_402':
        return {"classification":"REACTIVATION_AVITO_PARTIAL_CHAT_ACCESS","health":"waiting_external","dependency_state":"partial_external","service_continues":True,"confidence":1.0,"evidence_for":[cap],"safe_action":None,"owner_action_required":False,"auto_reprobe":True}
    if account_id and str(cap.get('state') or '')=='waiting_external' and _cap_class in {'avito_rate_limited','avito_api_unavailable','avito_transport_unreachable'}:
        return {"classification":"REACTIVATION_AVITO_PROVIDER_TEMPORARILY_UNAVAILABLE","health":"waiting_external","dependency_state":"waiting_external","confidence":1.0,"evidence_for":[cap],"safe_action":None,"owner_action_required":False,"auto_reprobe":True}
    if account_id and str(cap.get('state') or '')=='degraded':
        return {"classification":"REACTIVATION_AVITO_TRANSPORT_DEGRADED","health":"degraded","confidence":1.0,"evidence_for":[cap],"safe_action":"inspect_reactivation_errors","owner_action_required":False}
    age=a.get('scheduler_age_sec')
    if age is None or int(age)>1800:return {"classification":"REACTIVATION_SCHEDULER_STALLED","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"restart_reactivation_scheduler"}
    if str(a.get('scheduler_state') or '')!='ok':return {"classification":"REACTIVATION_SCHEDULER_DEGRADED","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"inspect_reactivation_errors"}
    # Portfolio capability failures outrank message-level ambiguity: an Avito
    # subscription/read-path block affects an entire active account and needs
    # owner/provider action, while delivery_unknown remains fail-closed.
    # Portfolio partial-402 is an external chat-scoped dependency, not internal
    # degradation. Evaluate explicit waiting/partial counts before generic
    # degraded count so healthy chats continue and guardian does not recovery-loop.
    if not account_id and (int(a.get('capability_waiting_external_accounts') or 0)>0 or int(a.get('capability_partial_external_accounts') or 0)>0):return {"classification":"REACTIVATION_AVITO_PORTFOLIO_EXTERNAL_ACCESS","health":"waiting_external","dependency_state":"partial_external" if int(a.get('capability_partial_external_accounts') or 0)>0 else "waiting_external","service_continues":True,"confidence":1.0,"evidence_for":[{"capability_waiting_external_accounts":a.get('capability_waiting_external_accounts'),"capability_partial_external_accounts":a.get('capability_partial_external_accounts')}],"safe_action":None,"owner_action_required":False,"auto_reprobe":True}
    if not account_id and int(a.get('capability_degraded_accounts') or 0)>0:return {"classification":"REACTIVATION_AVITO_PORTFOLIO_DEGRADED","health":"degraded","confidence":1.0,"evidence_for":[{"capability_degraded_accounts":a.get('capability_degraded_accounts')}],"safe_action":"recover_reactivation_transport","owner_action_required":False}
    if int(a.get('delivery_unknown_messages') or 0)>0:
        _unknown_age=int(a.get('delivery_unknown_oldest_age_sec') or 0)
        _details=a.get('scheduler_details') or {}
        _unresolved=int(_details.get('delivery_unresolved') or 0)
        _external_402=int(_details.get('delivery_unknown_external_402') or 0)
        _evidence_fresh=False
        try:
            from datetime import datetime as _dt_react, timezone as _tz_react
            _checked=_dt_react.fromisoformat(str(_details.get('delivery_unknown_evidence_checked_at') or '').replace('Z','+00:00'))
            if _checked.tzinfo is None: _checked=_checked.replace(tzinfo=_tz_react.utc)
            _evidence_fresh=(_dt_react.now(_tz_react.utc)-_checked).total_seconds() <= 1800
        except Exception:
            _evidence_fresh=False
        # REACTIVATION_AMBIGUOUS_EXACT_402_TRUTH_V1: age alone is never proof
        # of an external dependency. Only a fresh read-only exact-chat probe
        # published by safe recovery may move every ambiguity to waiting_external.
        if _unresolved > 0 and _external_402 >= int(a.get('delivery_unknown_messages') or 0) and _evidence_fresh:
            return {"classification":"REACTIVATION_DELIVERY_AMBIGUOUS_EXTERNAL","health":"waiting_external","dependency_state":"waiting_external","confidence":1.0,"evidence_for":[{"delivery_unknown_messages":a.get('delivery_unknown_messages'),"oldest_age_sec":_unknown_age,"safe_reconcile_unresolved":_unresolved,"exact_chat_external_402":_external_402}],"safe_action":None,"owner_action_required":False}
        return {"classification":"REACTIVATION_DELIVERY_UNKNOWN","health":"degraded","confidence":1.0,"evidence_for":[{"delivery_unknown_messages":a.get('delivery_unknown_messages'),"oldest_age_sec":_unknown_age,"exact_chat_external_402":_external_402,"exact_chat_evidence_fresh":_evidence_fresh}],"safe_action":"recover_reactivation_transport","owner_action_required":False}
    if str(st.get('mode') or 'recommend')=='auto' and int(a.get('stale_candidates_45d') or 0)>0:return {"classification":"REACTIVATION_AUTO_BACKLOG_STALLED","health":"degraded","confidence":1.0,"evidence_for":[{"stale_candidates_45d":a.get('stale_candidates_45d')}],"safe_action":"reconcile_reactivation_backlog"}
    if str(st.get('mode') or 'recommend')!='auto' and int(a.get('ready_messages') or 0)>0:return {"classification":"REACTIVATION_WAITING_CONFIRMATION","health":"pass","confidence":1.0,"evidence_for":[{"ready_messages":a.get('ready_messages')}],"safe_action":None,"owner_action_required":False}
    return {"classification":"REACTIVATION_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[a],"safe_action":None}


# LEAD_RADAR_CONTROL_ADAPTER_V2: owner-scoped multi-tenant Lead Radar adapter.
# V2 requires real profile/offering catalogs, full source-group coverage,
# persistent dedupe, provider quota enforcement and runtime self-heal evidence.
# The production contract marker lives beside the implementation it certifies.
def _prospecting_state(db, account_id: str | None) -> dict:
    """Lead Radar business state, multi-tenant and owner-scoped."""
    from sqlalchemy import inspect as _sa_inspect, text
    from app.services.lead_radar.health import health as _lead_radar_health
    from app.services.lead_radar.coverage import SOURCE_GROUPS, source_coverage
    from app.services.lead_radar.registry import build_connectors

    inspector=_sa_inspect(db.get_bind())
    tables=set(inspector.get_table_names())
    profile_table='lead_radar_profiles' in tables
    offerings_table='lead_radar_offerings' in tables
    tenants=[]
    coverage_errors=[]

    if profile_table and offerings_table:
        profiles=[dict(x) for x in db.execute(text(
            """select owner_user_id,name,business_type,mode,enabled,min_score,
                      geography_json,intent_keywords,negative_keywords,settings_json
               from lead_radar_profiles
               where enabled=true
               order by owner_user_id"""
        )).mappings().all()]
        for profile in profiles:
            owner=int(profile['owner_user_id'])
            catalog=[dict(x) for x in db.execute(text(
                """select offering_code,external_key,kind,name,keywords,negative_keywords,
                          price_min_rub,price_max_rub,currency,enabled,metadata_json
                   from lead_radar_offerings
                   where owner_user_id=:o and enabled=true
                   order by offering_code"""
            ),{'o':owner}).mappings().all()]
            try:
                connectors=build_connectors(owner,profile=profile,catalog=catalog)
                cov=source_coverage(connectors)
                cov_error=None
            except Exception as exc:
                connectors=[]
                cov=source_coverage([])
                cov_error=f"{type(exc).__name__}:{str(exc)[:300]}"
                coverage_errors.append({'owner_user_id':owner,'error':cov_error})
            try:
                runtime=_lead_radar_health(db.get_bind(),owner_user_id=owner)
            except Exception as exc:
                runtime={'status':'BLOCKED','error':f"{type(exc).__name__}:{str(exc)[:300]}",
                         'sources_blocked_external':0,'outreach_blocked':0}
            tenants.append({
                'owner_user_id':owner,
                'name':profile.get('name'),
                'business_type':profile.get('business_type'),
                'mode':profile.get('mode'),
                'offerings_count':len(catalog),
                'configured_groups':int(cov.get('configured_groups') or 0),
                'target_groups':len(SOURCE_GROUPS),
                'missing_groups':list(cov.get('missing_group_keys') or []),
                'coverage_ready':bool(cov.get('coverage_ready')),
                'coverage_error':cov_error,
                'runtime_status':str(runtime.get('status') or 'DEGRADED').upper(),
                'runtime':runtime,
            })
    else:
        owners=[int(x) for x in db.execute(text(
            "select distinct owner_user_id from lead_radar_sources where enabled=true and owner_user_id is not null order by owner_user_id"
        )).scalars().all()]
        for owner in owners:
            try:
                connectors=build_connectors(owner)
                cov=source_coverage(connectors)
                runtime=_lead_radar_health(db.get_bind(),owner_user_id=owner)
                err=None
            except Exception as exc:
                cov=source_coverage([])
                runtime={'status':'BLOCKED','error':str(exc)[:300]}
                err=f"{type(exc).__name__}:{str(exc)[:300]}"
                coverage_errors.append({'owner_user_id':owner,'error':err})
            tenants.append({
                'owner_user_id':owner,'name':None,'business_type':'legacy','mode':'legacy',
                'offerings_count':0,'configured_groups':int(cov.get('configured_groups') or 0),
                'target_groups':len(SOURCE_GROUPS),'missing_groups':list(cov.get('missing_group_keys') or []),
                'coverage_ready':False,'coverage_error':err,
                'runtime_status':str(runtime.get('status') or 'DEGRADED').upper(),'runtime':runtime,
            })

    def _rank(status):
        return {'PASS':0,'DEGRADED':1,'BLOCKED':2}.get(str(status or '').upper(),1)
    worst=max((t['runtime_status'] for t in tenants),key=_rank,default='DEGRADED')
    runtime={
        'status':worst,
        'total_leads':sum(int((t.get('runtime') or {}).get('total_leads') or 0) for t in tenants),
        'fresh_24h':sum(int((t.get('runtime') or {}).get('fresh_24h') or 0) for t in tenants),
        'stuck_outreach':sum(int((t.get('runtime') or {}).get('stuck_outreach') or 0) for t in tenants),
        'outreach_retrying':sum(int((t.get('runtime') or {}).get('outreach_retrying') or 0) for t in tenants),
        'outreach_blocked':sum(int((t.get('runtime') or {}).get('outreach_blocked') or 0) for t in tenants),
        'sources_recovering':sum(int((t.get('runtime') or {}).get('sources_recovering') or 0) for t in tenants),
        'sources_blocked_external':sum(int((t.get('runtime') or {}).get('sources_blocked_external') or 0) for t in tenants),
        'sources_overdue':sum(int((t.get('runtime') or {}).get('sources_overdue') or 0) for t in tenants),
        'owner_action_required':any(bool((t.get('runtime') or {}).get('owner_action_required')) for t in tenants),
    }

    active_sources=int(db.execute(text(
        "select count(*) from lead_radar_sources where enabled=true"
    )).scalar() or 0)
    last_success=db.execute(text(
        "select max(last_success_at) from lead_radar_sources where enabled=true"
    )).scalar()
    tenant_owners=[int(t['owner_user_id']) for t in tenants]
    if tenant_owners:
        crm_linked=int(db.execute(text(
            """select count(*)
               from lead_radar_leads l
               join boris_crm_deals d on d.id=l.crm_deal_id
               where l.owner_user_id = any(:owners)
                 and l.status not in ('rejected','stale','crm_error')
                 and d.status='open'"""
        ),{'owners':tenant_owners}).scalar() or 0)
    else:
        crm_linked=0

    source_rows=db.execute(text(
        "select policy_json from lead_radar_sources where enabled=true"
    )).scalars().all()
    quotas_obeyed=all(
        isinstance(policy,dict) and int(policy.get('min_interval_seconds') or 0)>0
        for policy in source_rows
    ) if source_rows else False

    unique_constraints=inspector.get_unique_constraints("lead_radar_leads")
    persistent_dedupe=any(
        set(x.get("column_names") or [])=={"owner_user_id","fingerprint"}
        for x in unique_constraints
    )
    lead_columns={x.get("name") for x in inspector.get_columns("lead_radar_leads")}
    source_columns={x.get("name") for x in inspector.get_columns("lead_radar_sources")}
    all_catalogs=bool(tenants) and profile_table and offerings_table and all(int(t['offerings_count'])>0 for t in tenants)
    all_coverage=bool(tenants) and all(bool(t['coverage_ready']) for t in tenants)
    min_groups=min((int(t['configured_groups']) for t in tenants),default=0)
    missing=sorted({g for t in tenants for g in t.get('missing_groups') or []})
    dev_counts=[int(t['offerings_count']) for t in tenants if str(t.get('mode') or '')=='development']

    return {
        "enabled":bool(tenants),
        "catalog_mode":"owner_scoped_products_services",
        "owner_scoped_catalogs":profile_table and offerings_table,
        "owner_scoped_sources":True,
        "active_tenants":len(tenants),
        "tenant_states":tenants,
        "all_enabled_tenants_have_catalog":all_catalogs,
        "offerings_count":sum(int(t['offerings_count']) for t in tenants),
        "services_count":max(dev_counts) if dev_counts else 0,
        "source_groups_count":min_groups,
        "target_source_groups_count":len(SOURCE_GROUPS),
        "configured_source_groups":sorted(set(SOURCE_GROUPS)-set(missing)) if tenants else [],
        "missing_source_groups":missing,
        "source_coverage_error":coverage_errors or None,
        "max_age_hours":168,
        "persistent_dedupe":persistent_dedupe,
        "cross_source_merge":"dedupe_key" in lead_columns,
        "crm":"boris_crm",
        "autocontact":"policy_gated",
        "internal_daily_volume_cap":None,
        "process_all_available_fresh_leads":True,
        "provider_quotas_obeyed":quotas_obeyed,
        "coverage_ready_for_business_pass":all_catalogs and all_coverage,
        "followup":"policy_gated",
        "self_heal":{"recovery_strategy","next_scan_at","consecutive_failures"}.issubset(source_columns),
        "fail_closed_without_catalog":True,
        "autonomous_continuation":True,
        "owner_operator_required":False,
        "escalate_only_when_blocked":True,
        "owner_is_operator":False,
        "active_sources":active_sources,
        "crm_linked_leads":crm_linked,
        "last_source_success_at":last_success.isoformat() if last_success else None,
        "runtime_health":runtime,
    }

def _prospecting_diagnose(db, account_id, requirement, a):
    runtime=dict(a.get("runtime_health") or {})
    state=str(runtime.get("status") or "DEGRADED").upper()
    if not bool(a.get("enabled")):
        return {"classification":"PROSPECTING_NO_ACTIVE_TENANTS","health":"degraded","confidence":1.0,
                "evidence_for":[a],"safe_action":None,"owner_action_required":False}
    if not bool(a.get("owner_scoped_catalogs")) or not bool(a.get("all_enabled_tenants_have_catalog")):
        return {"classification":"PROSPECTING_CLIENT_CATALOG_INCOMPLETE","health":"degraded","confidence":1.0,
                "evidence_for":[{"active_tenants":a.get("active_tenants"),
                                 "tenant_states":a.get("tenant_states")}],
                "safe_action":None,"owner_action_required":False}
    if not bool(a.get("coverage_ready_for_business_pass")):
        return {"classification":"PROSPECTING_COVERAGE_INCOMPLETE","health":"degraded","confidence":1.0,
                "evidence_for":[{"configured_groups_min":a.get("source_groups_count"),
                                 "target_groups":a.get("target_source_groups_count"),
                                 "missing_groups":a.get("missing_source_groups"),
                                 "tenant_states":a.get("tenant_states"),
                                 "coverage_error":a.get("source_coverage_error")}],
                "safe_action":None,"owner_action_required":False}
    if state=="BLOCKED":
        external=bool(int(runtime.get("sources_blocked_external") or 0) or int(runtime.get("outreach_blocked") or 0))
        return {"classification":"PROSPECTING_EXTERNAL_BLOCK" if external else "PROSPECTING_RUNTIME_BLOCKED",
                "health":"waiting_external" if external else "degraded",
                "dependency_state":"waiting_external" if external else None,
                "confidence":1.0,"evidence_for":[runtime],"safe_action":None,"owner_action_required":False}
    if state!="PASS":
        return {"classification":"PROSPECTING_SELF_HEAL_IN_PROGRESS","health":"degraded","confidence":1.0,
                "evidence_for":[runtime],"safe_action":None,"owner_action_required":False}
    return {"classification":"PROSPECTING_FLOW_OK","health":"pass","confidence":1.0,
            "evidence_for":[{"active_tenants":a.get("active_tenants"),
                             "offerings_count":a.get("offerings_count"),
                             "configured_groups_min":a.get("source_groups_count"),
                             "fresh_24h":runtime.get("fresh_24h"),
                             "last_source_success_at":a.get("last_source_success_at")}],
            "safe_action":None,"owner_action_required":False}


def _email_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    return {"queued":int(db.execute(text("select count(*) from email_queue where status in ('queued','retry')")).scalar() or 0),"stale_due":int(db.execute(text("select count(*) from email_queue where status in ('queued','retry') and coalesce(next_attempt_at,created_at)<now()-interval '15 minutes'")).scalar() or 0),"stale_sending":int(db.execute(text("select count(*) from email_queue where status='sending' and updated_at<now()-interval '15 minutes'")).scalar() or 0),"failed_24h":int(db.execute(text("select count(*) from email_queue where status in ('failed','delivery_unknown') and updated_at>=now()-interval '24 hours'")).scalar() or 0),"sent_24h":int(db.execute(text("select count(*) from email_queue where status='sent' and sent_at>=now()-interval '24 hours'")).scalar() or 0)}

def _email_diagnose(db, account_id, requirement, a):
    if int(a.get('stale_due') or 0)>0:return {"classification":"EMAIL_QUEUE_STALLED","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"run_email_queue_recovery"}
    if int(a.get('failed_24h') or 0)>0:return {"classification":"EMAIL_DELIVERY_FAILURES","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"inspect_email_failures"}
    return {"classification":"EMAIL_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}


def _telegram_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    runs=int(db.execute(text("select count(*) from telegram_sales_cycle_runs where started_at>=now()-interval '24 hours'")).scalar() or 0)
    replies=int(db.execute(text("select count(*) from boris_sales_tg_replies where created_at>=now()-interval '24 hours'")).scalar() or 0)
    alerts=int(db.execute(text("select count(*) from prospect_reply_alerts where created_at>=now()-interval '24 hours' and coalesce(status,'') in ('pending','failed')")).scalar() or 0)
    return {"sales_cycle_runs_24h":runs,"replies_24h":replies,"pending_or_failed_reply_alerts":alerts}

def _telegram_diagnose(db, account_id, requirement, a):
    if int(a.get('pending_or_failed_reply_alerts') or 0)>0:return {"classification":"TELEGRAM_REPLY_ALERT_BACKLOG","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"retry_reply_alerts"}
    return {"classification":"TELEGRAM_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}


def _analytics_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    today='daily_stats:' + __import__('datetime').date.today().isoformat()
    q=db.query(Storage).filter(Storage.key==today)
    if account_id:q=q.filter(Storage.account_id==str(account_id))
    snapshots=q.count()
    params={"a":str(account_id or '')}
    events=int(db.execute(text("select count(*) from revenue_events where (:a='' or account_id=:a) and occurred_at>=now()-interval '24 hours'"),params).scalar() or 0)
    return {"daily_stats_snapshots_today":snapshots,"revenue_events_24h":events}

def _analytics_diagnose(db, account_id, requirement, a):
    if account_id and int(a.get('daily_stats_snapshots_today') or 0)<=0:return {"classification":"ANALYTICS_SNAPSHOT_MISSING","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"run_stats_collection"}
    return {"classification":"ANALYTICS_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[a],"safe_action":None}


def _billing_state(db, account_id: str | None) -> dict:
    from sqlalchemy import text
    p={"a":str(account_id or '')};scope="(:a='' OR account_id=:a)"
    return {"failed_payments_24h":int(db.execute(text(f"select count(*) from payments where {scope} and status in ('failed','error') and created_at>=now()-interval '24 hours'"),p).scalar() or 0),"paid_30d":int(db.execute(text(f"select count(*) from payments where {scope} and status in ('paid','success') and coalesce(paid_at,created_at)>=now()-interval '30 days'"),p).scalar() or 0),"usage_intents_24h":int(db.execute(text(f"select count(*) from boris_billing_usage_intents where {scope} and created_at>=now()-interval '24 hours'"),p).scalar() or 0)}

def _billing_diagnose(db, account_id, requirement, a):
    if int(a.get('failed_payments_24h') or 0)>0:return {"classification":"BILLING_PAYMENT_FAILURES","health":"degraded","confidence":1.0,"evidence_for":[a],"safe_action":"inspect_failed_payments"}
    return {"classification":"BILLING_FLOW_OK","health":"pass","confidence":1.0,"evidence_for":[],"safe_action":None}


def _sitebuild_state(db, account_id: str | None) -> dict:
    from pathlib import Path
    v=Path('/root/BORIS/backend/images/_sitebuild/videos');sc=Path('/root/BORIS/backend/images/_sitebuild/screens')
    return {"video_dir_exists":v.exists(),"screen_dir_exists":sc.exists(),"videos":sum(1 for x in v.glob('*') if x.is_file() and x.suffix.lower() in {'.mp4','.webm','.mov'}) if v.exists() else 0,"screens":sum(1 for x in sc.glob('*') if x.is_file() and x.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}) if sc.exists() else 0}

def _sitebuild_diagnose(db, account_id, requirement, a):
    ok=bool(a.get('video_dir_exists')) and bool(a.get('screen_dir_exists'))
    return {"classification":"SITEBUILD_STORAGE_OK" if ok else "SITEBUILD_STORAGE_MISSING","health":"pass" if ok else "degraded","confidence":1.0,"evidence_for":[a],"safe_action":None if ok else "restore_sitebuild_storage"}

def _verify_from_business(state_fn: Callable, diagnose_fn: Callable) -> Callable:
    def _verify(db, account_id: str | None, requirement) -> dict:
        actual=state_fn(db,account_id) or {}
        diagnosis=diagnose_fn(db,account_id,requirement,actual) or {}
        passed=str(diagnosis.get("health") or "pass").lower()=="pass"
        return {"passed":passed,"actual_state":actual,"remaining_drift":{},"business_diagnosis":diagnosis,"source":"business_post_condition"}
    return _verify


def _marketing_verify(db, account_id: str | None, requirement) -> dict:
    actual=marketing_state(db,account_id) or {}
    desired=dict(getattr(requirement,"desired_state_json",{}) or {})
    remaining={k:{"desired":v,"actual":actual.get(k)} for k,v in desired.items() if actual.get(k)!=v}
    return {"passed":not remaining,"actual_state":actual,"remaining_drift":remaining,"source":"marketing_config_readback"}


def _generic(module: str) -> Adapter:
    return Adapter(module=module, get_state=lambda db, account_id, m=module: _generic_state(db, account_id, m))


register(Adapter(module="marketing", get_state=marketing_state, preflight=_marketing_preflight, apply=_marketing_apply, verify=_marketing_verify, rollback=_marketing_rollback, diagnose=_marketing_diagnose))
register(Adapter(module="mop", get_state=_mop_state, verify=_verify_from_business(_mop_state,_mop_diagnose), diagnose=_mop_diagnose))
register(Adapter(module="crm", get_state=_crm_state, verify=_verify_from_business(_crm_state,_crm_diagnose), diagnose=_crm_diagnose))
register(Adapter(module="messages", get_state=_messages_state, verify=_verify_from_business(_messages_state,_messages_diagnose), diagnose=_messages_diagnose))
register(Adapter(module="telephony", get_state=_telephony_state, verify=_verify_from_business(_telephony_state,_telephony_diagnose), diagnose=_telephony_diagnose))
register(Adapter(module="rop", get_state=_rop_state, verify=_verify_from_business(_rop_state,_rop_diagnose), diagnose=_rop_diagnose))
register(Adapter(module="direct", get_state=_direct_state, verify=_verify_from_business(_direct_state,_direct_diagnose), diagnose=_direct_diagnose))
register(Adapter(module="feed_factory", get_state=_feed_factory_state, verify=_verify_from_business(_feed_factory_state,_feed_factory_diagnose), diagnose=_feed_factory_diagnose))
register(Adapter(module="social", get_state=_social_state, verify=_verify_from_business(_social_state,_social_diagnose), diagnose=_social_diagnose))
register(Adapter(module="reactivation", get_state=_reactivation_state, verify=_verify_from_business(_reactivation_state,_reactivation_diagnose), diagnose=_reactivation_diagnose))
register(Adapter(module="prospecting", get_state=_prospecting_state, verify=_verify_from_business(_prospecting_state,_prospecting_diagnose), diagnose=_prospecting_diagnose))
register(Adapter(module="email", get_state=_email_state, verify=_verify_from_business(_email_state,_email_diagnose), diagnose=_email_diagnose))
register(Adapter(module="telegram", get_state=_telegram_state, verify=_verify_from_business(_telegram_state,_telegram_diagnose), diagnose=_telegram_diagnose))
register(Adapter(module="analytics", get_state=_analytics_state, verify=_verify_from_business(_analytics_state,_analytics_diagnose), diagnose=_analytics_diagnose))
register(Adapter(module="billing", get_state=_billing_state, verify=_verify_from_business(_billing_state,_billing_diagnose), diagnose=_billing_diagnose))
register(Adapter(module="sitebuild", get_state=_sitebuild_state, verify=_verify_from_business(_sitebuild_state,_sitebuild_diagnose), diagnose=_sitebuild_diagnose))



# CONTROL_PROFILE_V1: not every module should expose arbitrary mutation.
# A module is controllable when it has factual read/verify/diagnosis and an
# explicit fail-closed control policy. Mutable configuration additionally needs
# rollback/compensation. External irreversible actions remain policy gated.
CONTROL_PROFILES = {
    "marketing": {"control_mode":"safe_mutation","mutation_scope":"account_config","external_actions_gated":True,"rollback_strategy":"compare_and_set_compensation","safe_recovery":True},
    "mop": {"control_mode":"safe_recovery","mutation_scope":"internal_queue_recovery","external_actions_gated":True,"rollback_strategy":"idempotent_exactly_once","safe_recovery":True},
    # CRM_SAFE_RECOVERY_PROFILE_V1: generic external writes remain unavailable, but
    # the Brain does own bounded internal CRM recovery (next-action repair, task
    # surfacing, evidence-based completion and persisted-analysis enrichment).
    "crm": {"control_mode":"safe_recovery","mutation_scope":"internal_tasks_and_next_actions_only; no generic external write","external_actions_gated":True,"rollback_strategy":"idempotent_evidence_reconcile_and_audit_log","safe_recovery":True},
    "messages": {"control_mode":"read_verify","mutation_scope":"none","external_actions_gated":True,"rollback_strategy":"no_mutation","safe_recovery":False},
    "telephony": {"control_mode":"read_verify","mutation_scope":"none_generic; domain workers own sync/analysis","external_actions_gated":True,"rollback_strategy":"no_generic_mutation","safe_recovery":False},
    "rop": {"control_mode":"read_verify","mutation_scope":"none_generic; append-only audit worker owns actions","external_actions_gated":True,"rollback_strategy":"no_generic_mutation","safe_recovery":False},
    "feed_factory": {"control_mode":"safe_recovery","mutation_scope":"interrupted_job_recovery","external_actions_gated":True,"rollback_strategy":"idempotent_job_recovery","safe_recovery":True},
    "social": {"control_mode":"read_verify","mutation_scope":"none_generic; service supervisor owns restart","external_actions_gated":True,"rollback_strategy":"no_generic_mutation","safe_recovery":False},
    "reactivation": {"control_mode":"safe_recovery","mutation_scope":"scheduler_and_receipt_reconciliation_only","external_actions_gated":True,"rollback_strategy":"idempotent_timer_restart_no_resend","safe_recovery":True},
    "email": {"control_mode":"safe_recovery","mutation_scope":"delivery_unknown_recovery","external_actions_gated":True,"rollback_strategy":"no_automatic_resend","safe_recovery":True},
    "telegram": {"control_mode":"read_verify","mutation_scope":"none","external_actions_gated":True,"rollback_strategy":"no_mutation","safe_recovery":False},
    "analytics": {"control_mode":"read_verify","mutation_scope":"none","external_actions_gated":True,"rollback_strategy":"no_mutation","safe_recovery":False},
    "direct": {"control_mode":"external_gated","mutation_scope":"none_without_owner_policy","external_actions_gated":True,"rollback_strategy":"provider_rollback_when_action_exists","safe_recovery":False},
    "billing": {"control_mode":"external_gated","mutation_scope":"none_without_explicit_billing_intent","external_actions_gated":True,"rollback_strategy":"ledger_compensation_only","safe_recovery":False},
    "sitebuild": {"control_mode":"safe_recovery","mutation_scope":"local_storage_scaffold","external_actions_gated":True,"rollback_strategy":"idempotent_directory_recovery","safe_recovery":True},
}

def _control_profile(module: str) -> dict:
    return dict(CONTROL_PROFILES.get(module) or {"control_mode":"read_verify","mutation_scope":"none","external_actions_gated":True,"rollback_strategy":"no_mutation","safe_recovery":False})

def adapter_capabilities(adapter: Adapter) -> dict:
    """Machine-readable contract surface for BORIS System Brain."""
    custom = adapter.capabilities() if adapter.capabilities else {}
    base = {
        "get_state": True,
        "preflight": adapter.preflight is not None,
        "apply": adapter.apply is not None,
        "verify": adapter.verify is not None,
        "rollback": adapter.rollback is not None,
        "diagnose": adapter.diagnose is not None,
        **_control_profile(adapter.module),
    }
    if isinstance(custom, dict):
        base.update(custom)
    return base


def adapter_verify(db, adapter: Adapter, account_id: str | None, requirement, desired: dict | None = None) -> dict:
    """Canonical post-condition verification; never infers PASS from apply success."""
    if adapter.verify:
        result = adapter.verify(db, account_id, requirement) or {}
        if "passed" not in result:
            result["passed"] = bool(result.get("ok"))
        return result
    actual = adapter.get_state(db, account_id) or {}
    expected = dict(desired if desired is not None else (getattr(requirement, "desired_state_json", {}) or {}))
    remaining = {k: {"desired": v, "actual": actual.get(k)} for k, v in expected.items() if actual.get(k) != v}
    return {"passed": not remaining, "actual_state": actual, "remaining_drift": remaining, "source": "canonical_readback"}


def adapter_diagnose(db, adapter: Adapter, account_id: str | None, requirement=None, actual_state: dict | None = None) -> dict:
    """Deterministic diagnosis fallback. LLM output is never treated as factual evidence."""
    actual = actual_state if actual_state is not None else (adapter.get_state(db, account_id) or {})
    if adapter.diagnose:
        result = adapter.diagnose(db, account_id, requirement, actual) or {}
        return result if isinstance(result, dict) else {"classification": "UNKNOWN", "details": {}}
    workers = actual.get("workers") if isinstance(actual, dict) else None
    if isinstance(workers, list) and workers:
        bad = [w for w in workers if str(w.get("state") or "").lower() not in {"ok", "pass", "healthy", "running", "idle"}]
        if bad:
            return {"classification": "WORKER_DEGRADED", "confidence": 1.0, "evidence_for": bad, "safe_action": "inspect_or_restart_worker"}
    return {"classification": "NO_DETERMINISTIC_ROOT_CAUSE", "confidence": 0.0, "evidence_for": [], "safe_action": None}
