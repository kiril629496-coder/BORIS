"""
BORIS Factory L — FACTORY_L_V1.

Post-launch supervisor.

Purpose:
    Verify that a client marked launched is still operational.

Read-only inputs:
    factory_client_launch
    factory_execution_state
    factory_missing_fact_state
    factory_schedule
    factory_schedule_runs
    factory_confirmations
    factory_provisioning_state

Writes:
    factory_launch_health
    factory_launch_health_events

No external actions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import inspect, text


FACTORY_L_V1=True


DDL=(
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


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


def fp(v):
    raw=json.dumps(
        _json(v),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def table_exists(db, name):
    return bool(
        db.execute(
            text("SELECT to_regclass(:n)"),
            {"n":"public."+name},
        ).scalar()
    )


def cols(db, table):
    if not table_exists(db,table):
        return set()

    return {
        x["name"]
        for x in inspect(db.bind).get_columns(table)
    }


def launched_accounts(db):
    if not table_exists(db,"factory_client_launch"):
        return []

    c=cols(db,"factory_client_launch")

    if "account_id" not in c:
        return []

    status_col=(
        "launch_status"
        if "launch_status" in c
        else
        "status"
        if "status" in c
        else None
    )

    if not status_col:
        return []

    rows=db.execute(
        text(f"""
            SELECT account_id,{status_col}
            FROM factory_client_launch
            WHERE {status_col}='LAUNCHED'
            ORDER BY account_id
        """)
    ).fetchall()

    return [
        {
            "account_id":r[0],
            "launch_status":r[1],
        }
        for r in rows
    ]


def count_unresolved(db, account_id):
    if not table_exists(
        db,
        "factory_execution_state",
    ):
        return 0

    return db.execute(text("""
        SELECT count(*)
        FROM factory_execution_state
        WHERE account_id=:a
          AND status='SKIPPED_UNRESOLVED'
    """),{
        "a":account_id,
    }).scalar()


def count_waiting_facts(db, account_id):
    if not table_exists(
        db,
        "factory_missing_fact_state",
    ):
        return 0

    return db.execute(text("""
        SELECT count(*)
        FROM factory_missing_fact_state
        WHERE account_id=:a
          AND status='WAITING_FACT'
    """),{
        "a":account_id,
    }).scalar()


def count_pending_confirmations(db, account_id):
    """Count only unresolved confirmations that truly require a human.

    ZERO_TOUCH_OWNER_DECISION_TRUTH_V1
    Optional client preferences and already-derived BORIS internals may stay
    pending as audit/workflow facts, but they must not turn launch health into
    NEEDS_DECISION or escalate the owner into an operator role.
    """
    if not table_exists(
        db,
        "factory_confirmations",
    ):
        return 0

    c=cols(
        db,
        "factory_confirmations",
    )

    if not {
        "account_id",
        "status",
        "operation",
        "payload",
    }.issubset(c):
        return 0

    return db.execute(text("""
        SELECT count(*)
        FROM factory_confirmations
        WHERE account_id=:a
          AND LOWER(status) NOT IN (
              'approved',
              'rejected',
              'stale',
              'applied'
          )
          AND (
              operation='migrate.schedule'
              OR LOWER(COALESCE(payload->'decision'->>'money','false'))='true'
              OR (
                  LOWER(COALESCE(
                      payload->'decision'->'source_action'->>'severity',''
                  ))='blocker'
                  AND UPPER(COALESCE(
                      payload->'decision'->'source_action'->>'actor',''
                  )) <> 'BORIS'
              )
          )
    """),{
        "a":account_id,
    }).scalar()


def provisioning(db, account_id):
    if not table_exists(
        db,
        "factory_provisioning_state",
    ):
        return None

    c=cols(
        db,
        "factory_provisioning_state",
    )

    status_col=(
        "provisioning_status"
        if "provisioning_status" in c
        else
        "status"
        if "status" in c
        else None
    )

    if not status_col:
        return None

    row=db.execute(text(f"""
        SELECT {status_col}
        FROM factory_provisioning_state
        WHERE account_id=:a
        LIMIT 1
    """),{
        "a":account_id,
    }).fetchone()

    return row[0] if row else None


def schedule_counts(db, account_id):
    if not table_exists(
        db,
        "factory_schedule",
    ):
        return 0,0

    c=cols(
        db,
        "factory_schedule",
    )

    if "account_id" not in c:
        return 0,0

    status_col=(
        "status"
        if "status" in c
        else None
    )

    total=db.execute(text("""
        SELECT count(*)
        FROM factory_schedule
        WHERE account_id=:a
    """),{
        "a":account_id,
    }).scalar()

    if not status_col:
        return total,total

    active=db.execute(text("""
        SELECT count(*)
        FROM factory_schedule
        WHERE account_id=:a
          AND status IN (
              'ACTIVE',
              'READY',
              'ENABLED'
          )
    """),{
        "a":account_id,
    }).scalar()

    # Some older schedule rows may have no explicit status taxonomy.
    if total and active==0:
        active=total

    return total,active


def scheduler_stale(db, account_id):
    if not table_exists(
        db,
        "factory_schedule_runs",
    ):
        return False

    c=cols(
        db,
        "factory_schedule_runs",
    )

    if "account_id" not in c:
        return False

    time_col=(
        "finished_at"
        if "finished_at" in c
        else
        "started_at"
        if "started_at" in c
        else
        "created_at"
        if "created_at" in c
        else None
    )

    if not time_col:
        return False

    latest=db.execute(text(f"""
        SELECT max({time_col})
        FROM factory_schedule_runs
        WHERE account_id=:a
    """),{
        "a":account_id,
    }).scalar()

    # No previous run is not automatically unhealthy:
    # some launched accounts legitimately require no schedule.
    if latest is None:
        return False

    now=datetime.now(timezone.utc)

    if latest.tzinfo is None:
        latest=latest.replace(
            tzinfo=timezone.utc
        )

    age=(now-latest).total_seconds()

    # Conservative threshold: 24h.
    return age > 86400


def evaluate(db, account):
    aid=account["account_id"]

    unresolved=count_unresolved(
        db,
        aid,
    )

    waiting=count_waiting_facts(
        db,
        aid,
    )

    confirmations=count_pending_confirmations(
        db,
        aid,
    )

    provision_status=provisioning(
        db,
        aid,
    )

    sched_total,sched_present=schedule_counts(
        db,
        aid,
    )

    stale=scheduler_stale(
        db,
        aid,
    )

    reasons=[]
    hard=0

    if provision_status in (
        "BLOCKED_OWNER",
        "BLOCKED_PRODUCTS",
        "BLOCKED_CORE",
    ):
        hard += 1
        reasons.append(
            "provisioning:"+str(provision_status)
        )

    if stale:
        reasons.append(
            "scheduler_stale"
        )

    if confirmations:
        reasons.append(
            "pending_confirmations:%s"
            % confirmations
        )

    if unresolved:
        reasons.append(
            "unresolved_internal:%s"
            % unresolved
        )

    if waiting:
        reasons.append(
            "waiting_facts:%s"
            % waiting
        )

    if hard:
        health="BLOCKED"

    elif stale:
        health="DEGRADED"

    elif confirmations:
        health="NEEDS_DECISION"

    elif unresolved or waiting:
        # Still operational, but incomplete.
        health="LAUNCHED_WITH_GAPS"

    else:
        health="HEALTHY"

    payload={
        "account_id":aid,
        "launch_status":
            account["launch_status"],
        "health_status":health,
        "schedule_required":sched_total,
        "schedule_present":sched_present,
        "unresolved_internal":unresolved,
        "waiting_facts":waiting,
        "pending_confirmations":confirmations,
        "provisioning_status":provision_status,
        "scheduler_stale":stale,
        "hard_blockers":hard,
        "reasons":reasons,
    }

    payload["health_fingerprint"]=fp(
        payload
    )

    return payload


def build(db):
    return [
        evaluate(db,x)
        for x in launched_accounts(db)
    ]


def apply(db):
    ensure_schema(db)

    items=build(db)

    inserted=0
    updated=0
    unchanged=0
    events=0

    for x in items:

        old=db.execute(text("""
            SELECT
                health_status,
                health_fingerprint
            FROM factory_launch_health
            WHERE account_id=:a
        """),{
            "a":x["account_id"],
        }).fetchone()

        if (
            old
            and old[1]
                == x["health_fingerprint"]
        ):
            unchanged += 1
            continue

        before=old[0] if old else None

        db.execute(text("""
            INSERT INTO factory_launch_health (
                account_id,
                launch_status,
                health_status,
                schedule_required,
                schedule_present,
                unresolved_internal,
                waiting_facts,
                pending_confirmations,
                provisioning_status,
                scheduler_stale,
                hard_blockers,
                reasons,
                health_fingerprint,
                checked_at,
                updated_at
            )
            VALUES (
                :account_id,
                :launch_status,
                :health_status,
                :schedule_required,
                :schedule_present,
                :unresolved_internal,
                :waiting_facts,
                :pending_confirmations,
                :provisioning_status,
                :scheduler_stale,
                :hard_blockers,
                CAST(:reasons AS JSONB),
                :health_fingerprint,
                NOW(),
                NOW()
            )
            ON CONFLICT (account_id)
            DO UPDATE SET
                launch_status=EXCLUDED.launch_status,
                health_status=EXCLUDED.health_status,
                schedule_required=EXCLUDED.schedule_required,
                schedule_present=EXCLUDED.schedule_present,
                unresolved_internal=EXCLUDED.unresolved_internal,
                waiting_facts=EXCLUDED.waiting_facts,
                pending_confirmations=EXCLUDED.pending_confirmations,
                provisioning_status=EXCLUDED.provisioning_status,
                scheduler_stale=EXCLUDED.scheduler_stale,
                hard_blockers=EXCLUDED.hard_blockers,
                reasons=EXCLUDED.reasons,
                health_fingerprint=EXCLUDED.health_fingerprint,
                checked_at=NOW(),
                updated_at=NOW()
        """),{
            **{
                k:v
                for k,v in x.items()
                if k!="reasons"
            },
            "reasons":json.dumps(
                x["reasons"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        })

        db.execute(text("""
            INSERT INTO factory_launch_health_events (
                account_id,
                before_status,
                after_status,
                reasons,
                health_fingerprint,
                created_at
            )
            VALUES (
                :a,
                :b,
                :s,
                CAST(:r AS JSONB),
                :fp,
                NOW()
            )
        """),{
            "a":x["account_id"],
            "b":before,
            "s":x["health_status"],
            "r":json.dumps(
                x["reasons"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "fp":x["health_fingerprint"],
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
