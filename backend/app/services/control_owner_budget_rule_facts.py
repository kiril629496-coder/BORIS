"""Evidence for the existing owner-authenticated budget write; no business actions."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from sqlalchemy import text

KEYS = ("rule.marketing.daily_budget", "rule.marketing.budget_owner_provenance")
ENTRYPOINT = "app.api.avito:set_kpi_settings"
EVENT_SOURCE = "authenticated_budget_setting"
CONTEXT_KEY = "owner_budget_context"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _when(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def _money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return None
    return result if result.is_finite() and result >= 0 else None


def evaluate_owner_budget_facts(facts):
    """Local settings predicates; subsequent spending is not certified."""
    auth = facts.get("authorization") if isinstance(facts.get("authorization"), dict) else {}
    requested, stored, authorized = (_money(facts.get("requested_budget")), _money(facts.get("stored_budget")),
                                     _money(auth.get("daily_budget_limit_rub")))
    numeric = requested is not None and stored is not None and requested == stored
    actor, owner = facts.get("actor_user_id"), facts.get("account_owner_user_id")
    authority = type(actor) is int and actor > 0 and (
        (type(owner) is int and actor == owner) or facts.get("is_platform_owner") is True)
    provenance = (authority and auth.get("policy_version") == "MONEY_BUDGET_OWNER_PROVENANCE_V1"
                  and auth.get("source") == "authenticated_set_kpi_settings"
                  and type(auth.get("authorized_by_user_id")) is int and auth["authorized_by_user_id"] == actor
                  and _when(auth.get("authorized_at")) is not None
                  and auth.get("authorized_at") == facts.get("authorization_time")
                  and authorized is not None and authorized == requested == stored)
    common = {"account_id": facts.get("account_id"), "actor_user_id": actor, "account_owner_user_id": owner,
              "authorization_time": facts.get("authorization_time"), "storage_readback": facts.get("storage_readback") is True,
              "scope": "local_authenticated_setting", "provider_spend_verified": False}
    result = {}
    for key, passed, reason, detail in (
        (KEYS[0], numeric and provenance, "OWNER_BUDGET_VALUE_AND_AUTHORITY_ROUNDTRIP", {"requested_budget":facts.get("requested_budget"), "stored_budget":facts.get("stored_budget")}),
        (KEYS[1], provenance, "OWNER_BUDGET_AUTHORIZATION_ROUNDTRIP", {"authorization":auth, "authority_proven":authority}),
    ):
        read = facts.get("storage_readback") is True
        result[key] = {"evaluation_status":"EVALUATED" if read else "NOT_PROVEN", "predicate_result":bool(passed) if read else None,
            "reason":reason if read and passed else reason+"_NOT_PROVEN", "facts":dict(common, **detail),
            "evaluation_due_applicable":True, "whole_rule_postcondition_proven":False}
    return result


def capture_owner_budget_context(db, *, account_id, actor_user_id, account_owner_user_id, is_platform_owner,
                                 authorization, requested_budget):
    from app.services import control_rule_runtime as runtime
    from app.models.control_plane import ControlExecution, ControlIncident
    account = str(account_id or "").strip()
    if not account or account == "*" or type(actor_user_id) is not int or actor_user_id <= 0:
        raise ValueError("OWNER_BUDGET_SCOPE_NOT_PROVEN")
    actual_owner = db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a"), {"a":account}).scalar()
    if actual_owner is None or int(actual_owner) != int(account_owner_user_id or 0):
        raise ValueError("OWNER_BUDGET_ACCOUNT_OWNER_MISMATCH")
    when = _when((authorization or {}).get("authorized_at"))
    if when is None or when > datetime.now(timezone.utc) or _money(requested_budget) is None:
        raise ValueError("OWNER_BUDGET_EVENT_NOT_PROVEN")
    facts = {"account_id":account, "actor_user_id":actor_user_id, "account_owner_user_id":int(actual_owner),
             "is_platform_owner":is_platform_owner is True, "authorization":dict(authorization),
             "authorization_time":authorization["authorized_at"], "requested_budget":requested_budget}
    identity = _hash([account, actor_user_id, authorization])
    db.execute(text("SELECT pg_advisory_xact_lock(51012,hashtext(:k))"), {"k":identity})
    prior = db.query(ControlExecution).filter(ControlExecution.account_id==account,
        ControlExecution.module=="marketing", ControlExecution.plan_json["event_source"].astext==EVENT_SOURCE,
        ControlExecution.plan_json["event_id"].astext==identity).order_by(ControlExecution.created_at).first()
    if prior:
        context = (prior.plan_json or {}).get(CONTEXT_KEY)
        if not isinstance(context,dict) or context.get("input_facts")!=facts:
            raise ValueError("OWNER_BUDGET_CAPTURE_REPLAY_CONFLICT")
        return context
    # A failed first capture is itself a frozen event. Publishing a rule later
    # must not manufacture a prewrite version for that historical action.
    old_gap = db.query(ControlIncident).filter(ControlIncident.account_id==account,
        ControlIncident.incident_type=="RULE_EVIDENCE_GAP",
        ControlIncident.details_json["context"]["event_source"].astext==EVENT_SOURCE,
        ControlIncident.details_json["context"]["event_id"].astext==identity).order_by(ControlIncident.opened_at).first()
    if old_gap:
        context = (old_gap.details_json or {}).get("context")
        if not isinstance(context,dict) or (context.get("input_facts") is not None and context.get("input_facts")!=facts):
            raise ValueError("OWNER_BUDGET_GAP_REPLAY_CONFLICT")
        return context
    context = runtime.capture_bindings(db, account_id=account, module="marketing", rule_keys=KEYS,
        entrypoints={ENTRYPOINT}, event_source=EVENT_SOURCE, event_id=identity, observed_at=when)
    context.update(producer_entrypoint=ENTRYPOINT, input_facts=facts)
    for binding in context["bindings"]:
        ex = runtime._execution(db, context, binding)
        ex.plan_json = dict(ex.plan_json, **{CONTEXT_KEY:context})
        trigger = {"event_id":identity, "producer_entrypoint":ENTRYPOINT, "input_facts_sha256":_hash(facts)}
        runtime._evidence_once(db,ex,level="L1",evidence_type="domain_event",fact_id=identity,payload=trigger,passed=True)
        runtime._move(db,ex,"blocked",dict(trigger,evaluation_status="PENDING",predicate_result=None,
            evaluation_due_applicable=True,whole_rule_postcondition_proven=False),"OWNER_BUDGET_WRITE_RESULT_PENDING")
    if context.get("errors"):
        _gap(db,context,"OWNER_BUDGET_BINDING_NOT_PROVEN")
    return context


def _gap(db, context, reason, facts=None):
    from app.services.control_plane import open_incident
    from app.models.control_plane import ControlIncident
    account = str((context or {}).get("account_id") or "")
    key = _hash([account,(context or {}).get("event_id"),reason])
    db.execute(text("SELECT pg_advisory_xact_lock(51013,hashtext(:k))"),{"k":key})
    prior = db.query(ControlIncident).filter(ControlIncident.account_id==account,
        ControlIncident.incident_type=="RULE_EVIDENCE_GAP",ControlIncident.state=="open",
        ControlIncident.details_json["gap_key"].astext==key).first()
    if prior is not None:
        return prior
    return open_incident(db,incident_type="RULE_EVIDENCE_GAP",module="marketing",account_id=account,
        symptom="Owner budget write has incomplete versioned rule evidence",priority="P2",
        details={"gap_key":key,"reason":reason,"context":context,"facts":facts,"business_action_replay_allowed":False})



def _safe_gap(db, context, reason, facts=None):
    # Even the diagnostic sink is observational. Its failure must not roll back
    # the existing owner-authorized business transaction.
    from app.services.control_rule_runtime import _observe_rule_failure
    try:
        with db.begin_nested():
            return _gap(db, context, reason, facts)
    except Exception as exc:
        _observe_rule_failure(exc)
        return None


def safe_capture_owner_budget_context(db, **kwargs):
    from app.services.control_rule_runtime import _observe_rule_failure
    try:
        with db.begin_nested():
            return capture_owner_budget_context(db,**kwargs)
    except Exception as exc:
        _observe_rule_failure(exc)
        context = {"account_id":str(kwargs.get("account_id") or ""),"event_source":EVENT_SOURCE,
            "event_id":_hash([kwargs.get("account_id"),kwargs.get("actor_user_id"),kwargs.get("authorization")]),
            "producer_entrypoint":ENTRYPOINT,"capture_failed":True,"bindings":[]}
        _safe_gap(db,context,type(exc).__name__)
        return context


def record_owner_budget_result(db, context, *, stored_settings, observed_at):
    from app.services import control_rule_runtime as runtime
    if not isinstance(context,dict) or context.get("capture_failed") or context.get("event_source")!=EVENT_SOURCE:
        raise ValueError("OWNER_BUDGET_PREWRITE_VERSION_NOT_PROVEN")
    when = _when(observed_at)
    origin = _when(context.get("observed_at"))
    if when is None or origin is None or when < origin or when > datetime.now(timezone.utc):
        raise ValueError("OWNER_BUDGET_READBACK_TIME_NOT_PROVEN")
    facts = dict(context["input_facts"],stored_budget=stored_settings.get("daily_budget_limit_rub"),
        authorization=stored_settings.get("daily_budget_authorization"),storage_readback=True)
    decisions = evaluate_owner_budget_facts(facts)
    bindings = {b["requirement_key"]:b for b in context.get("bindings",[])}
    if set(bindings)!=set(KEYS):
        raise ValueError("OWNER_BUDGET_RULE_BINDING_NOT_PROVEN")
    result = []
    for key,decision in decisions.items():
        ex = runtime._execution(db,context,bindings[key])
        payload = dict(decision,source="storage.kpi_settings",input_facts_sha256=_hash(facts),
            authorization_time=context["input_facts"]["authorization_time"],
            source_observed_at=when.isoformat(), ledger_recorded_at_is_not_source_time=True)
        evidence = runtime._evidence_once(db,ex,level="L3",evidence_type="owner_budget_rule_assessment",
            fact_id=context["event_id"],payload=payload,passed=decision["predicate_result"] is True)
        evidence.observed_at = when
        runtime._move(db,ex,"blocked",dict(payload,postcondition_status="LOCAL_PREDICATE_VERIFIED"
            if decision["predicate_result"] is True else "NOT_PROVEN"),decision["reason"])
        if decision["evaluation_status"] == "EVALUATED":
            runtime._resolve_missing(db,ex)
        result.append({"rule_key":key,"execution_id":ex.id,"predicate_result":decision["predicate_result"]})
    return {"state":"RECORDED","rules":result,"business_actions":0,"provider_calls":0}


def observe_owner_budget_result(db, context, *, stored_settings, observed_at):
    from app.services.control_rule_runtime import _observe_rule_failure
    try:
        with db.begin_nested():
            return record_owner_budget_result(db,context,stored_settings=stored_settings,observed_at=observed_at)
    except Exception as exc:
        _observe_rule_failure(exc)
        incident = _safe_gap(db,context or {},type(exc).__name__,{"stored_settings":stored_settings,"observed_at":observed_at})
        return {"state":"NOT_PROVEN","incident_id":getattr(incident,"id",None),
                "diagnostic_stored":incident is not None,"business_actions":0,"provider_calls":0}


def reconcile_owner_budget_rule_gaps(db, *, now, limit):
    """Existing rule tick can replay stored evidence; never a KPI write."""
    from datetime import timedelta
    from app.models.control_plane import ControlIncident
    from app.services.control_rule_runtime import _observe_rule_failure
    gaps=db.query(ControlIncident).filter(ControlIncident.module=="marketing",
        ControlIncident.incident_type=="RULE_EVIDENCE_GAP",ControlIncident.state=="open",
        ControlIncident.details_json["context"]["event_source"].astext==EVENT_SOURCE,
        ControlIncident.details_json["facts"]["observed_at"].astext.isnot(None),
        ControlIncident.updated_at < now-timedelta(seconds=300)).order_by(ControlIncident.updated_at).limit(max(1,min(int(limit),25))).all()
    result={"checked":0,"recovered":0,"errors":[],"business_actions":0,"provider_calls":0}
    for gap in gaps:
        result["checked"]+=1
        try:
            with db.begin_nested():
                details=gap.details_json or {}
                context=details.get("context") or {}
                facts=details.get("facts") or {}
                if context.get("account_id")!=gap.account_id:
                    raise ValueError("OWNER_BUDGET_RECOVERY_TENANT_MISMATCH")
                record_owner_budget_result(db,context,stored_settings=facts["stored_settings"],observed_at=facts["observed_at"])
                gap.state="resolved";gap.resolved_at=now;gap.updated_at=now
            result["recovered"]+=1
        except Exception as exc:
            gap.updated_at=now
            _observe_rule_failure(exc)
            result["errors"].append({"incident_id":gap.id,"error":type(exc).__name__})
    return result
