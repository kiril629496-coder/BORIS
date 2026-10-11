"""Real PostgreSQL contract tests. Run only against the private test cluster.

The complete production ORM and candidate modules are imported. The reliability
audit sink is isolated; no SessionLocal, provider, credentials or application
startup is imported. The database URL and source tree are explicit test inputs.
"""
import copy
import ast
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import types

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

if not os.environ.get("BORIS_RULE_TEST_DATABASE_URL"):
    pytest.skip("Run through the private PostgreSQL wrapper", allow_module_level=True)
ROOT = Path(os.environ["BORIS_RULE_TEST_BACKEND"])
URL = os.environ["BORIS_RULE_TEST_DATABASE_URL"]
assert "host=/tmp/boris-rule-pg-" in URL, "Only the disposable Unix-socket cluster is allowed"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kit():
    saved = {key: value for key, value in sys.modules.items() if key == "app" or key.startswith("app.")}
    for key in list(saved):
        del sys.modules[key]
    for name in ("app", "app.db", "app.models", "app.services"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    base_module = types.ModuleType("app.db.base")
    base_module.Base = declarative_base()
    sys.modules[base_module.__name__] = base_module
    reliability = types.ModuleType("app.services.reliability")
    reliability.get_trace_id = lambda: "private-pg-contract-test"
    reliability.json_safe = lambda v: json.loads(json.dumps(v, default=str))
    reliability.record_event = lambda *args, **kwargs: None
    sys.modules[reliability.__name__] = reliability
    errors = []
    observable = types.ModuleType("app.services.exception_observability")
    observable.observe_suppressed = lambda *args, **kwargs: errors.append(type(args[1]).__name__)
    sys.modules[observable.__name__] = observable
    crm_package = types.ModuleType("app.crm"); crm_package.__path__ = []
    sys.modules[crm_package.__name__] = crm_package
    bridge = types.ModuleType("app.crm.bridge")
    from fastapi import HTTPException
    bridge.__dict__.update(text=text, HTTPException=HTTPException)
    parsed = ast.parse((ROOT / "app/crm/bridge.py").read_text())
    for node in parsed.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"_columns", "_assert_account_access"}:
            exec(compile(ast.Module(body=[node],type_ignores=[]), "canonical_crm_acl", "exec"), bridge.__dict__)
    sys.modules[bridge.__name__] = bridge
    model = load("app.models.control_plane", "app/models/control_plane.py")
    storage = load("app.models.storage", "app/models/storage.py")
    core = load("app.services.control_plane", "app/services/control_plane.py")
    message_facts = load("app.services.control_message_rule_facts", "app/services/control_message_rule_facts.py")
    feed_facts = load("app.services.control_feed_rule_facts", "app/services/control_feed_rule_facts.py")
    runtime = load("app.services.control_rule_runtime", "app/services/control_rule_runtime.py")
    budget = load("app.services.control_owner_budget_rule_facts", "app/services/control_owner_budget_rule_facts.py")
    mop = load("app.mop_core", "app/mop_core.py")
    crm_service = load('app.services.crm_service','app/services/crm_service.py')
    engine = create_engine(URL, pool_size=8, max_overflow=0)
    base_module.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE mop_drafts (
          id bigserial PRIMARY KEY, account_id text NOT NULL, avito_chat_id text NOT NULL,
          avito_message_id text NOT NULL, incoming_text text, item_id text,item_title text,client_name text,
          reply_text text,reply_author text,ai_summary text, status text NOT NULL DEFAULT 'new',
          outgoing_text_hash text, sent_at timestamptz,send_error text,locked_at timestamptz,locked_by text,
          regen_count int DEFAULT 0,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),
          UNIQUE(account_id,avito_message_id))"""))
        connection.execute(text("""CREATE TABLE mop_draft_events (
          id bigserial PRIMARY KEY,draft_id bigint NOT NULL REFERENCES mop_drafts(id),event text NOT NULL,
          from_status text,to_status text,channel text,actor_type text,actor_id text,payload text,
          metadata jsonb,at timestamptz DEFAULT now())"""))
        connection.execute(text("""CREATE TABLE messenger_messages (
          id bigserial PRIMARY KEY,account_id text,avito_chat_id text,avito_message_id text,
          text text,direction text,stored_at timestamp DEFAULT now(),avito_created_at bigint,
          UNIQUE(account_id,avito_message_id))"""))
        for ddl in (
            "CREATE TABLE campaigns(id bigint PRIMARY KEY,account_id text NOT NULL)",
            "CREATE TABLE users(id bigint PRIMARY KEY,role text)",
            "CREATE TABLE accounts(account_id text PRIMARY KEY,owner_user_id bigint,client_goal text,client_goal_text text)",
            "CREATE TABLE user_account_access(user_id bigint,account_id text,can_view boolean)",
            "CREATE TABLE boris_crm_conversations(id bigint PRIMARY KEY,owner_user_id bigint,deal_id bigint,channel text,account_id text,external_chat_id text)",
            "CREATE TABLE boris_crm_messages(id bigint PRIMARY KEY,owner_user_id bigint,conversation_id bigint,external_message_id text,direction text,body text)",
            "CREATE TABLE boris_crm_deals(id bigint PRIMARY KEY,owner_user_id bigint,responsible_user_id bigint,next_action_at timestamptz,contact_id bigint,avito_account_id text,avito_chat_id text,status text DEFAULT 'open')",
            "CREATE TABLE boris_crm_tasks(id bigint PRIMARY KEY,owner_user_id bigint,deal_id bigint,contact_id bigint,assigned_user_id bigint,due_at timestamptz,status text,source text,description text,created_at timestamptz DEFAULT now(),completed_at timestamptz)",
            "CREATE TABLE boris_crm_activities(id bigserial PRIMARY KEY,owner_user_id bigint,deal_id bigint,contact_id bigint,activity_type text,source text,source_ref text,metadata_json jsonb,created_at timestamptz DEFAULT now())",
            "CREATE TABLE messenger_leads(id bigserial PRIMARY KEY,account_id text,avito_chat_id text,assigned_user_id bigint)",
            "CREATE TABLE crm_tasks(id bigserial PRIMARY KEY,account_id text,avito_chat_id text,due_date date,due_time text,task_type text,title text,comment text,created_by text,status text DEFAULT 'planned',done_at timestamptz,done_comment text,updated_at timestamptz DEFAULT now())",
        ):
            connection.execute(text(ddl))
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    yield types.SimpleNamespace(model=model, core=core, runtime=runtime, budget=budget,Storage=storage.Storage, mop=mop, crm_service=crm_service,engine=engine, Session=Session, errors=errors)
    engine.dispose()
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    sys.modules.update(saved)


@pytest.fixture
def db(kit):
    fixture = json.loads((Path(__file__).parent / "registered-bindings.fixture.json").read_text())
    with kit.engine.begin() as connection:
        connection.execute(text("TRUNCATE control_requirements,control_module_contracts,mop_drafts,messenger_messages,accounts,users,user_account_access,boris_crm_conversations,boris_crm_messages,boris_crm_deals,boris_crm_tasks,boris_crm_activities,messenger_leads,crm_tasks,campaigns,storage RESTART IDENTITY CASCADE"))
    with kit.Session() as session:
        for source in fixture["module_contracts"]:
            data = {k: source[k] for k in ("module", "enabled", "contract_json", "capabilities_json", "verification_stale_seconds") if k in source}
            session.add(kit.model.ControlModuleContract(**data))
        session.flush()
        for source in fixture["action_owners"]:
            data = {k: v for k, v in source.items() if k in kit.model.ControlActionOwner.__table__.columns and k not in {"created_at", "updated_at"}}
            session.add(kit.model.ControlActionOwner(**data))
        for source in fixture["requirements"]:
            data = {k: v for k, v in source.items() if k in kit.model.ControlRequirement.__table__.columns and k not in {"created_at", "updated_at", "last_applied_at", "last_verified_at"}}
            session.add(kit.model.ControlRequirement(**data))
            session.flush()
            session.add(kit.model.ControlRequirementVersion(requirement_id=data["id"], version=data["version"], snapshot_json=data))
        session.commit()
        kit.errors.clear()
        yield session
        session.rollback()


def draft(kit, db, account="account_a", mid="in-1", *, old=False):
    did, created = kit.mop.create_draft(db, account, "chat-1", mid, "Нужна перегородка")
    assert created
    if old:
        # For due tests capture at the actual old fixture intake, not by editing
        # the already captured versioned context or a production timestamp.
        db.execute(text("DELETE FROM control_executions WHERE account_id=:a"), {"a": account})
        db.execute(text("DELETE FROM mop_draft_events WHERE draft_id=:d"), {"d": did})
        db.execute(text("UPDATE mop_drafts SET created_at=now()-interval '901 seconds' WHERE id=:d"), {"d": did})
        kit.mop.log_event(db, did, "draft_created", to_status="new", actor_type="system")
        db.commit()
    return did


def source_event(kit, db, did, event="ai_generated", meta=None):
    eid = kit.mop.log_event(db, did, event, to_status="draft_ready" if event == "ai_generated" else None,
                             actor_type="system", meta=meta)
    db.commit()
    return eid


def first_execution(kit, db, account="account_a"):
    return db.query(kit.model.ControlExecution).filter(
        kit.model.ControlExecution.account_id == account,
        kit.model.ControlExecution.plan_json["requirement_key"].astext == "rule.messages.first_response",
    ).one()


def make_sent(kit, db, account="account_a", *, local_message=True, other_account=None, future=False, provider=True):
    did = draft(kit, db, account)
    body = "Уточню стоимость и вернусь с точным ответом."
    db.execute(text("UPDATE mop_drafts SET status='sending',reply_text=:body WHERE id=:d"), {"body": body, "d": did})
    row = {"account_id": account, "avito_chat_id": "chat-1"}
    proof = kit.mop._send_receipt(row, {"status": "ok", "avito_message": {"id": "out-1", "content": {"text": body}}}, body) if provider else None
    kit.mop.mark_sent(db, did, body, provider_receipt=proof)
    eid = db.execute(text("SELECT max(id) FROM mop_draft_events WHERE draft_id=:d AND event='sent'"), {"d": did}).scalar_one()
    if local_message:
        db.execute(text("""INSERT INTO messenger_messages(account_id,avito_chat_id,avito_message_id,text,direction,stored_at)
          VALUES(:a,'chat-1','out-1',:body,'outgoing',:at)"""),
          {"a": other_account or account, "body": body, "at": datetime.now(timezone.utc) + timedelta(days=1) if future else datetime.now(timezone.utc) - timedelta(seconds=1)})
    db.commit()
    return did, eid


def provider_get(kit, db, account="account_a", **kwargs):
    return kit.runtime.record_messenger_get_readback(db, account, "chat-1", {"messages": [
        {"id": "out-1", "direction": "outgoing", "content": {"text": "Уточню стоимость и вернусь с точным ответом."}}
    ]}, **kwargs)


def test_new_event_captures_real_uuid_version_and_due(kit, db):
    draft(kit, db)
    ex = first_execution(kit, db)
    assert ex.requirement_id == "dddfcbcc-6c9f-473a-a3ba-b60e1e19fb9e"
    assert ex.plan_json["requirement_version"] == 1
    assert ex.plan_json["binding"]["verification_stale_seconds"] == 900
    assert ex.actual_state_json["evaluation_status"] == "PENDING"
    assert not kit.errors
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level.in_(["L5", "L6"])).count() == 0


def test_repeat_inbound_has_one_draft_and_one_execution(kit, db):
    did = draft(kit, db)
    assert kit.mop.create_draft(db, "account_a", "chat-1", "in-1", "Нужна перегородка") == (did, False)
    assert db.query(kit.model.ControlExecution).count() == 1


def test_real_decision_replay_is_idempotent(kit, db):
    eid = source_event(kit, db, draft(kit, db))
    before = db.query(kit.model.ControlEvidence).count()
    for _ in range(3):
        kit.runtime.record_mop_event(db, eid)
    assert db.query(kit.model.ControlEvidence).count() == before
    assert first_execution(kit, db).actual_state_json["evaluation_status"] == "EVALUATED"
    assert first_execution(kit, db).status != "pass"


def test_http_ok_without_provider_identity_never_gives_l5(kit, db):
    did, eid = make_sent(kit, db, provider=False)
    kit.runtime.record_mop_event(db, eid)
    assert first_execution(kit, db).status == "waiting_external"
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level == "L5").count() == 0


def test_provider_receipt_waits_for_exact_local_readback(kit, db):
    did, eid = make_sent(kit, db, local_message=False)
    assert first_execution(kit, db).status == "waiting_external"
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level == "L5").count() == 0


def test_l5_requires_exact_account_chat_id_and_hash(kit, db):
    did, eid = make_sent(kit, db)
    result = provider_get(kit, db)
    assert result["verified"] == 1
    ex = first_execution(kit, db)
    assert ex.status == "waiting_external" and ex.actual_state_json["delivery_proven"] is True
    assert ex.actual_state_json["postcondition_status"] == "DELIVERY_VERIFIED"
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level == "L5").count() == 1
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level == "L6").count() == 0
    # Delivery does not magically prove the other dialogue predicates.
    others = db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.id != ex.id).all()
    assert all(x.status != "pass" for x in others)
    assert db.query(kit.model.ControlRequirement).filter_by(id=ex.requirement_id).one().last_verified_at is None


@pytest.mark.parametrize("other_account,future", [("account_b", False), (None, True)])
def test_foreign_or_future_readback_does_not_pass(kit, db, other_account, future):
    did, eid = make_sent(kit, db, other_account=other_account, future=future)
    kit.runtime.record_mop_event(db, eid)
    assert first_execution(kit, db).status == "waiting_external"


def test_corrupted_text_does_not_pass(kit, db):
    did, eid = make_sent(kit, db)
    db.execute(text("UPDATE messenger_messages SET text='другой ответ'"))
    kit.runtime.record_mop_event(db, eid)
    assert first_execution(kit, db).status == "waiting_external"


@pytest.mark.parametrize("event,status,metadata", [
    ("closed_no_reply", "blocked", {"policy_version": "MOP_DETERMINISTIC_NO_REPLY_V1", "reason": "gratitude_ack"}),
    ("handed_to_human", "waiting_owner", {"policy_version": "MOP_HUMAN_REQUEST_V1", "reason": "client_requested_human"}),
    ("provider_deferred", "waiting_external", {"policy_version": "MOP_PROVIDER_DEFERRED_WAIT_V2", "reason": "ai_provider_or_budget_deferred"}),
])
def test_no_action_human_and_provider_decisions_remain_distinct(kit, db, event, status, metadata):
    did = draft(kit, db)
    eid = source_event(kit, db, did, event, metadata)
    ex = first_execution(kit, db)
    assert ex.status == status and ex.actual_state_json["evaluation_status"] == "EVALUATED"
    assert ex.actual_state_json["delivery_proven"] is False
    assert kit.runtime.check_rule_due(db, now=datetime.now(timezone.utc) + timedelta(days=1))["opened"] == 0


def test_due_once_and_real_decision_resolves_without_action(kit, db):
    did = draft(kit, db, old=True)
    assert kit.runtime.check_rule_due(db)["opened"] == 1
    assert kit.runtime.check_rule_due(db)["opened"] == 0
    source_event(kit, db, did, "provider_deferred", {"policy_version": "MOP_PROVIDER_DEFERRED_WAIT_V2"})
    assert db.query(kit.model.ControlIncident).filter_by(incident_type="RULE_NOT_EVALUATED", state="open").count() == 0
    assert first_execution(kit, db).status == "waiting_external"


def test_future_due_is_not_overdue(kit, db):
    draft(kit, db)
    assert kit.runtime.check_rule_due(db)["opened"] == 0


def test_same_event_id_is_tenant_scoped(kit, db):
    for account in ("account_a", "account_b"):
        source_event(kit, db, draft(kit, db, account))
    executions = db.query(kit.model.ControlExecution).all()
    assert len({x.idempotency_key for x in executions}) == len(executions)
    assert len([x for x in executions if x.plan_json["requirement_key"]=="rule.messages.first_response"])==2
    assert {x.account_id for x in executions}=={"account_a","account_b"}


def test_cross_tenant_context_fails_before_ledger_writes(kit, db):
    did = draft(kit, db)
    ctx = db.execute(text("SELECT metadata->'control_rule_runtime' FROM mop_draft_events WHERE draft_id=:d"), {"d": did}).scalar_one()
    ctx["account_id"] = "account_b"
    eid = db.execute(text("""INSERT INTO mop_draft_events(draft_id,event,metadata) VALUES(:d,'ai_generated',CAST(:m AS jsonb)) RETURNING id"""),
                     {"d": did, "m": json.dumps({"control_rule_runtime": ctx})}).scalar_one()
    before = db.query(kit.model.ControlEvidence).count()
    with pytest.raises(ValueError, match="TENANT"):
        kit.runtime.record_mop_event(db, eid)
    assert db.query(kit.model.ControlEvidence).count() == before


def test_old_version_is_history_and_does_not_verify_new_rule(kit, db):
    did = draft(kit, db)
    req = db.query(kit.model.ControlRequirement).filter_by(key="rule.messages.first_response").one()
    req.version = 2
    db.commit()
    source_event(kit, db, did)
    ex = first_execution(kit, db)
    assert ex.plan_json["requirement_version"] == 1
    out = kit.runtime.account_rule_projection(db, "account_a", since=datetime.now(timezone.utc)-timedelta(days=1), until=datetime.now(timezone.utc))
    assert next(r for r in out["rules"] if r["requirement_id"]==req.id)["status"] == "HISTORICAL_VERSION"
    assert req.last_verified_at is None
    draft(kit, db, mid="in-2")
    versions = {x.plan_json["requirement_version"] for x in db.query(kit.model.ControlExecution).all()}
    assert versions == {1, 2}


def test_unknown_version_is_not_silently_assigned_to_current(kit, db):
    did = draft(kit, db)
    req = db.query(kit.model.ControlRequirement).filter_by(key="rule.messages.first_response").one()
    req.version = 2
    db.query(kit.model.ControlRequirementVersion).filter_by(requirement_id=req.id).delete()
    db.commit()
    eid = source_event(kit, db, did)
    assert "ValueError" in kit.errors
    assert first_execution(kit, db).actual_state_json["evaluation_status"] == "PENDING"
    with pytest.raises(ValueError, match="VERSION_NOT_PROVEN"):
        kit.runtime.record_mop_event(db, eid)


def test_missing_owner_and_missing_due_fail_closed(kit, db):
    db.query(kit.model.ControlModuleContract).filter_by(module="messages").one().verification_stale_seconds = 0
    db.commit()
    did = draft(kit, db)
    assert db.query(kit.model.ControlExecution).count() == 0
    context = db.execute(text("SELECT metadata->'control_rule_runtime' FROM mop_draft_events WHERE draft_id=:d"), {"d": did}).scalar_one()
    assert {x["code"] for x in context["errors"]} == {"RULE_TRIGGER_CONTRACT_MISSING"}
    assert kit.runtime.check_rule_due(db)["opened"] == 0


def test_readonly_no_event_and_unversioned_event(kit, db):
    start, end = datetime.now(timezone.utc)-timedelta(hours=1), datetime.now(timezone.utc)
    out = kit.runtime.account_rule_projection(db, "account_a", since=start, until=end)
    assert out["state"] == "NO_EVENT" and out["external_intake_complete"] is False
    db.execute(text("INSERT INTO mop_drafts(account_id,avito_chat_id,avito_message_id) VALUES('account_a','chat','legacy')"))
    out = kit.runtime.account_rule_projection(db, "account_a", since=start, until=datetime.now(timezone.utc))
    assert out["state"] == "NOT_PROVEN" and not out["rules"]


def test_durable_source_recovers_after_ledger_failure(kit, db, monkeypatch):
    real = kit.runtime.record_mop_event
    def fail(*args):
        raise RuntimeError("simulated evidence writer failure")
    monkeypatch.setattr(kit.runtime, "record_mop_event", fail)
    did = draft(kit, db)
    assert db.query(kit.model.ControlExecution).count() == 0
    assert db.execute(text("SELECT count(*) FROM mop_draft_events")).scalar_one() == 1
    monkeypatch.setattr(kit.runtime, "record_mop_event", real)
    result = kit.runtime.reconcile_rule_events(db, now=datetime.now(timezone.utc)+timedelta(seconds=301))
    assert result["recorded"] == 1 and result["provider_calls"] == 0 and result["business_actions"] == 0
    assert first_execution(kit, db).actual_state_json["evaluation_status"] == "PENDING"


def test_parallel_materialization_one_logical_execution_and_evidence(kit, db, monkeypatch):
    real = kit.runtime.record_mop_event
    monkeypatch.setattr(kit.runtime, "record_mop_event", lambda *args: None)
    did = draft(kit, db)
    eid = db.execute(text("SELECT id FROM mop_draft_events WHERE draft_id=:d"), {"d": did}).scalar_one()
    db.commit()
    monkeypatch.setattr(kit.runtime, "record_mop_event", real)
    gate = threading.Barrier(4)
    def worker():
        with kit.Session() as session:
            gate.wait(timeout=5)
            result = kit.runtime.record_mop_event(session, eid)
            session.commit()
            return result["executions"][0]["execution_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: worker(), range(4)))
    db.expire_all()
    assert len(set(ids)) == 1
    assert db.query(kit.model.ControlExecution).count() == 1
    assert db.query(kit.model.ControlEvidence).count() == 1


def test_old_event_cannot_erase_later_handoff(kit, db):
    did, eid = make_sent(kit, db)
    source_event(kit, db, did, "handed_to_human", {"policy_version": "MOP_STRUCTURED_HANDOFF_V1", "reply_already_sent": True})
    provider_get(kit, db)
    assert first_execution(kit, db).status == "waiting_owner"
    assert db.query(kit.model.ControlEvidence).filter_by(level="L5").count() == 1


def test_post_local_echo_is_not_independent_provider_readback(kit, db):
    did, eid = make_sent(kit, db)
    kit.runtime.reconcile_rule_events(db)
    assert first_execution(kit, db).status == "waiting_external"
    assert db.query(kit.model.ControlEvidence).filter_by(level="L5").count() == 0


def test_future_get_timestamp_fails_closed(kit, db):
    make_sent(kit, db)
    with pytest.raises(ValueError, match="OBSERVED_AT"):
        provider_get(kit, db, observed_at=datetime.now(timezone.utc)+timedelta(days=1))


def test_get_before_send_and_cross_account_are_not_proof(kit, db):
    make_sent(kit, db)
    assert provider_get(kit, db, account="account_b")["verified"] == 0
    assert provider_get(kit, db, observed_at=datetime.now(timezone.utc)-timedelta(days=1))["verified"] == 0
    assert first_execution(kit, db).status == "waiting_external"


def test_replayed_get_does_not_freshen_evidence_or_ledger_time(kit, db):
    make_sent(kit, db)
    provider_get(kit, db)
    db.flush()
    ex = first_execution(kit, db)
    first_time, first_actual = ex.updated_at, copy.deepcopy(ex.actual_state_json)
    count = db.query(kit.model.ControlEvidence).count()
    provider_get(kit, db)
    assert ex.updated_at == first_time and ex.actual_state_json == first_actual
    assert db.query(kit.model.ControlEvidence).count() == count


def test_gap_is_durable_when_binding_capture_is_unavailable(kit, db, monkeypatch):
    def fail(*args):
        raise RuntimeError("registry unavailable")
    monkeypatch.setattr(kit.runtime, "capture_mop_context", fail)
    draft(kit, db)
    assert db.query(kit.model.ControlExecution).count() == 0
    gap = db.query(kit.model.ControlIncident).filter_by(incident_type="RULE_EVIDENCE_GAP").one()
    assert gap.account_id == "account_a" and gap.details_json["reason"] == "RULE_BINDING_CAPTURE_FAILED"


def test_missing_binding_has_visible_gap_and_not_fake_evaluation(kit, db):
    db.query(kit.model.ControlRequirement).filter_by(key="rule.messages.first_response").one().status = "draft"
    db.commit()
    draft(kit, db)
    assert db.query(kit.model.ControlExecution).count() == 0
    gap = db.query(kit.model.ControlIncident).filter_by(incident_type="RULE_EVIDENCE_GAP").one()
    assert gap.details_json["reason"] == "RULE_BINDING_MISSING"


def test_recovery_projection_uses_source_day_not_journal_creation_day(kit, db, monkeypatch):
    real = kit.runtime.record_mop_event
    monkeypatch.setattr(kit.runtime, "record_mop_event", lambda *args: None)
    did = draft(kit, db)
    at = (datetime.now(timezone.utc)-timedelta(days=1)).replace(hour=12)
    context = db.execute(text("SELECT metadata FROM mop_draft_events WHERE draft_id=:d"), {"d": did}).scalar_one()
    context["control_rule_runtime"]["observed_at"] = at.isoformat()
    for binding in context["control_rule_runtime"]["bindings"]:
        binding["verification_due_at"] = (at+timedelta(seconds=binding["verification_stale_seconds"])).isoformat()
    db.execute(text("UPDATE mop_draft_events SET metadata=CAST(:meta AS jsonb) WHERE draft_id=:d"), {"d": did, "meta": json.dumps(context)})
    db.commit()
    monkeypatch.setattr(kit.runtime, "record_mop_event", real)
    kit.runtime.reconcile_rule_events(db)
    out = kit.runtime.account_rule_projection(db, "account_a", since=at-timedelta(seconds=1), until=at+timedelta(seconds=1))
    assert len(out["rules"]) == 1 and out["rules"][0]["identity"]["observed_at"] == at.isoformat()


def test_post_body_mismatch_does_not_create_provider_identity(kit, db):
    assert kit.mop._send_receipt({"account_id": "a", "avito_chat_id": "c"},
        {"status": "ok", "avito_message": {"id": "p", "content": {"text": "truncated"}}}, "full text") is None


def crm_link(db, *, foreign=False, manager=11, body='Нужна перегородка'):
    db.execute(text("INSERT INTO users(id,role) VALUES(10,'client'),(11,'employee'),(12,'employee')"))
    db.execute(text("INSERT INTO accounts(account_id,owner_user_id) VALUES('account_a',10),('account_b',12)"))
    db.execute(text("INSERT INTO user_account_access VALUES(11,'account_a',true),(12,'account_b',true)"))
    db.execute(text("INSERT INTO boris_crm_conversations VALUES(20,10,30,'avito',:a,'chat-1')"), {'a':'account_b' if foreign else 'account_a'})
    db.execute(text("INSERT INTO boris_crm_messages VALUES(21,10,20,'in-1','in',:body)"), {'body':body})
    db.execute(text("INSERT INTO boris_crm_deals(id,owner_user_id,responsible_user_id,next_action_at,contact_id,avito_account_id,avito_chat_id) VALUES(30,10,:manager,NULL,50,'account_a','chat-1')"), {'manager':manager})
    db.execute(text("INSERT INTO boris_crm_tasks(id,owner_user_id,deal_id,assigned_user_id,due_at,status,source) VALUES(40,10,30,:manager,now()-interval '1 hour','open','mop_handoff_recovery')"), {'manager':manager})
    db.commit()


def crm_execution(kit, db):
    return db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.plan_json['requirement_key'].astext=='rule.messages.crm_handoff').one()


def test_crm_exact_link_and_canonical_employee_grant_preserve_wait_owner(kit,db):
    did=draft(kit,db); crm_link(db)
    eid=source_event(kit,db,did,'handed_to_human')
    ex=crm_execution(kit,db)
    assert not kit.errors
    assert ex.status=='waiting_owner' and ex.actual_state_json['postcondition_status']=='LINK_VERIFIED'
    proof=ex.actual_state_json['crm_readback']
    assert proof['responsible_user_id']==11 and proof['task']['id']==40
    assert proof['task_event_link_proven'] is False and proof['next_action_at'] is None
    assert proof['manager_action_completed'] is False
    stamp=proof['observed_at']; count=db.query(kit.model.ControlEvidence).count()
    kit.runtime.record_mop_event(db,eid)
    assert crm_execution(kit,db).actual_state_json['crm_readback']['observed_at']==stamp
    assert db.query(kit.model.ControlEvidence).count()==count
    assert db.execute(text('SELECT count(*) FROM boris_crm_tasks')).scalar()==1


@pytest.mark.parametrize('args',[{'foreign':True},{'manager':12},{'body':'другое сообщение'}])
def test_crm_cross_tenant_unauthorized_manager_and_wrong_body_not_proven(kit,db,args):
    did=draft(kit,db); crm_link(db,**args)
    source_event(kit,db,did,'handed_to_human')
    ex=crm_execution(kit,db)
    assert ex.status=='waiting_owner' and ex.actual_state_json['postcondition_status']=='NOT_PROVEN'
    assert not db.query(kit.model.ControlEvidence).filter_by(evidence_type='crm_handoff_readback').first()


def test_crm_late_sync_recovered_by_existing_tick_without_new_domain_action(kit,db):
    did=draft(kit,db); source_event(kit,db,did,'handed_to_human')
    assert crm_execution(kit,db).actual_state_json['postcondition_status']=='NOT_PROVEN'
    count=db.execute(text('SELECT count(*) FROM mop_draft_events')).scalar()
    crm_link(db)
    out=kit.runtime.reconcile_rule_events(db)
    assert out['crm_checked']==1 and out['business_actions']==out['provider_calls']==0
    assert crm_execution(kit,db).actual_state_json['postcondition_status']=='LINK_VERIFIED'
    assert db.execute(text('SELECT count(*) FROM mop_draft_events')).scalar()==count


def test_due_open_incident_does_not_starve_next_pending_account(kit,db):
    draft(kit,db,'account_a',old=True); draft(kit,db,'account_b',old=True)
    assert kit.runtime.check_rule_due(db,limit=1)['opened']==1
    db.commit()
    assert kit.runtime.check_rule_due(db,limit=1)['opened']==1
    assert db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_NOT_EVALUATED').count()==2


def test_catalog_coverage_never_expands_account_executions(kit,db):
    out=kit.runtime.account_rule_projection(db,'account_a',since=datetime.now(timezone.utc)-timedelta(hours=1),until=datetime.now(timezone.utc),modules=['mop','marketing'])
    coverage=out['binding_coverage']
    assert coverage['implemented_bindings']==18 and coverage['catalog_rules']>18
    assert coverage['catalog_entry_is_not_account_execution'] is True
    assert not out['rules'] and out['state']=='NO_EVENT'
    assert any(x['module']=='marketing' and x['binding_status']=='PROTECTED_PRODUCER_NOT_INSTALLED' for x in coverage['entries'])


def test_generic_control_plane_caller_preserves_requirement_touch_default(kit,db):
    req=db.query(kit.model.ControlRequirement).filter_by(key='rule.messages.first_response').one()
    ex=kit.core.start_execution(db,req,account_id='account_a',plan={},idempotency_key='old-caller')
    kit.core.add_evidence(db,ex,level='L5',evidence_type='existing_readback',source='test',passed=True,payload={})
    for state in ('parsed','mapped_to_requirements','planned','preflight','executing','technical_tested','runtime_verified','pass'):
        kit.core.transition_execution(db,ex,state)
    assert req.last_verified_at is not None and req.last_applied_at is not None


def test_actual_fetch_hook_records_only_successful_existing_get(kit,db):
    make_sent(kit,db)
    calls=[]
    payload={'messages':[{'id':'out-1','direction':'out','content':{'text':'Уточню стоимость и вернусь с точным ответом.'}}]}
    response=types.SimpleNamespace(status_code=200,text='',json=lambda:payload)
    ns={'SessionLocal':kit.Session,'_get_user_id_and_token':lambda *a,**k:('123','fake'),
        '_messenger_http_get':lambda *a,**k:(calls.append(a[0]) or response),
        '_invalidate_avito_token':lambda *a:None,'observe_suppressed':lambda *a,**k:kit.errors.append(a)}
    source_code=(ROOT/'app/api/messenger.py').read_text()
    node=next(n for n in ast.parse(source_code).body if isinstance(n,ast.FunctionDef) and n.name=='fetch_chat_messages')
    source=ast.get_source_segment(source_code,node)
    exec(compile(source,'actual_fetch_function','exec'),ns)
    result=ns['fetch_chat_messages']('account_a','chat-1'); db.expire_all()
    assert result=={'status':'ok','messages':payload} and len(calls)==1
    assert first_execution(kit,db).actual_state_json['delivery_proven'] is True
    count=db.query(kit.model.ControlEvidence).count()
    response.status_code=402
    assert ns['fetch_chat_messages']('account_a','chat-1')['status']=='error'
    assert len(calls)==2 and db.query(kit.model.ControlEvidence).count()==count


def test_report_provider_functions_preserved_and_rule_projection_truthful(kit,db):
    candidate=(ROOT/'app/api/inbox_daily.py').read_text()
    baseline=json.loads((Path(__file__).parent/'report-provider-baseline-ast.json').read_text())
    def function(code,name):
        return next(n for n in ast.parse(code).body if isinstance(n,ast.FunctionDef) and n.name==name)
    for name in ('_owner_report_mop_provider_fact','_owner_report_apply_mop_provider'):
        import hashlib
        assert hashlib.sha256(ast.dump(function(candidate,name),include_attributes=False).encode()).hexdigest()==baseline[name]
    ns={'_OWNER_MODULE_LABELS':{'messages':'МОП'}}
    exec(compile(ast.Module(body=[function(candidate,'_owner_event_rule_lines')],type_ignores=[]),'report_projection','exec'),ns)
    lines=ns['_owner_event_rule_lines']({'state':'NO_EVENT','rules':[], 'source_complete':True,
      'binding_coverage':{'entries':[{'module':'messages','binding_status':'RULE_EVENT_ADAPTER_MISSING'}]}})
    assert any('полнота внешнего потока' in x for x in lines)
    assert any('не считается проверками этого клиента' in x for x in lines)
    assert 'PASS' not in ' '.join(lines)
    assert 'не прочитаны' in ns['_owner_event_rule_lines']({'state':'READ_ERROR'})[0]
    for flag in ('protected_writer_capture_missing','protected_feed_writer_branches_missing'):
        lines=ns['_owner_event_rule_lines']({'binding_coverage':{'entries':[{'module':'crm','binding_status':'IMPLEMENTED',flag:True}]}})
        assert any('Часть правил' in line for line in lines)


def test_future_source_window_cannot_claim_no_event(kit,db):
    with pytest.raises(ValueError,match='RULE_SOURCE_WINDOW_NOT_CLOSED'):
        kit.runtime.account_rule_projection(db,'account_a',since=datetime.now(timezone.utc)-timedelta(hours=1),until=datetime.now(timezone.utc)+timedelta(hours=1))


def test_due_uses_frozen_event_deadline_not_journal_creation_order(kit,db):
    draft(kit,db,'account_a')
    draft(kit,db,'account_b',old=True)
    out=kit.runtime.check_rule_due(db,limit=1)
    assert out['opened']==1
    assert db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_NOT_EVALUATED').one().account_id=='account_b'


def crm_context(kit,db,mid='in-1'):
    return kit.runtime.capture_crm_sync_context(db,'account_a','chat-1',[{'external_message_id':mid,'body':'Нужна перегородка'}])


def crm_receipt():
    return {'deal_id':30,'deal_eligible':True,'deal_created':False,'conversation_id':20,'phone_received':False}


def test_crm_canonical_sync_freezes_real_versions_without_catalog_fanout(kit,db):
    crm_link(db); ctx=crm_context(kit,db)
    assert len(ctx['bindings'])==12 and not ctx['errors']
    out=kit.runtime.record_crm_sync_result(db,ctx,crm_receipt())
    keys={r['rule_key'] for r in out['rules']}
    assert keys=={'rule.crm.deal_creation','rule.crm.owner_assignment','rule.crm.next_action','rule.crm.task_creation'}
    assert db.query(kit.model.ControlExecution).count()==4
    assert db.query(kit.model.ControlExecution).filter_by(status='pass').count()==0
    assignment=next(r for r in out['rules'] if r['rule_key']=='rule.crm.owner_assignment')
    assert assignment['predicate_result'] is True
    assert db.execute(text('SELECT count(*) FROM boris_crm_tasks')).scalar()==1


def test_crm_observation_repeat_preserves_first_fact_time_and_same_execution(kit,db):
    crm_link(db)
    kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt());db.commit()
    before=[(x.id,x.payload_json) for x in db.query(kit.model.ControlEvidence).order_by(kit.model.ControlEvidence.id).all()]
    kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt());db.commit()
    after=[(x.id,x.payload_json) for x in db.query(kit.model.ControlEvidence).order_by(kit.model.ControlEvidence.id).all()]
    assert before==after and db.query(kit.model.ControlExecution).count()==4


def test_crm_new_published_version_is_new_observation_not_retroactive_mutation(kit,db):
    crm_link(db);ctx=crm_context(kit,db)
    req=db.query(kit.model.ControlRequirement).filter_by(key='rule.crm.owner_assignment').one()
    old=req.version;req.version+=1;db.commit()
    kit.runtime.record_crm_sync_result(db,ctx,crm_receipt())
    ex=db.query(kit.model.ControlExecution).filter_by(requirement_id=req.id).one()
    assert ex.plan_json['requirement_version']==old
    assert ex.actual_state_json['historical_mutation_version_proven'] is False
    kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt())
    assert db.query(kit.model.ControlExecution).filter_by(requirement_id=req.id).count()==2
    assert req.last_verified_at is None


def test_crm_duplicate_deal_and_wrong_assigned_manager_are_negative(kit,db):
    crm_link(db)
    db.execute(text("INSERT INTO boris_crm_deals(id,owner_user_id,responsible_user_id,avito_account_id,avito_chat_id) VALUES(31,10,11,'account_a','chat-1')"))
    db.execute(text("INSERT INTO messenger_leads(account_id,avito_chat_id,assigned_user_id) VALUES('account_a','chat-1',12)"))
    out=kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt())
    verdict={r['rule_key']:r['predicate_result'] for r in out['rules']}
    assert verdict['rule.crm.deal_creation'] is False and verdict['rule.crm.owner_assignment'] is False


def test_crm_tasks_boris_store_never_claims_legacy_writer_idempotence(kit,db):
    crm_link(db)
    ctx=crm_context(kit,db);kit.runtime.record_crm_sync_result(db,ctx,crm_receipt())
    ex=db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.plan_json['requirement_key'].astext=='rule.crm.task_creation').one()
    assert ex.actual_state_json['predicate_result'] is None
    assert ex.actual_state_json['facts']['writer_contract_mismatch'] is True
    assert ex.actual_state_json['evaluation_status']=='NOT_PROVEN'
    kit.runtime.check_rule_due(db,now=datetime.now(timezone.utc)+timedelta(seconds=1801))
    assert db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_NOT_EVALUATED',execution_id=ex.id).one()


def test_crm_later_capture_failure_is_gap_and_domain_rows_preserved(kit,db,monkeypatch):
    crm_link(db);ctx=crm_context(kit,db)
    real=kit.runtime.record_crm_sync_result
    def fail(*args):raise RuntimeError('readback unavailable')
    monkeypatch.setattr(kit.runtime,'record_crm_sync_result',fail)
    out=kit.runtime.observe_crm_sync(db,ctx,crm_receipt());db.commit()
    assert out['state']=='NOT_PROVEN'
    assert db.execute(text('SELECT count(*) FROM boris_crm_tasks')).scalar()==1
    monkeypatch.setattr(kit.runtime,'record_crm_sync_result',real)
    out=kit.runtime.reconcile_rule_events(db,now=datetime.now(timezone.utc)+timedelta(seconds=301))
    assert out['crm_recovered']==1 and out['business_actions']==0
    assert db.query(kit.model.ControlIncident).filter_by(module='crm',state='open').count()==0


def test_crm_historical_cleanup_receipt_is_not_current_rule_success(kit,db):
    crm_link(db)
    db.execute(text("UPDATE boris_crm_tasks SET status='cancelled',completed_at=now() WHERE id=40"))
    db.execute(text("INSERT INTO boris_crm_activities(owner_user_id,deal_id,activity_type,source,source_ref) VALUES(10,30,'task_cancelled','brain_task_reconcile','brain_task_already_answered:40')"))
    out=kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt())
    result=next(r for r in out['rules'] if r['rule_key']=='rule.crm.answered_inquiry_cleanup')
    assert result['predicate_result'] is None
    ex=db.get(kit.model.ControlExecution,result['execution_id'])
    assert ex.status!='pass' and ex.actual_state_json['reason']=='CRM_CLEANUP_MUTATION_VERSION_NOT_PROVEN'


def test_crm_missing_source_identity_never_gets_latest_rule_version(kit,db):
    crm_link(db)
    with pytest.raises(ValueError,match='SOURCE_MESSAGE_ID'):
        kit.runtime.capture_crm_sync_context(db,'account_a','chat-1',[{}])
    kit.runtime.record_crm_capture_failure(db,'account_a','chat-1',{},ValueError('unknown'))
    assert db.query(kit.model.ControlExecution).count()==0
    assert db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_EVIDENCE_GAP').count()==1


def test_actual_crm_sync_hook_precedes_commit_and_uses_canonical_source(kit,db):
    crm_link(db)
    bridge_source=(ROOT/'app/crm/bridge.py').read_text()
    node=next(n for n in ast.parse(bridge_source).body if isinstance(n,ast.FunctionDef) and n.name=='_sync_dialog')
    ns={'HTTPException':__import__('fastapi').HTTPException,'text':text,'re':__import__('re'),
      '_assert_account_access':sys.modules['app.crm.bridge']._assert_account_access,
      '_load_dialog':lambda *a:[{'external_message_id':'in-1','body':'Нужна перегородка','direction':'in','client_name':'Имя','item_title':'Перегородка','item_id':'item','sent_at':datetime.now(timezone.utc)}],
      '_normal_direction':lambda d:d,'_contact_for_dialog':lambda *a:(50,False),
      '_mop_job_seeker_evidence':lambda *a:None,'_deal_gate':lambda *a:(True,'engaged',{}),
      '_sync_deal_stage_from_maturity':lambda *a:None,'_conversation_for_dialog':lambda *a:(20,False),
      '_sync_messages':lambda *a:0,'_sync_goal_side_effects':lambda *a:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_crm_sync','exec'),ns)
    result=ns['_sync_dialog'](db,10,'account_a','chat-1')
    assert result['ok'] and result['deal_id']==30 and result['deal_created'] is False
    assert db.query(kit.model.ControlExecution).filter_by(module='crm').count()==4
    assert not kit.errors


def test_actual_legacy_task_writer_records_store_specific_unknown_idempotency(kit,db):
    crm_link(db)
    tid=kit.crm_service.create_task(db,10,'account_a','Позвонить','2026-10-09',chat_id='chat-1')
    ex=db.query(kit.model.ControlExecution).filter_by(module='crm').one()
    assert ex.actual_state_json['store']=='crm_tasks' and ex.actual_state_json['boris_crm_tasks_not_verified']
    assert ex.actual_state_json['reason']=='CRM_LEGACY_CREATE_REQUEST_IDENTITY_MISSING'
    assert ex.actual_state_json['canonical_owner_entrypoint_satisfied'] is True
    kit.crm_service.done_task(db,'account_a',tid,'готово')
    completion=db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.plan_json['requirement_key'].astext=='rule.crm.task_completion').one()
    assert completion.actual_state_json['current_status']=='done'
    assert completion.actual_state_json['predicate_result'] is None and completion.status!='pass'
    assert completion.actual_state_json['canonical_owner_entrypoint_satisfied'] is False
    assert db.execute(text('SELECT count(*) FROM boris_crm_tasks')).scalar()==1
    assert not kit.errors


def test_legacy_task_foreign_account_does_not_materialize_false_completion(kit,db):
    crm_link(db)
    tid=kit.crm_service.create_task(db,10,'account_a','Позвонить','2026-10-09')
    kit.crm_service.done_task(db,'account_b',tid,'готово')
    assert db.query(kit.model.ControlExecution).filter_by(account_id='account_b').count()==0
    assert db.query(kit.model.ControlIncident).filter_by(account_id='account_b',incident_type='RULE_EVIDENCE_GAP').count()==1
    assert db.execute(text('SELECT status FROM crm_tasks WHERE id=:id'),{'id':tid}).scalar()=='planned'


@pytest.mark.parametrize('tamper',['account','message','hash','deal'])
def test_crm_source_replay_scope_rejected_before_evidence_write(kit,db,tamper):
    crm_link(db); ctx=crm_context(kit,db);receipt=crm_receipt()
    if tamper=='account':ctx['account_id']='account_b'
    if tamper=='message':ctx['source_message_id']='other-message'
    if tamper=='hash':ctx['source_text_hash']=kit.runtime._body_hash('unrelated')
    if tamper=='deal':receipt['deal_id']=999
    with pytest.raises(ValueError,match='IDENTITY|SCOPE'):
        kit.runtime.record_crm_sync_result(db,ctx,receipt)
    assert db.query(kit.model.ControlExecution).count()==0
    assert db.query(kit.model.ControlEvidence).count()==0


def test_crm_peer_same_chat_foreign_deal_phone_and_call_links_do_not_pass(kit,db):
    crm_link(db)
    db.execute(text("INSERT INTO boris_crm_deals(id,owner_user_id,responsible_user_id,avito_account_id,avito_chat_id) VALUES(31,10,11,'account_a','chat-1')"))
    db.execute(text("INSERT INTO boris_crm_activities(owner_user_id,deal_id,source,source_ref,metadata_json) VALUES(10,31,'mop_goal','mop_goal:account_a:chat-1:phone_received','{}'),(10,30,'avito_calltracking','avito_call:account_a:call-1',CAST(:m AS jsonb))"),{'m':json.dumps({'account_id':'account_a','call_id':'call-1','missed':True})})
    db.execute(text("INSERT INTO boris_crm_tasks(id,owner_user_id,deal_id,source,description,status) VALUES(41,10,31,'avito_calltracking','avito_call_task:account_a:call-1','open')"))
    db.execute(text("UPDATE boris_crm_activities SET activity_type='call' WHERE source='avito_calltracking'"))
    receipt=crm_receipt(); receipt['phone_received']=True
    out=kit.runtime.record_crm_sync_result(db,crm_context(kit,db),receipt)
    values={r['rule_key']:r['predicate_result'] for r in out['rules']}
    assert values['rule.crm.phone_handoff'] is False
    assert values['rule.crm.missed_calls'] is None


def test_crm_peer_partial_due_and_unknown_owner_source_cannot_pass(kit,db):
    crm_link(db)
    db.execute(text("UPDATE boris_crm_deals SET next_action_at=now()-interval '2 hours' WHERE id=30"))
    db.execute(text("INSERT INTO boris_crm_tasks(id,owner_user_id,deal_id,source,status) VALUES(41,10,30,'manual','open')"))
    context=crm_context(kit,db);context['prior_assignments_complete']=False
    out=kit.runtime.record_crm_sync_result(db,context,crm_receipt())
    values={r['rule_key']:r['predicate_result'] for r in out['rules']}
    assert values['rule.crm.next_action'] is None and values['rule.crm.owner_assignment'] is None


def test_unknown_account_modules_do_not_expand_global_catalog(kit,db):
    result=kit.runtime.registered_rule_coverage(db,'unknown_account',modules=[])
    assert result['applicability']=='UNKNOWN' and result['entries']==[] and result['catalog_rules']==0


def test_negative_crm_predicate_l3_is_not_positive_evidence(kit,db):
    crm_link(db)
    db.execute(text("INSERT INTO boris_crm_deals(id,owner_user_id,responsible_user_id,avito_account_id,avito_chat_id) VALUES(31,10,11,'account_a','chat-1')"))
    out=kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt())
    eid=next(r['execution_id'] for r in out['rules'] if r['rule_key']=='rule.crm.deal_creation')
    proof=db.query(kit.model.ControlEvidence).filter_by(execution_id=eid,level='L3').one()
    assert proof.passed is False and proof.payload_json['evidence_claim']=='current_state_predicate_result'
    assert db.get(kit.model.ControlExecution,eid).actual_state_json['evaluation_status']=='EVALUATED'


def test_legacy_task_rule_version_is_frozen_before_action_and_lock_blocks_publisher(kit,db,monkeypatch):
    from concurrent.futures import TimeoutError as FutureTimeout
    crm_link(db)
    prepared=threading.Event();release=threading.Event();publish_started=threading.Event()
    original=kit.runtime.prepare_legacy_crm_task_context
    def capture(*args,**kwargs):
        context=original(*args,**kwargs);prepared.set();assert release.wait(5);return context
    monkeypatch.setattr(kit.runtime,'prepare_legacy_crm_task_context',capture)
    reqid=db.query(kit.model.ControlRequirement).filter_by(key='rule.crm.task_creation').one().id
    db.commit()
    def create():
        with kit.Session() as session:
            return kit.crm_service.create_task(session,10,'account_a','Позвонить','2026-10-09')
    def publish():
        with kit.Session() as session:
            publish_started.set()
            session.execute(text('UPDATE control_requirements SET version=2 WHERE id=:id'),{'id':reqid});session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        creation=pool.submit(create);assert prepared.wait(5)
        publication=pool.submit(publish);assert publish_started.wait(5)
        try:
            with pytest.raises(FutureTimeout):publication.result(timeout=.15)
        finally:release.set()
        assert creation.result(timeout=5)>0;publication.result(timeout=5)
    db.expire_all()
    ex=db.query(kit.model.ControlExecution).filter_by(requirement_id=reqid).one()
    assert ex.plan_json['requirement_version']==1 and db.get(kit.model.ControlRequirement,reqid).version==2


def generated_with_facts(kit,db,*,auto_send=True):
    did=draft(kit,db)
    helper=sys.modules['app.services.control_message_rule_facts'];body='Здравствуйте! Уточню стоимость.'
    db.execute(text("UPDATE mop_drafts SET reply_text=:body,status='draft_ready' WHERE id=:id"),{'body':body,'id':did})
    gen=helper.capture_generation_rule_facts(account_id='account_a',chat_id='chat-1',reply_text=body,
        first_manager_turn=True,configured_intro='Здравствуйте!',identity_violations=[],proactive_event=None,generation_model='test')
    gate=helper.capture_eligibility_rule_facts(account_id='account_a',chat_id='chat-1',inbound_id='in-1',
        module_allowed=True,entitlement_active=True,binding_enabled=True,owner_enabled=True,schedule_active=True,
        auto_send=auto_send,ai_enabled=True,proactive_event=None)
    source_event(kit,db,did,'ai_generated',{'message_rule_facts':gen,'message_eligibility_facts':gate})
    return did,helper,body


def test_pg_message_predicate_receipts_bind_real_uuid_negative_is_not_positive_l3(kit,db):
    generated_with_facts(kit,db,auto_send=False)
    ex=db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.plan_json['requirement_key'].astext=='rule.messages.autosend_eligibility').one()
    assert ex.actual_state_json['predicate_result'] is False and ex.actual_state_json['evaluation_status']=='EVALUATED'
    proof=db.query(kit.model.ControlEvidence).filter_by(execution_id=ex.id,level='L3').one()
    assert proof.passed is False and proof.payload_json['evidence_claim']=='local_predicate_result'
    assert ex.status!='pass' and not kit.errors


def test_pg_claim_guard_receipt_is_retained_on_sent_without_extra_provider_request(kit,db):
    did,helper,body=generated_with_facts(kit,db)
    meta={'message_send_rule_facts':helper.capture_send_rule_facts(account_id='account_a',chat_id='chat-1',inbound_id='in-1',
       reply_text=body,module_allowed=True,takeover_active=False,readback_decision='safe_to_send',actor_id='account_autopilot',channel='mop_autopilot')}
    row,prior=kit.mop.claim_for_send(db,did,'system','account_autopilot','mop_autopilot',meta=meta)
    assert row is not None
    kit.mop.mark_sent(db,did,body,channel='mop_autopilot',actor_id='account_autopilot')
    ex=db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.plan_json['requirement_key'].astext=='rule.messages.autosend_eligibility').one()
    assert ex.actual_state_json['predicate_result'] is None
    assert ex.actual_state_json['reason']=='AUTOSEND_SETTINGS_AT_SEND_NOT_PROVEN'
    assert ex.actual_state_json['facts']['final_preclaim_checks_passed'] is True
    assert ex.actual_state_json['facts']['send_started_event_id']
    assert ex.status!='pass' and not kit.errors


def test_unknown_message_rule_applicability_is_not_overdue_obligation(kit,db):
    source_event(kit,db,draft(kit,db))
    unknown=db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.actual_state_json['evaluation_due_applicable'].astext=='false').all()
    assert unknown
    kit.runtime.check_rule_due(db,now=datetime.now(timezone.utc)+timedelta(days=1))
    assert not db.query(kit.model.ControlIncident).filter(kit.model.ControlIncident.execution_id.in_([e.id for e in unknown]),kit.model.ControlIncident.incident_type=='RULE_NOT_EVALUATED').all()


@pytest.mark.parametrize('contact,expected',[(None,None),(99,None),(50,True)])
def test_crm_missed_call_requires_exact_known_contact_for_positive_link(kit,db,contact,expected):
    crm_link(db)
    db.execute(text("INSERT INTO boris_crm_activities(owner_user_id,deal_id,contact_id,activity_type,source,source_ref,metadata_json) VALUES(10,30,50,'call','avito_calltracking','avito_call:account_a:call-1',CAST(:meta AS jsonb))"),{'meta':json.dumps({'account_id':'account_a','call_id':'call-1','missed':True})})
    db.execute(text("INSERT INTO boris_crm_tasks(id,owner_user_id,deal_id,contact_id,source,description,status) VALUES(41,10,30,:contact,'avito_calltracking','avito_call_task:account_a:call-1','open')"),{'contact':contact})
    out=kit.runtime.record_crm_sync_result(db,crm_context(kit,db),crm_receipt())
    result=next(r for r in out['rules'] if r['rule_key']=='rule.crm.missed_calls')
    assert result['predicate_result'] is expected


def test_projection_aggregate_is_complete_when_event_details_are_limited(kit,db):
    draft(kit,db,mid='source-1')
    draft(kit,db,mid='source-2')
    result=kit.runtime.account_rule_projection(db,'account_a',since=datetime.now(timezone.utc)-timedelta(hours=1),until=datetime.now(timezone.utc),limit=1,modules=['mop'])
    assert len(result['rules'])==1 and result['source_complete'] is False
    assert result['aggregate_complete'] is True and result['event_details_truncated'] is True
    assert result['execution_count']==2 and result['unique_rule_versions']==1
    assert result['rule_coverage_counts'][0]['execution_count']==2
    assert result['rule_coverage_counts'][0]['pending']==2
    assert result['external_intake_complete'] is False


def test_projection_gap_without_local_mop_event_is_not_no_event(kit,db):
    kit.core.open_incident(db,incident_type='RULE_EVIDENCE_GAP',account_id='account_a',module='crm',symptom='missing actual version',details={'reason':'UNVERSIONED_DOMAIN_EVENT'})
    result=kit.runtime.account_rule_projection(db,'account_a',since=datetime.now(timezone.utc)-timedelta(hours=1),until=datetime.now(timezone.utc),modules=['crm'])
    assert result['state']=='NOT_PROVEN' and result['execution_count']==0
    assert result['aggregate_complete'] is True and result['gaps']


def feed_context(kit,db, *, account='account_a', campaign=10, stage='materialize', event='order-1', old=False):
    db.execute(text("INSERT INTO accounts(account_id,owner_user_id) VALUES(:a,1) ON CONFLICT DO NOTHING"),{'a':account})
    if campaign is not None:
        db.execute(text("INSERT INTO campaigns(id,account_id) VALUES(:c,:a) ON CONFLICT DO NOTHING"),{'a':account,'c':campaign})
    return kit.runtime.capture_feed_context(db,account_id=account,campaign_id=campaign,stage=stage,event_id=event,
        artifact_sha256='a'*64 if campaign is None else None,artifact_kind='canonical_feed_items_json' if campaign is None else None,
        observed_at=datetime.now(timezone.utc)-timedelta(seconds=1801 if old else 1))


def test_feed_preaction_capture_binds_real_rule_version_and_persists_pending(kit,db):
    context=feed_context(kit,db)
    assert context['bindings'][0]['requirement_id']=='6bf07903-438b-42b8-9238-6d8b437a4e6d'
    assert context['bindings'][0]['verification_stale_seconds']==1800
    ex=db.query(kit.model.ControlExecution).one()
    assert ex.plan_json['feed_context']==context
    assert ex.actual_state_json['evaluation_status']=='PENDING'
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level.in_(['L3','L5','L6'])).count()==0
    assert ex.status!='pass'


def test_feed_cross_account_campaign_rejected_before_pending(kit,db):
    db.execute(text("INSERT INTO accounts(account_id,owner_user_id) VALUES('account_a',1),('account_b',2)"))
    db.execute(text("INSERT INTO campaigns(id,account_id) VALUES(10,'account_b')"))
    with pytest.raises(ValueError,match='CAMPAIGN_ACCOUNT_SCOPE'):
        kit.runtime.capture_feed_context(db,account_id='account_a',campaign_id=10,stage='materialize',event_id='order-1')
    assert db.query(kit.model.ControlExecution).count()==0


def test_feed_direct_account_publish_requires_actual_artifact_identity(kit,db):
    db.execute(text("INSERT INTO accounts(account_id,owner_user_id) VALUES('account_a',1)"))
    with pytest.raises(ValueError,match='ARTIFACT_IDENTITY'):
        kit.runtime.capture_feed_context(db,account_id='account_a',campaign_id=None,stage='publish_submit',event_id='action-1')
    context=feed_context(kit,db,campaign=None,stage='publish_submit',event='upload-1')
    assert context['campaign_id'] is None
    assert len(db.query(kit.model.ControlExecution).all())==1 # Only publish; no invented retry/schedule applicability.
    assert set(b['requirement_key'] for b in context['bindings'])=={'rule.feed_factory.publish','rule.feed_factory.retry','rule.feed_factory.schedule'}


def test_feed_capture_replay_keeps_original_version_and_clock_after_publish(kit,db):
    first=feed_context(kit,db)
    req=db.query(kit.model.ControlRequirement).filter_by(key='rule.feed_factory.draft').one()
    req.version=2
    db.flush()
    second=kit.runtime.capture_feed_context(db,account_id='account_a',campaign_id=10,stage='materialize',event_id='order-1')
    assert second==first and second['bindings'][0]['requirement_version']==1
    assert db.query(kit.model.ControlExecution).count()==1


def test_feed_missing_binding_is_durable_gap_and_not_unreported_success(kit,db):
    req=db.query(kit.model.ControlRequirement).filter_by(key='rule.feed_factory.draft').one()
    req.status='disabled';db.flush()
    context=feed_context(kit,db)
    assert not context['bindings'] and context['errors']
    gap=db.query(kit.model.ControlIncident).one()
    assert gap.incident_type=='RULE_EVIDENCE_GAP' and gap.module=='feed_factory'
    assert gap.details_json['business_action_replay_allowed'] is False


def test_feed_crash_after_pending_commit_is_due_without_business_retry(kit,db):
    feed_context(kit,db,old=True)
    db.commit()
    result=kit.runtime.check_rule_due(db)
    assert result['opened']==1
    gap=db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_NOT_EVALUATED').one()
    assert gap.account_id=='account_a'
    assert db.execute(text('SELECT count(*) FROM campaigns')).scalar()==1


def test_feed_capture_concurrent_transactions_share_one_frozen_identity(kit,db):
    db.execute(text("INSERT INTO accounts(account_id,owner_user_id) VALUES('account_a',1)"))
    db.execute(text("INSERT INTO campaigns(id,account_id) VALUES(10,'account_a')"));db.commit()
    barrier=threading.Barrier(3)
    def run():
        with kit.Session() as session:
            barrier.wait()
            context=kit.runtime.capture_feed_context(session,account_id='account_a',campaign_id=10,stage='materialize',event_id='order-concurrent')
            session.commit()
            return context
    with ThreadPoolExecutor(max_workers=3) as pool:
        contexts=list(pool.map(lambda _:run(),range(3)))
    assert contexts[0]==contexts[1]==contexts[2]
    assert db.query(kit.model.ControlExecution).count()==1
    assert db.query(kit.model.ControlEvidence).count()==1


def test_feed_missing_binding_replay_does_not_retroactively_assign_new_version(kit,db):
    req=db.query(kit.model.ControlRequirement).filter_by(key='rule.feed_factory.draft').one()
    req.status='disabled';db.flush()
    first=feed_context(kit,db)
    req.status='active';req.version=2;db.flush()
    second=kit.runtime.capture_feed_context(db,account_id='account_a',campaign_id=10,stage='materialize',event_id='order-1')
    assert first==second and second['errors'] and not second['bindings']
    assert db.query(kit.model.ControlExecution).count()==0


def feed_materialized_facts(context):
    return {'account_id':context['account_id'],'campaign_id':context['campaign_id'],
        'stage':context['stage'],'event_id':context['source_event_id'],'artifact_sha256':context.get('artifact_sha256'),'artifact_kind':context.get('artifact_kind'),
        'observed_at':datetime.now(timezone.utc).isoformat(),
        'order_id':'order-1','requested_total':2,'materialized_ids':[101,102],'items_total':2,
        'created_count':2,'updated_count':0,'row_scope_verified':True}


def test_feed_real_materialize_predicate_l3_never_l5_or_whole_rule_pass(kit,db):
    context=feed_context(kit,db)
    facts=feed_materialized_facts(context)
    result=kit.runtime.record_feed_result(db,context,facts)
    assert result['state']=='RECORDED' and result['business_actions']==result['provider_calls']==0
    ex=db.query(kit.model.ControlExecution).one()
    assert ex.status!='pass' and ex.actual_state_json['whole_rule_postcondition_proven'] is False
    assert ex.actual_state_json['predicate_result'] is True
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level.in_(['L5','L6'])).count()==0
    assert db.query(kit.model.ControlEvidence).filter_by(level='L3',passed=True).count()==1


@pytest.mark.parametrize('field,value',[('account_id','account_b'),('campaign_id',999),('stage','publish_submit'),('event_id','other-order'),('artifact_sha256','b'*64)])
def test_feed_result_scope_tamper_never_yields_positive_evidence(kit,db,field,value):
    context=feed_context(kit,db)
    facts=feed_materialized_facts(context);facts[field]=value
    result=kit.runtime.observe_feed_result(db,context,facts)
    assert result['state']=='NOT_PROVEN'
    assert db.query(kit.model.ControlEvidence).filter_by(level='L3',passed=True).count()==0
    assert db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_EVIDENCE_GAP').count()==1


def test_feed_immutable_result_replay_preserves_observation_time_and_rejects_changed_facts(kit,db):
    context=feed_context(kit,db);facts=feed_materialized_facts(context)
    kit.runtime.record_feed_result(db,context,facts)
    ex=db.query(kit.model.ControlExecution).one();before=ex.updated_at
    replay=copy.deepcopy(facts);replay['observed_at']=datetime.now(timezone.utc).isoformat()
    kit.runtime.record_feed_result(db,context,replay)
    assert ex.actual_state_json['observed_at']==facts['observed_at']
    assert db.query(kit.model.ControlEvidence).count()==2 and ex.updated_at==before
    changed=dict(facts,created_count=1,updated_count=1)
    with pytest.raises(ValueError,match='REPLAY_CONFLICT'):
        kit.runtime.record_feed_result(db,context,changed)


def test_feed_transient_evidence_failure_preserves_domain_rows_and_recovers_without_action(kit,db,monkeypatch):
    context=feed_context(kit,db,old=True);facts=feed_materialized_facts(context)
    original=kit.runtime._evidence_once
    def fail_l3(*args,**kwargs):
        if kwargs.get('level')=='L3':raise RuntimeError('private injected evidence outage')
        return original(*args,**kwargs)
    monkeypatch.setattr(kit.runtime,'_evidence_once',fail_l3)
    result=kit.runtime.observe_feed_result(db,context,facts)
    assert result['state']=='NOT_PROVEN' and db.execute(text('SELECT count(*) FROM campaigns')).scalar()==1
    db.commit();monkeypatch.setattr(kit.runtime,'_evidence_once',original)
    recovery=kit.runtime.reconcile_rule_events(db,now=datetime.now(timezone.utc)+timedelta(seconds=301))
    assert recovery['feed']['recovered']==1
    assert db.query(kit.model.ControlIncident).filter_by(state='open').count()==0
    assert db.execute(text('SELECT count(*) FROM campaigns')).scalar()==1
    assert db.query(kit.model.ControlExecution).one().status!='pass'


def test_feed_result_with_future_clock_is_not_evidence(kit,db):
    context=feed_context(kit,db);facts=feed_materialized_facts(context)
    facts['observed_at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    with pytest.raises(ValueError,match='TIME_NOT_PROVEN'):
        kit.runtime.record_feed_result(db,context,facts)
    assert db.query(kit.model.ControlEvidence).filter_by(level='L3').count()==0


@pytest.mark.parametrize('completed', ['negative','not_applicable'])
def test_due_lock_rechecks_concurrently_completed_negative_or_inapplicable(kit,db,completed):
    from sqlalchemy import event as sa_event
    draft(kit,db,old=True);ex=first_execution(kit,db);eid=ex.id;db.commit()
    selected=threading.Event();release=threading.Event()
    def pause_before_lock(conn,cursor,statement,parameters,context,executemany):
        if threading.current_thread().name.startswith('due-race') and 'control_executions' in statement and 'FOR UPDATE' in statement:
            selected.set()
            assert release.wait(10),'main updater did not complete'
    sa_event.listen(kit.engine,'before_cursor_execute',pause_before_lock)
    def checker():
        with kit.Session() as session:
            result=kit.runtime.check_rule_due(session)
            session.commit()
            return result
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='due-race') as pool:
            future=pool.submit(checker)
            try:
                assert selected.wait(10),'due checker never reached row lock'
                with kit.Session() as updater:
                    current=updater.query(kit.model.ControlExecution).filter_by(id=eid).with_for_update().one()
                    state=dict(current.actual_state_json)
                    if completed=='negative':
                        state.update(evaluation_status='EVALUATED',predicate_result=False)
                        kit.core.add_evidence(updater,current,level='L3',evidence_type='rule_evaluation',source=kit.runtime.CONTRACT,
                            passed=False,payload={'evaluation_status':'EVALUATED','predicate_result':False,'requirement_version':1})
                    else:
                        state['evaluation_due_applicable']=False
                    current.actual_state_json=state;updater.commit()
            finally:
                release.set()
            result=future.result(timeout=10)
    finally:
        sa_event.remove(kit.engine,'before_cursor_execute',pause_before_lock)
    assert result['checked']==1 and result['opened']==0
    assert db.query(kit.model.ControlIncident).filter_by(incident_type='RULE_NOT_EVALUATED').count()==0


def test_due_completed_negative_l3_is_evaluation_even_before_projection_refresh(kit,db):
    draft(kit,db,old=True);ex=first_execution(kit,db)
    kit.core.add_evidence(db,ex,level='L3',evidence_type='crm_rule_assessment',source=kit.runtime.CONTRACT,passed=False,
        payload={'evaluation_status':'EVALUATED','predicate_result':False,'requirement_version':1})
    assert ex.actual_state_json['evaluation_status']=='PENDING'
    db.flush()
    assert kit.runtime.check_rule_due(db)['opened']==0


@pytest.mark.parametrize("sink_fails",[False,True])
def test_actual_crm_commit_survives_both_evidence_writers_failing(kit,db,monkeypatch,sink_fails):
    crm_link(db)
    bridge_source=(ROOT/'app/crm/bridge.py').read_text()
    node=next(n for n in ast.parse(bridge_source).body if isinstance(n,ast.FunctionDef) and n.name=='_sync_dialog')
    ns={'HTTPException':__import__('fastapi').HTTPException,'text':text,'re':__import__('re'),
      '_assert_account_access':sys.modules['app.crm.bridge']._assert_account_access,
      '_load_dialog':lambda *a:[{'external_message_id':'in-1','body':'Нужна перегородка','direction':'in','client_name':'Имя','item_title':'Перегородка','item_id':'item','sent_at':datetime.now(timezone.utc)}],
      '_normal_direction':lambda d:d,'_contact_for_dialog':lambda *a:(50,False),
      '_mop_job_seeker_evidence':lambda *a:None,'_deal_gate':lambda *a:(True,'engaged',{}),
      '_sync_deal_stage_from_maturity':lambda *a:None,'_conversation_for_dialog':lambda *a:(20,False),
      '_sync_messages':lambda *a:0,'_sync_goal_side_effects':lambda *a:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_crm_sync','exec'),ns)
    def fail_sql(session,*args,**kwargs):
        session.execute(text('SELECT * FROM deliberately_missing_rule_evidence_relation'))
    monkeypatch.setattr(kit.runtime,'record_crm_sync_result',fail_sql)
    monkeypatch.setattr(kit.runtime,'_record_crm_gap',fail_sql)
    if sink_fails:
        def sink_error(*args,**kwargs):raise RuntimeError('diagnostics sink unavailable')
        monkeypatch.setattr(sys.modules['app.services.exception_observability'],'observe_suppressed',sink_error)
    ns['_sync_goal_side_effects']=lambda session,*args:session.execute(text("UPDATE boris_crm_deals SET next_action_at=TIMESTAMPTZ '2026-10-10 12:00:00+00' WHERE id=30"))
    result=ns['_sync_dialog'](db,10,'account_a','chat-1')
    assert result['ok'] and result['deal_id']==30 and result['deal_created'] is False
    with kit.Session() as readback:
        assert readback.execute(text('SELECT next_action_at FROM boris_crm_deals WHERE id=30')).scalar()==datetime(2026,10,10,12,tzinfo=timezone.utc)
        assert readback.query(kit.model.ControlExecution).filter_by(module='crm').count()==0



@pytest.mark.parametrize('which',['legacy','feed_capture','feed_result'])
def test_observer_double_failure_preserves_real_domain_commit(kit,db,monkeypatch,which):
    def fail_sql(session,*args,**kwargs):
        session.execute(text('SELECT * FROM deliberately_missing_rule_gap_relation'))
    def sink_error(*args,**kwargs):raise RuntimeError('unavailable diagnostics')
    monkeypatch.setattr(sys.modules['app.services.exception_observability'],'observe_suppressed',sink_error)
    if which=='legacy':
        crm_link(db)
        monkeypatch.setattr(kit.runtime,'record_legacy_crm_task_result',fail_sql)
        monkeypatch.setattr(kit.runtime,'_record_crm_gap',fail_sql)
        tid=kit.crm_service.create_task(db,10,'account_a','Survives journal failure','2026-10-09')
        with kit.Session() as readback:
            assert readback.execute(text('SELECT title FROM crm_tasks WHERE id=:id'),{'id':tid}).scalar()=='Survives journal failure'
    else:
        context=feed_context(kit,db)
        monkeypatch.setattr(kit.runtime,'_record_feed_gap',fail_sql)
        if which=='feed_capture':
            monkeypatch.setattr(kit.runtime,'capture_feed_context',fail_sql)
            result=kit.runtime.safe_capture_feed_context(db,account_id='account_a',campaign_id=10,stage='materialize',event_id='order-2')
            assert result['capture_failed'] is True
        else:
            monkeypatch.setattr(kit.runtime,'record_feed_result',fail_sql)
            result=kit.runtime.observe_feed_result(db,context,feed_materialized_facts(context))
            assert result['state']=='NOT_PROVEN'
        assert result['evidence_gap_persisted'] is False
        db.commit()
        with kit.Session() as readback:
            assert readback.execute(text("SELECT account_id FROM campaigns WHERE id=10")).scalar()=='account_a'


def test_reconcile_second_gap_failure_does_not_starve_next_account(kit,db,monkeypatch):
    did1=draft(kit,db,account='account_a');did2=draft(kit,db,account='account_b')
    db.execute(text('DELETE FROM control_executions'));db.commit()
    original=kit.runtime.record_mop_event
    def fail_first(session,event_id):
        account=session.execute(text('SELECT d.account_id FROM mop_draft_events e JOIN mop_drafts d ON d.id=e.draft_id WHERE e.id=:i'),{'i':event_id}).scalar()
        if account=='account_a':session.execute(text('SELECT * FROM deliberately_missing_rule_evidence_relation'))
        return original(session,event_id)
    def gap_fail(session,*args,**kwargs):session.execute(text('SELECT * FROM deliberately_missing_rule_gap_relation'))
    monkeypatch.setattr(kit.runtime,'record_mop_event',fail_first)
    monkeypatch.setattr(kit.runtime,'record_mop_gap',gap_fail)
    result=kit.runtime.reconcile_rule_events(db)
    assert result['recorded']>=1
    assert any(e.get('evidence_gap_persisted') is False for e in result['errors'])
    db.commit()
    with kit.Session() as readback:
        assert readback.query(kit.model.ControlExecution).filter_by(account_id='account_b').count()==1
        assert readback.execute(text('SELECT count(*) FROM mop_drafts')).scalar()==2


def test_feed_pending_xml_is_waiting_event_not_failed_evidence(kit,db):
    context=feed_context(kit,db,stage='xmlcheck',old=True)
    facts={'account_id':'account_a','campaign_id':10,'stage':'xmlcheck','event_id':context['source_event_id'],
        'observed_at':datetime.now(timezone.utc).isoformat(),'artifact_sha256':None,'artifact_kind':None,
        'xml_written':True,'xml_items':2,'xmlcheck_status':'pending'}
    result=kit.runtime.observe_feed_result(db,context,facts)
    assert result['state']=='RECORDED'
    validation=db.query(kit.model.ControlExecution).filter(kit.model.ControlExecution.plan_json['requirement_key'].astext=='rule.feed_factory.validation').one()
    assert validation.actual_state_json['evaluation_status']=='WAITING_EVENT'
    assert validation.actual_state_json['evaluation_due_applicable'] is False
    kit.runtime.check_rule_due(db)
    assert db.query(kit.model.ControlIncident).filter_by(execution_id=validation.id).count()==0


def budget_context(kit,db, *, old=False):
    db.execute(text("INSERT INTO accounts(account_id,owner_user_id) VALUES('budget_account',10) ON CONFLICT DO NOTHING"))
    auth={'authorized_at':(datetime.now(timezone.utc)-timedelta(seconds=901 if old else 1)).isoformat(),
          'authorized_by_user_id':10,'daily_budget_limit_rub':500,
          'policy_version':'MONEY_BUDGET_OWNER_PROVENANCE_V1','source':'authenticated_set_kpi_settings'}
    context=kit.budget.capture_owner_budget_context(db,account_id='budget_account',actor_user_id=10,
        account_owner_user_id=10,is_platform_owner=False,authorization=auth,requested_budget=500)
    settings={'daily_budget_limit_rub':500,'daily_budget_authorization':auth}
    row=kit.Storage(account_id='budget_account',key='kpi_settings',value=json.dumps(settings))
    db.add(row);db.flush();db.refresh(row)
    return context,row,json.loads(row.value),datetime.now(timezone.utc).isoformat()


def test_owner_budget_actual_orm_storage_roundtrip_and_full_rule_limits(kit,db):
    context,row,settings,observed=budget_context(kit,db)
    result=kit.budget.record_owner_budget_result(db,context,stored_settings=settings,observed_at=observed)
    db.commit()
    assert result['state']=='RECORDED' and len(result['rules'])==2
    assert all(x['predicate_result'] is True for x in result['rules'])
    assert db.query(kit.model.ControlEvidence).filter_by(level='L3',passed=True).count()==2
    assert db.query(kit.model.ControlEvidence).filter(kit.model.ControlEvidence.level.in_(['L5','L6'])).count()==0
    assert all(e.status!='pass' and e.actual_state_json['source_observed_at']==observed for e in db.query(kit.model.ControlExecution).all())
    assert db.query(kit.model.ControlRequirement).filter(kit.model.ControlRequirement.last_verified_at.isnot(None)).count()==0
    with kit.Session() as readback:
        assert json.loads(readback.query(kit.Storage).filter_by(id=row.id).one().value)==settings


def test_owner_budget_gap_recovery_preserves_source_time_and_does_not_rewrite_settings(kit,db,monkeypatch):
    context,row,settings,observed=budget_context(kit,db,old=True)
    original=kit.runtime._evidence_once
    def fail_l3(*a,**kw):
        if kw.get('level')=='L3':raise RuntimeError('private evidence outage')
        return original(*a,**kw)
    monkeypatch.setattr(kit.runtime,'_evidence_once',fail_l3)
    failed=kit.budget.observe_owner_budget_result(db,context,stored_settings=settings,observed_at=observed)
    assert failed['state']=='NOT_PROVEN' and failed['diagnostic_stored'] is True
    db.commit();monkeypatch.setattr(kit.runtime,'_evidence_once',original)
    before=db.query(kit.Storage).filter_by(id=row.id).one().value
    out=kit.runtime.reconcile_rule_events(db,now=datetime.now(timezone.utc)+timedelta(seconds=301))
    assert out['owner_budget']['recovered']==1
    assert db.query(kit.model.ControlIncident).filter_by(state='open').count()==0
    assert db.query(kit.Storage).filter_by(id=row.id).one().value==before
    for e in db.query(kit.model.ControlEvidence).filter_by(level='L3').all():
        assert e.payload_json['source_observed_at']==observed and e.observed_at==datetime.fromisoformat(observed)


def test_owner_budget_actual_orm_failed_first_capture_not_rebound_later(kit,db):
    for req in db.query(kit.model.ControlRequirement).filter(kit.model.ControlRequirement.key.in_(kit.budget.KEYS)):
        req.status='disabled'
    db.flush()
    context,row,settings,observed=budget_context(kit,db)
    assert not context['bindings'] and context['errors']
    for req in db.query(kit.model.ControlRequirement).filter(kit.model.ControlRequirement.key.in_(kit.budget.KEYS)):
        req.status='active';req.version=2
    db.flush()
    replay=kit.budget.capture_owner_budget_context(db,account_id='budget_account',actor_user_id=10,
        account_owner_user_id=10,is_platform_owner=False,authorization=settings['daily_budget_authorization'],requested_budget=500)
    assert replay==context and db.query(kit.model.ControlExecution).count()==0


def _due_coverage_fixture(kit, db, *, stage='materialize', event='coverage-1', status='EVALUATED', applicable=True, old=True):
    context=feed_context(kit,db,stage=stage,event=event,old=old)
    rows=db.query(kit.model.ControlExecution).filter_by(account_id='account_a').all()
    for ex in rows:
        if (ex.plan_json or {}).get('event_id')!=context['event_id']:
            continue
        actual=dict(ex.actual_state_json or {},evaluation_status=status,predicate_result=False)
        if applicable=='UNKNOWN':
            actual.pop('evaluation_due_applicable',None)
        else:
            actual['evaluation_due_applicable']=applicable
        ex.actual_state_json=actual
    db.flush()


def _due_projection(kit,db):
    return kit.runtime.account_rule_projection(db,'account_a',since=datetime.now(timezone.utc)-timedelta(hours=2),until=datetime.now(timezone.utc),limit=1,modules=['feed_factory'])


def test_unique_due_coverage_repeated_rule_counts_once_and_negative_is_evaluated(kit,db):
    _due_coverage_fixture(kit,db,event='event-1')
    _due_coverage_fixture(kit,db,event='event-2')
    out=_due_projection(kit,db)
    assert out['execution_count']==2 and out['event_details_truncated'] is True
    assert out['due_rule_versions']==1 and out['evaluated_due_rule_versions']==1
    assert out['rule_coverage_counts'][0]['due_event_count']==2


def test_unique_due_coverage_two_distinct_rules_count_twice(kit,db):
    _due_coverage_fixture(kit,db,stage='local_validation')
    out=_due_projection(kit,db)
    assert out['due_rule_versions']==2 and out['evaluated_due_rule_versions']==2
    assert len({r['requirement_id'] for r in out['rule_coverage_counts']})==2


def test_unique_due_coverage_no_event_is_zero_not_success(kit,db):
    out=_due_projection(kit,db)
    assert out['due_rule_versions']==out['evaluated_due_rule_versions']==0
    code=(ROOT/'app/api/inbox_daily.py').read_text()
    node=next(n for n in ast.parse(code).body if isinstance(n,ast.FunctionDef) and n.name=='_owner_event_rule_lines')
    ns={'_OWNER_MODULE_LABELS':{}}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'report_projection','exec'),ns)
    lines=' '.join(ns['_owner_event_rule_lines'](out))
    assert '0 из 0' in lines and 'не означает успешное' in lines


def test_unique_due_coverage_one_evaluation_cannot_hide_same_rule_missed_event(kit,db):
    _due_coverage_fixture(kit,db,event='event-evaluated')
    _due_coverage_fixture(kit,db,event='event-missed',status='PENDING')
    out=_due_projection(kit,db)
    assert out['due_rule_versions']==1 and out['evaluated_due_rule_versions']==0
    row=out['rule_coverage_counts'][0]
    assert row['due_event_count']==2 and row['evaluated_due_event_count']==1


@pytest.mark.parametrize('status,applicable,old',[
    ('WAITING_EVENT',True,True),('NOT_PROVEN',False,True),
    ('NOT_PROVEN','UNKNOWN',True),('UNKNOWN',True,True),('EVALUATED',True,False)])
def test_unique_due_coverage_excludes_waiting_unknown_inapplicable_and_future(kit,db,status,applicable,old):
    _due_coverage_fixture(kit,db,status=status,applicable=applicable,old=old)
    out=_due_projection(kit,db)
    assert out['execution_count']==1
    assert out['due_rule_versions']==out['evaluated_due_rule_versions']==0
