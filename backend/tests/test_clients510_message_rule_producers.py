"""Execute actual changed producer function bodies with provider-free seams."""
import ast
from pathlib import Path
import sys
import types
import hashlib
import re
import os
import pytest
import importlib.util

# Canonical QA imports this file as tests.<name>. Resolve the fixture by its
# exact sibling path; do not depend on a bare test-module name or sys.path.
_unit_path = Path(__file__).resolve().with_name("test_clients510_message_rule_facts.py")
_unit_spec = importlib.util.spec_from_file_location("clients510_message_rule_fixture", _unit_path)
assert _unit_spec is not None and _unit_spec.loader is not None
unit = importlib.util.module_from_spec(_unit_spec)
_unit_spec.loader.exec_module(unit)

ROOT = Path(__file__).parent
SOURCE = Path(os.environ.get("BORIS_MSG_PRODUCER_SOURCE_ROOT", str(ROOT / "producer-candidate" if (ROOT / "producer-candidate").exists() else ROOT.parent)))


def function(path, name, scope=None):
    tree = ast.parse(path.read_text())
    if scope:
        tree = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == scope)
    fn = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.Module(body=[fn], type_ignores=[])


@pytest.fixture(autouse=True)
def module(monkeypatch):
    monkeypatch.setitem(sys.modules, "app", types.ModuleType("app"))
    monkeypatch.setitem(sys.modules, "app.services", types.ModuleType("app.services"))
    monkeypatch.setitem(sys.modules, "app.services.control_message_rule_facts", unit.facts)


def finalizer(policy=None):
    namespace = {"account_id": "A", "chat_id": "C", "proactive_event": None,
        "_first_manager_turn": True, "_first_account_intro": "Здравствуйте!", "_first_account_name": "",
        "_mop_client_visible_text": lambda value: (value, False),
        "_mop_output_policy_violations": policy or (lambda value: []),
        "observe_suppressed": lambda *args, **kwargs: None,
        "_wrap": lambda text, usage, return_meta, analysis: {"text": text, "usage": usage, "analysis": analysis}}
    exec(compile(function(SOURCE / "app/api/messenger.py", "_finalize", "generate_ai_draft_reply"), "actual-finalizer", "exec"), namespace)
    return namespace["_finalize"]


def test_actual_finalizer_retains_response_and_adds_bound_receipt():
    usage = {"model": "test", "cost_rub": 0}
    result = finalizer()("Да.", usage, True)
    assert result["text"] == "Здравствуйте! Да."
    receipt = result["usage"]["message_rule_facts"]
    assert receipt["configured_intro_prefix"] is True
    assert receipt["reply_text_hash"] == unit.facts._hash(result["text"])
    assert usage == {"model": "test", "cost_rub": 0}


@pytest.mark.parametrize("usage", [None, {}, False])
def test_failed_usage_cannot_become_successful_from_receipt(usage):
    assert finalizer()("Да.", usage, True)["usage"] is usage


def test_observer_failure_does_not_change_reply_or_mark_qa_pass():
    def failed(value): raise RuntimeError("checker unavailable")
    result = finalizer(failed)("Да.", {"model": "test"}, True)
    assert result["text"] == "Здравствуйте! Да."
    assert "message_rule_facts" not in result["usage"]


def test_actual_claim_metadata_same_atomic_transition():
    calls = []
    def set_status(*args, **kwargs):
        calls.append((args, kwargs)); return {"id": 1}, "draft_ready"
    ns = {"set_status": set_status}
    exec(compile(function(SOURCE / "app/mop_core.py", "claim_for_send"), "actual-claim", "exec"), ns)
    meta = {"message_send_rule_facts": {"actual": True}}
    ns["claim_for_send"](object(), 1, "telegram_user", "account_autopilot", "mop_autopilot", meta=meta)
    assert len(calls) == 1
    assert calls[0][1]["event"] == "send_started"
    assert calls[0][1]["meta"] is meta
    assert calls[0][1]["allowed_from"] == ("draft_ready", "in_progress", "waiting_confirm", "send_failed")


def test_hash_exact_canonical_normalization_including_non_newline_separators():
    tree = ast.parse((SOURCE / "app/mop_core.py").read_text())
    fns = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"normalize_text", "text_hash"}]
    ns = {"hashlib": hashlib, "re": re}
    exec(compile(ast.Module(body=fns, type_ignores=[]), "canonical-hash", "exec"), ns)
    for value in (None, " A\r\n B \tC \n\n\n D ", "a\vb\fc\u2028d", "", "Привет"):
        assert unit.facts._hash(value) == ns["text_hash"](value)


def test_initial_snapshot_and_usage_carry_preserve_generation_success_condition():
    tree = ast.parse((SOURCE / "app/api/messenger.py").read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_mop_incoming")
    # Actual carry block runs only after generation returns; it must not make an
    # empty usage success. This executes the exact changed AST, not a copy.
    nodes = fn.body
    start = next(i for i,n in enumerate(nodes) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "usage" for t in n.targets))
    block = ast.Module(body=nodes[start:start+2], type_ignores=[])
    for value in (None, {}, {"model": "canonical"}):
        ns = {"gen": {"usage": value}, "_message_eligibility_facts": {"checks": "frozen"}}
        exec(compile(block, "actual-carry", "exec"), ns)
        assert bool(ns["usage"]) == bool(value)
        if value:
            assert ns["usage"]["message_eligibility_facts"] == {"checks": "frozen"}
            assert value == {"model": "canonical"}
