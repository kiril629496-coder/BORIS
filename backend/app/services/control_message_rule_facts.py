"""Local branch receipts for seven registered MSG rules; never a rule PASS.

No provider, billing, policy change, commit or external action. Producers freeze
the actual canonical decision inputs. Historical absent inputs stay unknown.
The ControlPlane caller owns binding/version validation and L3 persistence.
"""
from datetime import datetime, timezone
import hashlib
import re

CONTRACT = "mop_message_rule_facts_v1"
KEYS = tuple("rule.messages." + k for k in (
    "greeting", "empty_chat", "viewed_phone", "phone_received", "job_seeker",
    "first_turn_identity", "autosend_eligibility",
))


def _hash(value):
    # Exact normalization used by mop_core.text_hash, with no startup import.
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def capture_generation_rule_facts(*, account_id, chat_id, reply_text,
                                  first_manager_turn, configured_intro,
                                  identity_violations, proactive_event,
                                  generation_model, analysis=None):
    """Called by the existing finalizer after canonical intro/output handling.

    False from _mop_first_manager_turn may also mean a read failed, so it does
    not prove a non-first turn. A true result proves the existing local policy
    classified it as first; it does not claim complete external chat history.
    """
    intro = str(configured_intro or "").strip()
    norm = lambda x: " ".join(re.findall(r"[а-яёa-z0-9]+", str(x or "").lower().replace("ё", "е"), re.I))
    violations = (sorted(set(identity_violations) & {"automation_disclosure", "internal_or_invented_identity"})
                  if isinstance(identity_violations, list) and all(isinstance(x, str) for x in identity_violations) else None)
    analysis = analysis if isinstance(analysis, dict) else {}
    return {
        "contract": CONTRACT, "source": "app.api.messenger:generate_ai_draft_reply._finalize",
        "observed_at": _now(), "account_id": str(account_id), "chat_id": str(chat_id),
        "reply_text_hash": _hash(reply_text), "first_manager_turn": first_manager_turn if type(first_manager_turn) is bool else None,
        "configured_intro_present": bool(intro), "configured_intro_hash": _hash(intro) if intro else None,
        "configured_intro_prefix": bool(intro and norm(reply_text).startswith(norm(intro))),
        "identity_violations": violations, "proactive_event": proactive_event,
        "generation_model": str(generation_model or ""),
        "phone_received": analysis.get("phone_received") if type(analysis.get("phone_received")) is bool else None,
        "human_handoff": analysis.get("human_handoff") if type(analysis.get("human_handoff")) is bool else None,
        "external_history_complete": False,
    }


def capture_eligibility_rule_facts(*, account_id, chat_id, inbound_id,
                                   module_allowed, entitlement_active, binding_enabled,
                                   owner_enabled, schedule_active, auto_send,
                                   ai_enabled, proactive_event):
    """Observe the actual _mop_incoming gate, not a new policy evaluator."""
    values = dict(module_allowed=module_allowed, entitlement_active=entitlement_active,
                  binding_enabled=binding_enabled, owner_enabled=owner_enabled,
                  schedule_active=schedule_active, auto_send=auto_send, ai_enabled=ai_enabled)
    return {
        "contract": CONTRACT, "source": "app.api.messenger:_mop_incoming",
        "observed_at": _now(), "account_id": str(account_id), "chat_id": str(chat_id),
        "inbound_id": str(inbound_id), "proactive_event": proactive_event,
        "checks": {key: value if type(value) is bool else None for key, value in values.items()},
    }


def capture_send_rule_facts(*, account_id, chat_id, inbound_id, reply_text,
                            module_allowed, takeover_active, readback_decision,
                            actor_id, channel):
    """Observe the last existing do_send checks before atomic send claim."""
    return {
        "contract": CONTRACT, "source": "app.mop_core:do_send",
        "observed_at": _now(), "account_id": str(account_id), "chat_id": str(chat_id),
        "inbound_id": str(inbound_id), "reply_text_hash": _hash(reply_text),
        "module_allowed": module_allowed if type(module_allowed) is bool else None,
        "takeover_active": takeover_active if type(takeover_active) is bool else None,
        "readback_decision": str(readback_decision or ""),
        "actor_id": str(actor_id or ""), "channel": str(channel or ""),
    }


def _decision(status, result, reason, row, *, evaluation_due_applicable=True, **facts):
    return {"evaluation_status": status, "predicate_result": result, "reason": reason,
            "evaluation_due_applicable": evaluation_due_applicable,
            "source_observed_at": _time(row.get("at")).isoformat() if _time(row.get("at")) else None,
            "facts": {"event_row_id": row.get("event_row_id"), "draft_id": row.get("draft_id"),
                      "account_id": row.get("account_id"), "chat_id": row.get("avito_chat_id"),
                      "inbound_id": row.get("avito_message_id"), "rule_pass": False, **facts}}


def _valid(blob, row, source, *, body=False, inbound=False):
    if not isinstance(blob, dict) or blob.get("contract") != CONTRACT or blob.get("source") != source:
        return False
    if blob.get("account_id") != str(row.get("account_id")) or blob.get("chat_id") != str(row.get("avito_chat_id")):
        return False
    observed, at = _time(blob.get("observed_at")), _time(row.get("at"))
    # PostgreSQL event.at uses transaction now(); a final guard is often
    # observed later in that same transaction. It is not a wall-clock upper
    # bound. Reject future/naive facts, retaining both original timestamps.
    if observed is None or at is None or observed > datetime.now(timezone.utc):
        return False
    if body and (not str(row.get("reply_text") or "").strip() or blob.get("reply_text_hash") != _hash(row.get("reply_text"))):
        return False
    if inbound and blob.get("inbound_id") != str(row.get("avito_message_id")):
        return False
    return True


def _generation_event(db, row):
    """Read one actual prior generation receipt, scoped to the same draft."""
    from sqlalchemy import text
    found = db.execute(text("""SELECT id AS event_row_id,at,metadata FROM mop_draft_events
       WHERE draft_id=:d AND id<=:e AND event IN ('ai_generated','handed_to_human')
         AND metadata ? 'message_rule_facts'
       ORDER BY id DESC LIMIT 1"""), {"d": int(row["draft_id"]), "e": int(row["event_row_id"])}).mappings().first()
    if not found:
        return None, None, None
    metadata = found.get("metadata") or {}
    event_row = dict(row, at=found["at"])
    gen = metadata.get("message_rule_facts")
    gate = metadata.get("message_eligibility_facts")
    if not _valid(gen, event_row, "app.api.messenger:generate_ai_draft_reply._finalize", body=True):
        gen = None
    if not _valid(gate, event_row, "app.api.messenger:_mop_incoming", inbound=True):
        gate = None
    return gen, gate, int(found["event_row_id"])


def _send_event(db, row):
    """Recover immutable preclaim evidence after the result event is appended."""
    from sqlalchemy import text
    if row.get("event") == "send_started":
        source = row
    else:
        found = db.execute(text("""SELECT id AS event_row_id,at,metadata,actor_id,channel
           FROM mop_draft_events WHERE draft_id=:d AND id<=:e AND event='send_started'
           AND metadata ? 'message_send_rule_facts' ORDER BY id DESC LIMIT 1"""),
           {"d": int(row["draft_id"]), "e": int(row["event_row_id"])}).mappings().first()
        if not found:
            return None, None
        source = dict(row, **dict(found))
    metadata = source.get("metadata") or {}
    final = metadata.get("message_send_rule_facts") if isinstance(metadata, dict) else None
    valid = (_valid(final, source, "app.mop_core:do_send", body=True, inbound=True)
             and final.get("actor_id") == str(source.get("actor_id") or "")
             and final.get("channel") == str(source.get("channel") or ""))
    return (final, int(source["event_row_id"])) if valid else (None, None)


def _trigger_facts(db, row, event):
    """The canonical trigger/draft uniqueness check is local, not Avito absence."""
    from sqlalchemy import text
    inbound = str(row.get("avito_message_id") or "")
    prefix = "system:" + event + ":"
    if inbound.startswith(prefix) and inbound[len(prefix):]:
        raw = inbound[len(prefix):]
        ids = (inbound, raw)
        source = db.execute(text("""SELECT msg_type,text FROM messenger_messages
           WHERE account_id=:a AND avito_chat_id=:c AND avito_message_id=:m
           ORDER BY id DESC LIMIT 2"""), {"a": row["account_id"], "c": row["avito_chat_id"], "m": raw}).mappings().all()
        if len(source) != 1 or source[0].get("msg_type") != "system":
            return None
        # Existing classifier is pure and performs no provider calls.
        from app.api.messenger import _system_proactive_event
        if _system_proactive_event(source[0].get("text")) != event:
            return None
    elif event == "empty_chat" and inbound == "empty:" + str(row.get("avito_chat_id")):
        ids = (inbound, inbound)
    else:
        return None
    drafts = db.execute(text("""SELECT id,avito_chat_id,avito_message_id FROM mop_drafts
       WHERE account_id=:a AND avito_message_id IN (:i,:raw) ORDER BY id LIMIT 3"""),
       {"a": row["account_id"], "i": ids[0], "raw": ids[1]}).mappings().all()
    matches = (len(drafts) == 1 and int(drafts[0]["id"]) == int(row["draft_id"])
               and str(drafts[0]["avito_chat_id"]) == str(row["avito_chat_id"]))
    return {"canonical_trigger": event, "source_inbound_id": ids[1], "draft_count": len(drafts),
            "canonical_unique_draft": matches, "external_absence_proven": False}


def evaluate_message_rule_facts(db, row):
    """Produce account/event-scoped local L3 decisions, without writes or I/O.

    Predicate True means this local canonical predicate was evaluated; the
    caller MUST NOT promote it to L5/L6 or a whole-rule PASS. Applicable source
    branches with missing frozen facts are explicit NOT_PROVEN.
    """
    out = {}
    event, metadata = str(row.get("event") or ""), row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    generating = (event == "ai_generated" or (event == "handed_to_human"
                  and isinstance(metadata.get("message_rule_facts"), dict)))
    send_event = event in {"send_started", "sent", "send_recovered", "send_failed"}
    gen, gate, generation_id = None, None, None
    if generating:
        candidate = metadata.get("message_rule_facts")
        if _valid(candidate, row, "app.api.messenger:generate_ai_draft_reply._finalize", body=True):
            gen = candidate
        candidate = metadata.get("message_eligibility_facts")
        if _valid(candidate, row, "app.api.messenger:_mop_incoming", inbound=True):
            gate = candidate
    elif send_event:
        gen, gate, generation_id = _generation_event(db, row)
    if generating or send_event:
        for short in ("greeting", "first_turn_identity"):
            key = "rule.messages." + short
            if gen is None or gen.get("first_manager_turn") is not True:
                out[key] = _decision("NOT_PROVEN", None, "FIRST_TURN_SNAPSHOT_NOT_PROVEN", row,
                                     evaluation_due_applicable=False)
            elif short == "greeting":
                if gen.get("configured_intro_present") is not True:
                    out[key] = _decision("NOT_PROVEN", None, "CONFIGURED_INTRO_NOT_PROVEN", row)
                else:
                    value = gen.get("configured_intro_prefix")
                    out[key] = _decision("EVALUATED" if type(value) is bool else "NOT_PROVEN", value if type(value) is bool else None,
                                         "CANONICAL_FIRST_TURN_INTRO_PREFIX", row,
                                         configured_intro_hash=gen.get("configured_intro_hash"), generation_event_id=generation_id)
            else:
                violations = gen.get("identity_violations")
                out[key] = _decision("EVALUATED" if isinstance(violations, list) else "NOT_PROVEN",
                                     not violations if isinstance(violations, list) else None,
                                     "CANONICAL_IDENTITY_POLICY" if isinstance(violations, list) else "IDENTITY_POLICY_NOT_PROVEN",
                                     row, identity_violations=violations, generation_event_id=generation_id)
    proactive = (gate or {}).get("proactive_event") or (gen or {}).get("proactive_event")
    inbound = str(row.get("avito_message_id") or "")
    for short in ("empty_chat", "viewed_phone"):
        if proactive == short or inbound.startswith("system:" + short + ":") or (short == "empty_chat" and inbound.startswith("empty:")):
            facts = _trigger_facts(db, row, short)
            if short == "empty_chat" and inbound.startswith("empty:") and proactive != "empty_chat":
                facts = None
            out["rule.messages." + short] = _decision(
                "EVALUATED" if facts is not None else "NOT_PROVEN",
                facts["canonical_unique_draft"] if facts else None,
                "CANONICAL_LOCAL_TRIGGER_UNIQUENESS" if facts else "CANONICAL_TRIGGER_NOT_PROVEN", row, **(facts or {}))
    if gen and gen.get("generation_model") == "phone_handoff_rule_v1":
        received, handoff = gen.get("phone_received"), gen.get("human_handoff")
        known = type(received) is bool and type(handoff) is bool
        out["rule.messages.phone_received"] = _decision(
            "EVALUATED" if known else "NOT_PROVEN", received and handoff if known else None,
            "ACCOUNT_PHONE_HANDOFF_POLICY_SELECTED" if known else "PHONE_HANDOFF_FACTS_NOT_PROVEN", row,
            phone_received=received, human_handoff=handoff, crm_postcondition="NOT_PROVEN",
            policy_source="MOP_PHONE_HANDOFF_RULE_V1")
    elif generating or send_event:
        out["rule.messages.phone_received"] = _decision("NOT_PROVEN", None, "PHONE_POLICY_APPLICABILITY_NOT_PROVEN", row,
                                                        evaluation_due_applicable=False)
    if (event == "closed_no_reply" and row.get("actor_id") == "job_seeker_guard"
            and metadata.get("policy_version") == "MOP_JOB_SEEKER_NO_SALES_V1"):
        safe = metadata.get("external_action") is False and row.get("to_status") == "no_reply_required"
        out["rule.messages.job_seeker"] = _decision("EVALUATED", safe,
            "CANONICAL_NON_RECRUITMENT_JOB_SEEKER_BLOCK", row, policy_source=metadata["policy_version"],
            external_action=metadata.get("external_action"), crm_exclusion_proven=False)
    elif event == "closed_no_reply" and row.get("actor_id") == "job_seeker_guard":
        out["rule.messages.job_seeker"] = _decision("NOT_PROVEN", None, "JOB_SEEKER_POLICY_NOT_PROVEN", row)
    if generating or send_event:
        checks = (gate or {}).get("checks")
        names = ("module_allowed", "entitlement_active", "binding_enabled", "owner_enabled", "schedule_active", "auto_send", "ai_enabled")
        known = isinstance(checks, dict) and all(type(checks.get(k)) is bool for k in names)
        final, send_event_id = _send_event(db, row) if send_event else (None, None)
        final_valid = final is not None
        automatic = final_valid and (final.get("channel") == "mop_autopilot" or final.get("actor_id") == "account_autopilot")
        if known and checks["auto_send"] is False:
            out[KEYS[-1]] = _decision("EVALUATED", False, "ACCOUNT_AUTOSEND_DISABLED", row, initial_checks=checks)
        elif known and not all(checks.values()):
            out[KEYS[-1]] = _decision("EVALUATED", False, "INITIAL_ELIGIBILITY_DENIED", row, initial_checks=checks)
        elif known and final_valid and automatic:
            final_known = type(final.get("module_allowed")) is bool and type(final.get("takeover_active")) is bool
            decision = final.get("readback_decision")
            if final_known and decision in {"safe_to_send", "answered_externally", "superseded", "already_sent"}:
                allowed = final["module_allowed"] is True and final["takeover_active"] is False and decision == "safe_to_send"
                # A recovered attempt may follow an older generation while the
                # account changed autosend/schedule/binding. do_send does not
                # re-evaluate those settings. Observe what it actually checked,
                # never turn older initial settings into current full eligibility.
                out[KEYS[-1]] = _decision("NOT_PROVEN" if allowed else "EVALUATED", None if allowed else False,
                                         "AUTOSEND_SETTINGS_AT_SEND_NOT_PROVEN" if allowed else "CANONICAL_AUTOSEND_PRECLAIM_DENIED", row,
                                         initial_checks=checks, final_checks=final, generation_event_id=generation_id,
                                         send_started_event_id=send_event_id, initial_eligibility_passed=True,
                                         final_preclaim_checks_passed=allowed, current_settings_at_send_proven=False)
            else:
                out[KEYS[-1]] = _decision("NOT_PROVEN", None, "FINAL_READBACK_ELIGIBILITY_NOT_PROVEN", row, initial_checks=checks)
        else:
            out[KEYS[-1]] = _decision("NOT_PROVEN", None, "AUTOSEND_FINAL_GUARDS_NOT_PROVEN" if known else "INITIAL_ELIGIBILITY_NOT_PROVEN", row,
                                     initial_checks=checks if known else None)
        # A generation result is not an automatic-send attempt. Unknown first
        # turn/phone applicability above likewise must not create a false due
        # incident. Keep uncertainty visible separately from due applicability.
        out[KEYS[-1]]["evaluation_due_applicable"] = bool(send_event and (
            automatic or row.get("channel") == "mop_autopilot" or row.get("actor_id") == "account_autopilot"))
    return out
