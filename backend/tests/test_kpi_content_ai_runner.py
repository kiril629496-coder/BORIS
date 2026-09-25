from pathlib import Path

ROOT = Path("/root/BORIS/backend")
SRC = (ROOT / "kpi_content_ai_runner.py").read_text(encoding="utf-8")


def test_content_ai_runner_is_bounded_and_paid_account_scoped():
    assert "MAX_TASKS_PER_CYCLE = 4" in SRC
    assert "get_goal_auto_accounts" in SRC
    assert "one task per account" in SRC.lower()
    assert "ready_for_ai" in SRC


def test_content_ai_runner_has_service_caller_fence():
    assert "boris-kpi-content-ai.service" in SRC
    assert "KPI_CONTENT_AI_BLOCKED_UNTRUSTED_CALLER" in SRC
    assert "/proc/self/cgroup" in SRC


def test_content_ai_runner_renews_current_day_ai_reservation_before_call():
    reserve = SRC.index("_ensure_current_day_reservation(task)")
    execute = SRC.index("kpi_ai_prepare_execute(KpiAiPrepareExecuteRequest", reserve)
    assert reserve < execute
    assert "_kpi_ai_reserve_internal" in SRC
    assert "kpi_ai_ledger:" in SRC


def test_content_ai_runner_has_no_money_or_avito_mutation_primitive():
    for forbidden in ("apply_one(", "setManual", "set_bid", "kpi_publish_changes(", "feed_items ="):
        assert forbidden not in SRC


def test_dry_run_cannot_run_queue_maintenance_before_service_fence():
    main = SRC[SRC.index("def main() -> int:"):]
    dry = main.index("if not args.apply:")
    caller = main.index("if not _service_caller_ok():")
    repair = main.index("repaired = _terminalize_exhausted_rejections(active)")
    assert dry < caller < repair


def test_twice_rejected_fact_guard_task_is_terminalized_not_retried_forever():
    assert "def _terminalize_exhausted_rejections" in SRC
    assert 'int(data.get("repair_attempts") or 0) < 1' in SRC
    assert 'proposal_rejected_reason' in SRC
    assert 'fact_guard_all_rejected' in SRC
    assert 'terminal_reason"] = "fact_guard_rejected_twice"' in SRC
    assert "_terminalize_second_rejection(task, result)" in SRC
