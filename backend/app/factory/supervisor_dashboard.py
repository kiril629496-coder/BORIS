"""
BORIS Factory S — FACTORY_S_V1.

Read/aggregate operational state produced by previous Factory stages.

Purpose:
    one deterministic supervisor row per launched account.

Status precedence:
    ESCALATED
    NEEDS_DECISION
    AUTO_RECOVERY
    WAITING_FACT
    BLOCKED
    HEALTHY

No external effects.

Writes only:
    factory_supervisor_state
    factory_supervisor_events
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import inspect
from sqlalchemy import text


FACTORY_S_V1=True


DDL=(
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))


def _columns(db, table):
    insp=inspect(db.bind)

    return {
        c["name"]
        for c in insp.get_columns(table)
    }


def _pick(cols, *names):
    for name in names:
        if name in cols:
            return name

    return None


def stable_hash(value):
    raw=json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        default=str,
    ).encode()

    return hashlib.sha256(
        raw
    ).hexdigest()


def launched_accounts(db):
    cols=_columns(
        db,
        "factory_launch_health",
    )

    account_col=_pick(
        cols,
        "account_id",
    )

    health_col=_pick(
        cols,
        "health_status",
        "status",
        "launch_health",
    )

    if not account_col:
        raise RuntimeError(
            "factory_launch_health.account_id missing"
        )

    health_expr=(
        health_col
        if health_col
        else "NULL"
    )

    rows=db.execute(text(
        f"""
        SELECT
            {account_col} AS account_id,
            {health_expr} AS health_status
        FROM factory_launch_health
        ORDER BY {account_col}
        """
    )).mappings().all()

    return [
        dict(x)
        for x in rows
    ]


def attention_counts(db):
    rows=db.execute(text("""
        SELECT
            account_id,

            count(*) FILTER (
                WHERE status='OPEN'
                  AND route='OWNER_REQUIRED'
            ) AS owner_open,

            count(*) FILTER (
                WHERE status='OPEN'
                  AND route='AUTO_INTERNAL'
            ) AS auto_open,

            max(priority) FILTER (
                WHERE status='OPEN'
            ) AS max_priority,

            count(*) FILTER (
                WHERE status='OPEN'
                  AND issue_type='REMEDIATION_ESCALATION'
            ) AS escalations

        FROM factory_attention_state
        GROUP BY account_id
    """)).mappings().all()

    return {
        x["account_id"]:dict(x)
        for x in rows
    }


def waiting_counts(db):
    cols=_columns(
        db,
        "factory_missing_fact_state",
    )

    account_col=_pick(
        cols,
        "account_id",
    )

    status_col=_pick(
        cols,
        "status",
        "fact_status",
        "queue_status",
    )

    if not account_col:
        return {}

    if not status_col:
        return {}

    rows=db.execute(text(
        f"""
        SELECT
            {account_col} AS account_id,
            count(*) AS n
        FROM factory_missing_fact_state
        WHERE {status_col}='WAITING_FACT'
        GROUP BY {account_col}
        """
    )).fetchall()

    return dict(rows)


def recovery_counts(db):
    cols=_columns(
        db,
        "factory_recovery_state",
    )

    account_col=_pick(
        cols,
        "account_id",
    )

    status_col=_pick(
        cols,
        "recovery_status",
        "status",
    )

    if not account_col:
        return {}

    if not status_col:
        return {}

    rows=db.execute(text(
        f"""
        SELECT
            {account_col} AS account_id,
            count(*) AS n
        FROM factory_recovery_state
        WHERE {status_col} NOT IN (
            'RESOLVED',
            'PROTECTED_OWNER_REQUIRED',
            'NOOP',
            'DONE'
        )
        GROUP BY {account_col}
        """
    )).fetchall()

    return dict(rows)


def classify(
    health,
    attention,
    waiting,
    recovery,
):
    owner=int(
        attention.get(
            "owner_open",
            0,
        )
        or 0
    )

    auto=int(
        attention.get(
            "auto_open",
            0,
        )
        or 0
    )

    escalations=int(
        attention.get(
            "escalations",
            0,
        )
        or 0
    )

    priority=int(
        attention.get(
            "max_priority",
            0,
        )
        or 0
    )

    waiting=int(
        waiting
        or 0
    )

    recovery=int(
        recovery
        or 0
    )

    health_status=(
        health
        or "UNKNOWN"
    )

    if escalations > 0:
        return (
            "ESCALATED",
            max(priority,90),
            f"owner_escalations:{escalations}",
            "FACTORY_R",
        )

    if owner > 0:
        return (
            "NEEDS_DECISION",
            max(priority,70),
            f"owner_required:{owner}",
            "FACTORY_M",
        )

    if recovery > 0 or auto > 0:
        return (
            "AUTO_RECOVERY",
            max(priority,50),
            (
                f"auto_items:{auto},"
                f"recovery:{recovery}"
            ),
            "FACTORY_N/P",
        )

    if waiting > 0:
        return (
            "WAITING_FACT",
            max(priority,40),
            f"waiting_facts:{waiting}",
            "FACTORY_I",
        )

    if health_status in {
        "LAUNCHED_WITH_GAPS",
        "BLOCKED",
        "BLOCKED_CORE",
    }:
        return (
            "BLOCKED",
            max(priority,30),
            f"launch_health:{health_status}",
            "FACTORY_L",
        )

    return (
        "HEALTHY",
        priority,
        "no_open_operational_blockers",
        "FACTORY_S",
    )


def plan(db):
    accounts=launched_accounts(db)

    attention=attention_counts(db)
    waiting=waiting_counts(db)
    recovery=recovery_counts(db)

    out=[]

    for account in accounts:
        account_id=account[
            "account_id"
        ]

        a=attention.get(
            account_id,
            {},
        )

        w=waiting.get(
            account_id,
            0,
        )

        r=recovery.get(
            account_id,
            0,
        )

        (
            status,
            priority,
            reason,
            source,
        )=classify(
            account.get(
                "health_status"
            ),
            a,
            w,
            r,
        )

        item={
            "account_id":
                account_id,

            "operational_status":
                status,

            "priority":
                priority,

            "reason":
                reason,

            "source":
                source,

            "health_status":
                account.get(
                    "health_status"
                ),

            "open_owner_items":
                int(
                    a.get(
                        "owner_open",
                        0,
                    )
                    or 0
                ),

            "open_auto_items":
                int(
                    a.get(
                        "auto_open",
                        0,
                    )
                    or 0
                ),

            "waiting_facts":
                int(w or 0),

            "active_recovery":
                int(r or 0),

            "escalations":
                int(
                    a.get(
                        "escalations",
                        0,
                    )
                    or 0
                ),
        }

        item["fingerprint"]=stable_hash(
            item
        )

        out.append(item)

    return out


def apply(db):
    ensure_schema(db)

    items=plan(db)

    inserted=0
    updated=0
    unchanged=0
    events=0

    for item in items:

        old=db.execute(text("""
            SELECT
                operational_status,
                fingerprint
            FROM factory_supervisor_state
            WHERE account_id=:account_id
        """),{
            "account_id":
                item["account_id"],
        }).mappings().first()

        if (
            old
            and old["fingerprint"]
            == item["fingerprint"]
        ):
            unchanged += 1
            continue

        before=(
            old["operational_status"]
            if old
            else None
        )

        db.execute(text("""
            INSERT INTO factory_supervisor_state (
                account_id,
                operational_status,
                priority,
                reason,
                source,
                health_status,
                open_owner_items,
                open_auto_items,
                waiting_facts,
                active_recovery,
                escalations,
                fingerprint,
                created_at,
                updated_at
            )
            VALUES (
                :account_id,
                :operational_status,
                :priority,
                :reason,
                :source,
                :health_status,
                :open_owner_items,
                :open_auto_items,
                :waiting_facts,
                :active_recovery,
                :escalations,
                :fingerprint,
                NOW(),
                NOW()
            )
            ON CONFLICT (account_id)
            DO UPDATE SET
                operational_status=
                    EXCLUDED.operational_status,
                priority=
                    EXCLUDED.priority,
                reason=
                    EXCLUDED.reason,
                source=
                    EXCLUDED.source,
                health_status=
                    EXCLUDED.health_status,
                open_owner_items=
                    EXCLUDED.open_owner_items,
                open_auto_items=
                    EXCLUDED.open_auto_items,
                waiting_facts=
                    EXCLUDED.waiting_facts,
                active_recovery=
                    EXCLUDED.active_recovery,
                escalations=
                    EXCLUDED.escalations,
                fingerprint=
                    EXCLUDED.fingerprint,
                updated_at=NOW()
        """),item)

        result=db.execute(text("""
            INSERT INTO factory_supervisor_events (
                account_id,
                before_status,
                after_status,
                priority,
                reason,
                source,
                fingerprint,
                created_at
            )
            VALUES (
                :account_id,
                :before_status,
                :operational_status,
                :priority,
                :reason,
                :source,
                :fingerprint,
                NOW()
            )
            ON CONFLICT (
                account_id,
                fingerprint
            )
            DO NOTHING
            RETURNING id
        """),{
            **item,
            "before_status":
                before,
        }).first()

        if result:
            events += 1

        if old:
            updated += 1
        else:
            inserted += 1

    return {
        "items":items,
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
        "events":events,
    }
