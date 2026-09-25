from datetime import datetime, timezone
from pathlib import Path

from app.factory import rule_runtime as R


def _rule(target=4):
    return {
        "timezone": "UTC",
        "window_start": "00:00",
        "window_end": "23:59",
        "daily_target": target,
    }


def test_daily_target_is_spread_across_day_not_claimed_as_done_by_one_run():
    rule = _rule(4)
    day, expected_00 = R._expected(rule, datetime(2026, 9, 11, 0, 1, tzinfo=timezone.utc))
    _, expected_06 = R._expected(rule, datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc))
    _, expected_12 = R._expected(rule, datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc))
    _, expected_end = R._expected(rule, datetime(2026, 9, 11, 23, 59, tzinfo=timezone.utc))
    assert day.isoformat() == "2026-09-11"
    assert expected_00 == 0
    assert expected_06 == 1
    assert expected_12 == 2
    assert expected_end == 4


def test_execution_key_is_stable_per_rule_day_slot_and_unique_across_slots():
    a = R._execution_key("rule", datetime(2026, 9, 11).date(), 1)
    b = R._execution_key("rule", datetime(2026, 9, 11).date(), 1)
    c = R._execution_key("rule", datetime(2026, 9, 11).date(), 2)
    assert a == b
    assert a != c
    assert len(a) == 64


def test_runtime_has_no_arbitrary_shell_executor():
    assert set(R.EXECUTORS) == {"core_integrity_audit", "mop_takeover_reconcile"}


def test_core_integrity_executor_checks_current_production_sources():
    out = R._executor_core_integrity(None, {}, "qa")
    assert out["duplicate_symbols"] == {}
    assert out["human_takeover_state_defs"] == 1
    assert out["legacy_human_takeover_active_defs"] == 0
    assert out["do_send_defs"] == 1


def test_existing_factory_cycle_is_the_only_rule_runtime_scheduler_hook():
    root = Path(__file__).resolve().parents[1]
    src = (root / "factory_h_runner.py").read_text(encoding="utf-8")
    assert src.count("_rule_runtime.run_due(") == 1
    assert "RULE_RUNTIME_V1" in src
    assert set(R.VERIFIERS) == set(R.EXECUTORS)


def test_core_integrity_has_separate_postcondition_verifier():
    out = R._verify_result(None, {"executor_key": "core_integrity_audit"}, "qa", {})
    assert out["verified"] is True
    assert out["checks"]["human_takeover_state_defs"] == 1



def test_negative_verifier_is_fail_closed(monkeypatch):
    import pytest
    monkeypatch.setitem(R.VERIFIERS, "qa_false", lambda db, rule, key, result: {"verified": False})
    with pytest.raises(RuntimeError, match="postcondition_not_verified"):
        R._verify_result(None, {"executor_key": "qa_false"}, "qa", {})




def test_missing_verifier_is_fail_closed(monkeypatch):
    import pytest
    monkeypatch.delitem(R.VERIFIERS, "core_integrity_audit", raising=False)
    with pytest.raises(RuntimeError, match="verifier_not_registered"):
        R._verify_result(None, {"executor_key": "core_integrity_audit"}, "qa", {})


def test_run_due_verifies_before_success_write():
    import inspect
    src = inspect.getsource(R.run_due)
    assert src.index("_verify_result(") < src.index("status='SUCCESS'")


def test_force_run_verifies_before_success_write():
    import inspect
    src = inspect.getsource(R.force_run)
    assert src.index("_verify_result(") < src.index("status='SUCCESS'")
    assert "verification" in src
