from app.services.architecture_registry import (
    REQUIRED_DEPENDENCY_CLOSURES,
    dependency_closure,
    mandatory_tests_for_modules,
)


def _contracts():
    # Same dependency direction as Module Contracts: module -> modules it consumes.
    return {
        "crm": {"dependencies": ["messages", "telephony"], "mandatory_tests": ["crm_test"]},
        "messages": {"dependencies": ["crm"], "mandatory_tests": ["messages_test"]},
        "mop": {"dependencies": ["messages", "crm"], "mandatory_tests": ["mop_test"]},
        "rop": {"dependencies": ["crm", "messages", "telephony"], "mandatory_tests": ["rop_test"]},
        "telephony": {"dependencies": ["crm", "billing"], "mandatory_tests": ["phone_test"]},
        "calltracking": {"dependencies": ["telephony", "crm"], "mandatory_tests": ["calltracking_test"]},
        "reactivation": {"dependencies": ["messages", "crm"], "mandatory_tests": ["reactivation_test"]},
        "billing": {"dependencies": [], "mandatory_tests": ["billing_test"]},
        "email": {"dependencies": ["billing"], "mandatory_tests": ["email_test"]},
        "marketing": {"dependencies": ["crm", "billing"], "mandatory_tests": ["marketing_test"]},
        "feed_factory": {"dependencies": ["marketing"], "mandatory_tests": ["feed_test"]},
        "social": {"dependencies": ["billing"], "mandatory_tests": ["social_test"]},
    }


def test_required_dependency_closures_are_preserved():
    contracts = _contracts()
    for changed, required in REQUIRED_DEPENDENCY_CLOSURES.items():
        closure = set(dependency_closure(contracts, [changed]))
        assert required <= closure, (changed, required - closure)
        assert mandatory_tests_for_modules(contracts, [changed])


def test_crm_change_selects_sales_and_phone_dependents():
    closure = set(dependency_closure(_contracts(), ["crm"]))
    assert {"crm", "mop", "rop", "telephony", "calltracking"} <= closure


def test_missing_dependency_edge_is_detectable():
    contracts = _contracts()
    contracts["mop"] = {**contracts["mop"], "dependencies": ["messages"]}
    contracts["rop"] = {**contracts["rop"], "dependencies": ["messages", "telephony"]}
    contracts["telephony"] = {**contracts["telephony"], "dependencies": ["billing"]}
    contracts["calltracking"] = {**contracts["calltracking"], "dependencies": ["telephony"]}
    closure = set(dependency_closure(contracts, ["crm"]))
    assert not {"mop", "rop", "telephony", "calltracking"} <= closure
