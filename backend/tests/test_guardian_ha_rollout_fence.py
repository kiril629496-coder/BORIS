import inspect
from unittest.mock import patch

import boris_guardian


def test_canonical_backend_rollout_active_detects_real_rolling_process():
    with patch.object(
        boris_guardian,
        "sh",
        return_value=(0, "123 /bin/bash /root/BORIS/backend/rolling_restart_backend.sh"),
    ):
        assert boris_guardian.canonical_backend_rollout_active() is True


def test_canonical_backend_rollout_active_is_false_without_rolling_process():
    with patch.object(boris_guardian, "sh", return_value=(1, "")):
        assert boris_guardian.canonical_backend_rollout_active() is False


def test_quick_fences_replica_self_heal_during_canonical_rollout():
    src = inspect.getsource(boris_guardian.quick)
    assert "rollout_active=canonical_backend_rollout_active()" in src
    assert "if rollout_active:" in src
    assert "API recovery delegated to boris-deploy" in src
    assert "heal(unit,s)" not in src
    assert src.index("if rollout_active:") < src.index("API recovery delegated to boris-deploy")


def test_rule_runtime_scheduler_heal_only_restarts_factory_timer():
    state = {}
    with patch.object(boris_guardian.time, "time", return_value=1000.0), \
         patch.object(boris_guardian, "enabled", return_value=True), \
         patch.object(boris_guardian, "active", return_value=True), \
         patch.object(boris_guardian, "sh", return_value=(0, "ok")) as run:
        out = boris_guardian.heal_factory_rule_runtime_scheduler(
            {"bad": ["scheduler_stalled"]}, state
        )
    assert out == {"attempted": True, "healed": True, "pending": True}
    run.assert_called_once_with("systemctl restart boris-factory.timer", timeout=15)
    assert state["factory_rule_runtime_recovery_pending_until"] == 1300.0


def test_rule_runtime_scheduler_heal_never_starts_factory_service_directly():
    src = inspect.getsource(boris_guardian.heal_factory_rule_runtime_scheduler)
    assert "boris-factory.timer" in src
    assert "boris-factory.service" not in src


def test_rule_runtime_stall_threshold_exceeds_factory_timer_cadence():
    src = inspect.getsource(boris_guardian.factory_rule_runtime_health)
    assert ">2400" in src
    assert ">1200" not in src
