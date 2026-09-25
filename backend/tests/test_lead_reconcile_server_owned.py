# -*- coding: utf-8 -*-
from app.ext_api import a2a, lead


def _job(jid="dev_1057"):
    return {"dev_job_id": jid, "status": "planning", "current_phase": "PLANNING",
            "scope": {"server_owned": True, "autonomous_dev": True},
            "acceptance_criteria": [{"text": "x", "done": False}]}


def test_reconcile_finds_server_owned_job_despite_unrelated_accepted_backlog(monkeypatch):
    target = _job()
    unrelated = [_job(f"dev_{i}") | {"scope": {"server_owned": False}}
                 for i in range(2000, 2250)]
    monkeypatch.setattr(lead.dev, "listing", lambda limit=50, status=None: [target] + unrelated)
    monkeypatch.setattr(lead.dev, "get", lambda jid: target | {
        "status": "backend", "current_phase": "BACKEND"})
    monkeypatch.setattr(lead.a2a, "listing", lambda **kw: [{
        "order_id": 91603, "dev_job_id": "dev_1057", "status": a2a.ACCEPTED,
        "assigned_to": "gpt-executor"}] if kw.get("dev_job_id") == "dev_1057" else [])
    monkeypatch.setattr(lead.a2a, "get", lambda oid: {
        "order_id": oid, "dev_job_id": "dev_1057", "status": a2a.ACCEPTED,
        "assigned_to": "gpt-executor", "last_evidence": {"evidence": "PASS"}})
    monkeypatch.setattr(lead.repo, "reviewed_order_apply_state",
                        lambda order: {"ready": True, "patches": []})
    def exact_gate(sql, **params):
        if "status = ANY" in sql:
            return None
        if "status=:s" in sql:
            return {"id": 91603}
        return None
    monkeypatch.setattr(lead.db, "one", exact_gate)
    calls = []
    monkeypatch.setattr(lead, "_advance", lambda order: calls.append(order["order_id"]))

    moved = lead.reconcile(limit=1)
    assert calls == [91603]
    assert moved and moved[0]["phase"] == "BACKEND"


def test_reconcile_duplicate_tick_does_not_advance_when_next_order_is_open(monkeypatch):
    target = _job()
    monkeypatch.setattr(lead.dev, "listing", lambda limit=50, status=None: [target])
    monkeypatch.setattr(lead.a2a, "listing", lambda **kw: [
        {"order_id": 91603, "dev_job_id": "dev_1057", "status": a2a.ACCEPTED,
         "assigned_to": "gpt-executor"},
        {"order_id": 91604, "dev_job_id": "dev_1057", "status": a2a.QUEUED,
         "assigned_to": "gpt-executor"},
    ])
    monkeypatch.setattr(lead.db, "one", lambda sql, **params: {"id": 91604}
                        if "status = ANY" in sql else {"id": 91603})
    calls = []
    monkeypatch.setattr(lead, "_advance", lambda order: calls.append(order["order_id"]))
    assert lead.reconcile(limit=1) == []
    assert calls == []



def test_next_order_directly_refuses_duplicate_when_open_order_exists(monkeypatch):
    job = {
        "dev_job_id": "dev_1057",
        "status": "testing",
        "current_phase": "REGRESSION",
        "goal": "dedupe regression",
        "scope": {"server_owned": True, "autonomous_dev": True},
        "phases": {
            "REGRESSION": {"state": "pass"},
            "ACCEPTANCE": {"state": "pending"},
        },
        "required_phases": ["REGRESSION", "ACCEPTANCE"],
        "acceptance_criteria": [{"text": "still open", "done": False}],
    }
    prev = {
        "order_id": 70020,
        "dev_job_id": "dev_1057",
        "phase": "REGRESSION",
        "assigned_to": "gpt-executor",
        "max_iterations": 6,
        "forbidden_changes": [],
        "required_qa": [],
        "required_evidence": [],
        "last_evidence": {"summary": "regression pass"},
    }
    created = []
    monkeypatch.setattr(lead, "_has_open_order", lambda jid: True)
    monkeypatch.setattr(
        lead.a2a, "create_order",
        lambda *args, **kwargs: created.append((args, kwargs)) or {"order_id": 70021},
    )

    out = lead._next_order(job, prev)

    assert out is None
    assert created == []


def test_server_owned_required_phase_order_exists_after_parent_criteria_done(monkeypatch):
    job = {
        "dev_job_id": "dev_1057",
        "status": "testing",
        "current_phase": "PRODUCTION_QA",
        "goal": "state machine",
        "scope": {"server_owned": True, "autonomous_dev": True},
        "required_phases": ["ACCEPTANCE", "PRODUCTION_QA"],
        "phases": {
            "ACCEPTANCE": {"state": "pass"},
            "PRODUCTION_QA": {"state": "pending"},
        },
        "acceptance_criteria": [{"text": "done", "done": True}],
    }
    prev = {
        "order_id": 92354,
        "dev_job_id": "dev_1057",
        "phase": "ACCEPTANCE",
        "assigned_to": "gpt-executor",
        "max_iterations": 6,
        "forbidden_changes": [],
        "required_qa": [],
        "required_evidence": [],
        "last_evidence": {"summary": "acceptance pass"},
    }
    captured = {}
    monkeypatch.setattr(lead, "_has_open_order", lambda jid: False)
    monkeypatch.setattr(
        lead.a2a, "create_order",
        lambda *args, **kwargs: captured.update(kwargs)
        or {"order_id": 92355, "phase": "PRODUCTION_QA"},
    )

    out = lead._next_order(job, prev, phase="PRODUCTION_QA")

    assert out["phase"] == "PRODUCTION_QA"
    assert captured["acceptance"]
    assert "PRODUCTION_QA" in captured["acceptance"][0]


def test_server_owned_required_phase_and_completion_never_need_plan(monkeypatch):
    pending = {
        "dev_job_id": "dev_1057",
        "status": "testing",
        "current_phase": "PRODUCTION_QA",
        "goal": "state machine",
        "scope": {"server_owned": True, "autonomous_dev": True},
        "required_phases": ["ACCEPTANCE", "PRODUCTION_QA"],
        "phases": {
            "ACCEPTANCE": {"state": "pass"},
            "PRODUCTION_QA": {"state": "pending"},
        },
        "acceptance_criteria": [{"text": "done", "done": True}],
    }
    prev = {
        "order_id": 92354,
        "dev_job_id": "dev_1057",
        "phase": "ACCEPTANCE",
        "iteration": 0,
        "assigned_to": "gpt-executor",
        "last_evidence": {"summary": "acceptance pass"},
    }
    next_calls = []
    monkeypatch.setattr(lead.dev, "get", lambda jid: pending)
    monkeypatch.setattr(lead, "_close_criteria", lambda order, job: [])
    monkeypatch.setattr(
        lead, "_next_order",
        lambda job, order, phase=None, goal_override=None:
            next_calls.append(phase) or {"order_id": 92355, "phase": phase},
    )
    monkeypatch.setattr(
        lead, "plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("plan must not run before required phase")
        ),
    )
    monkeypatch.setattr(lead.a2a, "message", lambda *args, **kwargs: None)

    lead._advance(prev)
    assert next_calls == ["PRODUCTION_QA"]

    done = {
        **pending,
        "phases": {
            "ACCEPTANCE": {"state": "pass"},
            "PRODUCTION_QA": {"state": "pass"},
        },
    }
    completed = {**done, "status": "completed", "current_phase": "COMPLETED"}
    prod_order = {
        **prev,
        "order_id": 92355,
        "phase": "PRODUCTION_QA",
        "last_evidence": {"summary": "production qa pass"},
    }
    monkeypatch.setattr(lead.dev, "get", lambda jid: done)
    complete_calls = []
    monkeypatch.setattr(
        lead.dev, "complete",
        lambda key, jid, summary=None:
            complete_calls.append((jid, summary)) or completed,
    )
    done_calls = []
    monkeypatch.setattr(lead.notify, "done", lambda job: done_calls.append(job))

    out = lead._advance(prod_order)

    assert out["status"] == "completed"
    assert len(complete_calls) == 1
    assert len(done_calls) == 1



def test_blocked_child_is_live_and_prevents_replaying_accepted_parent(monkeypatch):
    monkeypatch.setattr(lead.dev, "get", lambda jid: {"id": 1057})
    captured = {}

    def fake_one(sql, **params):
        captured["states"] = list(params["st"])
        return {"id": 91604}

    monkeypatch.setattr(lead.db, "one", fake_one)

    assert lead._has_open_order("dev_1057") is True
    assert a2a.BLOCKED_INFRA in captured["states"]
    assert a2a.BLOCKED_HUMAN in captured["states"]
