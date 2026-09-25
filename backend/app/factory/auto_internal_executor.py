"""
BORIS Factory N — FACTORY_N_V1.

Executor for M items routed explicitly as AUTO_INTERNAL.

V1 is deliberately conservative.

Supported:
    SCHEDULER_STALE
        -> read-only scheduler diagnostic

    INTERNAL_GAP
        -> read-only internal-gap diagnostic

Never processes:
    OWNER_REQUIRED

Never:
    approves confirmation
    modifies schedule
    sends client message
    publishes
    spends money
    changes Client Config
    calls external API

Writes only:
    factory_auto_execution_state
    factory_auto_execution_events
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import inspect, text


FACTORY_N_V1=True


ALLOWLIST={
    "SCHEDULER_STALE":
        "DIAGNOSE_SCHEDULER",

    "INTERNAL_GAP":
        "DIAGNOSE_INTERNAL_GAP",
}


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
        default=str,
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


def columns(db, table):
    if not table_exists(db,table):
        return set()

    return {
        x["name"]
        for x in inspect(db.bind).get_columns(table)
    }


def queue(db):
    rows=db.execute(text("""
        SELECT
            attention_key,
            account_id,
            issue_type,
            route,
            priority,
            status,
            source_fingerprint,
            attention_fingerprint
        FROM factory_attention_state
        WHERE status='OPEN'
          AND route='AUTO_INTERNAL'
        ORDER BY priority DESC,account_id,issue_type
    """)).mappings().all()

    result=[]

    for row in rows:
        row=dict(row)

        # Fail closed:
        # route already filtered, issue must also be allowlisted.
        if row["issue_type"] not in ALLOWLIST:
            continue

        row["action_type"]=ALLOWLIST[
            row["issue_type"]
        ]

        result.append(row)

    return result


def scheduler_diagnostic(db, account_id):
    result={
        "diagnostic":"scheduler",
        "account_id":account_id,
        "schedule_rows":0,
        "run_rows":0,
        "latest_run":None,
        "assessment":"NO_DATA",
    }

    if table_exists(db,"factory_schedule"):
        c=columns(
            db,
            "factory_schedule",
        )

        if "account_id" in c:
            result["schedule_rows"]=db.execute(
                text("""
                    SELECT count(*)
                    FROM factory_schedule
                    WHERE account_id=:a
                """),
                {"a":account_id},
            ).scalar()

    if table_exists(
        db,
        "factory_schedule_runs",
    ):
        c=columns(
            db,
            "factory_schedule_runs",
        )

        if "account_id" in c:
            result["run_rows"]=db.execute(
                text("""
                    SELECT count(*)
                    FROM factory_schedule_runs
                    WHERE account_id=:a
                """),
                {"a":account_id},
            ).scalar()

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

            if time_col:
                latest=db.execute(
                    text(f"""
                        SELECT max({time_col})
                        FROM factory_schedule_runs
                        WHERE account_id=:a
                    """),
                    {"a":account_id},
                ).scalar()

                result["latest_run"]=(
                    latest.isoformat()
                    if latest
                    else None
                )

                if latest:
                    if latest.tzinfo is None:
                        latest=latest.replace(
                            tzinfo=timezone.utc
                        )

                    age=(
                        datetime.now(timezone.utc)
                        - latest
                    ).total_seconds()

                    result[
                        "latest_run_age_seconds"
                    ]=int(age)

    if result["schedule_rows"] == 0:
        result["assessment"]="NO_SCHEDULE"

    elif result["latest_run"] is None:
        result["assessment"]="NEVER_RUN"

    elif result.get(
        "latest_run_age_seconds",
        10**12,
    ) > 86400:
        result["assessment"]="STALE_CONFIRMED"

    else:
        result["assessment"]="CURRENTLY_HEALTHY"

    return result


def internal_gap_diagnostic(db, account_id):
    unresolved=0
    waiting=0

    if table_exists(
        db,
        "factory_execution_state",
    ):
        unresolved=db.execute(text("""
            SELECT count(*)
            FROM factory_execution_state
            WHERE account_id=:a
              AND status='SKIPPED_UNRESOLVED'
        """),{
            "a":account_id,
        }).scalar()

    if table_exists(
        db,
        "factory_missing_fact_state",
    ):
        waiting=db.execute(text("""
            SELECT count(*)
            FROM factory_missing_fact_state
            WHERE account_id=:a
              AND status='WAITING_FACT'
        """),{
            "a":account_id,
        }).scalar()

    assessment=(
        "OWNER_FACT_REQUIRED"
        if waiting
        else
        "AUTO_RESOLVER_RECHECK"
        if unresolved
        else
        "GAP_DISAPPEARED"
    )

    return {
        "diagnostic":"internal_gap",
        "account_id":account_id,
        "unresolved_internal":
            unresolved,
        "waiting_facts":
            waiting,
        "assessment":
            assessment,
    }


def execute_one(db, item):
    action=item["action_type"]

    if action=="DIAGNOSE_SCHEDULER":
        result=scheduler_diagnostic(
            db,
            item["account_id"],
        )

    elif action=="DIAGNOSE_INTERNAL_GAP":
        result=internal_gap_diagnostic(
            db,
            item["account_id"],
        )

    else:
        raise RuntimeError(
            "non-allowlisted action reached executor"
        )

    fp=stable_hash({
        "attention_key":
            item["attention_key"],

        "source_attention_fingerprint":
            item["attention_fingerprint"],

        "action_type":
            action,

        "result":
            result,
    })

    return {
        **item,
        "result":result,
        "execution_fingerprint":fp,
        "execution_status":
            "COMPLETED_DIAGNOSTIC",
    }


def build(db):
    return [
        execute_one(db,item)
        for item in queue(db)
    ]


def apply(db):
    ensure_schema(db)

    items=build(db)

    inserted=0
    updated=0
    unchanged=0
    events=0

    for x in items:

        # Absolute route protection.
        if x["route"] != "AUTO_INTERNAL":
            raise RuntimeError(
                "OWNER_REQUIRED reached N"
            )

        if x["issue_type"] not in ALLOWLIST:
            raise RuntimeError(
                "non-allowlisted issue reached N"
            )

        old=db.execute(text("""
            SELECT
                status,
                execution_fingerprint
            FROM factory_auto_execution_state
            WHERE attention_key=:k
        """),{
            "k":x["attention_key"],
        }).fetchone()

        if (
            old
            and old[0]
                == x["execution_status"]
            and old[1]
                == x["execution_fingerprint"]
        ):
            unchanged += 1
            continue

        before=old[0] if old else None

        params={
            "attention_key":
                x["attention_key"],

            "account_id":
                x["account_id"],

            "issue_type":
                x["issue_type"],

            "action_type":
                x["action_type"],

            "status":
                x["execution_status"],

            "source_fp":
                x["attention_fingerprint"],

            "result":
                json.dumps(
                    x["result"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),

            "execution_fp":
                x["execution_fingerprint"],
        }

        db.execute(text("""
            INSERT INTO factory_auto_execution_state (
                attention_key,
                account_id,
                issue_type,
                action_type,
                status,
                source_attention_fingerprint,
                result,
                execution_fingerprint,
                executed_at,
                updated_at
            )
            VALUES (
                :attention_key,
                :account_id,
                :issue_type,
                :action_type,
                :status,
                :source_fp,
                CAST(:result AS JSONB),
                :execution_fp,
                NOW(),
                NOW()
            )
            ON CONFLICT (attention_key)
            DO UPDATE SET
                account_id=EXCLUDED.account_id,
                issue_type=EXCLUDED.issue_type,
                action_type=EXCLUDED.action_type,
                status=EXCLUDED.status,
                source_attention_fingerprint=
                    EXCLUDED.source_attention_fingerprint,
                result=EXCLUDED.result,
                execution_fingerprint=
                    EXCLUDED.execution_fingerprint,
                executed_at=NOW(),
                updated_at=NOW()
        """),params)

        db.execute(text("""
            INSERT INTO factory_auto_execution_events (
                attention_key,
                account_id,
                issue_type,
                action_type,
                before_status,
                after_status,
                execution_fingerprint,
                result,
                created_at
            )
            VALUES (
                :attention_key,
                :account_id,
                :issue_type,
                :action_type,
                :before_status,
                :status,
                :execution_fp,
                CAST(:result AS JSONB),
                NOW()
            )
        """),{
            **params,
            "before_status":before,
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
