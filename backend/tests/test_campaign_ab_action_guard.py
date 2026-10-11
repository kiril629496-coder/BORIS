import pytest

from app.services.campaign_ab_action_guard import evaluate_ab_action_evidence


def test_scale_requires_minimum_sample_and_leader_state():
    assert evaluate_ab_action_evidence("scale",{"experiment_state":"leader","views":29,"contacts":10})["allowed"] is False
    assert evaluate_ab_action_evidence("scale",{"experiment_state":"leader","views":30,"contacts":2})["allowed"] is False
    blocked=evaluate_ab_action_evidence("scale",{"experiment_state":"testing","views":100,"contacts":10})
    assert blocked["allowed"] is False
    assert blocked["code"]=="ab_not_proven_winner"
    passed=evaluate_ab_action_evidence("scale",{"experiment_state":"leader","views":30,"contacts":3,"conversion":10})
    assert passed["allowed"] is True
    assert passed["code"]=="ab_winner_evidence_pass"


def test_finish_allows_zero_contact_loser_after_enough_views():
    blocked=evaluate_ab_action_evidence("finish",{"experiment_state":"weak","views":29,"contacts":0})
    assert blocked["allowed"] is False
    passed=evaluate_ab_action_evidence("finish",{"experiment_state":"weak","views":30,"contacts":0})
    assert passed["allowed"] is True
    assert passed["code"]=="ab_loser_evidence_pass"


def test_finish_with_one_or_two_contacts_keeps_collecting():
    for contacts in (1,2):
        out=evaluate_ab_action_evidence("finish",{
            "experiment_state":"weak","views":100,"contacts":contacts
        })
        assert out["allowed"] is False
        assert out["code"]=="ab_insufficient_evidence"


def test_finish_requires_server_weak_state_even_with_large_sample():
    out=evaluate_ab_action_evidence("finish",{
        "experiment_state":"testing","views":500,"contacts":20,"conversion":4
    })
    assert out["allowed"] is False
    assert out["code"]=="ab_not_proven_loser"


def test_missing_evidence_fails_closed_for_finish_and_scale():
    for action in ("finish","scale"):
        out=evaluate_ab_action_evidence(action,None)
        assert out["allowed"] is False
        assert out["code"]=="ab_evidence_missing"


def test_non_destructive_actions_are_not_evidence_gated():
    for action in ("pause","resume","edit"):
        assert evaluate_ab_action_evidence(action,{})["allowed"] is True


def test_enforce_current_ab_action_blocks_direct_api_bypass(monkeypatch):
    from fastapi import HTTPException
    from app.services.campaign_ab_action_guard import enforce_current_ab_action
    from app.api import campaigns as C

    monkeypatch.setattr(C,"campaign_ab_tests",lambda account_id,db,user:{
        "items":[{
            "item_id":77,
            "experiment_state":"collecting",
            "views":12,
            "contacts":1,
            "conversion":8.33,
        }]
    })
    with pytest.raises(HTTPException) as exc:
        enforce_current_ab_action(object(),object(),"acc",77,"scale")
    assert exc.value.status_code==409
    detail=exc.value.detail
    assert detail["code"]=="ab_insufficient_evidence"
    assert detail["evidence"]["views"]==12
    assert detail["evidence"]["contacts"]==1


def test_enforce_current_ab_action_passes_confirmed_leader(monkeypatch):
    from app.services.campaign_ab_action_guard import enforce_current_ab_action
    from app.api import campaigns as C

    monkeypatch.setattr(C,"campaign_ab_tests",lambda account_id,db,user:{
        "items":[{
            "item_id":77,
            "experiment_state":"leader",
            "views":50,
            "contacts":7,
            "conversion":14.0,
        }]
    })
    out=enforce_current_ab_action(object(),object(),"acc",77,"scale")
    assert out["allowed"] is True
    assert out["code"]=="ab_winner_evidence_pass"
