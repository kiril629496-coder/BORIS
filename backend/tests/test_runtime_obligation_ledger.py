from pathlib import Path

from app.services.runtime_obligation_ledger import REQUIRED_LEDGER_MODULES, _facts


def test_required_runtime_ledger_modules_are_exact_audit_set():
    assert set(REQUIRED_LEDGER_MODULES) == {
        "marketing", "mop", "email", "telephony", "feed_factory", "social", "prospecting"
    }


def test_marketing_plan_fact_is_not_timer_health():
    fact = _facts("marketing", {
        "state": "portfolio", "target_leads_total": 32, "actual_leads_total": 15,
        "portfolio_accounts": [{"runtime_planned": 2, "runtime_failed": 0}],
    }, {"health": "degraded", "classification": "KPI_OFF_TARGET"})
    assert fact["plan_quantity"] == 32
    assert fact["succeeded_quantity"] == 15
    assert fact["waiting_quantity"] == 17
    assert fact["state"] == "DEGRADED"


def test_mop_plan_fact_exposes_wait_and_failure_counts():
    fact = _facts("mop", {
        "drafts_24h": 23, "sent_24h": 7, "unresolved_send_failed": 1,
        "stale_analyzing": 2, "waiting_external": 4, "auto_send_unresolved_ready": 1,
        "owner_required_human_required": 0,
    }, {"health": "degraded", "classification": "MOP_SEND_FAILURES"})
    assert fact["plan_quantity"] == 23
    assert fact["attempted_quantity"] == 23
    assert fact["succeeded_quantity"] == 7
    assert fact["failed_quantity"] == 3
    assert fact["waiting_quantity"] == 5


def test_email_plan_fact_counts_queue_and_delivery():
    fact = _facts("email", {"sent_24h": 24, "queued": 3, "stale_due": 0, "stale_sending": 0, "failed_24h": 1}, {"health": "degraded", "classification": "EMAIL_DELIVERY_FAILURES"})
    assert fact["plan_quantity"] == 28
    assert fact["succeeded_quantity"] == 24
    assert fact["waiting_quantity"] == 3
    assert fact["failed_quantity"] == 1


def test_runtime_ledger_migration_uses_existing_control_plane_tables():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "048_runtime_ledger_obligations.sql").read_text(encoding="utf-8")
    assert sql.count("('runtime_ledger.") == 7
    assert "runtime_ledger_obligation_v1" in sql
    assert "control_executions" in sql
    assert "control_execution_steps" in sql
    assert "control_execution_attempts" in sql
    assert "control_evidence" in sql


def test_guardian_materializes_plan_fact_and_skips_registry_execution():
    root = Path(__file__).resolve().parents[1]
    guardian = (root / "control_plane_guardian_runner.py").read_text(encoding="utf-8")
    control = (root / "app" / "services" / "control_plane.py").read_text(encoding="utf-8")
    assert "RUNTIME_LEDGER_PLAN_FACT_V1" in guardian
    assert "record_required_snapshots" in guardian
    assert '~ControlRequirement.requirement_type.in_(("architecture_rule","business_rule"))' in guardian
    assert "REGISTRY_ONLY_REQUIREMENT_TYPES" in control
    assert "if r.requirement_type in REGISTRY_ONLY_REQUIREMENT_TYPES" in control


def test_runtime_ledger_facts_serialize_native_datetime():
    import json
    from datetime import datetime, timezone

    ts = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    fact = _facts(
        "mop",
        {"drafts_24h": 1, "sent_24h": 1, "last_message_at": ts},
        {"health": "pass", "classification": "MOP_OK", "checked_at": ts},
    )
    encoded = json.dumps(fact, ensure_ascii=False)
    assert "2026-09-11 20:00:00+00:00" in encoded
    assert fact["metrics"]["last_message_at"] == "2026-09-11 20:00:00+00:00"
    assert fact["diagnosis"]["checked_at"] == "2026-09-11 20:00:00+00:00"
