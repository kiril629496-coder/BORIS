"""
BORIS Factory O — FACTORY_O_V1.

Policy / guardrail layer for Factory N.

Factory O DOES NOT execute remediation.

It defines whether a future N AUTO_INTERNAL action is:
    ALLOWED
    COOLDOWN
    EXHAUSTED
    EXPIRED
    FORBIDDEN

OWNER_REQUIRED is ALWAYS FORBIDDEN.

V1 safe actions:
    SCHEDULER_STALE -> DIAGNOSE_SCHEDULER
    INTERNAL_GAP   -> DIAGNOSE_INTERNAL_GAP

Both are currently diagnostic-only.

Writes only:
    factory_remediation_policy
    factory_remediation_policy_events
    factory_remediation_guard_state

No:
    external APIs
    client messages
    publishing
    money
    confirmation approval
    schedule mutation
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


FACTORY_O_V1=True


POLICY={
    "SCHEDULER_STALE": {
        "action_type":"DIAGNOSE_SCHEDULER",
        "enabled":True,
        "max_attempts":3,
        "cooldown_seconds":900,
        "ttl_seconds":86400,
        "rollback_required":False,
        "external_effects_allowed":False,
        "escalate_after_exhaustion":True,
    },

    "INTERNAL_GAP": {
        "action_type":"DIAGNOSE_INTERNAL_GAP",
        "enabled":True,
        "max_attempts":3,
        "cooldown_seconds":900,
        "ttl_seconds":86400,
        "rollback_required":False,
        "external_effects_allowed":False,
        "escalate_after_exhaustion":True,
    },
}


DDL=(
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

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


def policy_rows():
    rows=[]

    for issue_type,cfg in sorted(
        POLICY.items()
    ):
        payload={
            "issue_type":issue_type,
            **cfg,
        }

        payload["policy_fingerprint"]=stable_hash(
            payload
        )

        rows.append(payload)

    return rows


def sync_policy(db):
    ensure_schema(db)

    inserted=0
    updated=0
    unchanged=0
    events=0

    for item in policy_rows():

        old=db.execute(text("""
            SELECT policy_fingerprint
            FROM factory_remediation_policy
            WHERE issue_type=:issue_type
        """),{
            "issue_type":item["issue_type"],
        }).scalar()

        if old == item["policy_fingerprint"]:
            unchanged += 1
            continue

        existed=old is not None

        db.execute(text("""
            INSERT INTO factory_remediation_policy (
                issue_type,
                action_type,
                enabled,
                max_attempts,
                cooldown_seconds,
                ttl_seconds,
                rollback_required,
                external_effects_allowed,
                escalate_after_exhaustion,
                policy_fingerprint,
                created_at,
                updated_at
            )
            VALUES (
                :issue_type,
                :action_type,
                :enabled,
                :max_attempts,
                :cooldown_seconds,
                :ttl_seconds,
                :rollback_required,
                :external_effects_allowed,
                :escalate_after_exhaustion,
                :policy_fingerprint,
                NOW(),
                NOW()
            )
            ON CONFLICT (issue_type)
            DO UPDATE SET
                action_type=EXCLUDED.action_type,
                enabled=EXCLUDED.enabled,
                max_attempts=EXCLUDED.max_attempts,
                cooldown_seconds=EXCLUDED.cooldown_seconds,
                ttl_seconds=EXCLUDED.ttl_seconds,
                rollback_required=EXCLUDED.rollback_required,
                external_effects_allowed=EXCLUDED.external_effects_allowed,
                escalate_after_exhaustion=
                    EXCLUDED.escalate_after_exhaustion,
                policy_fingerprint=EXCLUDED.policy_fingerprint,
                updated_at=NOW()
        """),item)

        db.execute(text("""
            INSERT INTO factory_remediation_policy_events (
                issue_type,
                action_type,
                event_type,
                policy_fingerprint,
                created_at
            )
            VALUES (
                :issue_type,
                :action_type,
                :event_type,
                :policy_fingerprint,
                NOW()
            )
        """),{
            **item,
            "event_type":(
                "POLICY_UPDATED"
                if existed
                else "POLICY_CREATED"
            ),
        })

        if existed:
            updated += 1
        else:
            inserted += 1

        events += 1

    return {
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
        "events":events,
    }


def attempts_for(db, attention_key):
    return db.execute(text("""
        SELECT count(*)
        FROM factory_auto_execution_events
        WHERE attention_key=:k
    """),{
        "k":attention_key,
    }).scalar()


def latest_attempt(db, attention_key):
    return db.execute(text("""
        SELECT max(created_at)
        FROM factory_auto_execution_events
        WHERE attention_key=:k
    """),{
        "k":attention_key,
    }).scalar()


def evaluate(db, attention):
    route=attention["route"]
    issue_type=attention["issue_type"]

    now=datetime.now(timezone.utc)

    if route != "AUTO_INTERNAL":
        decision="FORBIDDEN"
        reason="route_not_auto_internal"

        action_type="NONE"
        rollback_required=False
        external_effects_allowed=False

        attempts=0
        next_allowed=None
        expires_at=None

    elif issue_type not in POLICY:
        decision="FORBIDDEN"
        reason="issue_not_allowlisted"

        action_type="NONE"
        rollback_required=False
        external_effects_allowed=False

        attempts=0
        next_allowed=None
        expires_at=None

    else:
        cfg=POLICY[issue_type]

        action_type=cfg["action_type"]
        rollback_required=cfg[
            "rollback_required"
        ]

        external_effects_allowed=cfg[
            "external_effects_allowed"
        ]

        attempts=attempts_for(
            db,
            attention["attention_key"],
        )

        created=attention["created_at"]

        if created.tzinfo is None:
            created=created.replace(
                tzinfo=timezone.utc
            )

        expires_at=created + timedelta(
            seconds=cfg["ttl_seconds"]
        )

        last=latest_attempt(
            db,
            attention["attention_key"],
        )

        if last and last.tzinfo is None:
            last=last.replace(
                tzinfo=timezone.utc
            )

        next_allowed=(
            last + timedelta(
                seconds=cfg[
                    "cooldown_seconds"
                ]
            )
            if last
            else None
        )

        if not cfg["enabled"]:
            decision="FORBIDDEN"
            reason="policy_disabled"

        elif cfg["external_effects_allowed"]:
            # V1 invariant:
            # O cannot authorize an externally visible action.
            decision="FORBIDDEN"
            reason="external_effects_forbidden_in_O_v1"

        elif now >= expires_at:
            decision="EXPIRED"
            reason="attention_ttl_expired"

        elif attempts >= cfg["max_attempts"]:
            decision="EXHAUSTED"
            reason="max_attempts_reached"

        elif (
            next_allowed is not None
            and now < next_allowed
        ):
            decision="COOLDOWN"
            reason="cooldown_active"

        else:
            decision="ALLOWED"
            reason="policy_allows_safe_internal_action"

    payload={
        "attention_key":
            attention["attention_key"],

        "account_id":
            attention["account_id"],

        "issue_type":
            issue_type,

        "action_type":
            action_type,

        "route":
            route,

        "decision":
            decision,

        "attempts":
            attempts,

        "next_allowed_at":
            next_allowed,

        "expires_at":
            expires_at,

        "rollback_required":
            rollback_required,

        "external_effects_allowed":
            external_effects_allowed,

        "reason":
            reason,

        "source_attention_fingerprint":
            attention[
                "attention_fingerprint"
            ],
    }

    payload["guard_fingerprint"]=stable_hash(
        payload
    )

    return payload


def build(db):
    ensure_schema(db)

    rows=db.execute(text("""
        SELECT
            attention_key,
            account_id,
            issue_type,
            route,
            priority,
            status,
            attention_fingerprint,
            created_at
        FROM factory_attention_state
        WHERE status='OPEN'
        ORDER BY priority DESC,account_id,issue_type
    """)).mappings().all()

    return [
        evaluate(db,dict(row))
        for row in rows
    ]


def apply_guard_state(db):
    ensure_schema(db)

    items=build(db)

    inserted=0
    updated=0
    unchanged=0

    for x in items:

        old=db.execute(text("""
            SELECT guard_fingerprint
            FROM factory_remediation_guard_state
            WHERE attention_key=:k
        """),{
            "k":x["attention_key"],
        }).scalar()

        if old == x["guard_fingerprint"]:
            unchanged += 1
            continue

        existed=old is not None

        db.execute(text("""
            INSERT INTO factory_remediation_guard_state (
                attention_key,
                account_id,
                issue_type,
                action_type,
                route,
                decision,
                attempts,
                first_seen_at,
                last_attempt_at,
                next_allowed_at,
                expires_at,
                rollback_required,
                external_effects_allowed,
                reason,
                source_attention_fingerprint,
                guard_fingerprint,
                updated_at
            )
            VALUES (
                :attention_key,
                :account_id,
                :issue_type,
                :action_type,
                :route,
                :decision,
                :attempts,
                NOW(),
                NULL,
                :next_allowed_at,
                :expires_at,
                :rollback_required,
                :external_effects_allowed,
                :reason,
                :source_attention_fingerprint,
                :guard_fingerprint,
                NOW()
            )
            ON CONFLICT (attention_key)
            DO UPDATE SET
                account_id=EXCLUDED.account_id,
                issue_type=EXCLUDED.issue_type,
                action_type=EXCLUDED.action_type,
                route=EXCLUDED.route,
                decision=EXCLUDED.decision,
                attempts=EXCLUDED.attempts,
                next_allowed_at=EXCLUDED.next_allowed_at,
                expires_at=EXCLUDED.expires_at,
                rollback_required=EXCLUDED.rollback_required,
                external_effects_allowed=EXCLUDED.external_effects_allowed,
                reason=EXCLUDED.reason,
                source_attention_fingerprint=
                    EXCLUDED.source_attention_fingerprint,
                guard_fingerprint=EXCLUDED.guard_fingerprint,
                updated_at=NOW()
        """),x)

        if existed:
            updated += 1
        else:
            inserted += 1

    return {
        "items":items,
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
    }
