from pathlib import Path


def test_module_activity_invariant_is_reconciled_after_activity_scan():
    source = (Path(__file__).resolve().parents[1] / "control_plane_guardian_runner.py").read_text(encoding="utf-8")
    activity = source.index("MODULE_ACTIVITY_HEALTH_V1")
    post = source.index("MODULE_ACTIVITY_INVARIANT_POST_RECONCILE_V1")
    business = source.index("BUSINESS_STATE_HEALTH_V1")
    assert activity < post < business
    block = source[post:business]
    assert 'incident_type=="MODULE_STALLED"' in block
    assert 'evidence_type="module_activity_post_reconcile"' in block
    assert 'transition_execution(db,_health_ex,"pass"' in block
    assert 'incident_type=="SYSTEM_INVARIANT_FAILED"' in block
    ledger = source.index("RUNTIME_LEDGER_PLAN_FACT_V1")
    post_ledger = source.index("MODULE_ACTIVITY_POST_RUNTIME_LEDGER_V1")
    registry = source.index("ARCH_REGISTRY_RUNTIME_EVIDENCE_V1")
    assert ledger < post_ledger < registry
    post_ledger_block = source[post_ledger:registry]
    assert 'incident_type=="MODULE_STALLED"' in post_ledger_block
    assert 'ControlExecution.status.in_(("pass","degraded","blocked","waiting_external","waiting_owner"))' in post_ledger_block
    assert 'ControlRequirement.last_verified_at.isnot(None)' in post_ledger_block
    assert 'MODULE_ACTIVITY_INVARIANT_FINAL_RECONCILE_V1' in post_ledger_block
    assert 'evidence_type="module_activity_final_reconcile"' in post_ledger_block
