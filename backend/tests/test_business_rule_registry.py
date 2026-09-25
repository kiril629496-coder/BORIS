from pathlib import Path
from types import SimpleNamespace

from app.services.architecture_registry import (
    CANONICAL_STATES,
    REQUIRED_BUSINESS_RULE_COUNT,
    REQUIRED_BUSINESS_RULE_FAMILY_COUNTS,
    validate_business_rules,
)


def _contracts():
    return {module: {"writer": f"{module}_owner"} for module in REQUIRED_BUSINESS_RULE_FAMILY_COUNTS}


def _rows():
    rows = []
    seq = 0
    for module, count in REQUIRED_BUSINESS_RULE_FAMILY_COUNTS.items():
        for idx in range(count):
            seq += 1
            rows.append(SimpleNamespace(
                key=f"rule.{module}.test_{idx}",
                module=module,
                scope_type="global",
                scope_id="*",
                source_json={
                    "catalog": "architecture_business_rule_v1",
                    "source_of_truth": "control_plane",
                    "rule_id": f"TEST-{seq:03d}",
                    "owner": f"{module}_owner",
                    "operation": "INTERNAL_TEST",
                    "mandatory_tests": ["test_contract"],
                    "external_side_effect": False,
                    "money_side_effect": False,
                    "tenant_scope": "account_required",
                },
                desired_state_json={
                    "rule_id": f"TEST-{seq:03d}",
                    "operation": "INTERNAL_TEST",
                    "allowed_states": list(CANONICAL_STATES),
                },
            ))
    return rows


def test_business_rule_catalog_minimum_is_115():
    assert REQUIRED_BUSINESS_RULE_COUNT == 115
    assert sum(REQUIRED_BUSINESS_RULE_FAMILY_COUNTS.values()) == 115


def test_business_rule_validator_accepts_complete_catalog():
    result = validate_business_rules(_rows(), _contracts(), {})
    assert result["ok"] is True
    assert result["rules"] == result["required_rules"] == 115


def test_business_rule_validator_blocks_missing_family_rule():
    rows = _rows()
    rows.pop()
    result = validate_business_rules(rows, _contracts(), {})
    assert result["ok"] is False
    assert any(e.get("code") in {"business_rule_family_incomplete", "business_rule_catalog_incomplete"} for e in result["errors"])


def test_business_rule_migration_contains_full_audit_catalog():
    text = (Path(__file__).resolve().parents[1] / "migrations" / "047_business_rule_registry.sql").read_text(encoding="utf-8")
    assert text.count("('rule.") == REQUIRED_BUSINESS_RULE_COUNT
    for module, required in REQUIRED_BUSINESS_RULE_FAMILY_COUNTS.items():
        assert text.count(f"('rule.{module}.") == required
    assert "control_requirement_versions" in text
    assert "architecture_business_rule_v1" in text
