import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import pathlib
import os
import sys
import types

import pytest

source = os.environ.get("BORIS_MESSAGE_RULE_FACTS_SOURCE")
local = pathlib.Path(__file__).with_name("control_message_rule_facts.py")
if source or local.exists():
    spec = importlib.util.spec_from_file_location("facts", source or local)
    facts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(facts)
else:
    from app.services import control_message_rule_facts as facts


class Result:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows


class DB:
    def __init__(self, generation=None, drafts=None, source=None, send=None):
        self.generation = generation
        self.drafts = drafts or []
        self.source = source or []
        self.send = send
        self.queries = []
    def execute(self, sql, params):
        assert str(sql).lstrip().startswith("SELECT"), "must be read-only"
        self.queries.append((str(sql), params))
        if "event='send_started'" in str(sql): return Result([self.send] if self.send else [])
        if "FROM mop_draft_events" in str(sql): return Result([self.generation] if self.generation else [])
        if "FROM messenger_messages" in str(sql): return Result(self.source)
        return Result(self.drafts)


@pytest.fixture(autouse=True)
def dependencies(monkeypatch):
    # Pure evaluator SQL binding check; full SQL execution belongs to runtime's
    # real PG integration suite. No connection/provider/import of live app.
    monkeypatch.setitem(sys.modules, "sqlalchemy", types.SimpleNamespace(text=lambda value: value))
    monkeypatch.setitem(sys.modules, "app", types.ModuleType("app"))
    monkeypatch.setitem(sys.modules, "app.api", types.ModuleType("app.api"))
    monkeypatch.setitem(sys.modules, "app.api.messenger", types.SimpleNamespace(
        _system_proactive_event=lambda value: {"canonical-empty": "empty_chat", "canonical-phone": "viewed_phone"}.get(value)))


def row(event="ai_generated"):
    gen = facts.capture_generation_rule_facts(account_id="A", chat_id="C", reply_text="Здравствуйте! Да.",
        first_manager_turn=True, configured_intro="Здравствуйте!", identity_violations=[],
        proactive_event=None, generation_model="test", analysis={})
    gate = facts.capture_eligibility_rule_facts(account_id="A", chat_id="C", inbound_id="I",
        module_allowed=True, entitlement_active=True, binding_enabled=True, owner_enabled=True,
        schedule_active=True, auto_send=True, ai_enabled=True, proactive_event=None)
    return dict(event_row_id=10, draft_id=5, event=event, at=datetime.now(timezone.utc),
                account_id="A", avito_chat_id="C", avito_message_id="I",
                reply_text="Здравствуйте! Да.", channel="mop_autopilot", actor_id="account_autopilot",
                metadata={"message_rule_facts": gen, "message_eligibility_facts": gate})


def get(r, key, db=None):
    return facts.evaluate_message_rule_facts(db or DB(), r)["rule.messages." + key]


def test_configured_greeting_and_identity_are_local_l3_only():
    r = row()
    for key in ("greeting", "first_turn_identity"):
        decision = get(r, key)
        assert decision["evaluation_status"] == "EVALUATED"
        assert decision["predicate_result"] is True
        assert decision["facts"]["rule_pass"] is False


@pytest.mark.parametrize("field,value", [("account_id", "OTHER"), ("chat_id", "OTHER"),
    ("contract", "v2"), ("source", "invented"), ("reply_text_hash", "x"),
    ("observed_at", "invalid"), ("observed_at", "2026-01-01T00:00:00"),
    ("observed_at", "2999-01-01T00:00:00Z")])
def test_generation_receipt_invalid_fails_unknown(field, value):
    r = row(); r["metadata"]["message_rule_facts"][field] = value
    assert get(r, "greeting")["evaluation_status"] == "NOT_PROVEN"


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_first_turn_false_or_unknown_never_claims_not_applicable(value):
    r = row(); r["metadata"]["message_rule_facts"]["first_manager_turn"] = value
    assert get(r, "greeting")["predicate_result"] is None


def test_pg_transaction_timestamp_may_precede_actual_capture():
    r = row(); r["at"] -= timedelta(seconds=20)
    assert get(r, "greeting")["predicate_result"] is True
    assert get(r, "greeting")["source_observed_at"] == r["at"].isoformat()


def test_greeting_missing_intro_unknown_and_mismatch_negative():
    r = row(); r["metadata"]["message_rule_facts"]["configured_intro_present"] = False
    assert get(r, "greeting")["predicate_result"] is None
    r = row(); r["metadata"]["message_rule_facts"]["configured_intro_prefix"] = False
    assert get(r, "greeting")["predicate_result"] is False


@pytest.mark.parametrize("violation", ["internal_or_invented_identity", "automation_disclosure"])
def test_identity_canonical_violation_is_negative(violation):
    r = row(); r["metadata"]["message_rule_facts"]["identity_violations"] = [violation]
    assert get(r, "first_turn_identity")["predicate_result"] is False


def test_missing_identity_check_unknown():
    r = row(); r["metadata"]["message_rule_facts"]["identity_violations"] = None
    assert get(r, "first_turn_identity")["evaluation_status"] == "NOT_PROVEN"


def test_no_event_has_no_evaluation():
    assert facts.evaluate_message_rule_facts(DB(), {"event": "unknown"}) == {}


@pytest.mark.parametrize("event,source", [("empty_chat", "canonical-empty"), ("viewed_phone", "canonical-phone")])
def test_proactive_exact_tenant_bound_source_and_unique_trigger(event, source):
    r = row("draft_created"); r["avito_message_id"] = "system:" + event + ":RAW"
    db = DB(drafts=[{"id": 5, "avito_chat_id": "C", "avito_message_id": r["avito_message_id"]}], source=[{"msg_type": "system", "text": source}])
    result = get(r, event, db)
    assert result["predicate_result"] is True
    assert result["facts"]["external_absence_proven"] is False
    for sql, args in db.queries:
        assert args["a"] == "A"


@pytest.mark.parametrize("case", ["missing", "wrong_type", "wrong_text", "ambiguous"])
def test_proactive_unproven_provider_source(case):
    r = row("draft_created"); r["avito_message_id"] = "system:viewed_phone:RAW"
    src = {"msg_type": "system", "text": "canonical-phone"}
    sources = {"missing": [], "wrong_type": [dict(src, msg_type="text")],
               "wrong_text": [dict(src, text="canonical-empty")], "ambiguous": [src, src]}[case]
    assert get(r, "viewed_phone", DB(source=sources))["predicate_result"] is None


def test_proactive_duplicate_canonical_and_legacy_drafts_is_negative():
    r = row("draft_created"); r["avito_message_id"] = "system:viewed_phone:RAW"
    db = DB(source=[{"msg_type": "system", "text": "canonical-phone"}], drafts=[
        {"id": 5, "avito_chat_id": "C"}, {"id": 6, "avito_chat_id": "C"}])
    assert get(r, "viewed_phone", db)["predicate_result"] is False


def test_old_empty_chat_without_branch_snapshot_unknown():
    r = row("draft_created"); r["avito_message_id"] = "empty:C"
    db = DB(drafts=[{"id": 5, "avito_chat_id": "C"}])
    assert get(r, "empty_chat", db)["predicate_result"] is None


def test_phone_policy_per_account_not_inferred_from_model_analysis():
    r = row(); gen = r["metadata"]["message_rule_facts"]
    gen.update(phone_received=True, human_handoff=True)
    assert get(r, "phone_received")["predicate_result"] is None
    gen["generation_model"] = "phone_handoff_rule_v1"
    result = get(r, "phone_received")
    assert result["predicate_result"] is True
    assert result["facts"]["crm_postcondition"] == "NOT_PROVEN"
    gen["phone_received"] = "true"
    assert get(r, "phone_received")["predicate_result"] is None


def test_viewed_company_phone_is_not_received_client_phone():
    r = row(); r["metadata"]["message_rule_facts"].update(generation_model="viewed_phone_safe_continuation_v1", phone_received=False)
    assert get(r, "phone_received")["predicate_result"] is None


def test_job_seeker_requires_exact_actor_policy_and_blocked_status():
    r = row("closed_no_reply"); r.update(actor_id="job_seeker_guard", to_status="no_reply_required",
        metadata={"policy_version": "MOP_JOB_SEEKER_NO_SALES_V1", "external_action": False})
    result = get(r, "job_seeker")
    assert result["predicate_result"] is True and result["facts"]["crm_exclusion_proven"] is False
    r["metadata"]["external_action"] = "false"
    assert get(r, "job_seeker")["predicate_result"] is False
    r["metadata"]["policy_version"] = "unknown"
    assert get(r, "job_seeker")["predicate_result"] is None
    r["actor_id"] = "other"
    assert "rule.messages.job_seeker" not in facts.evaluate_message_rule_facts(DB(), r)


def send_row():
    generation = row()
    r = row("send_started")
    r["event_row_id"] = 11
    r["metadata"] = {"message_send_rule_facts": facts.capture_send_rule_facts(
        account_id="A", chat_id="C", inbound_id="I", reply_text=r["reply_text"],
        module_allowed=True, takeover_active=False, readback_decision="safe_to_send",
        actor_id=r["actor_id"], channel=r["channel"])}
    return r, DB(generation=generation)


def test_initial_gate_does_not_prove_final_send_eligibility():
    assert get(row(), "autosend_eligibility")["evaluation_status"] == "NOT_PROVEN"


def test_frozen_initial_and_final_guards_do_not_claim_current_settings():
    r, db = send_row()
    result = get(r, "autosend_eligibility", db)
    assert result["predicate_result"] is None
    assert result["evaluation_status"] == "NOT_PROVEN"
    assert result["reason"] == "AUTOSEND_SETTINGS_AT_SEND_NOT_PROVEN"
    assert result["facts"]["initial_eligibility_passed"] is True
    assert result["facts"]["final_preclaim_checks_passed"] is True
    assert result["facts"]["generation_event_id"] == 10
    assert result["facts"]["rule_pass"] is False


@pytest.mark.parametrize("field", ["module_allowed", "entitlement_active", "binding_enabled", "owner_enabled", "schedule_active", "auto_send", "ai_enabled"])
def test_initial_false_gate_blocks_and_never_enables_autosend(field):
    r, db = send_row(); db.generation["metadata"]["message_eligibility_facts"]["checks"][field] = False
    assert get(r, "autosend_eligibility", db)["predicate_result"] is False


@pytest.mark.parametrize("value", ["true", 1, None])
def test_initial_gate_strict_bool(value):
    r, db = send_row(); db.generation["metadata"]["message_eligibility_facts"]["checks"]["owner_enabled"] = value
    assert get(r, "autosend_eligibility", db)["predicate_result"] is None


def test_unknown_provider_readback_is_not_eligibility_pass():
    r, db = send_row(); r["metadata"]["message_send_rule_facts"]["readback_decision"] = "unknown"
    assert get(r, "autosend_eligibility", db)["predicate_result"] is None


def test_changed_reply_does_not_reuse_initial_eligibility():
    r, db = send_row(); r["reply_text"] = "Changed"
    assert get(r, "autosend_eligibility", db)["predicate_result"] is None


def test_legacy_events_unknown_and_no_current_settings_query():
    r = row(); r["metadata"] = {}
    db = DB()
    assert get(r, "autosend_eligibility", db)["predicate_result"] is None
    assert not db.queries


def test_evaluator_does_not_mutate_input_or_refresh_receipt():
    r = row(); before = copy.deepcopy(r)
    assert facts.evaluate_message_rule_facts(DB(), r) == facts.evaluate_message_rule_facts(DB(), r)
    assert r == before


def test_final_actor_and_channel_must_match_actual_claim_event():
    for key in ("actor_id", "channel"):
        r, db = send_row(); r["metadata"]["message_send_rule_facts"][key] = "other"
        assert get(r, "autosend_eligibility", db)["predicate_result"] is None


def test_unknown_greeting_prefix_is_unknown_not_evaluated():
    r = row(); r["metadata"]["message_rule_facts"]["configured_intro_prefix"] = None
    assert get(r, "greeting")["evaluation_status"] == "NOT_PROVEN"


def test_bad_identity_checker_result_cannot_become_clean():
    value = facts.capture_generation_rule_facts(account_id="A", chat_id="C", reply_text="Hi",
        first_manager_turn=True, configured_intro="Hi", identity_violations="error",
        proactive_event=None, generation_model="test")
    assert value["identity_violations"] is None


@pytest.mark.parametrize("event", ["sent", "send_failed", "send_recovered"])
def test_result_event_preserves_same_draft_preclaim_evaluation(event):
    claim, db = send_row()
    db.send = {key: claim[key] for key in ("event_row_id", "at", "metadata", "actor_id", "channel")}
    result = dict(claim, event=event, event_row_id=12, metadata={}, actor_id="system-result")
    decision = get(result, "autosend_eligibility", db)
    assert decision["predicate_result"] is None
    assert decision["facts"]["final_preclaim_checks_passed"] is True
    assert decision["facts"]["send_started_event_id"] == 11
    assert all(args.get("d") == 5 and args.get("e") == 12 for sql,args in db.queries)


def test_result_does_not_reuse_other_reply_preclaim_or_invent_missing_receipt():
    claim, db = send_row()
    result = dict(claim, event="sent", event_row_id=12, metadata={})
    assert get(result, "autosend_eligibility", db)["predicate_result"] is None
    db.send = {key: claim[key] for key in ("event_row_id", "at", "metadata", "actor_id", "channel")}
    db.send["metadata"]["message_send_rule_facts"]["reply_text_hash"] = "different-body"
    assert get(result, "autosend_eligibility", db)["predicate_result"] is None


def test_recovered_attempt_old_initial_settings_never_become_current_eligibility():
    r, db = send_row()
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db.generation["at"] = old
    for key in ("message_rule_facts", "message_eligibility_facts"):
        db.generation["metadata"][key]["observed_at"] = old.isoformat()
    result = get(r, "autosend_eligibility", db)
    assert result["predicate_result"] is None
    assert result["facts"]["current_settings_at_send_proven"] is False


def test_direct_human_handoff_does_not_invent_generation_applicability():
    r = row("handed_to_human"); r.update(reply_text=None, metadata={"policy_version": "MOP_CHAT_HANDOFF_LOCK_V1"})
    assert facts.evaluate_message_rule_facts(DB(), r) == {}


def test_unknown_first_turn_or_phone_applicability_stays_visible_without_due_incident():
    r = row(); r["metadata"]["message_rule_facts"]["first_manager_turn"] = False
    for key in ("greeting", "first_turn_identity", "phone_received"):
        result = get(r, key)
        assert result["evaluation_status"] == "NOT_PROVEN"
        assert result["evaluation_due_applicable"] is False


def test_auto_send_due_starts_with_actual_automatic_attempt_not_generation():
    assert get(row(), "autosend_eligibility")["evaluation_due_applicable"] is False
    r, db = send_row()
    assert get(r, "autosend_eligibility", db)["evaluation_due_applicable"] is True
    r["actor_id"] = "human"; r["channel"] = "telegram"
    assert get(r, "autosend_eligibility", db)["evaluation_due_applicable"] is False
