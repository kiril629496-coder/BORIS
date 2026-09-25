"""BORIS Factory Q — internal outcome observation.

Q does NOT remediate anything.

It only classifies current internal recovery/attention outcome.

Allowed persistent writes:
    factory_outcome_state
    factory_outcome_events
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import inspect, text


FACTORY_Q_V1 = True

ALLOWED_RESULTS = {
    "RESOLVED",
    "STILL_PRESENT",
    "OBSERVE",
    "ESCALATE",
    "PROTECTED_OWNER_REQUIRED",
}


def utcnow():
    return datetime.now(timezone.utc)


def stable_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def fingerprint(value):
    return hashlib.sha256(
        stable_json(value).encode("utf-8")
    ).hexdigest()


def table_exists(db, table):
    return inspect(db.bind).has_table(table)


def columns(db, table):
    if not table_exists(db, table):
        return set()

    return {
        c["name"]
        for c in inspect(db.bind).get_columns(table)
    }


def rows_as_dicts(db, sql, params=None):
    return [
        dict(r._mapping)
        for r in db.execute(
            text(sql),
            params or {},
        )
    ]


def _first(row, *names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def load_attention(db):
    if not table_exists(
        db,
        "factory_attention_state",
    ):
        raise RuntimeError(
            "factory_attention_state missing"
        )

    return rows_as_dicts(
        db,
        """
        SELECT *
        FROM factory_attention_state
        ORDER BY account_id
        """,
    )


def load_recovery(db):
    if not table_exists(
        db,
        "factory_recovery_state",
    ):
        raise RuntimeError(
            "factory_recovery_state missing"
        )

    rows=rows_as_dicts(
        db,
        """
        SELECT *
        FROM factory_recovery_state
        ORDER BY account_id
        """,
    )

    result={}

    for row in rows:
        account=str(
            _first(
                row,
                "account_id",
                default="",
            )
        )

        issue=str(
            _first(
                row,
                "issue_type",
                "problem_type",
                "kind",
                default="",
            )
        )

        key=(account, issue)

        result.setdefault(
            key,
            [],
        ).append(row)

    return result


def classify(attention, recovery_rows):
    account=str(
        _first(
            attention,
            "account_id",
            default="",
        )
    )

    route=str(
        _first(
            attention,
            "route",
            default="",
        )
    )

    status=str(
        _first(
            attention,
            "status",
            default="",
        )
    )

    issue=str(
        _first(
            attention,
            "issue_type",
            "problem_type",
            "kind",
            default="UNKNOWN",
        )
    )

    if route == "OWNER_REQUIRED":
        return {
            "account_id": account,
            "issue_type": issue,
            "route": route,
            "attention_status": status,
            "result": "PROTECTED_OWNER_REQUIRED",
            "reason": (
                "owner_required_is_observation_only"
            ),
            "recovery_rows": len(recovery_rows),
        }

    if status in {
        "RESOLVED",
        "CLOSED",
        "DONE",
    }:
        return {
            "account_id": account,
            "issue_type": issue,
            "route": route,
            "attention_status": status,
            "result": "RESOLVED",
            "reason": "attention_item_closed",
            "recovery_rows": len(recovery_rows),
        }

    if recovery_rows:
        terminal=False
        attempts=0

        for r in recovery_rows:
            rstatus=str(
                _first(
                    r,
                    "status",
                    "decision",
                    "result",
                    default="",
                )
            ).upper()

            attempts=max(
                attempts,
                int(
                    _first(
                        r,
                        "attempts",
                        "attempt_count",
                        default=0,
                    )
                    or 0
                ),
            )

            if any(
                token in rstatus
                for token in (
                    "TERMINAL",
                    "ESCALAT",
                    "FAILED",
                )
            ):
                terminal=True

        if terminal:
            return {
                "account_id": account,
                "issue_type": issue,
                "route": route,
                "attention_status": status,
                "result": "ESCALATE",
                "reason": "terminal_internal_recovery",
                "recovery_rows": len(recovery_rows),
                "attempts": attempts,
            }

        return {
            "account_id": account,
            "issue_type": issue,
            "route": route,
            "attention_status": status,
            "result": "STILL_PRESENT",
            "reason": "attention_open_after_recovery",
            "recovery_rows": len(recovery_rows),
            "attempts": attempts,
        }

    return {
        "account_id": account,
        "issue_type": issue,
        "route": route,
        "attention_status": status,
        "result": "OBSERVE",
        "reason": "no_recovery_attempt_observed",
        "recovery_rows": 0,
    }


def plan(db):
    attention=load_attention(db)
    recovery=load_recovery(db)

    out=[]

    for item in attention:
        account=str(
            _first(
                item,
                "account_id",
                default="",
            )
        )

        issue=str(
            _first(
                item,
                "issue_type",
                "problem_type",
                "kind",
                default="UNKNOWN",
            )
        )

        matches=recovery.get(
            (account, issue),
            [],
        )

        # Some P schemas may not persist issue_type.
        if not matches:
            matches=[
                r
                for (acc, _kind), rr
                in recovery.items()
                if acc == account
                for r in rr
            ]

        decision=classify(
            item,
            matches,
        )

        if decision["result"] not in ALLOWED_RESULTS:
            raise RuntimeError(
                "invalid Q result"
            )

        decision["source_fingerprint"] = fingerprint(
            {
                "attention": item,
                "recovery": matches,
                "decision": decision,
            }
        )

        out.append(decision)

    return out


def ensure_schema(db):
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))

    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))


def apply(db, decisions):
    ensure_schema(db)

    inserted=0
    updated=0
    unchanged=0
    events=0

    for d in decisions:
        old=db.execute(
            text("""
                SELECT
                    outcome,
                    reason,
                    source_fingerprint
                FROM factory_outcome_state
                WHERE account_id=:account_id
                  AND issue_type=:issue_type
            """),
            d,
        ).mappings().first()

        if old is None:
            db.execute(
                text("""
                    INSERT INTO factory_outcome_state (
                        account_id,
                        issue_type,
                        route,
                        attention_status,
                        outcome,
                        reason,
                        recovery_rows,
                        attempts,
                        source_fingerprint
                    )
                    VALUES (
                        :account_id,
                        :issue_type,
                        :route,
                        :attention_status,
                        :result,
                        :reason,
                        :recovery_rows,
                        :attempts,
                        :source_fingerprint
                    )
                """),
                {
                    **d,
                    "attempts": int(
                        d.get("attempts", 0)
                    ),
                },
            )

            inserted += 1

        elif (
            old["source_fingerprint"]
            == d["source_fingerprint"]
            and old["outcome"]
            == d["result"]
            and old["reason"]
            == d["reason"]
        ):
            unchanged += 1

        else:
            db.execute(
                text("""
                    UPDATE factory_outcome_state
                    SET
                        route=:route,
                        attention_status=:attention_status,
                        outcome=:result,
                        reason=:reason,
                        recovery_rows=:recovery_rows,
                        attempts=:attempts,
                        source_fingerprint=:source_fingerprint,
                        last_seen_at=NOW()
                    WHERE account_id=:account_id
                      AND issue_type=:issue_type
                """),
                {
                    **d,
                    "attempts": int(
                        d.get("attempts", 0)
                    ),
                },
            )

            updated += 1

        result=db.execute(
            text("""
                INSERT INTO factory_outcome_events (
                    account_id,
                    issue_type,
                    outcome,
                    reason,
                    source_fingerprint
                )
                VALUES (
                    :account_id,
                    :issue_type,
                    :result,
                    :reason,
                    :source_fingerprint
                )
                ON CONFLICT (
                    account_id,
                    issue_type,
                    source_fingerprint
                )
                DO NOTHING
                RETURNING id
            """),
            d,
        ).first()

        if result:
            events += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "events": events,
    }
