"""
BORIS Factory I — FACTORY_I_V1.

Internal missing-facts queue.

Purpose:
    Convert unresolved internal execution requirements into a deterministic,
    deduplicated internal queue.

V1:
    profile.timezone -> missing geography fact

Statuses:
    WAITING_FACT
    RESOLVED
    STALE

Rules:
    - one queue row per account_id + field
    - no client messages
    - no external APIs
    - no guessing
    - no Client Config writes
    - no money/publishing/KPI/CPX/autopilot
    - H remains the resolver authority
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from sqlalchemy import text

from app.factory import h_resolver as H


FACTORY_I_V1 = True


DDL = (
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


FIELD_RULES = {
    "profile.timezone": {
        "fact_kind": "geo.city_or_region",
        "question_text": (
            "Укажите город или регион работы для определения часового пояса."
        ),
        "reason": "timezone_requires_structured_geo",
    },
}


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))


def _json(v):
    return json.loads(
        json.dumps(
            v,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def stable_hash(v):
    raw=json.dumps(
        _json(v),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def execution_rows(db):
    rows=db.execute(text("""
        SELECT
            account_id,
            field,
            target_value,
            status,
            execution_fingerprint
        FROM factory_execution_state
        ORDER BY account_id,field
    """)).mappings().all()

    return [dict(x) for x in rows]


def desired_state(db, row):
    field=row["field"]

    rule=FIELD_RULES.get(field)

    if not rule:
        return None

    resolved=H.get_value(
        db,
        row["account_id"],
        field,
    )

    if resolved:
        status="RESOLVED"
        resolved_value=resolved["value"]

        evidence={
            "resolver":"Factory H",
            "confidence":float(
                resolved.get("confidence") or 0
            ),
            "resolution_kind":
                resolved.get("resolution_kind"),
            "resolution_source":
                resolved.get("source"),
            "resolution_fingerprint":
                resolved.get("source_fingerprint"),
        }

    elif row["status"]=="SKIPPED_UNRESOLVED":
        status="WAITING_FACT"
        resolved_value=None

        evidence={
            "resolver":"Factory H",
            "reason":"no_structured_resolution_available",
        }

    else:
        # Field is no longer unresolved and H has no resolution.
        status="STALE"
        resolved_value=None

        evidence={
            "reason":"execution_no_longer_waiting_for_fact",
            "execution_status":row["status"],
        }

    payload={
        "account_id":row["account_id"],
        "field":field,
        "fact_kind":rule["fact_kind"],
        "status":status,
        "reason":rule["reason"],
        "question_text":rule["question_text"],
        "current_value":row["target_value"],
        "resolved_value":resolved_value,
        "source_execution_fingerprint":
            row["execution_fingerprint"],
        "evidence":evidence,
    }

    payload["source_fact_fingerprint"]=stable_hash(
        payload
    )

    return payload


def build(db):
    items=[]

    for row in execution_rows(db):
        x=desired_state(db,row)

        if x:
            items.append(x)

    return items


def current(db, account_id, field):
    row=db.execute(text("""
        SELECT *
        FROM factory_missing_fact_state
        WHERE account_id=:a
          AND field=:f
    """),{
        "a":account_id,
        "f":field,
    }).mappings().first()

    return dict(row) if row else None


def apply(db):
    ensure_schema(db)

    items=build(db)

    inserted=0
    updated=0
    unchanged=0
    events=0

    for item in items:

        old=current(
            db,
            item["account_id"],
            item["field"],
        )

        if (
            old
            and old["source_fact_fingerprint"]
                == item["source_fact_fingerprint"]
        ):
            unchanged += 1
            continue

        before_status=(
            old["status"]
            if old
            else None
        )

        resolved_at_sql=(
            "NOW()"
            if item["status"]=="RESOLVED"
            else "NULL"
        )

        db.execute(text(f"""
            INSERT INTO factory_missing_fact_state (
                account_id,
                field,
                fact_kind,
                status,
                reason,
                question_text,
                current_value,
                resolved_value,
                source_execution_fingerprint,
                source_fact_fingerprint,
                evidence,
                first_seen_at,
                updated_at,
                resolved_at
            )
            VALUES (
                :account_id,
                :field,
                :fact_kind,
                :status,
                :reason,
                :question_text,
                CAST(:current_value AS JSONB),
                CAST(:resolved_value AS JSONB),
                :source_execution_fingerprint,
                :source_fact_fingerprint,
                CAST(:evidence AS JSONB),
                NOW(),
                NOW(),
                {resolved_at_sql}
            )
            ON CONFLICT (account_id,field)
            DO UPDATE SET
                fact_kind=EXCLUDED.fact_kind,
                status=EXCLUDED.status,
                reason=EXCLUDED.reason,
                question_text=EXCLUDED.question_text,
                current_value=EXCLUDED.current_value,
                resolved_value=EXCLUDED.resolved_value,
                source_execution_fingerprint=
                    EXCLUDED.source_execution_fingerprint,
                source_fact_fingerprint=
                    EXCLUDED.source_fact_fingerprint,
                evidence=EXCLUDED.evidence,
                updated_at=NOW(),
                resolved_at=
                    CASE
                        WHEN EXCLUDED.status='RESOLVED'
                        THEN NOW()
                        ELSE NULL
                    END
        """),{
            **{
                k:v
                for k,v in item.items()
                if k not in (
                    "current_value",
                    "resolved_value",
                    "evidence",
                )
            },

            "current_value":json.dumps(
                item["current_value"],
                ensure_ascii=False,
                sort_keys=True,
            ),

            "resolved_value":json.dumps(
                item["resolved_value"],
                ensure_ascii=False,
                sort_keys=True,
            ),

            "evidence":json.dumps(
                item["evidence"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        })

        event_type=(
            "FACT_OPENED"
            if old is None
            else
            "FACT_RESOLVED"
            if item["status"]=="RESOLVED"
            else
            "FACT_STALE"
            if item["status"]=="STALE"
            else
            "FACT_REFRESHED"
        )

        db.execute(text("""
            INSERT INTO factory_missing_fact_events (
                account_id,
                field,
                event_type,
                before_status,
                after_status,
                fact_kind,
                source_fact_fingerprint,
                payload,
                created_at
            )
            VALUES (
                :a,
                :f,
                :e,
                :b,
                :s,
                :k,
                :fp,
                CAST(:p AS JSONB),
                NOW()
            )
        """),{
            "a":item["account_id"],
            "f":item["field"],
            "e":event_type,
            "b":before_status,
            "s":item["status"],
            "k":item["fact_kind"],
            "fp":item["source_fact_fingerprint"],
            "p":json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
            ),
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
        "events":events,
    }


def queue(db):
    ensure_schema(db)

    return [
        dict(x)
        for x in db.execute(text("""
            SELECT
                account_id,
                field,
                fact_kind,
                status,
                reason,
                question_text,
                current_value,
                resolved_value,
                evidence,
                updated_at
            FROM factory_missing_fact_state
            ORDER BY
                CASE status
                    WHEN 'WAITING_FACT' THEN 1
                    WHEN 'RESOLVED' THEN 2
                    ELSE 3
                END,
                account_id,
                field
        """)).mappings()
    ]
