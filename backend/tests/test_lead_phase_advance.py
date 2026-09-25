# -*- coding: utf-8 -*-
from app.ext_api import lead


def test_structured_review_evidence_satisfies_phase_pass_gate():
    ev = {
        "summary": "autonomous claim and execution verified",
        "tests": ["provider failover PASS"],
        "acceptance_criteria": [
            {"criterion": "Server executor must claim autonomously",
             "status": "PASS", "evidence": "CLAIMED then execution evidence"}
        ],
    }
    text = lead._phase_evidence(ev)
    assert "autonomous claim and execution verified" in text
    assert "Server executor must claim autonomously" in text
    assert text.strip()


def test_advance_with_structured_evidence_closes_phase_before_next_order(monkeypatch):
    order = {
        "order_id": 85563, "dev_job_id": "dev_1056", "phase": "PLANNING",
        "iteration": 0, "assigned_to": "gpt-executor",
        "last_evidence": {"summary": "real execution evidence",
                          "acceptance_criteria": []},
    }
    calls = []
    jobs = [
        {"dev_job_id": "dev_1056", "current_phase": "BACKEND",
         "status": "backend", "acceptance_criteria": [{"text": "remaining", "done": False}],
         "phases": {"PLANNING": {"state": "pass"}, "BACKEND": {"state": "pending"}},
         "required_phases": ["PLANNING", "BACKEND"], "goal": "canary", "scope": {}},
    ]
    monkeypatch.setattr(lead.dev, "update_phase",
                        lambda key, jid, phase, state, **kw: calls.append(
                            ("phase", phase, state, kw.get("evidence"))))
    monkeypatch.setattr(lead.dev, "get", lambda jid: jobs[0])
    monkeypatch.setattr(lead, "_close_criteria", lambda order, job: [])
    monkeypatch.setattr(lead, "_next_order", lambda job, prev: calls.append(("next", job["current_phase"])) or {"order_id": 85564, "phase": "BACKEND"})
    monkeypatch.setattr(lead.a2a, "message", lambda *a, **k: None)

    lead._advance(order)
    assert calls == [("next", "BACKEND")]
