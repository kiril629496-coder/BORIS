from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.models.campaign_item import CampaignItem


def summarize(db, campaign, ff: dict, account_id: str, *, payload_fn: Callable, validate_fn: Callable) -> dict:
    """Truthful exact-quantity order progress for one Feed Factory campaign."""
    ff = dict(ff or {})
    order = dict(ff.get("order_contract") or {})
    aid = str(account_id or order.get("account_id") or getattr(campaign, "default_account_id", "") or "")
    requested = max(0, int(order.get("requested_total") or ff.get("items_planned") or ff.get("quantity") or 0))

    rows = (
        db.query(CampaignItem)
        .filter(
            CampaignItem.campaign_id == int(getattr(campaign, "id", 0) or 0),
            CampaignItem.account_id == aid,
            CampaignItem.source == "feed_factory",
            CampaignItem.status != "superseded",
        )
        .order_by(CampaignItem.id.asc())
        .all()
    )

    by_direction = {
        str(direction): {
            "requested": int(count or 0),
            "created": 0,
            "qa_pass": 0,
            "ready": 0,
            "blocked": 0,
        }
        for direction, count in dict(order.get("requested_per_direction") or {}).items()
    }

    created = len(rows)
    qa_pass = 0
    ready = 0
    blocked = 0
    failed = 0

    for row in rows:
        payload = payload_fn(row)
        direction = str(payload.get("direction") or row.segment or "").strip()
        if direction not in by_direction:
            by_direction[direction] = {
                "requested": 0,
                "created": 0,
                "qa_pass": 0,
                "ready": 0,
                "blocked": 0,
            }
        by_direction[direction]["created"] += 1

        try:
            valid = bool(validate_fn(row, db).get("valid"))
        except Exception:
            valid = False

        if valid:
            qa_pass += 1
            by_direction[direction]["qa_pass"] += 1
        else:
            blocked += 1
            by_direction[direction]["blocked"] += 1

        if str(row.status or "") == "ready" and valid:
            ready += 1
            by_direction[direction]["ready"] += 1

        if str(row.status or "") in {"failed", "error"}:
            failed += 1

    order.update(
        {
            "created_count": created,
            "qa_pass_count": qa_pass,
            "ready_count": ready,
            "blocked_count": blocked,
            "failed_count": failed,
            "remaining_count": max(0, requested - created),
            "by_direction": by_direction,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    if requested and created > requested:
        order["status"] = "BLOCKED"
        order["blocking_reason"] = "created_exceeds_requested"
    elif created < requested:
        order["status"] = "RUNNING"
        order["blocking_reason"] = "materialization_incomplete"
    elif qa_pass < requested:
        order["status"] = "RUNNING"
        order["blocking_reason"] = "qa_incomplete"
    elif str(getattr(campaign, "status", "") or "").lower() == "ready_for_review" and ready == requested:
        order["status"] = "READY_FOR_OWNER_REVIEW"
        order["blocking_reason"] = None
    else:
        order["status"] = "RUNNING"
        order["blocking_reason"] = None

    return order


def exact_ready(order: dict) -> bool:
    order = dict(order or {})
    requested = max(0, int(order.get("requested_total") or 0))
    return bool(
        requested > 0
        and int(order.get("created_count") or 0) == requested
        and int(order.get("qa_pass_count") or 0) == requested
    )


def exact_terminal_ready(order: dict) -> bool:
    order = dict(order or {})
    requested = max(0, int(order.get("requested_total") or 0))
    return bool(
        requested > 0
        and int(order.get("created_count") or 0) == requested
        and int(order.get("qa_pass_count") or 0) == requested
        and int(order.get("ready_count") or 0) == requested
    )
