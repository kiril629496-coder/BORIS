from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_observation_window_is_not_false_stale_incident():
    s=(ROOT/"guardian_lifecycle_snapshot.py").read_text(encoding="utf-8")
    assert "observation_wait" in s and "waiting_observation_window" in s
    assert "severity=\"observation\"" in s
def test_bounded_provider_stall_is_immediate_incident():
    s=(ROOT/"guardian_lifecycle_snapshot.py").read_text(encoding="utf-8")
    assert "publish_provider_stalled_at" in s
    assert "rollback_provider_stalled_at" in s
    assert "severity=\"incident\"" in s

def test_budget_brake_self_heal_uses_early_safety_cutoff():
    s=(ROOT/"guardian_lifecycle_snapshot.py").read_text(encoding="utf-8")
    assert "GUARDIAN_BUDGET_BRAKE_SELF_HEAL_V1" in s
    assert "used < 90.0" in s
    assert "used < 100.0" not in s
