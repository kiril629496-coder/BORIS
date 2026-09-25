from __future__ import annotations

import uuid
from typing import Any


ALLOWED = {
    "in_work",
    "contacted",
    "replied",
    "qualified",
    "won",
    "lost",
    "not_fit",
}


REASONS = {
    "not_our_service",
    "vacancy",
    "self_promo",
    "too_weak",
    "no_contact",
    "duplicate",
    "irrelevant",
    "no_response",
    "price",
    "competitor",
    "other",
}


def record(
    cur,
    message_id: str,
    outcome: str,
    reason: str | None = None,
    note: str | None = None,
    actor: str = "owner",
) -> dict[str, Any]:

    if outcome not in ALLOWED:
        raise ValueError(
            f"unsupported outcome={outcome}"
        )

    if reason and reason not in REASONS:
        raise ValueError(
            f"unsupported reason={reason}"
        )

    cur.execute("""
    SELECT
        m.id,
        m.source_id,
        m.matched_rule_id,
        m.score_total,
        m.payload,
        m.status
    FROM monitor_messages m
    WHERE m.id=%s
    """,(message_id,))

    row=cur.fetchone()

    if not row:
        raise KeyError(
            f"message not found: {message_id}"
        )

    (
        mid,
        source_id,
        rule_id,
        match_score,
        payload,
        current_status,
    )=row

    sale_score=int(
        (
            (payload or {})
            .get("sale_score",{})
            .get("score",0)
        )
        or 0
    )

    oid=str(uuid.uuid4())

    cur.execute("""
    INSERT INTO monitor_finding_outcomes (
        id,
        message_id,
        outcome,
        reason,
        note,
        sale_score,
        match_score,
        source_id,
        rule_id,
        actor,
        payload,
        created_at
    )
    VALUES (
        %s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,
        '{}'::jsonb,
        NOW()
    )
    RETURNING created_at
    """,(
        oid,
        mid,
        outcome,
        reason,
        note,
        sale_score,
        match_score,
        source_id,
        rule_id,
        actor,
    ))

    created_at=cur.fetchone()[0]

    # Существующие разрешённые monitor_messages.status
    # используем только там, где смысл совпадает.
    if outcome == "in_work":
        cur.execute("""
        UPDATE monitor_messages
        SET status='in_work'
        WHERE id=%s
          AND status NOT IN ('closed','rejected')
        """,(mid,))

    elif outcome == "won":
        cur.execute("""
        UPDATE monitor_messages
        SET status='closed'
        WHERE id=%s
        """,(mid,))

    elif outcome in {"lost","not_fit"}:
        cur.execute("""
        UPDATE monitor_messages
        SET status='rejected'
        WHERE id=%s
        """,(mid,))

    return {
        "id":oid,
        "message_id":str(mid),
        "outcome":outcome,
        "reason":reason,
        "sale_score":sale_score,
        "match_score":match_score,
        "created_at":created_at,
    }


def latest_for_messages(
    cur,
    message_ids: list[str],
) -> dict[str,dict[str,Any]]:

    if not message_ids:
        return {}

    cur.execute("""
    SELECT DISTINCT ON (message_id)
        message_id,
        outcome,
        reason,
        note,
        created_at
    FROM monitor_finding_outcomes
    WHERE message_id = ANY(%s::uuid[])
    ORDER BY message_id,created_at DESC,id DESC
    """,(message_ids,))

    result={}

    for (
        message_id,
        outcome,
        reason,
        note,
        created_at,
    ) in cur.fetchall():

        result[str(message_id)]={
            "outcome":outcome,
            "reason":reason,
            "note":note,
            "created_at":created_at.isoformat(),
        }

    return result
