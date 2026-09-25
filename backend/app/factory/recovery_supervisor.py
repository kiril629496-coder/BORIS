"""BORIS Factory P — recovery/escalation supervisor.

Internal-only state machine.

It DOES NOT:
- approve confirmations
- change schedules
- call external APIs
- send client messages
- publish
- spend money
- execute remediation itself

It only classifies internal remediation state and persists an
internal escalation/recovery decision.
"""

import hashlib
import json

from sqlalchemy import text


FACTORY_P_V1=True

ALLOWED_SOURCE_ROUTES={
    "AUTO_INTERNAL",
}

OWNER_ROUTE="OWNER_REQUIRED"

TERMINAL_REASONS={
    "EXHAUSTED",
    "EXPIRED",
}

NOOP_REASONS={
    "COOLDOWN",
    "SUCCESS",
    "RESOLVED",
}


DDL=[
'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
]


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))
    db.commit()


def _fp(payload):
    raw=json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def _table_exists(db, name):
    return bool(
        db.execute(
            text("""
                SELECT to_regclass(:name)
            """),
            {"name":name},
        ).scalar()
    )


def _columns(db, table):
    rows=db.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=:table
        """),
        {"table":table},
    ).fetchall()

    return {r[0] for r in rows}


def _load_attention(db):
    return db.execute(text("""
        SELECT
            account_id,
            issue_type,
            route,
            priority,
            status
        FROM factory_attention_state
        WHERE status='OPEN'
        ORDER BY priority DESC, account_id, issue_type
    """)).mappings().all()


def _load_guard_map(db):
    """
    O schemas evolved during installation.
    Read only columns that actually exist.
    """
    table="factory_remediation_guard"

    if not _table_exists(db,table):
        return {}

    cols=_columns(db,table)

    account_col=(
        "account_id"
        if "account_id" in cols
        else None
    )

    issue_col=(
        "issue_type"
        if "issue_type" in cols
        else None
    )

    if not account_col or not issue_col:
        return {}

    optional=[
        x for x in (
            "decision",
            "attempts",
            "reason",
            "status",
            "updated_at",
        )
        if x in cols
    ]

    selected=[
        account_col,
        issue_col,
        *optional,
    ]

    sql="SELECT %s FROM %s" % (
        ",".join(selected),
        table,
    )

    rows=db.execute(text(sql)).mappings().all()

    out={}

    for r in rows:
        key=(r[account_col],r[issue_col])
        out[key]=dict(r)

    return out


def classify(item, guard):
    route=item["route"]

    # Absolute invariant.
    if route == OWNER_ROUTE:
        return {
            "recovery_status":"PROTECTED_OWNER_REQUIRED",
            "target_route":None,
            "reason":"owner_required_is_never_auto_recovered",
            "attempts":0,
            "external_effect":False,
        }

    if route not in ALLOWED_SOURCE_ROUTES:
        return {
            "recovery_status":"NOOP",
            "target_route":None,
            "reason":"route_not_auto_internal",
            "attempts":0,
            "external_effect":False,
        }

    if not guard:
        return {
            "recovery_status":"OBSERVE",
            "target_route":None,
            "reason":"no_guard_state",
            "attempts":0,
            "external_effect":False,
        }

    decision=str(
        guard.get("decision")
        or guard.get("status")
        or ""
    ).upper()

    reason=str(
        guard.get("reason")
        or ""
    ).upper()

    attempts=int(
        guard.get("attempts")
        or 0
    )

    combined="%s %s" % (
        decision,
        reason,
    )

    if any(x in combined for x in TERMINAL_REASONS):
        return {
            "recovery_status":"ESCALATE_OWNER",
            "target_route":OWNER_ROUTE,
            "reason":"safe_internal_remediation_terminal",
            "attempts":attempts,
            "external_effect":False,
        }

    if "COOLDOWN" in combined:
        return {
            "recovery_status":"COOLDOWN",
            "target_route":None,
            "reason":"remediation_cooldown",
            "attempts":attempts,
            "external_effect":False,
        }

    if any(x in combined for x in NOOP_REASONS):
        return {
            "recovery_status":"NOOP",
            "target_route":None,
            "reason":"remediation_not_terminal",
            "attempts":attempts,
            "external_effect":False,
        }

    return {
        "recovery_status":"OBSERVE",
        "target_route":None,
        "reason":"remediation_state_nonterminal",
        "attempts":attempts,
        "external_effect":False,
    }


def plan(db):
    attention=_load_attention(db)
    guards=_load_guard_map(db)

    result=[]

    for item in attention:
        key=(
            item["account_id"],
            item["issue_type"],
        )

        decision=classify(
            item,
            guards.get(key),
        )

        payload={
            "account_id":item["account_id"],
            "issue_type":item["issue_type"],
            "source_route":item["route"],
            **decision,
        }

        payload["attention_key"]="%s:%s" % key
        payload["fingerprint"]=_fp(payload)

        result.append(payload)

    return result


def apply(db, rows):
    inserted=0
    updated=0
    unchanged=0
    events=0

    for r in rows:
        old=db.execute(
            text("""
                SELECT fingerprint
                FROM factory_recovery_state
                WHERE attention_key=:attention_key
            """),
            {
                "attention_key":r["attention_key"],
            },
        ).scalar()

        if old == r["fingerprint"]:
            unchanged += 1
            continue

        exists=old is not None

        db.execute(
            text("""
                INSERT INTO factory_recovery_state (
                    attention_key,
                    account_id,
                    issue_type,
                    source_route,
                    recovery_status,
                    target_route,
                    reason,
                    attempts,
                    external_effect,
                    fingerprint,
                    updated_at
                )
                VALUES (
                    :attention_key,
                    :account_id,
                    :issue_type,
                    :source_route,
                    :recovery_status,
                    :target_route,
                    :reason,
                    :attempts,
                    :external_effect,
                    :fingerprint,
                    NOW()
                )
                ON CONFLICT (attention_key)
                DO UPDATE SET
                    account_id=EXCLUDED.account_id,
                    issue_type=EXCLUDED.issue_type,
                    source_route=EXCLUDED.source_route,
                    recovery_status=EXCLUDED.recovery_status,
                    target_route=EXCLUDED.target_route,
                    reason=EXCLUDED.reason,
                    attempts=EXCLUDED.attempts,
                    external_effect=EXCLUDED.external_effect,
                    fingerprint=EXCLUDED.fingerprint,
                    updated_at=NOW()
            """),
            r,
        )

        db.execute(
            text("""
                INSERT INTO factory_recovery_events (
                    attention_key,
                    account_id,
                    issue_type,
                    recovery_status,
                    target_route,
                    reason,
                    fingerprint
                )
                VALUES (
                    :attention_key,
                    :account_id,
                    :issue_type,
                    :recovery_status,
                    :target_route,
                    :reason,
                    :fingerprint
                )
                ON CONFLICT (attention_key, fingerprint)
                DO NOTHING
            """),
            r,
        )

        if exists:
            updated += 1
        else:
            inserted += 1

        events += 1

    db.commit()

    return {
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
        "events":events,
    }
