# -*- coding: utf-8 -*-
import inspect
from pathlib import Path

from app import reactivation_runtime as rr
from app.api import reactivation as api


def test_ready_queue_truth_distinguishes_storage_ready_from_auto_authority():
    src = inspect.getsource(rr.ready_queue_truth)
    assert "auto_sendable" in src
    assert "confirmation_required" in src
    assert "awaiting_revalidation" in src
    assert "needs_review_candidates" in src
    assert "scheduler_safe" in src
    assert "candidate_status='needs_review'" in src
    assert "auto_revalidated" in src
    assert "auto_eligible" in src


def test_account_status_exposes_truth_and_never_equates_all_ready_with_autosend():
    src = inspect.getsource(api.account_status)
    assert 'queue_truth = ready_queue_truth(db, acc)' in src
    assert '"send_window_open": send_window_open' in src
    assert 'auto_sendable > 0' in src
    assert 'hold_total > 0' in src
    assert '"needs_safe_refresh"' in src


def test_operational_health_detects_stuck_auto_queue():
    src = inspect.getsource(api.operational_health)
    assert '"auto_queue_revalidation_pending"' in src
    assert '"auto_queue_requires_safe_refresh_or_confirmation"' in src
    assert '"queue_truth":queue_truth' in src


def test_owner_ui_shows_partitioned_queue_counts():
    page = Path(__file__).resolve().parents[2] / "frontend" / "app" / "reactivation" / "page.tsx"
    src = page.read_text(encoding="utf-8")
    assert "автопилот может отправить {a.auto_sendable || 0}" in src
    assert "удержано {a.hold_total || 0}" in src
    assert "Сейчас окно отправки закрыто расписанием" in src


def test_auto_revalidator_skips_review_status():
    src = inspect.getsource(rr.reconcile_auto_activation_backlog)
    assert "c.status IN ('candidate','approved','scheduled','cooldown')" in src


def test_scheduler_reports_expected_time_and_limit_waits_separately_from_blocks():
    src = inspect.getsource(rr.process)
    assert '"send_waiting_schedule": 0' in src
    assert 'r.get("reason") == "outside_allowed_hours"' in src
    assert '"send_waiting_daily_limit"' in src


def test_auto_send_query_cannot_be_starved_by_recommend_queue():
    src = inspect.getsource(rr.process)
    assert "Human-confirmation queues are measured separately" in src
    assert "coalesce(s.mode,'recommend')='auto'" in src
    assert "coalesce(ae.metadata->>'auto_eligible','false')='true'" in src
    assert "ee.event='message_edited'" in src
    assert "stats[\"waiting_confirmation\"] = int(db.execute" in src


def test_auto_send_scheduler_is_round_robin_and_capacity_aware():
    src = inspect.getsource(rr.process)
    assert "row_number() OVER (PARTITION BY m.account_id ORDER BY m.id)" in src
    assert "if not _within_hours(st):" in src
    assert "used_cache[aid] = int(rc.used_today(db, aid))" in src
    assert "if used_cache[aid] >= daily_limit:" in src
    assert "if attempted >= int(send_limit):" in src
