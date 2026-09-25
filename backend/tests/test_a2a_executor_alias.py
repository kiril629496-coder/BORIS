# -*- coding: utf-8 -*-
from app.ext_api import a2a


def test_boris_executor_and_gpt_executor_are_same_claim_pool():
    assert a2a._assignment_matches("boris-executor", "gpt-executor")
    assert a2a._assignment_matches("gpt-executor", "boris-executor")
    assert set(a2a._assignment_names("gpt-executor")) == {
        "gpt-executor",
        "boris-executor",
    }


def test_executor_alias_does_not_match_unrelated_agents():
    assert not a2a._assignment_matches("boris-executor", "claude-lead")
    assert not a2a._assignment_matches("boris-executor", "chatgpt")
    assert not a2a._assignment_matches("claude-lead", "gpt-executor")


def test_suffixed_executor_units_share_the_same_claim_pool():
    for worker in ("boris-executor-2", "boris-executor-8",
                   "gpt-executor-2", "gpt-executor-8"):
        assert a2a._assignment_matches("gpt-executor", worker)
        assert a2a._assignment_matches("boris-executor", worker)
        assert set(a2a._assignment_names(worker)) == {
            "gpt-executor",
            "boris-executor",
        }
