from pathlib import Path
from types import SimpleNamespace

from app.services.architecture_registry import (
    CANONICAL_STATES,
    REQUIRED_ACTIONS,
    validate_action_owners,
)


MODULE_BY_ACTION = {
    "AVITO_BID_WRITE": "marketing",
    "AVITO_BUDGET_WRITE": "marketing",
    "AVITO_PROMOTION_WRITE": "marketing",
    "AVITO_PUBLISH": "feed_factory",
    "AVITO_UNPUBLISH": "feed_factory",
    "MESSAGE_SEND": "messages",
    "EMAIL_SEND": "email",
    "PHONE_PROVIDER_COMMAND": "telephony",
    "PHONE_RECORDING_FETCH": "telephony",
    "PHONE_PUSH_SEND": "telephony",
    "CRM_DEAL_CREATE": "crm",
    "CRM_STATE_WRITE": "crm",
    "CRM_TASK_WRITE": "crm",
    "FEED_PUBLISH": "feed_factory",
    "SOCIAL_PUBLISH": "social",
    "SOCIAL_DELETE": "social",
    "BILLING_CHARGE": "billing",
    "BILLING_USAGE_WRITE": "billing",
    "DEPLOY_RELEASE": "development",
    "DB_SCHEMA_CHANGE": "development",
}


def _rows():
    rows = {}
    for action in REQUIRED_ACTIONS:
        rows[action] = SimpleNamespace(
            owner_module=MODULE_BY_ACTION[action],
            owner_workstream="canonical_workstream",
            owner_service="canonical.service",
            canonical_entrypoint="app.services.architecture_registry:validate_action_owners",
            source_of_truth="canonical durable state",
            terminal_states_json=list(CANONICAL_STATES),
            idempotency_required=True,
            runtime_ledger_required=True,
            tenant_scope_required=action not in {"DEPLOY_RELEASE", "DB_SCHEMA_CHANGE"},
            proof_required=True,
            external_side_effect=True,
            money_side_effect=action in {"AVITO_BID_WRITE", "AVITO_BUDGET_WRITE", "AVITO_PROMOTION_WRITE", "BILLING_CHARGE", "BILLING_USAGE_WRITE"},
            source_json={"source_of_truth": "control_plane"},
        )
    return rows


def _contracts():
    return {module: {} for module in set(MODULE_BY_ACTION.values())}


def test_required_action_owner_registry_is_complete():
    result = validate_action_owners(_rows(), _contracts())
    assert result["ok"] is True
    assert result["actions"] == result["required_actions"] == len(REQUIRED_ACTIONS)


def test_missing_action_owner_is_release_blocking():
    rows = _rows()
    rows.pop("MESSAGE_SEND")
    result = validate_action_owners(rows, _contracts())
    assert result["ok"] is False
    assert any(e.get("code") == "action_owners_missing" and "MESSAGE_SEND" in e.get("actions", []) for e in result["errors"])


def test_money_action_must_be_tenant_scoped():
    rows = _rows()
    rows["AVITO_BID_WRITE"].tenant_scope_required = False
    result = validate_action_owners(rows, _contracts())
    assert result["ok"] is False
    assert {"code": "money_action_not_tenant_scoped", "action": "AVITO_BID_WRITE"} in result["errors"]


def test_action_owner_migration_has_versioned_registry():
    text = (Path(__file__).resolve().parents[1] / "migrations" / "046_action_owner_registry.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS control_action_owners" in text
    assert "CREATE TABLE IF NOT EXISTS control_action_owner_versions" in text
    for action in REQUIRED_ACTIONS:
        assert "'" + action + "'" in text

def test_nonexistent_entrypoint_remains_release_blocking():
    rows = _rows()
    rows["MESSAGE_SEND"].canonical_entrypoint = "canonical.module:call"
    result = validate_action_owners(rows, _contracts())
    assert result["ok"] is False
    assert any(e["code"] == "action_owner_entrypoint_missing" for e in result["errors"])
