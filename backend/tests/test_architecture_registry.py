# -*- coding: utf-8 -*-
from app.services.architecture_registry import (
    CANONICAL_STATES,
    REQUIRED_MODULES,
    dependency_closure,
    mandatory_tests_for_modules,
    validate_contract_documents,
)


def _doc(module, *, deps=None, tests=None):
    return {
        "contract_version": 2,
        "writer": f"{module}_writer",
        "source_of_truth": f"{module}_truth",
        "input": "input",
        "output": "output",
        "states": list(CANONICAL_STATES),
        "idempotency": "required",
        "retry": "bounded",
        "dependencies": list(deps or []),
        "done_proof": "durable evidence",
        "mandatory_tests": list(tests or [f"test_{module}"]),
        "tenant_isolation": "required",
    }


def _complete_contracts():
    return {name: _doc(name) for name in REQUIRED_MODULES}


def test_complete_contract_set_is_valid():
    contracts = _complete_contracts()
    result = validate_contract_documents(contracts)
    assert result["ok"] is True
    assert result["errors"] == []


def test_contract_requires_exact_canonical_states_and_known_dependencies():
    contracts = _complete_contracts()
    contracts["marketing"] = _doc("marketing", deps=["missing_module"])
    contracts["marketing"]["states"] = ["OK", "FAILED_INTERNAL"]
    result = validate_contract_documents(contracts)
    codes = {(x["code"], x.get("module")) for x in result["errors"]}
    assert ("operation_states_mismatch", "marketing") in codes
    assert ("unknown_dependencies", "marketing") in codes


def test_dependency_closure_includes_transitive_dependents():
    contracts = _complete_contracts()
    contracts["analytics"] = _doc("analytics", tests=["analytics_test"])
    contracts["marketing"] = _doc("marketing", deps=["analytics"], tests=["marketing_test"])
    contracts["feed_factory"] = _doc("feed_factory", deps=["marketing"], tests=["feed_test"])
    closure = dependency_closure(contracts, {"analytics"})
    assert "analytics" in closure
    assert "marketing" in closure
    assert "feed_factory" in closure
    tests = mandatory_tests_for_modules(contracts, {"analytics"})
    assert {"analytics_test", "marketing_test", "feed_test"}.issubset(set(tests))


def test_missing_required_module_is_rejected():
    contracts = _complete_contracts()
    contracts.pop("telephony")
    result = validate_contract_documents(contracts)
    assert result["ok"] is False
    assert any(x["code"] == "missing_module_contracts" and "telephony" in x["modules"] for x in result["errors"])
