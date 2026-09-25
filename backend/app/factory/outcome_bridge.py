"""
BORIS Factory R — FACTORY_R_V1.

Bridge from Factory Q outcomes back into attention lifecycle.

Rules:

1. Q outcome RESOLVED:
   - ONLY if source route == AUTO_INTERNAL
   - close the corresponding OPEN AUTO_INTERNAL attention.
   - never close OWNER_REQUIRED.

2. Q outcome ESCALATE:
   - ONLY if source route == AUTO_INTERNAL
   - create ONE dedicated OWNER_REQUIRED escalation.
   - do not auto-approve anything.
   - do not contact owner/client externally.
   - do not mutate money/schedule/publishing.

3. PROTECTED_OWNER_REQUIRED:
   - NOOP.

Writes:
    factory_attention_state
    factory_attention_events
    factory_outcome_bridge_state
    factory_outcome_bridge_events
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text


FACTORY_R_V1=True


DDL=(
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))


def stable_hash(value):
    raw=json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        default=str,
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def load(db):
    rows=db.execute(text("""
        SELECT
            q.account_id,
            q.issue_type,
            q.route,
            q.attention_status,
            q.outcome,
            q.reason,
            q.source_fingerprint,

            a.attention_key,
            a.priority,
            a.status AS current_attention_status,
            a.title,
            a.detail,
            a.source_health,
            a.attention_fingerprint

        FROM factory_outcome_state q

        LEFT JOIN factory_attention_state a
          ON a.account_id=q.account_id
         AND a.issue_type=q.issue_type

        ORDER BY
            q.account_id,
            q.issue_type
    """)).mappings().all()

    return [
        dict(x)
        for x in rows
    ]


def plan_one(row):
    outcome=row["outcome"]
    route=row["route"]

    attention_key=row.get(
        "attention_key"
    )

    issue=row["issue_type"]
    account=row["account_id"]

    # Absolute owner protection.
    if route == "OWNER_REQUIRED":
        action="NOOP_OWNER_REQUIRED"
        target_key=None

    elif route != "AUTO_INTERNAL":
        action="NOOP_UNKNOWN_ROUTE"
        target_key=None

    elif outcome == "RESOLVED":
        if (
            attention_key
            and row.get(
                "current_attention_status"
            ) == "OPEN"
        ):
            action="CLOSE_AUTO_INTERNAL"
        else:
            action="NOOP_ALREADY_CLOSED"

        target_key=attention_key

    elif outcome == "ESCALATE":
        action="CREATE_OWNER_ESCALATION"

        target_key=(
            "%s:%s:OWNER_ESCALATION"
            % (
                account,
                issue,
            )
        )

    else:
        action="NOOP_OUTCOME"
        target_key=None

    payload={
        "source_attention_key":
            attention_key
            or (
                "%s:%s"
                % (
                    account,
                    issue,
                )
            ),

        "account_id":
            account,

        "issue_type":
            issue,

        "route":
            route,

        "outcome":
            outcome,

        "bridge_action":
            action,

        "target_attention_key":
            target_key,

        "source_fingerprint":
            row["source_fingerprint"],

        "priority":
            int(
                row.get("priority")
                or 0
            ),

        "source_health":
            row.get(
                "source_health"
            ),

        "current_attention_status":
            row.get(
                "current_attention_status"
            ),
    }

    payload["bridge_key"]=(
        "%s:%s:%s"
        % (
            account,
            issue,
            action,
        )
    )

    payload["bridge_fingerprint"]=stable_hash(
        payload
    )

    return payload


def plan(db):
    return [
        plan_one(row)
        for row in load(db)
    ]


def close_auto_internal(
    db,
    item,
):
    """
    Critical SQL conditions guarantee that an OWNER_REQUIRED
    attention row can NEVER be closed here.
    """

    row=db.execute(text("""
        SELECT
            attention_key,
            account_id,
            route,
            priority,
            status,
            attention_fingerprint
        FROM factory_attention_state
        WHERE attention_key=:key
        FOR UPDATE
    """),{
        "key":
            item["source_attention_key"],
    }).mappings().first()

    if not row:
        return "ABSENT"

    if row["route"] != "AUTO_INTERNAL":
        raise RuntimeError(
            "FAIL CLOSED: R attempted to close non-AUTO_INTERNAL"
        )

    if row["status"] != "OPEN":
        return "ALREADY_CLOSED"

    result=db.execute(text("""
        UPDATE factory_attention_state
        SET
            status='RESOLVED',
            updated_at=NOW(),
            resolved_at=NOW()
        WHERE attention_key=:key
          AND route='AUTO_INTERNAL'
          AND status='OPEN'
        RETURNING attention_key
    """),{
        "key":
            item["source_attention_key"],
    }).first()

    if not result:
        raise RuntimeError(
            "FAIL CLOSED: protected close condition failed"
        )

    db.execute(text("""
        INSERT INTO factory_attention_events (
            attention_key,
            account_id,
            before_status,
            after_status,
            route,
            priority,
            attention_fingerprint,
            created_at
        )
        VALUES (
            :attention_key,
            :account_id,
            'OPEN',
            'RESOLVED',
            'AUTO_INTERNAL',
            :priority,
            :attention_fingerprint,
            NOW()
        )
    """),{
        "attention_key":
            row["attention_key"],

        "account_id":
            row["account_id"],

        "priority":
            row["priority"],

        "attention_fingerprint":
            row["attention_fingerprint"],
    })

    return "CLOSED"


def create_owner_escalation(
    db,
    item,
):
    """
    Creates an INTERNAL owner attention row only.
    It sends nothing and performs no external action.
    """

    key=item[
        "target_attention_key"
    ]

    if not key:
        raise RuntimeError(
            "missing escalation attention key"
        )

    priority=max(
        int(
            item.get("priority")
            or 0
        ),
        90,
    )

    title="Требуется вмешательство владельца"

    detail=(
        "Безопасное внутреннее восстановление "
        "исчерпано или требует эскалации. "
        "Причина: %s"
        % item["issue_type"]
    )

    source_fp=item[
        "source_fingerprint"
    ]

    attention_fp=stable_hash({
        "attention_key":key,
        "account_id":
            item["account_id"],
        "issue_type":
            "REMEDIATION_ESCALATION",
        "route":
            "OWNER_REQUIRED",
        "source_issue_type":
            item["issue_type"],
        "source_fingerprint":
            source_fp,
    })

    existing=db.execute(text("""
        SELECT
            status,
            attention_fingerprint
        FROM factory_attention_state
        WHERE attention_key=:key
    """),{
        "key":key,
    }).mappings().first()

    if (
        existing
        and existing["status"]=="OPEN"
        and existing[
            "attention_fingerprint"
        ] == attention_fp
    ):
        return "UNCHANGED"

    before=(
        existing["status"]
        if existing
        else None
    )

    db.execute(text("""
        INSERT INTO factory_attention_state (
            attention_key,
            account_id,
            source,
            issue_type,
            route,
            priority,
            status,
            title,
            detail,
            source_health,
            source_fingerprint,
            attention_fingerprint,
            created_at,
            updated_at,
            resolved_at
        )
        VALUES (
            :attention_key,
            :account_id,
            'FACTORY_R',
            'REMEDIATION_ESCALATION',
            'OWNER_REQUIRED',
            :priority,
            'OPEN',
            :title,
            :detail,
            'NEEDS_DECISION',
            :source_fingerprint,
            :attention_fingerprint,
            NOW(),
            NOW(),
            NULL
        )
        ON CONFLICT (attention_key)
        DO UPDATE SET
            account_id=EXCLUDED.account_id,
            source='FACTORY_R',
            issue_type='REMEDIATION_ESCALATION',
            route='OWNER_REQUIRED',
            priority=EXCLUDED.priority,
            status='OPEN',
            title=EXCLUDED.title,
            detail=EXCLUDED.detail,
            source_health='NEEDS_DECISION',
            source_fingerprint=
                EXCLUDED.source_fingerprint,
            attention_fingerprint=
                EXCLUDED.attention_fingerprint,
            updated_at=NOW(),
            resolved_at=NULL
    """),{
        "attention_key":key,
        "account_id":
            item["account_id"],
        "priority":
            priority,
        "title":
            title,
        "detail":
            detail,
        "source_fingerprint":
            source_fp,
        "attention_fingerprint":
            attention_fp,
    })

    db.execute(text("""
        INSERT INTO factory_attention_events (
            attention_key,
            account_id,
            before_status,
            after_status,
            route,
            priority,
            attention_fingerprint,
            created_at
        )
        VALUES (
            :attention_key,
            :account_id,
            :before_status,
            'OPEN',
            'OWNER_REQUIRED',
            :priority,
            :attention_fingerprint,
            NOW()
        )
    """),{
        "attention_key":
            key,

        "account_id":
            item["account_id"],

        "before_status":
            before,

        "priority":
            priority,

        "attention_fingerprint":
            attention_fp,
    })

    return (
        "UPDATED"
        if existing
        else "CREATED"
    )


def apply(db):
    ensure_schema(db)

    items=plan(db)

    inserted=0
    updated=0
    unchanged=0
    actions=0
    events=0

    for item in items:

        action=item[
            "bridge_action"
        ]

        # Runtime owner protection.
        if (
            item["route"]
            == "OWNER_REQUIRED"
            and action
            not in {
                "NOOP_OWNER_REQUIRED",
            }
        ):
            raise RuntimeError(
                "FAIL CLOSED: owner item reached mutating R action"
            )

        mutation_result="NOOP"

        if action=="CLOSE_AUTO_INTERNAL":
            mutation_result=close_auto_internal(
                db,
                item,
            )

            actions += (
                1
                if mutation_result=="CLOSED"
                else 0
            )

        elif action=="CREATE_OWNER_ESCALATION":
            mutation_result=create_owner_escalation(
                db,
                item,
            )

            actions += (
                1
                if mutation_result
                in {
                    "CREATED",
                    "UPDATED",
                }
                else 0
            )

        old=db.execute(text("""
            SELECT
                status,
                bridge_fingerprint
            FROM factory_outcome_bridge_state
            WHERE bridge_key=:key
        """),{
            "key":
                item["bridge_key"],
        }).mappings().first()

        bridge_status=(
            "APPLIED"
            if action
            in {
                "CLOSE_AUTO_INTERNAL",
                "CREATE_OWNER_ESCALATION",
            }
            and mutation_result
            not in {
                "ABSENT",
            }
            else "NOOP"
        )

        if (
            old
            and old[
                "bridge_fingerprint"
            ] == item[
                "bridge_fingerprint"
            ]
            and old[
                "status"
            ] == bridge_status
        ):
            unchanged += 1
            continue

        before=(
            old["status"]
            if old
            else None
        )

        db.execute(text("""
            INSERT INTO factory_outcome_bridge_state (
                bridge_key,
                source_attention_key,
                account_id,
                issue_type,
                outcome,
                bridge_action,
                status,
                target_attention_key,
                source_fingerprint,
                bridge_fingerprint,
                created_at,
                updated_at
            )
            VALUES (
                :bridge_key,
                :source_attention_key,
                :account_id,
                :issue_type,
                :outcome,
                :bridge_action,
                :status,
                :target_attention_key,
                :source_fingerprint,
                :bridge_fingerprint,
                NOW(),
                NOW()
            )
            ON CONFLICT (bridge_key)
            DO UPDATE SET
                source_attention_key=
                    EXCLUDED.source_attention_key,
                account_id=
                    EXCLUDED.account_id,
                issue_type=
                    EXCLUDED.issue_type,
                outcome=
                    EXCLUDED.outcome,
                bridge_action=
                    EXCLUDED.bridge_action,
                status=
                    EXCLUDED.status,
                target_attention_key=
                    EXCLUDED.target_attention_key,
                source_fingerprint=
                    EXCLUDED.source_fingerprint,
                bridge_fingerprint=
                    EXCLUDED.bridge_fingerprint,
                updated_at=NOW()
        """),{
            **item,
            "status":
                bridge_status,
        })

        db.execute(text("""
            INSERT INTO factory_outcome_bridge_events (
                bridge_key,
                account_id,
                issue_type,
                outcome,
                bridge_action,
                before_status,
                after_status,
                bridge_fingerprint,
                created_at
            )
            VALUES (
                :bridge_key,
                :account_id,
                :issue_type,
                :outcome,
                :bridge_action,
                :before_status,
                :after_status,
                :bridge_fingerprint,
                NOW()
            )
            ON CONFLICT (
                bridge_key,
                bridge_fingerprint
            )
            DO NOTHING
            RETURNING id
        """),{
            **item,
            "before_status":
                before,
            "after_status":
                bridge_status,
        })

        if old:
            updated += 1
        else:
            inserted += 1

        events += 1

    return {
        "items":items,
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
        "actions":actions,
        "events":events,
    }
