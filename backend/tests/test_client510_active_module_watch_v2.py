from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_active_module_watch_uses_scope_aware_rule_coverage():
    src = (ROOT / "boris_guardian.py").read_text(encoding="utf-8")
    assert "ACTIVE_MODULE_WATCH_SCOPE_AWARE_LEDGER_V2" in src
    assert "RULE_COVERAGE_SCOPE_AWARE_V2" in src
    assert "scope_type='global'" in src
    assert "scope_type='account'" in src
    assert "global_contract_plus_exact_account_scope_v2" in src
    assert "'version':2" in src


def test_provider_402_is_owner_visible_in_watch_snapshot():
    src = (ROOT / "boris_guardian.py").read_text(encoding="utf-8")
    assert "MOP_PROVIDER_PARTIAL_402" in src
    assert "MOP_PROVIDER_BLOCKED_402" in src
    assert "owner_action_required=True" in src
    assert "restore_avito_messenger_api_access" in src


def test_rule_coverage_incident_uses_applicable_scope_truth():
    src = (ROOT / "boris_guardian.py").read_text(encoding="utf-8")
    assert "applicable business rules evaluated today" in src
    assert "Record missing global contract or exact account-scoped rule evaluation evidence" in src
    assert "active_client_module_watch_v2" in src
