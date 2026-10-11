"""Event evidence adapters for the existing Control Plane.

Domain owners still make every business decision and external request. This
module only freezes the published rule identity, reads their durable facts and
materializes executions/evidence/incidents. The existing Guardian calls the
bounded reconciliation entry point; there is no scheduler or action executor.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.control_plane import (
    ControlActionOwner, ControlEvidence, ControlExecution, ControlIncident,
    ControlModuleContract, ControlRequirement, ControlRequirementVersion,
)
from app.services.control_plane import add_evidence, open_incident, start_execution, transition_execution
from app.services.control_message_rule_facts import KEYS as MESSAGE_FACT_KEYS, evaluate_message_rule_facts

CONTRACT = "control_rule_event_v1"
MOP_ENTRYPOINT = "app.api.messenger:send_message"
CPX_ENTRYPOINT = "app.api.cpx_advisor:apply_one"
MOP_RULE_KEYS = (
    "rule.messages.first_response", "rule.messages.reply_dedupe",
    "rule.messages.send_ownership", "rule.messages.human_takeover",
    "rule.messages.stale_inbound", "rule.messages.provider_wait",
    "rule.messages.entitlement_402", "rule.messages.technical_handoff",
    "rule.messages.crm_handoff",
)
MOP_LEDGER_EVENTS = frozenset({"draft_created", "ai_generated", "generation_failed", "provider_deferred",
    "send_started", "sent", "send_recovered", "send_failed", "handed_to_human", "closed_no_reply",
    "answered_externally", "returned_to_ai"})



def _observe_rule_failure(exc):
    """An unavailable diagnostics sink must not become a business failure."""
    try:
        from app.services.exception_observability import observe_suppressed
        observe_suppressed(__name__,exc,line=0)
        return True
    except Exception as sink_exc:
        try:
            import logging
            logging.getLogger(__name__).error('Rule evidence failure %s; diagnostic sink unavailable: %s',
                                              type(exc).__name__,type(sink_exc).__name__)
            return True
        except Exception:
            # The caller exposes an unpersisted GAP, never a success receipt.
            return False


def _save_rule_gap_safely(db, writer, *args, **kwargs):
    try:
        with db.begin_nested():
            return writer(db,*args,**kwargs)
    except Exception as gap_exc:
        _observe_rule_failure(gap_exc)
        return None


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _body_hash(value):
    # Identical to mop_core.text_hash without importing its sender/callbacks.
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = "\n".join(line.strip() for line in value.split("\n")).strip()
    return hashlib.sha256(value.encode()).hexdigest()


def capture_bindings(db, *, account_id, module, rule_keys, entrypoints, event_source, event_id, observed_at):
    """Read published identities at the actual domain decision, never on replay.

    Only explicit keys belonging to this adapter are considered. A catalog with
    116 global rules is never expanded into 116 obligations for one account.
    Module verification TTL is the due for evidence materialization, not a
    promise about response time or the customer's business result.
    """
    account_id = str(account_id or "").strip()
    when = _utc(observed_at)
    if not account_id or account_id == "*" or not event_source or not str(event_id or "") or when is None:
        raise ValueError("RULE_EVENT_IDENTITY_REQUIRED")
    contract = db.query(ControlModuleContract).filter(
        ControlModuleContract.module == module, ControlModuleContract.enabled.is_(True),
    ).first()
    ttl = int(contract.verification_stale_seconds or 0) if contract else 0
    result = {"contract": CONTRACT, "account_id": account_id, "module": module,
              "event_source": str(event_source), "event_id": str(event_id),
              "observed_at": when.isoformat(), "bindings": [], "errors": []}
    for key in sorted(set(rule_keys)):
        # Shared row locks freeze each version while this domain transaction is
        # committed. They do not serialize unrelated accounts with each other.
        req = db.query(ControlRequirement).filter(ControlRequirement.key == key).populate_existing().with_for_update(read=True).first()
        code = None
        if req is None or req.status != "active":
            code = "RULE_BINDING_MISSING"
        elif req.module != module or req.requirement_type != "business_rule":
            code = "RULE_BINDING_SCOPE_MISMATCH"
        elif not (req.scope_type == "account" and req.scope_id == account_id or
                  req.scope_type == "global" and req.scope_id == "*" and
                  (req.source_json or {}).get("tenant_scope") == "account_required"):
            code = "RULE_BINDING_SCOPE_MISMATCH"
        source = req.source_json if req is not None and isinstance(req.source_json, dict) else {}
        operation = str(source.get("operation") or "")
        owner = db.query(ControlActionOwner).filter(
            ControlActionOwner.action_key == operation, ControlActionOwner.enabled.is_(True),
        ).first() if operation else None
        if code is None and (not owner or owner.canonical_entrypoint not in entrypoints):
            code = "RULE_ACTION_OWNER_MISMATCH"
        if code is None and (not source.get("rule_id") or source.get("source_of_truth") != "control_plane"):
            code = "RULE_BINDING_MISSING"
        if code is None and ttl <= 0:
            code = "RULE_TRIGGER_CONTRACT_MISSING"
        if code:
            result["errors"].append({"key": key, "code": code})
            continue
        result["bindings"].append({
            "requirement_id": str(req.id), "requirement_version": int(req.version),
            "requirement_key": key, "rule_id": source["rule_id"], "module": module,
            "operation": operation, "owner": owner.owner_service,
            "action_owner_version": int(owner.version), "entrypoint": owner.canonical_entrypoint,
            "verification_due_at": (when + timedelta(seconds=ttl)).isoformat(),
            "due_source": "control_module_contracts.verification_stale_seconds",
            "verification_stale_seconds": ttl,
        })
    return result


def capture_mop_context(db, draft_id, event):
    draft = db.execute(text("SELECT account_id,avito_message_id,created_at FROM mop_drafts WHERE id=:id"), {"id": int(draft_id)}).mappings().one()
    if event != "draft_created":
        row = db.execute(text("""SELECT metadata FROM mop_draft_events
          WHERE draft_id=:id AND event='draft_created' ORDER BY id LIMIT 1"""), {"id": int(draft_id)}).scalar()
        context = (row or {}).get("control_rule_runtime") if isinstance(row, dict) else None
        # A late read cannot assign today's rule version to a legacy decision.
        if not isinstance(context, dict) or context.get("contract") != CONTRACT:
            return {"contract": CONTRACT, "account_id": str(draft["account_id"]),
                    "event_source": "mop_inbound", "event_id": str(draft["avito_message_id"]),
                    "bindings": [], "errors": [{"code": "UNVERSIONED_DOMAIN_EVENT"}]}
        return context
    return capture_bindings(
        db, account_id=draft["account_id"], module="messages", rule_keys=(*MOP_RULE_KEYS, *MESSAGE_FACT_KEYS),
        entrypoints={MOP_ENTRYPOINT, "app.crm.bridge:sync_dialog_for_account"},
        event_source="mop_inbound", event_id=draft["avito_message_id"], observed_at=draft["created_at"],
    )


def _validate_binding(db, context, binding):
    account = str(context.get("account_id") or "")
    req = db.query(ControlRequirement).filter(
        ControlRequirement.id == binding.get("requirement_id"),
    ).populate_existing().with_for_update(read=True).first()
    if req is None:
        raise ValueError("RULE_BINDING_MISSING")
    version = binding.get("requirement_version")
    if type(version) is not int or version < 1:
        raise ValueError("RULE_VERSION_INVALID")
    snapshot = None
    if int(req.version) == version:
        snapshot = {"key": req.key, "module": req.module, "scope_type": req.scope_type,
                    "scope_id": req.scope_id, "source_json": req.source_json}
    else:
        history = db.query(ControlRequirementVersion).filter(
            ControlRequirementVersion.requirement_id == req.id,
            ControlRequirementVersion.version == version,
        ).first()
        snapshot = history.snapshot_json if history else None
    if not isinstance(snapshot, dict) or snapshot.get("key") != binding.get("requirement_key") or snapshot.get("module") != binding.get("module"):
        raise ValueError("RULE_VERSION_NOT_PROVEN")
    source = snapshot.get("source_json") if isinstance(snapshot.get("source_json"), dict) else {}
    scope_ok = (snapshot.get("scope_type") == "account" and snapshot.get("scope_id") == account or
                snapshot.get("scope_type") == "global" and snapshot.get("scope_id") == "*" and source.get("tenant_scope") == "account_required")
    if not account or not scope_ok or source.get("rule_id") != binding.get("rule_id") or source.get("operation") != binding.get("operation"):
        raise ValueError("RULE_BINDING_SCOPE_MISMATCH")
    if binding.get("module") != context.get("module"):
        raise ValueError("RULE_BINDING_SCOPE_MISMATCH")
    owner = db.query(ControlActionOwner).filter(ControlActionOwner.action_key == binding["operation"],
        ControlActionOwner.enabled.is_(True)).first()
    if not owner or owner.version != binding.get("action_owner_version") or owner.canonical_entrypoint != binding.get("entrypoint") or owner.owner_service != binding.get("owner"):
        raise ValueError("RULE_ACTION_OWNER_VERSION_NOT_PROVEN")
    when, due = _utc(context.get("observed_at")), _utc(binding.get("verification_due_at"))
    ttl = binding.get("verification_stale_seconds")
    if when is None or due is None or type(ttl) is not int or ttl <= 0 or due != when + timedelta(seconds=ttl):
        raise ValueError("RULE_TRIGGER_CONTRACT_MISSING")
    return req


def _execution(db, context, binding):
    req = _validate_binding(db, context, binding)
    identity = {"requirement_id": str(req.id), "requirement_version": binding["requirement_version"],
                "account_id": context["account_id"], "module": binding["module"],
                "event_source": context["event_source"], "event_id": context["event_id"]}
    idem = "rule-event:" + _hash(identity)
    plan = dict(identity, kind=CONTRACT, rule_id=binding["rule_id"],
                requirement_key=binding["requirement_key"], binding=binding,
                observed_at=_utc(context.get("observed_at")).isoformat(), verification_due_at=binding["verification_due_at"])
    ex = start_execution(db, req, account_id=context["account_id"], plan=plan, idempotency_key=idem)
    # Every evidence write locks the same execution, across all callers and
    # separate transactions. Unique execution identity handles the insert race.
    ex = db.query(ControlExecution).filter(ControlExecution.id == ex.id).populate_existing().with_for_update().one()
    if any((ex.plan_json or {}).get(k) != v for k, v in identity.items()) or ex.account_id != context["account_id"]:
        raise ValueError("RULE_EVENT_IDENTITY_CONFLICT")
    return ex


def _evidence_once(db, ex, *, level, evidence_type, fact_id, payload, passed):
    payload = _json(dict(payload, contract=CONTRACT, fact_id=str(fact_id),
                         account_id=ex.account_id, requirement_version=ex.plan_json["requirement_version"]))
    rows = db.query(ControlEvidence).filter(
        ControlEvidence.execution_id == ex.id, ControlEvidence.evidence_type == evidence_type,
        ControlEvidence.level == level,
        ControlEvidence.payload_json["fact_id"].astext == str(fact_id),
    ).all()
    if rows:
        if len(rows) != 1 or rows[0].payload_json != payload or rows[0].passed is not bool(passed):
            raise ValueError("RULE_EVENT_REPLAY_CONFLICT")
        return rows[0]
    return add_evidence(db, ex, level=level, evidence_type=evidence_type, source=CONTRACT, passed=passed, payload=payload)


def _move(db, ex, status, actual, reason):
    # Reconciliation never creates attempts or calls a domain action. Existing
    # terminal waits may acquire later evidence without leaving phantom work.
    old = str(ex.status)
    if old == status:
        actual = _json(actual)
        if ex.actual_state_json != actual:
            ex.actual_state_json = actual
            ex.updated_at = datetime.now(timezone.utc)
        return
    if old == "pass":
        transition_execution(db, ex, "degraded", actual_state=_json(actual), reason=reason, touch_requirement=False)
        old = "degraded"
    if old in {"blocked", "failed", "waiting_owner", "waiting_external", "degraded"}:
        transition_execution(db, ex, "preflight", touch_requirement=False)
        old = "preflight"
    for next_state in ("parsed", "mapped_to_requirements", "planned", "preflight"):
        chain = {"received": "parsed", "parsed": "mapped_to_requirements", "mapped_to_requirements": "planned", "planned": "preflight"}
        if chain.get(str(ex.status)) == next_state:
            transition_execution(db, ex, next_state, touch_requirement=False)
    if status in {"waiting_owner", "waiting_external", "blocked", "failed"}:
        transition_execution(db, ex, status, actual_state=_json(actual), reason=reason, error_code=reason, touch_requirement=False)
        return
    for next_state in ("executing", "technical_tested", "runtime_verified"):
        transition_execution(db, ex, next_state, touch_requirement=False)
    transition_execution(db, ex, status, actual_state=_json(actual), reason=reason, error_code=reason, touch_requirement=False)


def _resolve_missing(db, ex):
    now = datetime.now(timezone.utc)
    for row in db.query(ControlIncident).filter(
        ControlIncident.execution_id == ex.id, ControlIncident.account_id == ex.account_id,
        ControlIncident.incident_type == "RULE_NOT_EVALUATED", ControlIncident.state == "open",
    ).all():
        row.state = "resolved"
        row.resolved_at = now
        row.updated_at = now
        row.remediation = "Exact versioned domain decision recovered; no business action replayed"


def _mop_event(db, event_id):
    return db.execute(text("""SELECT e.id AS event_row_id,e.event,e.to_status,e.from_status,e.metadata,e.at,
      e.actor_id,e.actor_type,e.channel,e.payload,d.id AS draft_id,d.account_id,d.avito_chat_id,d.avito_message_id,
      d.status AS current_status,d.outgoing_text_hash,d.sent_at,d.reply_text,d.incoming_text
      FROM mop_draft_events e JOIN mop_drafts d ON d.id=e.draft_id WHERE e.id=:id"""), {"id": int(event_id)}).mappings().one()


def record_mop_gap(db, event_id, reason, *, rule_key=None):
    """A missing adapter/binding is durable diagnostic data, never a fake eval."""
    row = _mop_event(db, event_id)
    key = _hash([row["account_id"], "mop_draft_events", int(event_id), rule_key or "", reason])
    db.execute(text("SELECT pg_advisory_xact_lock(51007,hashtext(:key))"), {"key": key})
    incident = db.query(ControlIncident).filter(
        ControlIncident.incident_type == "RULE_EVIDENCE_GAP", ControlIncident.account_id == row["account_id"],
        ControlIncident.details_json["gap_key"].astext == key, ControlIncident.state == "open",
    ).first()
    details = {"gap_key": key, "event_source": "mop_draft_events", "event_id": int(event_id),
               "inbound_id": row["avito_message_id"], "rule_key": rule_key, "reason": str(reason),
               "source_observed_at": _utc(row["at"]).isoformat(), "business_action_replay_allowed": False}
    if incident is None:
        incident = open_incident(db, incident_type="RULE_EVIDENCE_GAP", symptom="Domain event exists but its rule evidence is not proven",
                                 account_id=row["account_id"], module="messages", priority="P2", details=details)
    incident.updated_at = datetime.now(timezone.utc)
    return incident


def _mop_binding_keys(row):
    event = str(row["event"])
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    keys = {"rule.messages.first_response"}
    if event in {"send_started", "sent", "send_recovered", "answered_externally"}:
        keys.add("rule.messages.reply_dedupe")
    if meta.get("canonical_sender") == MOP_ENTRYPOINT:
        keys.add("rule.messages.send_ownership")
    if event in {"handed_to_human", "answered_externally", "returned_to_ai"} and "HUMAN" in str(meta.get("policy_version") or ""):
        keys.add("rule.messages.human_takeover")
    if event == "closed_no_reply" and (str(row.get("actor_id") or "") == "incoming_superseded_guard" or "superseded" in str(row.get("payload") or "").lower()):
        keys.add("rule.messages.stale_inbound")
    if event == "provider_deferred":
        keys.update({"rule.messages.provider_wait", "rule.messages.technical_handoff"})
    if event == "send_failed" and re.search(r"\b402\b", str(row.get("payload") or "")):
        keys.update({"rule.messages.provider_wait", "rule.messages.entitlement_402"})
    if event == "handed_to_human":
        keys.add("rule.messages.crm_handoff")
    return keys


def _mop_delivery_readback(db, row, provider_readback=None):
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    receipt = meta.get("provider_receipt")
    if not isinstance(receipt, dict) or receipt.get("account_id") != row["account_id"] or receipt.get("chat_id") != row["avito_chat_id"]:
        return None
    mid, wanted = str(receipt.get("message_id") or ""), str(receipt.get("text_hash") or "")
    if not mid or not wanted or wanted != row["outgoing_text_hash"]:
        return None
    existing = db.query(ControlEvidence).filter(
        ControlEvidence.evidence_type == "message_delivery_readback", ControlEvidence.source == CONTRACT,
        ControlEvidence.payload_json["event_row_id"].astext == str(row["event_row_id"]),
        ControlEvidence.payload_json["account_id"].astext == row["account_id"],
        ControlEvidence.passed.is_(True),
    ).first()
    proof = existing.payload_json if existing else provider_readback
    if not isinstance(proof, dict):
        return None
    at = _utc(proof.get("observed_at"))
    if (proof.get("readback_source") != "app.api.messenger:fetch_chat_messages" or proof.get("method") != "GET"
            or proof.get("account_id") != row["account_id"] or proof.get("chat_id") != row["avito_chat_id"]
            or proof.get("message_id") != mid or proof.get("text_hash") != wanted
            or at is None or at < _utc(row["at"]) or at > datetime.now(timezone.utc)):
        return None
    return {k: proof[k] for k in ("readback_source", "method", "account_id", "chat_id", "message_id", "text_hash", "observed_at", "provider")}


def record_messenger_get_readback(db, account_id, chat_id, response, *, observed_at=None):
    """Called only after the existing canonical provider GET returned HTTP 200.

    Local cache/POST echoes never enter this function. Positive existence does
    not require a complete paginated history; absence never proves delivery or
    NO_EVENT. The first exact independent proof is immutable on repeated GETs.
    """
    at = _utc(observed_at) if observed_at is not None else datetime.now(timezone.utc)
    if at is None or at > datetime.now(timezone.utc):
        raise ValueError("RULE_PROVIDER_OBSERVED_AT_INVALID")
    messages = response.get("messages", []) if isinstance(response, dict) else response
    if not isinstance(messages, list):
        return {"checked": 0, "verified": 0, "reason": "PROVIDER_MESSAGE_SHAPE_UNKNOWN"}
    by_id = {}
    for message in messages[:100]:
        if not isinstance(message, dict) or not message.get("id") or not str(message.get("direction") or "").lower().startswith("out"):
            continue
        content = message.get("content")
        body = content.get("text") if isinstance(content, dict) else message.get("text")
        if isinstance(body, str):
            by_id[str(message["id"])] = _body_hash(body)
    if not by_id:
        return {"checked": 0, "verified": 0}
    rows = db.execute(text("""SELECT e.id FROM mop_draft_events e JOIN mop_drafts d ON d.id=e.draft_id
      WHERE d.account_id=:a AND d.avito_chat_id=:c AND e.event IN ('sent','send_recovered')
        AND e.metadata->'control_rule_runtime'->>'contract'=:contract
        AND e.metadata->'provider_receipt'->>'message_id'=ANY(:ids)
      ORDER BY e.at DESC,e.id DESC LIMIT 100"""),
      {"a": str(account_id), "c": str(chat_id), "contract": CONTRACT, "ids": list(by_id)}).scalars().all()
    out = {"checked": len(rows), "verified": 0}
    for event_id in rows:
        row = _mop_event(db, event_id)
        receipt = (row["metadata"] or {}).get("provider_receipt") or {}
        mid = str(receipt.get("message_id") or "")
        if at < _utc(row["at"]) or by_id.get(mid) != receipt.get("text_hash"):
            continue
        proof = {"readback_source": "app.api.messenger:fetch_chat_messages", "method": "GET",
                 "provider": "avito", "account_id": str(account_id), "chat_id": str(chat_id),
                 "message_id": mid, "text_hash": by_id[mid], "observed_at": at.isoformat()}
        result = record_mop_event(db, event_id, provider_readback=proof)
        out["verified"] += int(any(x.get("delivery_proven") is True for x in result.get("executions", [])))
    return out


def _mop_outcome(row):
    event = row["event"]
    if event in {"draft_created", "send_started", "returned_to_ai"}:
        return "pending", "blocked", "RULE_PENDING_EVALUATION"
    if event == "handed_to_human" or row["to_status"] == "human_required":
        return "waiting_owner", "waiting_owner", "MOP_HANDOFF_RECORDED"
    if event in {"closed_no_reply", "answered_externally"}:
        return "no_action", "blocked", "MOP_NO_REPLY_REQUIRED"
    if event == "provider_deferred" or row["to_status"] == "waiting_external":
        return "waiting_external", "waiting_external", "MOP_PROVIDER_WAIT"
    if event == "send_failed":
        return "waiting_external", "waiting_external", "MOP_SEND_NOT_CONFIRMED"
    if event in {"sent", "send_recovered"}:
        return "sent_unverified", "waiting_external", "MOP_DELIVERY_READBACK_PENDING"
    return "evaluated", "blocked", "MOP_RESULT_PENDING"


def _crm_handoff_readback(db, row):
    """Read canonical CRM entities and authorization, without creating work.

    Entity existence proves only the handoff's persisted link. It never proves
    that the manager has answered, that a task is complete, or an invented due.
    """
    from fastapi import HTTPException
    from app.crm.bridge import _assert_account_access
    records = db.execute(text("""SELECT c.id AS conversation_id,c.owner_user_id,c.deal_id,
      m.id AS message_id,m.body,d.responsible_user_id,d.next_action_at
      FROM accounts a JOIN boris_crm_conversations c
        ON c.account_id=a.account_id AND c.owner_user_id=a.owner_user_id
      JOIN boris_crm_messages m ON m.conversation_id=c.id AND m.owner_user_id=c.owner_user_id
      JOIN boris_crm_deals d ON d.id=c.deal_id AND d.owner_user_id=c.owner_user_id
      WHERE a.account_id=:a AND c.channel='avito' AND c.external_chat_id=:chat
        AND m.external_message_id=:incoming AND m.direction='in'
      ORDER BY c.id,m.id LIMIT 2"""), {"a": row["account_id"], "chat": row["avito_chat_id"],
                                         "incoming": row["avito_message_id"]}).mappings().all()
    if len(records) != 1:
        return None, "CRM_LINK_MISSING_OR_AMBIGUOUS"
    linked = records[0]
    if _body_hash(linked["body"]) != _body_hash(row["incoming_text"]) or not linked["responsible_user_id"]:
        return None, "CRM_INBOUND_OR_MANAGER_NOT_PROVEN"
    try:
        _assert_account_access(db, linked["responsible_user_id"], row["account_id"])
    except HTTPException as exc:
        return None, "CRM_MANAGER_ACCESS_NOT_PROVEN_" + str(exc.status_code)
    # A deal-level task is context, not proof that this particular inbound
    # created it. Expose its real deadline and association explicitly.
    task = db.execute(text("""SELECT id,assigned_user_id,due_at,status,source FROM boris_crm_tasks
      WHERE deal_id=:d AND owner_user_id=:o AND status='open'
      ORDER BY due_at NULLS LAST,id LIMIT 1"""), {"d": linked["deal_id"], "o": linked["owner_user_id"]}).mappings().first()
    proof = {"readback_source": "canonical_crm_tables", "account_id": row["account_id"],
             "chat_id": row["avito_chat_id"], "inbound_id": row["avito_message_id"],
             "inbound_text_hash": _body_hash(row["incoming_text"]),
             "conversation_id": linked["conversation_id"], "message_id": linked["message_id"],
             "deal_id": linked["deal_id"], "owner_user_id": linked["owner_user_id"],
             "responsible_user_id": linked["responsible_user_id"], "manager_access_verified": True,
             "next_action_at": _utc(linked["next_action_at"]).isoformat() if linked["next_action_at"] else None,
             "task": _json(dict(task)) if task else None,
             "task_event_link_proven": False, "manager_action_completed": False}
    return proof, "CRM_HANDOFF_LINK_VERIFIED"


def record_mop_event(db, event_id, *, provider_readback=None):
    """Materialize one actual local event; caller owns commit and savepoint."""
    row = _mop_event(db, event_id)
    if row["event"] not in MOP_LEDGER_EVENTS:
        return {"state": "NOT_APPLICABLE", "event_id": int(event_id)}
    context = (row["metadata"] or {}).get("control_rule_runtime") if isinstance(row["metadata"], dict) else None
    if not isinstance(context, dict) or context.get("contract") != CONTRACT:
        record_mop_gap(db, event_id, "RULE_BINDING_CAPTURE_FAILED")
        return {"state": "NOT_PROVEN", "reason": "UNVERSIONED_DOMAIN_EVENT", "event_id": int(event_id)}
    if context.get("account_id") != row["account_id"] or context.get("event_id") != row["avito_message_id"] or context.get("event_source") != "mop_inbound":
        raise ValueError("RULE_DOMAIN_TENANT_MISMATCH")
    predicate_decisions = evaluate_message_rule_facts(db, row)
    keys = _mop_binding_keys(row) | set(predicate_decisions)
    bindings = [b for b in context.get("bindings", []) if b.get("requirement_key") in keys]
    for error in context.get("errors", []):
        if error.get("key") in keys or not error.get("key"):
            record_mop_gap(db, event_id, error.get("code") or "RULE_BINDING_MISSING", rule_key=error.get("key"))
    captured = {b["requirement_key"] for b in bindings}
    for key in keys - captured:
        if not any(e.get("key") == key for e in context.get("errors", [])):
            record_mop_gap(db, event_id, "RULE_BINDING_MISSING", rule_key=key)
    outcome, status, reason = _mop_outcome(row)
    readback = _mop_delivery_readback(db, row, provider_readback) if row["event"] in {"sent", "send_recovered"} else None
    results = []
    for binding in bindings:
        ex = _execution(db, context, binding)
        predicate = predicate_decisions.get(binding["requirement_key"])
        if predicate is not None:
            payload = dict(predicate, event_source="mop_draft_events", event_row_id=int(event_id),
                           requirement_version=binding["requirement_version"])
            _evidence_once(db, ex, level="L1", evidence_type="domain_event", fact_id=f"mop:{event_id}", payload=payload, passed=True)
            evaluated = predicate.get("evaluation_status") == "EVALUATED"
            payload['evidence_claim']='local_predicate_result'
            _evidence_once(db, ex, level="L3", evidence_type="rule_evaluation", fact_id=f"mop:{event_id}", payload=payload, passed=evaluated and predicate.get('predicate_result') is True)
            if evaluated:
                _resolve_missing(db, ex)
            current = dict(payload, postcondition_status="NOT_PROVEN")
            if int(event_id) >= int((ex.actual_state_json or {}).get("event_row_id") or 0):
                _move(db, ex, "blocked", current, predicate["reason"])
            results.append({"execution_id": ex.id,"rule_key":binding["requirement_key"],"status":ex.status,"delivery_proven":False})
            continue
        payload = {"event_source": "mop_draft_events", "event_row_id": int(event_id), "draft_id": row["draft_id"],
                   "inbound_id": row["avito_message_id"], "chat_id": row["avito_chat_id"],
                   "event": row["event"], "outcome": outcome, "reason": reason,
                   "policy_version": (row["metadata"] or {}).get("policy_version"),
                   "evidence_claim": "canonical_decision_observed_not_whole_rule_success",
                   "source_observed_at": _utc(row["at"]).isoformat(), "delivery_proven": False}
        _evidence_once(db, ex, level="L1", evidence_type="domain_event", fact_id=f"mop:{event_id}", payload=payload, passed=True)
        if outcome != "pending":
            _evidence_once(db, ex, level="L3", evidence_type="rule_evaluation", fact_id=f"mop:{event_id}", payload=payload, passed=True)
            _resolve_missing(db, ex)
        current = dict(payload, evaluation_due_applicable=True, evaluation_status="PENDING" if outcome == "pending" else "EVALUATED",
                       postcondition_status="NOT_PROVEN", requirement_version=binding["requirement_version"])
        previous = ex.actual_state_json or {}
        if previous.get("delivery_proven") is True and isinstance(previous.get("readback"), dict):
            current.update(delivery_proven=True, readback=previous["readback"])
        next_status, next_reason = status, reason
        if binding["requirement_key"] == "rule.messages.crm_handoff" and row["event"] == "handed_to_human":
            crm, crm_reason = _crm_handoff_readback(db, row)
            current["crm_checked_at"] = datetime.now(timezone.utc).isoformat()
            current["crm_reason"] = crm_reason
            if crm is not None:
                fact_id = "crm_link:" + _hash(crm)
                prior = db.query(ControlEvidence).filter(ControlEvidence.execution_id == ex.id,
                    ControlEvidence.evidence_type == "crm_handoff_readback",
                    ControlEvidence.payload_json["fact_id"].astext == fact_id).first()
                crm["observed_at"] = prior.payload_json["observed_at"] if prior else datetime.now(timezone.utc).isoformat()
                _evidence_once(db, ex, level="L5", evidence_type="crm_handoff_readback", fact_id=fact_id, payload=crm, passed=True)
                current.update(postcondition_status="LINK_VERIFIED", crm_readback=crm)
            # The user still awaits a human action even with verified CRM links.
            next_status, next_reason = "waiting_owner", crm_reason
        # One delivered message proves this reply's delivery. It does not prove
        # all dialogue predicates or the absence of another provider message.
        if readback and binding["requirement_key"] == "rule.messages.first_response":
            fact = dict(readback, event_row_id=int(event_id), requirement_key=binding["requirement_key"])
            _evidence_once(db, ex, level="L5", evidence_type="message_delivery_readback", fact_id=f"provider_message:{readback['message_id']}", payload=fact, passed=True)
            current.update(delivery_proven=True, postcondition_status="DELIVERY_VERIFIED", readback=readback)
            next_status, next_reason = "waiting_external", "MOP_RULE_POSTCONDITION_NOT_PROVEN"
        # Replaying an earlier event must never replace a later handoff or send.
        previous_id = int((ex.actual_state_json or {}).get("event_row_id") or 0)
        if int(event_id) >= previous_id:
            _move(db, ex, next_status, current, next_reason)
        elif readback and binding["requirement_key"] == "rule.messages.first_response":
            # A later manager handoff remains waiting_owner when an earlier
            # acknowledgement acquires its first independent GET proof.
            enriched = dict(ex.actual_state_json or {}, delivery_proven=True, readback=readback)
            if ex.actual_state_json != enriched:
                ex.actual_state_json = enriched
                ex.updated_at = datetime.now(timezone.utc)
        results.append({"execution_id": ex.id, "rule_key": binding["requirement_key"], "status": ex.status,
                        "delivery_proven": bool((ex.actual_state_json or {}).get("delivery_proven"))})
    return {"state": "RECORDED" if results else "NOT_PROVEN", "event_id": int(event_id),
            "executions": results, "binding_errors": context.get("errors", [])}


def check_rule_due(db, *, now=None, limit=100):
    """Inspect only versioned expectations, never infer all rules from a catalog."""
    now = _utc(now) or datetime.now(timezone.utc)
    rows = db.query(ControlExecution).filter(
        ControlExecution.plan_json["kind"].astext == CONTRACT,
        ControlExecution.actual_state_json["evaluation_status"].astext.in_(["PENDING", "NOT_PROVEN"]),
        (ControlExecution.actual_state_json['evaluation_due_applicable'].astext.is_(None) |
         (ControlExecution.actual_state_json['evaluation_due_applicable'].astext != 'false')),
        ControlExecution.plan_json["verification_due_at"].astext < now.isoformat(),
        ~ControlExecution.id.in_(db.query(ControlIncident.execution_id).filter(
            ControlIncident.incident_type == "RULE_NOT_EVALUATED", ControlIncident.state == "open",
            ControlIncident.execution_id.isnot(None))),
    ).order_by(ControlExecution.plan_json["verification_due_at"].astext, ControlExecution.id).limit(max(1, min(int(limit), 500))).all()
    out = {"checked": 0, "opened": 0, "unknown_due": 0}
    for candidate in rows:
        ex = db.query(ControlExecution).filter(ControlExecution.id == candidate.id).populate_existing().with_for_update().one()
        out["checked"] += 1
        # The initial scan is not a lock. A producer can finish between that
        # scan and this row lock, including a completed negative predicate.
        # Re-read applicability/status from the locked, refreshed row.
        locked_actual = ex.actual_state_json or {}
        if locked_actual.get('evaluation_status') not in {'PENDING','NOT_PROVEN'} or locked_actual.get('evaluation_due_applicable') is False:
            continue
        due = _utc((ex.plan_json or {}).get("verification_due_at"))
        if due is None:
            out["unknown_due"] += 1
            continue
        if due >= now or db.query(ControlEvidence).filter(
            ControlEvidence.execution_id == ex.id, ControlEvidence.source == CONTRACT,
            ControlEvidence.payload_json['requirement_version'].astext == str((ex.plan_json or {}).get('requirement_version')),
            ControlEvidence.level == "L3",
            ((ControlEvidence.evidence_type == "rule_evaluation") & ControlEvidence.passed.is_(True)) |
            (ControlEvidence.payload_json['evaluation_status'].astext == 'EVALUATED'),
        ).first():
            continue
        if db.query(ControlIncident).filter(
            ControlIncident.execution_id == ex.id, ControlIncident.account_id == ex.account_id,
            ControlIncident.incident_type == "RULE_NOT_EVALUATED", ControlIncident.state == "open",
        ).first():
            continue
        open_incident(db, incident_type="RULE_NOT_EVALUATED", symptom="Eligible domain event has no versioned decision evidence by verification due",
                      module=ex.module, account_id=ex.account_id, requirement_id=ex.requirement_id, execution_id=ex.id,
                      priority="P2", details={"identity": ex.plan_json, "verification_due_at": due.isoformat(), "checked_at": now.isoformat(),
                                               "business_action_replay_allowed": False})
        out["opened"] += 1
    return out


def reconcile_rule_events(db, *, limit=100, now=None):
    """Bounded local readback on the existing Guardian tick, zero provider calls.

    Selection uses the durable per-event marker and missing evidence rather
    than a max(id) cursor: transactions can commit IDs out of order. A failed
    item is retried, while savepoints preserve other accounts' evidence.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    n = max(1, min(int(limit), 500))
    rows = db.execute(text("""SELECT e.id FROM mop_draft_events e JOIN mop_drafts d ON d.id=e.draft_id
      WHERE (e.metadata->'control_rule_runtime'->>'contract'=:contract
        OR e.metadata->>'control_rule_writer'=:contract)
        AND NOT EXISTS (SELECT 1 FROM control_incidents ci WHERE ci.incident_type='RULE_EVIDENCE_GAP'
          AND ci.account_id=d.account_id AND ci.state='open' AND ci.updated_at>:retry_cutoff
          AND ci.details_json->>'event_id'=CAST(e.id AS text))
        AND (
          NOT EXISTS (SELECT 1 FROM control_evidence ce WHERE ce.evidence_type='domain_event'
            AND ce.source=:contract AND ce.payload_json->>'event_row_id'=CAST(e.id AS text))

        ) ORDER BY e.at,e.id LIMIT :lim"""), {"contract": CONTRACT, "lim": n + 1, "retry_cutoff": now-timedelta(seconds=300)}).scalars().all()
    out = {"checked_at": now.isoformat(), "checked": 0, "recorded": 0, "errors": [],
           "source": "persisted_mop_events", "source_complete": len(rows) <= n,
           "provider_calls": 0, "business_actions": 0}
    for event_id in rows[:n]:
        out["checked"] += 1
        try:
            with db.begin_nested():
                record_mop_event(db, event_id)
            out["recorded"] += 1
        except Exception as exc:
            out["errors"].append({"event_id": int(event_id), "error": type(exc).__name__})
            incident=_save_rule_gap_safely(db,record_mop_gap,event_id,type(exc).__name__)
            out['errors'][-1]['evidence_gap_persisted']=incident is not None
    out["due"] = check_rule_due(db, now=now, limit=n)
    out["feed"] = reconcile_feed_rule_gaps(db, now=now, limit=n)
    from app.services.control_owner_budget_rule_facts import reconcile_owner_budget_rule_gaps
    out["owner_budget"] = reconcile_owner_budget_rule_gaps(db, now=now, limit=n)
    crm_gaps = db.query(ControlIncident).filter(ControlIncident.module == 'crm',
        ControlIncident.incident_type == 'RULE_EVIDENCE_GAP', ControlIncident.state == 'open',
        ControlIncident.details_json['context']['event_source'].astext.in_(['crm_avito_sync','crm_legacy_task_created','crm_legacy_task_completed']),
        ControlIncident.updated_at < now-timedelta(seconds=300)).order_by(ControlIncident.updated_at).limit(min(n,25)).all()
    out['crm_recovered'] = 0
    for gap in crm_gaps:
        try:
            with db.begin_nested():
                details=gap.details_json or {}
                if details['context'].get('account_id') != gap.account_id:
                    raise ValueError('CRM_RECOVERY_TENANT_MISMATCH')
                if details['context']['event_source']=='crm_avito_sync':
                    record_crm_sync_result(db,details['context'],details['receipt'])
                else:
                    record_legacy_crm_task_result(db,details['context'],details['receipt']['task_id'])
                gap.state='resolved'; gap.resolved_at=now; gap.updated_at=now
            out['crm_recovered'] += 1
        except Exception as exc:
            gap.updated_at=now
            out['errors'].append({'incident_id':gap.id,'error':type(exc).__name__,'adapter':'crm_sync'})
    # CRM synchronization can commit after the MOP handoff. Rotate bounded
    # pending checks by reconciliation time; never replay the domain action.
    pending = db.query(ControlExecution).filter(
        ControlExecution.plan_json["kind"].astext == CONTRACT,
        ControlExecution.plan_json["requirement_key"].astext == "rule.messages.crm_handoff",
        ControlExecution.actual_state_json["postcondition_status"].astext == "NOT_PROVEN",
    ).order_by(ControlExecution.updated_at, ControlExecution.id).limit(min(n, 25)).all()
    out["crm_checked"] = 0
    for ex in pending:
        event_id = (ex.actual_state_json or {}).get("event_row_id")
        try:
            with db.begin_nested():
                record_mop_event(db, event_id)
            out["crm_checked"] += 1
        except Exception as exc:
            out["errors"].append({"event_id": event_id, "error": type(exc).__name__, "adapter": "crm_readback"})
            incident=_save_rule_gap_safely(db,record_mop_gap,event_id,type(exc).__name__,rule_key='rule.messages.crm_handoff')
            out['errors'][-1]['evidence_gap_persisted']=incident is not None
    return out


CRM_RULE_KEYS = (
    'rule.crm.deal_creation', 'rule.crm.owner_assignment', 'rule.crm.mop_handoff',
    'rule.crm.human_mop_ownership', 'rule.crm.next_action', 'rule.crm.phone_handoff',
    'rule.crm.calltracking_handoff', 'rule.crm.missed_calls', 'rule.crm.task_creation',
    'rule.crm.task_completion', 'rule.crm.stale_task_cleanup', 'rule.crm.answered_inquiry_cleanup',
)


def capture_crm_sync_context(db, account_id, chat_id, rows):
    """Freeze rules at an actual canonical sync, not at historical task creation."""
    latest = rows[-1] if rows else {}
    mid = str(latest.get('external_message_id') or '')
    if not mid:
        raise ValueError('CRM_SOURCE_MESSAGE_ID_NOT_PROVEN')
    context = capture_bindings(db, account_id=account_id, module='crm', rule_keys=CRM_RULE_KEYS,
        entrypoints={'app.crm.bridge:sync_dialog_for_account', 'app.services.crm_service:create_task'},
        event_source='crm_avito_sync', event_id=_hash([str(chat_id), mid]), observed_at=datetime.now(timezone.utc))
    context.update(chat_id=str(chat_id), source_message_id=mid,
                   source_text_hash=_body_hash(latest['body']) if isinstance(latest.get('body'),str) else None)
    previous=db.execute(text("""SELECT d.id,d.responsible_user_id FROM boris_crm_deals d
      JOIN accounts a ON a.account_id=d.avito_account_id AND a.owner_user_id=d.owner_user_id
      WHERE a.account_id=:a AND d.avito_chat_id=:c ORDER BY d.id DESC LIMIT 201"""),
      {'a':str(account_id),'c':str(chat_id)}).mappings().all()
    context['prior_assignments']={str(d['id']):d['responsible_user_id'] for d in previous[:200]}
    context['prior_assignments_complete']=len(previous)<=200
    return context


def _crm_current_facts(db, account_id, chat_id):
    owner = db.execute(text('SELECT owner_user_id FROM accounts WHERE account_id=:a'), {'a': account_id}).scalar()
    if owner is None:
        raise ValueError('CRM_ACCOUNT_OWNER_NOT_PROVEN')
    deals = db.execute(text("""SELECT id,responsible_user_id,contact_id,next_action_at,status
      FROM boris_crm_deals WHERE avito_account_id=:a AND avito_chat_id=:c AND owner_user_id=:o
      ORDER BY id DESC LIMIT 201"""), {'a': account_id,'c': chat_id,'o': owner}).mappings().all()
    ids=[r['id'] for r in deals[:200]]
    tasks = db.execute(text("""SELECT id,deal_id,contact_id,assigned_user_id,status,source,description,due_at,created_at,completed_at
      FROM boris_crm_tasks WHERE owner_user_id=:o AND deal_id=ANY(:ids) ORDER BY id DESC LIMIT 201"""),
      {'o': owner,'ids':ids}).mappings().all() if ids else []
    activities = db.execute(text("""SELECT id,deal_id,contact_id,activity_type,source,source_ref,metadata_json,created_at
      FROM boris_crm_activities WHERE owner_user_id=:o AND deal_id=ANY(:ids) ORDER BY id DESC LIMIT 201"""),
      {'o':owner,'ids':ids}).mappings().all() if ids else []
    mop = db.execute(text("""SELECT id,status,locked_by,avito_message_id FROM mop_drafts
      WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 201"""), {'a':account_id,'c':chat_id}).mappings().all()
    lead = db.execute(text('SELECT assigned_user_id FROM messenger_leads WHERE account_id=:a AND avito_chat_id=:c ORDER BY id DESC LIMIT 1'),{'a':account_id,'c':chat_id}).scalar()
    return {'owner':owner,'deals':[dict(r) for r in deals[:200]],'tasks':[dict(r) for r in tasks[:200]],
            'activities':[dict(r) for r in activities[:200]],'mop':[dict(r) for r in mop[:200]],
            'lead_assigned_user_id':lead,'complete':max(len(deals),len(tasks),len(activities),len(mop))<=200}


def _crm_rule_decisions(db, context, receipt, facts):
    """Evaluate current entities; historical mutation versions are never inferred.

    Only facts making a rule applicable create an execution. Task mutation
    lineage owned by immutable brain_recovery remains explicitly NOT_PROVEN.
    """
    from app.crm.bridge import _assert_account_access
    from fastapi import HTTPException
    decisions={}
    def add(key,result,reason,details,*,complete=False):
        decisions['rule.crm.'+key]={'evaluation_due_applicable':True,'evaluation_status':'EVALUATED' if result is not None else 'NOT_PROVEN',
            'predicate_result':result,'reason':reason,'facts':_json(details),
            'whole_rule_postcondition_proven':bool(complete and result is True),
            'assessment_scope':'current_state_readback','historical_mutation_version_proven':False}
    if not facts['complete']:
        add('deal_creation',None,'CRM_READBACK_WINDOW_TRUNCATED',{'bounded':True})
        return decisions
    active=[d for d in facts['deals'] if d['status']=='open']
    add('deal_creation',len(active)<=1,'CRM_CURRENT_OPEN_DEAL_CARDINALITY',
        {'active_deal_ids':[d['id'] for d in active], 'canonical_deal_eligible':receipt.get('deal_eligible'),
         'canonical_deal_created':receipt.get('deal_created'),'creation_retry_identity_proven':False})
    selected=next((d for d in facts['deals'] if d['id']==receipt.get('deal_id')),None)
    if selected:
        manager=selected['responsible_user_id']; authorized=False
        if manager is not None:
            try:
                _assert_account_access(db,manager,context['account_id']); authorized=True
            except HTTPException:
                authorized=False
        expected=facts['lead_assigned_user_id']
        origin='messenger_leads.assigned_user_id'
        if expected is None:
            previous=context.get('prior_assignments') or {}
            expected=previous.get(str(selected['id']))
            origin='preserved_existing_manual_assignment' if expected is not None else 'accounts.owner_user_id_fallback'
            if expected is None:expected=facts['owner']
        assignment=(authorized and manager==expected) if context.get('prior_assignments_complete') is True else None
        add('owner_assignment',assignment,
            'CRM_CANONICAL_ASSIGNMENT_READBACK',{'deal_id':selected['id'],'manager_user_id':manager,
             'expected_user_id':expected,'assignment_source':origin,'authorized':authorized},complete=True)
        open_tasks=[t for t in facts['tasks'] if t['deal_id']==selected['id'] and t['status']=='open']
        if open_tasks:
            due=[_utc(t['due_at']) for t in open_tasks if _utc(t['due_at'])]
            declared=_utc(selected['next_action_at'])
            add('next_action', (declared<=min(due)) if due and declared and len(due)==len(open_tasks) else None, 'CRM_NEXT_ACTION_TASK_DEADLINE_READBACK',
                {'deal_id':selected['id'],'task_ids':[t['id'] for t in open_tasks],
                 'next_action_at':declared,'earliest_open_due_at':min(due) if due else None,
                 'tasks_missing_due':[t['id'] for t in open_tasks if not t['due_at']]})
    human=[m for m in facts['mop'] if m['status']=='human_required']
    if human:
        sending=[m['id'] for m in facts['mop'] if m['status']=='sending']
        add('human_mop_ownership',not bool(sending),'CRM_HUMAN_AND_SENDER_STATE_READBACK',
            {'human_draft_ids':[m['id'] for m in human],'sending_draft_ids':sending,'takeover_and_external_ownership_proven':False})
        add('mop_handoff',selected is not None,'CRM_MOP_CURRENT_LINK_READBACK',
            {'human_draft_ids':[m['id'] for m in human],'deal_id':receipt.get('deal_id'),
             'transition_uniqueness_proven':False})
    refs={str(a['source_ref']):a for a in facts['activities'] if a.get('source_ref')}
    goal='mop_goal:'+context['account_id']+':'+context['chat_id']+':phone_received'
    if receipt.get('phone_received') or goal in refs:
        add('phone_handoff',goal in refs and selected is not None and refs[goal]['deal_id']==selected['id'],'CRM_PHONE_GOAL_LINK_READBACK',
            {'activity_id':refs[goal]['id'] if goal in refs else None,'deal_id':receipt.get('deal_id'),
             'source_ref':goal})
    calls=[a for a in facts['activities'] if a['source']=='avito_calltracking' and
           isinstance(a['metadata_json'],dict) and a['metadata_json'].get('account_id')==context['account_id']]
    if calls:
        valid=[a for a in calls if a['activity_type']=='call' and
            isinstance(a['metadata_json'].get('call_id'),(str,int)) and not isinstance(a['metadata_json'].get('call_id'),bool) and
            str(a['metadata_json']['call_id']).strip() and
            a['source_ref']=='avito_call:'+context['account_id']+':'+str(a['metadata_json']['call_id'])]
        add('calltracking_handoff',len(valid)==len(calls) and len({a['source_ref'] for a in valid})==len(valid),'CRM_CALL_IDENTITY_LINK_READBACK',
            {'activity_ids':[a['id'] for a in calls],'exact_identity_ids':[a['id'] for a in valid]})
        missed=[a for a in valid if a['metadata_json'].get('missed') is True]
        if missed:
            counts=[]
            for a in missed:
                suffix=str(a['source_ref'])[len('avito_call:'):]
                matching=[t for t in facts['tasks'] if t['source']=='avito_calltracking' and
                    t['deal_id']==a['deal_id'] and t['description']=='avito_call_task:'+suffix]
                contact_proven=bool(a['contact_id'] is not None and matching and
                    all(t['contact_id'] is not None and t['contact_id']==a['contact_id'] for t in matching))
                counts.append({'activity_id':a['id'],'task_count':len(matching),'contact_identity_proven':contact_proven})
            result=False if any(x['task_count']>1 for x in counts) else True if all(x['task_count']==1 and x['contact_identity_proven'] for x in counts) else None
            add('missed_calls',result,'CRM_MISSED_CALL_EXACT_TASK_CARDINALITY',{'calls':counts,
                'zero_tasks_requires_canonical_answered_or_deferred_decision':True})
    tasks=facts['tasks']
    if tasks:
        groups={}
        for t in tasks:
            if t['source']=='mop_handoff_recovery' and t['status']=='open':
                identity=(t['deal_id'],t['source'],'open')
            elif str(t['description'] or '').startswith(('mop_followup:','avito_call_task:')):
                identity=(t['deal_id'],t['source'],t['description'])
            else:
                continue
            groups.setdefault(identity,[]).append(t['id'])
        duplicate=[ids for ids in groups.values() if len(ids)>1]
        add('task_creation',False if duplicate else None,'CRM_TASK_CURRENT_DEDUP_NOT_MUTATION_PROOF',
            {'duplicate_task_ids':duplicate,'task_ids':[t['id'] for t in tasks],
             'canonical_registered_writer':'app.services.crm_service:create_task',
             'observed_store':'boris_crm_tasks','writer_contract_mismatch':True})
        done=[t for t in tasks if t['status']=='done']
        if done:
            links=[]
            for t in done:
                evidence=[refs[prefix+str(t['id'])]['id'] for prefix in ('brain_task_reconcile:','brain_mop_handoff_human_reply:') if prefix+str(t['id']) in refs]
                links.append({'task_id':t['id'],'activity_ids':evidence,'completed_at':t['completed_at']})
            add('task_completion',None,'CRM_OUTCOME_RECEIPT_REQUIRES_INDEPENDENT_RESULT_READBACK',{'tasks':links,'writer':'immutable brain_recovery.py'})
        for key,prefix in (('answered_inquiry_cleanup','brain_task_already_answered:'),('stale_task_cleanup','brain_task_stale_import:')):
            linked=[{'task_id':t['id'],'status':t['status'],'activity_id':refs[prefix+str(t['id'])]['id']} for t in tasks if prefix+str(t['id']) in refs]
            if linked:
                add(key,None,'CRM_CLEANUP_MUTATION_VERSION_NOT_PROVEN',{'receipts':linked,'writer':'immutable brain_recovery.py'})
    return decisions


def record_crm_sync_result(db, context, receipt):
    if context.get('event_source')!='crm_avito_sync' or not context.get('account_id') or not context.get('chat_id'):
        raise ValueError('CRM_RULE_EVENT_SCOPE_REQUIRED')
    source=db.execute(text("""SELECT m.body FROM boris_crm_conversations c
      JOIN accounts a ON a.account_id=c.account_id AND a.owner_user_id=c.owner_user_id
      JOIN boris_crm_messages m ON m.conversation_id=c.id AND m.owner_user_id=c.owner_user_id
      WHERE c.id=:cid AND c.account_id=:a AND c.external_chat_id=:chat AND c.channel='avito'
        AND m.external_message_id=:mid LIMIT 2"""),{'cid':receipt.get('conversation_id'),
        'a':context['account_id'],'chat':context['chat_id'],'mid':context.get('source_message_id')}).scalars().all()
    if len(source)!=1 or not context.get('source_text_hash') or _body_hash(source[0])!=context['source_text_hash']:
        raise ValueError('CRM_SOURCE_IDENTITY_READBACK_NOT_PROVEN')
    facts=_crm_current_facts(db,context['account_id'],context['chat_id'])
    if receipt.get('deal_id') is not None and not any(d['id']==receipt['deal_id'] for d in facts['deals']):
        raise ValueError('CRM_DEAL_RECEIPT_SCOPE_MISMATCH')
    decisions=_crm_rule_decisions(db,context,receipt,facts)
    results=[]
    bindings={b['requirement_key']:b for b in context.get('bindings',[])}
    for key,decision in decisions.items():
        if key not in bindings:
            raise ValueError('CRM_RULE_BINDING_NOT_PROVEN:'+key)
        ex=_execution(db,context,bindings[key])
        trigger={'event_source':'crm_avito_sync','event_id':context['event_id'],'chat_id':context['chat_id'],
                 'source_message_id':context['source_message_id'],'source_observed_at':ex.plan_json['observed_at']}
        _evidence_once(db,ex,level='L1',evidence_type='domain_event',fact_id='crm_sync:'+context['event_id'],payload=trigger,passed=True)
        fingerprint=_hash(decision)
        prior=db.query(ControlEvidence).filter(ControlEvidence.execution_id==ex.id,
            ControlEvidence.evidence_type=='crm_rule_assessment',ControlEvidence.payload_json['fact_id'].astext==fingerprint).first()
        observed=prior.payload_json['observed_at'] if prior else datetime.now(timezone.utc).isoformat()
        assessment=dict(decision,observed_at=observed)
        assessment['evidence_claim']='current_state_predicate_result'
        _evidence_once(db,ex,level='L3',evidence_type='crm_rule_assessment',fact_id=fingerprint,payload=assessment,
                       passed=decision['evaluation_status']=='EVALUATED' and decision['predicate_result'] is True)
        actual=dict(trigger,**assessment,postcondition_status='STATE_VERIFIED' if decision['predicate_result'] is True else 'NOT_PROVEN')
        _move(db,ex,'blocked',actual,decision['reason'])
        if decision['evaluation_status']=='EVALUATED':
            _resolve_missing(db,ex)
        results.append({'rule_key':key,'execution_id':ex.id,'predicate_result':decision['predicate_result']})
    return {'state':'RECORDED','rules':results,'business_actions':0,'provider_calls':0}


def observe_crm_sync(db, context, receipt):
    """Evidence failure cannot undo CRM work; preserve exact retry context."""
    try:
        with db.begin_nested():
            return record_crm_sync_result(db,context,receipt)
    except Exception as exc:
        _observe_rule_failure(exc)
        incident=_save_rule_gap_safely(db,_record_crm_gap,context,type(exc).__name__,receipt)
        return {'state':'NOT_PROVEN','incident_id':incident.id if incident else None,'evidence_gap_persisted':incident is not None}


def _record_crm_gap(db, context, reason, receipt=None):
    key=_hash([context.get('account_id'),context.get('event_source'),context.get('event_id'),reason])
    db.execute(text('SELECT pg_advisory_xact_lock(51008,hashtext(:k))'),{'k':key})
    incident=db.query(ControlIncident).filter(ControlIncident.incident_type=='RULE_EVIDENCE_GAP',
        ControlIncident.account_id==context.get('account_id'),ControlIncident.state=='open',
        ControlIncident.details_json['gap_key'].astext==key).first()
    if incident is None:
        incident=open_incident(db,incident_type='RULE_EVIDENCE_GAP',module='crm',account_id=context.get('account_id'),
            symptom='Canonical CRM event exists but its rule evidence is not proven',priority='P2',
            details={'gap_key':key,'reason':reason,'context':context,'receipt':_json(receipt),
                     'business_action_replay_allowed':False})
    return incident


def record_crm_capture_failure(db, account_id, chat_id, latest, error):
    context={'account_id':str(account_id),'event_source':'crm_rule_capture_failed',
             'event_id':_hash([str(chat_id),latest.get('external_message_id')]),'chat_id':str(chat_id),
             'source_message_id':latest.get('external_message_id'),'observed_at':datetime.now(timezone.utc).isoformat()}
    return _record_crm_gap(db,context,type(error).__name__)



def record_legacy_crm_task_result(db, context, task_id):
    row=db.execute(text('SELECT id,status,created_by,done_at FROM crm_tasks WHERE id=:i AND account_id=:a'),
                   {'i':int(task_id),'a':context['account_id']}).mappings().first()
    if row is None:
        raise ValueError('CRM_TASK_EXACT_ACCOUNT_READBACK_MISSING')
    binding=next(iter(context.get('bindings',[])),None)
    if binding is None:
        raise ValueError('CRM_TASK_RULE_BINDING_MISSING')
    ex=_execution(db,context,binding)
    event={'event_source':context['event_source'],'task_id':int(task_id),'source_observed_at':ex.plan_json['observed_at'],
           'store':'crm_tasks','boris_crm_tasks_not_verified':True}
    _evidence_once(db,ex,level='L1',evidence_type='domain_event',fact_id=context['event_id'],payload=event,passed=True)
    reason='CRM_LEGACY_CREATE_REQUEST_IDENTITY_MISSING' if context['event_source']=='crm_legacy_task_created' else 'CRM_LEGACY_COMPLETION_OUTCOME_NOT_PROVEN'
    actual=dict(event,evaluation_due_applicable=True,evaluation_status='NOT_PROVEN',postcondition_status='NOT_PROVEN',reason=reason,
                current_status=row['status'],row_id=int(row['id']),predicate_result=None,
                canonical_owner_entrypoint_satisfied=context['event_source']=='crm_legacy_task_created')
    fingerprint=_hash(actual)
    _evidence_once(db,ex,level='L3',evidence_type='crm_rule_assessment',fact_id=fingerprint,payload=actual,passed=False)
    _move(db,ex,'blocked',actual,reason)
    return {'state':'NOT_PROVEN','execution_id':ex.id,'reason':reason}


def prepare_legacy_crm_task_context(db, account_id, event):
    key='rule.crm.task_creation' if event=='created' else 'rule.crm.task_completion'
    return capture_bindings(db,account_id=account_id,module='crm',rule_keys=[key],
        entrypoints={'app.services.crm_service:create_task'},event_source='crm_legacy_task_'+event,
        event_id='pending:'+event,observed_at=datetime.now(timezone.utc))


def observe_legacy_crm_task(db, account_id, task_id, event, context=None):
    try:
        with db.begin_nested():
            if not context or context.get('account_id')!=str(account_id) or context.get('event_source')!='crm_legacy_task_'+event:
                raise ValueError('CRM_TASK_PREMUTATION_BINDING_NOT_PROVEN')
            context=dict(context,event_id=str(int(task_id)))
            return record_legacy_crm_task_result(db,context,task_id)
    except Exception as exc:
        _observe_rule_failure(exc)
        context=context or {'account_id':str(account_id),'event_source':'crm_rule_capture_failed',
                            'event_id':'legacy:'+str(task_id)+':'+event}
        incident=_save_rule_gap_safely(db,_record_crm_gap,context,type(exc).__name__,{'task_id':int(task_id)})
        return {'state':'NOT_PROVEN','reason':type(exc).__name__,'evidence_gap_persisted':incident is not None}


FEED_ENTRYPOINT = 'app.api.avito:feed_send_to_avito'



def _feed_scope(db, account_id, campaign_id):
    account = str(account_id or '').strip()
    if not account or account == '*' or db.execute(text('SELECT 1 FROM accounts WHERE account_id=:a'), {'a':account}).scalar() != 1:
        raise ValueError('FEED_ACCOUNT_SCOPE_NOT_PROVEN')
    if campaign_id is not None:
        if type(campaign_id) is not int or campaign_id <= 0:
            raise ValueError('FEED_CAMPAIGN_ID_INVALID')
        if db.execute(text('SELECT 1 FROM campaigns WHERE id=:c AND account_id=:a'), {'c':campaign_id,'a':account}).scalar() != 1:
            raise ValueError('FEED_CAMPAIGN_ACCOUNT_SCOPE_MISMATCH')
    return account


def capture_feed_context(db, *, account_id, campaign_id, stage, event_id, observed_at=None, artifact_sha256=None, artifact_kind=None):
    """Freeze a real stage invocation before its existing action/commit.

    The producer supplies an existing durable invocation identity. Mandatory
    stage predicates receive PENDING obligations; conditional branches do not
    become obligations before the result proves their applicability. A crash
    after the producer commits this intent is visible to the existing due tick;
    reconciliation never retries the publication itself.
    """
    from app.services.control_feed_rule_facts import STAGE_RULES, MANDATORY_STAGE_RULES
    account = _feed_scope(db, account_id, campaign_id)
    stage = str(stage or '')
    source_id = str(event_id or '').strip()
    if stage not in STAGE_RULES or not source_id:
        raise ValueError('FEED_STAGE_EVENT_IDENTITY_REQUIRED')
    artifact = str(artifact_sha256).lower() if artifact_sha256 is not None else None
    if artifact is not None and re.fullmatch('[0-9a-f]{64}', artifact) is None:
        raise ValueError('FEED_ARTIFACT_SHA_INVALID')
    if campaign_id is None and (artifact is None or artifact_kind not in {'canonical_feed_items_json','submitted_xml_bytes'}):
        raise ValueError('FEED_DIRECT_ARTIFACT_IDENTITY_REQUIRED')
    now = datetime.now(timezone.utc)
    when = _utc(observed_at) if observed_at is not None else now
    if when is None or when > now:
        raise ValueError('FEED_EVENT_TIME_NOT_PROVEN')
    identity = _hash([account,campaign_id,stage,source_id,artifact,artifact_kind])
    db.execute(text('SELECT pg_advisory_xact_lock(51009,hashtext(:k))'),{'k':identity})
    prior = db.query(ControlExecution).filter(ControlExecution.account_id==account,
        ControlExecution.module=='feed_factory', ControlExecution.plan_json['kind'].astext==CONTRACT,
        ControlExecution.plan_json['event_source'].astext=='feed_stage',
        ControlExecution.plan_json['event_id'].astext==identity).order_by(ControlExecution.created_at).first()
    if prior:
        context = (prior.plan_json or {}).get('feed_context')
        if not isinstance(context,dict) or context.get('account_id')!=account or context.get('stage')!=stage or context.get('campaign_id')!=campaign_id or context.get('source_event_id')!=source_id or context.get('artifact_sha256')!=artifact or context.get('artifact_kind')!=artifact_kind:
            raise ValueError('FEED_CAPTURE_REPLAY_IDENTITY_CONFLICT')
        return context
    prior_gap = db.query(ControlIncident).filter(ControlIncident.account_id==account,
        ControlIncident.module=='feed_factory',ControlIncident.incident_type=='RULE_EVIDENCE_GAP',
        ControlIncident.details_json['context']['event_source'].astext=='feed_stage',
        ControlIncident.details_json['context']['event_id'].astext==identity).order_by(ControlIncident.opened_at).first()
    if prior_gap:
        frozen=(prior_gap.details_json or {}).get('context')
        if not isinstance(frozen,dict) or frozen.get('account_id')!=account or frozen.get('event_id')!=identity:
            raise ValueError('FEED_CAPTURE_GAP_IDENTITY_CONFLICT')
        # A later published rule cannot retroactively supply a missing binding
        # for this existing invocation. Its durable GAP remains explicit.
        return frozen
    context = capture_bindings(db,account_id=account,module='feed_factory',rule_keys=STAGE_RULES[stage],
        entrypoints={FEED_ENTRYPOINT},event_source='feed_stage',event_id=identity,observed_at=when)
    context.update(stage=stage,campaign_id=campaign_id,source_event_id=source_id,artifact_sha256=artifact,artifact_kind=artifact_kind)
    required = set(MANDATORY_STAGE_RULES[stage])
    for binding in context['bindings']:
        if binding['requirement_key'] not in required:
            continue
        ex = _execution(db,context,binding)
        ex.plan_json = dict(ex.plan_json,feed_context=_json(context))
        trigger = {'stage':stage,'campaign_id':campaign_id,'source_event_id':source_id,
                   'artifact_sha256':artifact,'artifact_kind':artifact_kind,'source_observed_at':ex.plan_json['observed_at'],
                   'evidence_claim':'canonical_stage_invocation_observed_not_action_success'}
        _evidence_once(db,ex,level='L1',evidence_type='domain_event',fact_id=identity,payload=trigger,passed=True)
        _move(db,ex,'blocked',dict(trigger,evaluation_due_applicable=True,evaluation_status='PENDING',predicate_result=None,
              postcondition_status='NOT_PROVEN',reason='FEED_STAGE_RESULT_PENDING'), 'FEED_STAGE_RESULT_PENDING')
    if context['errors']:
        _record_feed_gap(db,context,'FEED_RULE_BINDING_NOT_PROVEN')
    return context


def safe_capture_feed_context(db, **kwargs):
    try:
        with db.begin_nested():
            return capture_feed_context(db,**kwargs)
    except Exception as exc:
        _observe_rule_failure(exc)
        context = {'contract':CONTRACT,'account_id':str(kwargs.get('account_id') or ''),
                   'module':'feed_factory','event_source':'feed_capture_failed',
                   'event_id':_hash([kwargs.get('campaign_id'),kwargs.get('stage'),kwargs.get('event_id')]),
                   'source_event_id':str(kwargs.get('event_id') or ''),'stage':kwargs.get('stage'),
                   'campaign_id':kwargs.get('campaign_id'),'artifact_sha256':kwargs.get('artifact_sha256'),'artifact_kind':kwargs.get('artifact_kind'),
                   'observed_at':datetime.now(timezone.utc).isoformat(),'bindings':[],
                   'errors':[{'code':type(exc).__name__}],'capture_failed':True}
        incident=_save_rule_gap_safely(db,_record_feed_gap,context,type(exc).__name__)
        context['evidence_gap_persisted']=incident is not None
        return context


def record_feed_result(db, context, facts):
    from app.services.control_feed_rule_facts import STAGE_RULES, evaluate_feed_rule_facts
    if not isinstance(context,dict) or context.get('contract')!=CONTRACT or context.get('event_source')!='feed_stage' or context.get('capture_failed'):
        raise ValueError('FEED_PREACTION_VERSION_NOT_PROVEN')
    account = _feed_scope(db,context.get('account_id'),context.get('campaign_id'))
    if not isinstance(facts,dict) or any(facts.get(k)!=context.get(c) for k,c in (
        ('account_id','account_id'),('campaign_id','campaign_id'),('stage','stage'),
        ('event_id','source_event_id'),('artifact_sha256','artifact_sha256'),('artifact_kind','artifact_kind'))):
        raise ValueError('FEED_RESULT_IDENTITY_MISMATCH')
    observed = _utc(facts.get('observed_at'))
    if observed is None or observed < _utc(context.get('observed_at')) or observed > datetime.now(timezone.utc):
        raise ValueError('FEED_RESULT_TIME_NOT_PROVEN')
    decisions = evaluate_feed_rule_facts(facts)
    if not isinstance(decisions,dict) or set(decisions)-set(STAGE_RULES[context['stage']]):
        raise ValueError('FEED_RESULT_RULE_SCOPE_MISMATCH')
    bindings = {b['requirement_key']:b for b in context.get('bindings',[])}
    result=[]
    for key,decision in decisions.items():
        if key not in bindings:
            raise ValueError('FEED_RULE_BINDING_NOT_PROVEN:'+key)
        if not isinstance(decision,dict) or decision.get('evaluation_status') not in {'EVALUATED','NOT_PROVEN','PENDING','WAITING_EVENT'} or type(decision.get('predicate_result')) not in {bool,type(None)}:
            raise ValueError('FEED_RULE_DECISION_INVALID')
        # A stage receipt describes its exact predicate. It cannot certify the
        # whole published rule, provider publication or marketing KPI.
        if decision.get('whole_rule_postcondition_proven') is True or (decision.get('facts') or {}).get('rule_pass') is True:
            raise ValueError('FEED_WHOLE_RULE_PROOF_NOT_AVAILABLE')
        ex = _execution(db,context,bindings[key])
        if not (ex.plan_json or {}).get('feed_context'):
            ex.plan_json=dict(ex.plan_json,feed_context=_json(context))
        trigger={'stage':context['stage'],'campaign_id':context['campaign_id'],
                 'source_event_id':context['source_event_id'],'artifact_sha256':context.get('artifact_sha256'),'artifact_kind':context.get('artifact_kind'),
                 'source_observed_at':ex.plan_json['observed_at'],
                 'evidence_claim':'canonical_stage_invocation_observed_not_action_success'}
        _evidence_once(db,ex,level='L1',evidence_type='domain_event',fact_id=context['event_id'],payload=trigger,passed=True)
        # An identical retry may be observed again later. It retains the first
        # assessment clock; a changed result or independent provider timestamp
        # changes the semantic fingerprint and must use a new domain event ID.
        fingerprint = _hash({k:v for k,v in facts.items() if k!='observed_at'})
        prior_assessment = db.query(ControlEvidence).filter(ControlEvidence.execution_id==ex.id,
            ControlEvidence.evidence_type=='feed_rule_assessment',
            ControlEvidence.payload_json['fact_id'].astext==context['event_id']).first()
        same_result = bool(prior_assessment and prior_assessment.payload_json.get('input_facts_sha256')==fingerprint)
        assessment_time = prior_assessment.payload_json.get('observed_at') if same_result else observed.isoformat()
        if same_result:
            decision=dict(decision)
            if 'source_observed_at' in decision:
                decision['source_observed_at']=prior_assessment.payload_json.get('source_observed_at')
            if isinstance(decision.get('facts'),dict) and 'observed_at' in decision['facts']:
                decision['facts']=dict(decision['facts'],observed_at=(prior_assessment.payload_json.get('facts') or {}).get('observed_at'))
        payload=dict(decision,observed_at=assessment_time,evidence_claim='frozen_feed_stage_predicate_result',
                     input_facts_sha256=fingerprint,whole_rule_postcondition_proven=False)
        _evidence_once(db,ex,level='L3',evidence_type='feed_rule_assessment',fact_id=context['event_id'],payload=payload,
                       passed=decision['evaluation_status']=='EVALUATED' and decision['predicate_result'] is True)
        actual={**trigger,**{k:v for k,v in payload.items() if k!='evidence_claim'},
                'postcondition_status':'LOCAL_PREDICATE_VERIFIED' if decision['predicate_result'] is True else 'NOT_PROVEN'}
        _move(db,ex,'blocked',actual,decision.get('reason') or 'FEED_WHOLE_RULE_NOT_PROVEN')
        if decision['evaluation_status']=='EVALUATED':
            _resolve_missing(db,ex)
        result.append({'rule_key':key,'execution_id':ex.id,'predicate_result':decision['predicate_result']})
    return {'state':'RECORDED','rules':result,'provider_calls':0,'business_actions':0,'account_id':account}


def _record_feed_gap(db, context, reason, facts=None):
    key=_hash([context.get('account_id'),context.get('event_source'),context.get('event_id'),reason])
    db.execute(text('SELECT pg_advisory_xact_lock(51010,hashtext(:k))'),{'k':key})
    incident=db.query(ControlIncident).filter(ControlIncident.incident_type=='RULE_EVIDENCE_GAP',
        ControlIncident.account_id==context.get('account_id'),ControlIncident.state=='open',
        ControlIncident.details_json['gap_key'].astext==key).first()
    if incident is None:
        incident=open_incident(db,incident_type='RULE_EVIDENCE_GAP',module='feed_factory',account_id=context.get('account_id'),
            symptom='Feed stage exists but its versioned rule evidence is not proven',priority='P2',
            details={'gap_key':key,'reason':reason,'context':_json(context),'facts':_json(facts),
                     'business_action_replay_allowed':False})
    return incident


def observe_feed_result(db, context, facts):
    try:
        with db.begin_nested():
            return record_feed_result(db,context,facts)
    except Exception as exc:
        _observe_rule_failure(exc)
        incident=_save_rule_gap_safely(db,_record_feed_gap,context or {},type(exc).__name__,facts)
        return {'state':'NOT_PROVEN','incident_id':incident.id if incident else None,
                'evidence_gap_persisted':incident is not None,'provider_calls':0,'business_actions':0}


def reconcile_feed_rule_gaps(db, *, now, limit):
    gaps=db.query(ControlIncident).filter(ControlIncident.module=='feed_factory',
        ControlIncident.incident_type=='RULE_EVIDENCE_GAP',ControlIncident.state=='open',
        ControlIncident.details_json['context']['event_source'].astext=='feed_stage',
        ControlIncident.details_json['facts']['stage'].astext.isnot(None),
        ControlIncident.updated_at < now-timedelta(seconds=300)).order_by(ControlIncident.updated_at).limit(min(limit,25)).all()
    result={'checked':0,'recovered':0,'errors':[]}
    for gap in gaps:
        result['checked']+=1
        try:
            with db.begin_nested():
                details=gap.details_json or {}
                if (details.get('context') or {}).get('account_id')!=gap.account_id:
                    raise ValueError('FEED_RECOVERY_TENANT_MISMATCH')
                record_feed_result(db,details['context'],details['facts'])
                gap.state='resolved';gap.resolved_at=now;gap.updated_at=now
            result['recovered']+=1
        except Exception as exc:
            gap.updated_at=now
            result['errors'].append({'incident_id':gap.id,'error':type(exc).__name__})
    return result


def registered_rule_coverage(db, account_id, *, modules=None):
    """Describe implemented bindings separately from account evaluations."""
    selected = {str(x) for x in (modules or []) if x}
    if modules is not None and not selected:
        return {'applicability':'UNKNOWN','reason':'ACCOUNT_MODULES_NOT_PROVEN','catalog_rules':0,
                'implemented_bindings':0,'entries':[],'catalog_entry_is_not_account_execution':True}
    if "mop" in selected:
        selected.add("messages")
    query = db.query(ControlRequirement).filter(ControlRequirement.status == "active", ControlRequirement.requirement_type == "business_rule")
    query = query.filter(((ControlRequirement.scope_type == "global") & (ControlRequirement.scope_id == "*")) |
                         ((ControlRequirement.scope_type == "account") & (ControlRequirement.scope_id == account_id)))
    if selected:
        query = query.filter(ControlRequirement.module.in_(sorted(selected)))
    rows = query.order_by(ControlRequirement.module, ControlRequirement.key).all()
    from app.services.control_feed_rule_facts import STAGE_RULES
    from app.services.control_owner_budget_rule_facts import KEYS as OWNER_BUDGET_KEYS
    feed_keys = {key for keys in STAGE_RULES.values() for key in keys}
    protected_feed_only = {'rule.feed_factory.review','rule.feed_factory.rejection',
                           'rule.feed_factory.partial_publish','rule.feed_factory.terminal_states'}
    protected_feed_branches = protected_feed_only | {'rule.feed_factory.retry','rule.feed_factory.superseded','rule.feed_factory.schedule'}
    implemented = set(MOP_RULE_KEYS) | set(MESSAGE_FACT_KEYS) | set(CRM_RULE_KEYS) | feed_keys | set(OWNER_BUDGET_KEYS)
    entries = []
    for req in rows:
        source = req.source_json if isinstance(req.source_json, dict) else {}
        supported = req.key in implemented
        entries.append({"module": req.module, "operation": source.get("operation"), "requirement_id": str(req.id),
                        "requirement_version": int(req.version), "rule_key": req.key, "rule_id": source.get("rule_id"),
                        "binding_status": ("ADAPTER_IMPLEMENTED_PRODUCER_PROTECTED" if req.key in protected_feed_only else "IMPLEMENTED") if supported else "PROTECTED_PRODUCER_NOT_INSTALLED" if req.module=="marketing" else "RULE_EVENT_ADAPTER_MISSING",
                        "event_source": ("authenticated_budget_setting" if req.key in OWNER_BUDGET_KEYS else "feed_stage" if req.key in feed_keys else "crm_avito_sync" if req.key in CRM_RULE_KEYS else "mop_draft_events") if supported else None,
                        "capture_scope": "current_authenticated_budget_write" if req.key in OWNER_BUDGET_KEYS else "current_feed_stage_observation" if req.key in feed_keys else "current_CRM_sync_observation" if req.key in CRM_RULE_KEYS else "current_MOP_event" if supported else None,
                        "predicate_adapter_implemented": supported,
                        "ordinary_producer_instrumented": supported and req.key not in protected_feed_only,
                        "feed_stages": [stage for stage,keys in STAGE_RULES.items() if req.key in keys] if req.key in feed_keys else [],
                        "protected_feed_writer_branches_missing": req.key in protected_feed_branches,
                        "historical_mutation_version_proven": False,
                        "protected_writer_capture_missing": req.key in {
                            'rule.crm.task_creation','rule.crm.task_completion','rule.crm.stale_task_cleanup',
                            'rule.crm.answered_inquiry_cleanup','rule.crm.missed_calls'},
                        "result_readback": "provider_GET" if req.key == "rule.messages.first_response" else "canonical_CRM_link" if req.key == "rule.messages.crm_handoff" else "NOT_PROVEN",
                        "catalog_entry_is_not_account_execution": True})
    return {"catalog_rules": len(entries), "implemented_bindings": sum(x["binding_status"] == "IMPLEMENTED" for x in entries),
            "entries": entries, "catalog_entry_is_not_account_execution": True}


def account_rule_projection(db, account_id, *, since, until, limit=200, modules=None):
    """Read-only exact-tenant report projection; no global-rule fan-out."""
    start, end = _utc(since), _utc(until)
    if not account_id or start is None or end is None or start >= end:
        raise ValueError("RULE_SOURCE_WINDOW_REQUIRED")
    if end > datetime.now(timezone.utc):
        raise ValueError("RULE_SOURCE_WINDOW_NOT_CLOSED")
    n = max(1, min(int(limit), 500))
    rows = db.query(ControlExecution).filter(
        ControlExecution.account_id == account_id, ControlExecution.plan_json["kind"].astext == CONTRACT,
        # ISO UTC strings are produced by this adapter at capture time. This
        # window follows the domain event even if a delayed journal is created
        # tomorrow; it never follows the reconciliation timestamp.
        ControlExecution.plan_json["observed_at"].astext >= start.isoformat(),
        ControlExecution.plan_json["observed_at"].astext < end.isoformat(),
    ).order_by(ControlExecution.plan_json["observed_at"].astext, ControlExecution.id).limit(n + 1).all()
    expected_events = db.execute(text("""SELECT count(*) FROM mop_drafts
      WHERE account_id=:a AND created_at>=:start AND created_at<:end"""),
      {"a": account_id, "start": start, "end": end}).scalar()
    result = []
    for ex in rows[:n]:
        req = db.query(ControlRequirement).filter(ControlRequirement.id == ex.requirement_id).first()
        current = bool(req and req.status == "active" and int(req.version) == (ex.plan_json or {}).get("requirement_version"))
        result.append({"execution_id": ex.id, "account_id": ex.account_id, "module": ex.module,
                       "rule_id": (ex.plan_json or {}).get("rule_id"), "requirement_id": ex.requirement_id,
                       "requirement_version": (ex.plan_json or {}).get("requirement_version"), "current_version": current,
                       "status": ex.status if current else "HISTORICAL_VERSION", "identity": ex.plan_json,
                       "actual": ex.actual_state_json,
                       "source_observed_at": (ex.actual_state_json or {}).get("source_observed_at"),
                       "verified_at": ((ex.actual_state_json or {}).get("readback") or {}).get("observed_at"),
                       "crm_link_verified_at": ((ex.actual_state_json or {}).get("crm_readback") or {}).get("observed_at"),
                       "assessment_observed_at": (ex.actual_state_json or {}).get("observed_at"),
                       "ledger_reconciled_at": _utc(ex.updated_at).isoformat() if ex.updated_at else None})
    gaps = db.query(ControlIncident).filter(ControlIncident.account_id == account_id,
        ControlIncident.incident_type.in_(["RULE_EVIDENCE_GAP", "RULE_NOT_EVALUATED"]),
        ControlIncident.state == "open").order_by(ControlIncident.opened_at).limit(n + 1).all()
    # This aggregate uses the complete closed window, independently of the
    # bounded event-detail sample. Count unique UUID/version identities once;
    # catalog entries are not executions and do not enter these totals.
    groups = db.execute(text("""SELECT e.requirement_id, e.module,
        e.plan_json->>'requirement_version' AS requirement_version,
        e.plan_json->>'rule_id' AS rule_id,
        (r.status='active' AND CAST(r.version AS text)=e.plan_json->>'requirement_version') AS current_version,
        count(*) AS execution_count,
        count(*) FILTER(WHERE e.actual_state_json->>'evaluation_status'='EVALUATED') AS evaluated,
        count(*) FILTER(WHERE e.actual_state_json->>'evaluation_status'='PENDING') AS pending,
        count(*) FILTER(WHERE e.actual_state_json->>'evaluation_status'='WAITING_EVENT') AS waiting_event,
        count(*) FILTER(WHERE e.actual_state_json->>'evaluation_status'='NOT_PROVEN') AS not_proven,
        count(*) FILTER(WHERE e.actual_state_json->'evaluation_due_applicable'='true'::jsonb
          AND e.actual_state_json->>'evaluation_status' IN ('PENDING','NOT_PROVEN','EVALUATED')
          AND e.plan_json->>'verification_due_at'<=:end) AS due_event_count,
        count(*) FILTER(WHERE e.actual_state_json->'evaluation_due_applicable'='true'::jsonb
          AND e.actual_state_json->>'evaluation_status'='EVALUATED'
          AND e.plan_json->>'verification_due_at'<=:end) AS evaluated_due_event_count,
        min(e.plan_json->>'observed_at') AS first_source_observed_at,
        max(e.plan_json->>'observed_at') AS last_source_observed_at
      FROM control_executions e JOIN control_requirements r ON r.id=e.requirement_id
      WHERE e.account_id=:a AND e.plan_json->>'kind'=:kind
        AND e.plan_json->>'observed_at'>=:start AND e.plan_json->>'observed_at'<:end
      GROUP BY e.requirement_id,e.module,e.plan_json->>'requirement_version',e.plan_json->>'rule_id',r.status,r.version
      ORDER BY e.module,e.requirement_id,e.plan_json->>'requirement_version'"""),
      {'a':account_id,'kind':CONTRACT,'start':start.isoformat(),'end':end.isoformat()}).mappings().all()
    coverage_counts = [dict(x, requirement_id=str(x['requirement_id']),
                            requirement_version=int(x['requirement_version'])) for x in groups]
    return {"account_id": account_id, "window": {"from": start.isoformat(), "until": end.isoformat()},
            "state": "OBSERVED" if rows else "NOT_PROVEN" if expected_events or gaps else "NO_EVENT",
            "aggregate_complete": True, "aggregate_source": "all_stored_rule_executions_in_closed_window",
            "execution_count": sum(x['execution_count'] for x in coverage_counts),
            "unique_rule_versions": len(coverage_counts), "rule_coverage_counts": coverage_counts,
            # One due rule/version is covered only when every applicable event
            # whose deadline falls before window end has an evaluation. A
            # successful/negative evaluation cannot hide another missed event.
            "due_rule_versions": sum(x['due_event_count']>0 for x in coverage_counts),
            "evaluated_due_rule_versions": sum(x['due_event_count']>0 and
                x['evaluated_due_event_count']==x['due_event_count'] for x in coverage_counts),
            "due_coverage_scope": "unique_account_requirement_version_all_due_events_evaluated",
            "event_details_truncated": len(rows)>n, "open_gaps_truncated": len(gaps)>n,
            "source": "persisted_mop_intake", "external_intake_complete": False,
            "source_complete": len(rows) <= n and len(gaps) <= n, "expected_local_events": int(expected_events or 0), "rules": result,
            "gaps": [{"incident_id": x.id, "module": x.module, "type": x.incident_type, "details": x.details_json} for x in gaps[:n]],
            "binding_coverage": registered_rule_coverage(db, account_id, modules=modules)}

