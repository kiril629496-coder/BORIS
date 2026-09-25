#!/usr/bin/env python3
"""Bounded paid-AI executor for existing KPI content prepare tasks.

This runner NEVER changes Avito, feed items, bids or promotion. It only turns
already-reserved ``ready_for_ai`` content tasks into validated ``proposal_ready``
results through the existing kpi_ai_prepare_execute() implementation.

Production apply is accepted only from boris-kpi-content-ai.service. Candidate
accounts come from get_goal_auto_accounts(), so expired/unpaid/disabled tenants
are not granted AI work here. One task per account, oldest tasks first.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.api.avito import (
    KpiAiPrepareExecuteRequest,
    _kpi_ai_reserve_internal,
    _kpi_marketing_today,
    kpi_ai_prepare_execute,
)
from kpi_goal_runner import get_goal_auto_accounts

MAX_TASKS_PER_CYCLE = 4
SERVICE_CGROUP = "boris-kpi-content-ai.service"
SUPPORTED_ACTIONS = {"prepare_weak_titles", "prepare_weak_descriptions"}


def _service_caller_ok() -> bool:
    try:
        raw = Path("/proc/self/cgroup").read_text(errors="ignore")
    except Exception:
        return False
    return SERVICE_CGROUP in raw


def _active_paid_account_ids() -> list[str]:
    return sorted({
        str(x.get("account_id") or "")
        for x in (get_goal_auto_accounts() or [])
        if str(x.get("account_id") or "")
    })


def _select_ready_tasks(account_ids: list[str], only_account: str | None = None) -> list[dict]:
    wanted = set(account_ids)
    if only_account:
        wanted &= {str(only_account)}
    if not wanted:
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(Storage)
            .filter(Storage.account_id.in_(sorted(wanted)), Storage.key.like("kpi_prepare:%"))
            .order_by(Storage.id.asc())
            .all()
        )
        first_by_account: dict[str, dict] = {}
        for row in rows:
            aid = str(row.account_id or "")
            if aid in first_by_account:
                continue
            try:
                data = json.loads(row.value or "{}")
            except Exception:
                continue
            if not isinstance(data, dict) or str(data.get("status") or "") != "ready_for_ai":
                continue
            action = str(data.get("action") or "")
            if action not in SUPPORTED_ACTIONS:
                continue
            first_by_account[aid] = {
                "storage_id": int(row.id),
                "account_id": aid,
                "operation_id": str(data.get("operation_id") or row.key.split(":", 1)[-1]),
                "action": action,
                "estimated_ai_rub": float(data.get("estimated_ai_rub") or 0.0),
            }
        return sorted(first_by_account.values(), key=lambda x: (x["storage_id"], x["account_id"]))
    finally:
        db.close()


def _ensure_current_day_reservation(task: dict) -> dict:
    """Make an old ready task count against the CURRENT marketing-day AI cap."""
    aid = task["account_id"]
    op_id = task["operation_id"]
    action = task["action"]
    amount = max(0.0, float(task.get("estimated_ai_rub") or 0.0))
    day = _kpi_marketing_today().isoformat()
    key = f"kpi_ai_ledger:{day}"
    db = SessionLocal()
    try:
        row = (
            db.query(Storage)
            .filter(Storage.account_id == aid, Storage.key == key)
            .order_by(Storage.id.desc())
            .first()
        )
        payload = {}
        if row:
            try:
                payload = json.loads(row.value or "{}")
            except Exception:
                payload = {}
        for existing in (payload.get("operations") or []):
            if str(existing.get("operation_id") or "") != op_id:
                continue
            status = str(existing.get("status") or "")
            if status in {"reserved", "committed"}:
                return {"status": "ready", "day": day, "ledger_status": status}
            return {"status": "blocked", "reason": f"current_day_ai_ledger_status_{status or unknown}", "day": day}
        reserve = _kpi_ai_reserve_internal(db, aid, op_id, action, amount)
        if str(reserve.get("status") or "") == "reserved":
            return {"status": "ready", "day": day, "ledger_status": "reserved", "renewed": True}
        return {"status": "blocked", "reason": str(reserve.get("reason") or reserve.get("status") or "ai_reservation_failed"), "day": day}
    finally:
        db.close()


def _terminalize_exhausted_rejections(account_ids: list[str]) -> list[dict]:
    """Close a twice-rejected AI hypothesis so it cannot starve the account.

    ``repair_attempts>=1`` means kpi_goal_tick already granted the single
    controlled repair. If Fact Guard rejected that repaired proposal again,
    no more AI retries for this hypothesis are allowed. This is internal state
    only: no feed/provider/money mutation.
    """
    if not account_ids:
        return []
    from datetime import datetime, timezone
    db = SessionLocal()
    changed = []
    try:
        rows = (
            db.query(Storage)
            .filter(Storage.account_id.in_(sorted(set(account_ids))), Storage.key.like("kpi_prepare:%"))
            .order_by(Storage.id.asc())
            .all()
        )
        for row in rows:
            try:
                data = json.loads(row.value or "{}")
            except Exception:
                continue
            if str(data.get("status") or "") != "proposal_rejected":
                continue
            if int(data.get("repair_attempts") or 0) < 1:
                continue
            if str(data.get("proposal_rejected_reason") or "") != "fact_guard_all_rejected":
                continue
            data["status"] = "terminal_noop_or_unpublishable"
            data["terminal_reason"] = "fact_guard_rejected_twice"
            data["terminal_at"] = datetime.now(timezone.utc).isoformat()
            row.value = json.dumps(data, ensure_ascii=False)
            changed.append({
                "account_id": str(row.account_id or ""),
                "operation_id": str(data.get("operation_id") or ""),
                "terminal_reason": data["terminal_reason"],
            })
        if changed:
            db.commit()
        else:
            db.rollback()
        return changed
    finally:
        db.close()


def _terminalize_second_rejection(task: dict, result: dict) -> dict | None:
    """Immediately close the just-rejected repaired task after its second try."""
    if str(result.get("status") or "") != "proposal_rejected":
        return None
    aid = task["account_id"]
    op_id = task["operation_id"]
    db = SessionLocal()
    try:
        row = (
            db.query(Storage)
            .filter(Storage.account_id == aid, Storage.key == f"kpi_prepare:{op_id}")
            .first()
        )
        if not row:
            return None
        try:
            data = json.loads(row.value or "{}")
        except Exception:
            return None
        if str(data.get("status") or "") != "proposal_rejected":
            return None
        if int(data.get("repair_attempts") or 0) < 1:
            return None
        if str(data.get("proposal_rejected_reason") or "") != "fact_guard_all_rejected":
            return None
        from datetime import datetime, timezone
        data["status"] = "terminal_noop_or_unpublishable"
        data["terminal_reason"] = "fact_guard_rejected_twice"
        data["terminal_at"] = datetime.now(timezone.utc).isoformat()
        row.value = json.dumps(data, ensure_ascii=False)
        db.commit()
        return {"status": data["status"], "terminal_reason": data["terminal_reason"]}
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=MAX_TASKS_PER_CYCLE)
    parser.add_argument("--account")
    args = parser.parse_args()

    limit = max(1, min(int(args.limit or MAX_TASKS_PER_CYCLE), MAX_TASKS_PER_CYCLE))
    active = _active_paid_account_ids()

    # Dry-run is strictly read-only. Queue maintenance and AI execution are both
    # allowed only inside the dedicated trusted systemd service cgroup.
    if not args.apply:
        tasks = _select_ready_tasks(active, args.account)[:limit]
        print("KPI_CONTENT_AI_READY", json.dumps({"active_paid_accounts": len(active), "terminalized_exhausted": [], "selected": tasks}, ensure_ascii=False), flush=True)
        return 0
    if not _service_caller_ok():
        print(f"KPI_CONTENT_AI_BLOCKED_UNTRUSTED_CALLER required={SERVICE_CGROUP}", flush=True)
        return 23

    repaired = _terminalize_exhausted_rejections(active)
    tasks = _select_ready_tasks(active, args.account)[:limit]
    print("KPI_CONTENT_AI_READY", json.dumps({"active_paid_accounts": len(active), "terminalized_exhausted": repaired, "selected": tasks}, ensure_ascii=False), flush=True)

    failures = 0
    results = []
    for task in tasks:
        aid = task["account_id"]
        op_id = task["operation_id"]
        try:
            reserve = _ensure_current_day_reservation(task)
            if reserve.get("status") != "ready":
                results.append({"account_id": aid, "operation_id": op_id, "status": "blocked", "reason": reserve.get("reason")})
                continue
            result = kpi_ai_prepare_execute(KpiAiPrepareExecuteRequest(account_id=aid, operation_id=op_id)) or {}
            terminalized = _terminalize_second_rejection(task, result)
            status = str(result.get("status") or "")
            results.append({
                "account_id": aid,
                "operation_id": op_id,
                "status": status,
                "ai_calls_made": int(result.get("ai_calls_made") or 0),
                "ai_cost_rub": float(result.get("ai_cost_rub") or 0.0),
                "proposal_count": len(result.get("proposal") or []),
                "reason": str(result.get("reason") or "")[:240],
            })
            if status in {"ai_failed", "error"}:
                failures += 1
        except Exception as exc:
            failures += 1
            results.append({"account_id": aid, "operation_id": op_id, "status": "error", "reason": f"{type(exc).__name__}: {str(exc)[:220]}"})

    print("KPI_CONTENT_AI_DONE", json.dumps({"selected": len(tasks), "failures": failures, "results": results}, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
