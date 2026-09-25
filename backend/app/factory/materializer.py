"""BORIS Factory safe materializer — FACTORY_E_V1.

Writes ONLY:
    factory_resolved_values
    factory_action_log

Never writes:
    client_config
    storage
    accounts
    money_mandates
    tg_routes
    account access
    billing
    cron
    external APIs

This is configuration PREPARATION, not consumer cutover.
"""

import hashlib
import json

from sqlalchemy import text


AUTO_ACTIONS = {
    "DERIVE",
    "MIGRATE",
}


# Explicit allowlist.
#
# Unknown fields are NEVER auto materialized.
SAFE_FIELDS = {
    "profile.timezone",
    "profile.city",
    "kpi.business_hours",
    "knowledge.facts_scope",
    "knowledge.prompt_ref",

    "schedule.autopilot",
    "schedule.mop_poll",
    "schedule.memory_extract",
}


BLOCKED_PREFIXES = (
    "automation.money",
    "limits.",
)


BLOCKED_FIELDS = {
    "channels.avito",
    "channels.telegram_routes",
    "access.roles",
    "automation.mode",
    "products.mop",
    "products.rop",
    "products.cpx",
    "products.feed",
}


def stable_hash(obj):
    raw=json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",",":"),
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def safe_field(field):
    if field in BLOCKED_FIELDS:
        return False

    if any(
        field.startswith(prefix)
        for prefix in BLOCKED_PREFIXES
    ):
        return False

    return field in SAFE_FIELDS


def extract_value(action):
    ev=action.get("evidence") or {}

    if "candidate_value" in ev:
        return ev.get("candidate_value")

    if "normalized_value" in ev:
        return ev.get("normalized_value")

    return None


def resolution(action):
    field=action.get("field")
    kind=action.get("action")

    if kind not in AUTO_ACTIONS:
        return {
            "decision":"SKIP",
            "reason":"action type is not auto-materializable",
        }

    if not safe_field(field):
        return {
            "decision":"SKIP",
            "reason":"field is not on explicit safe allowlist",
        }

    value=extract_value(action)

    if value is None:
        return {
            "decision":"SKIP",
            "reason":"no deterministic value in evidence",
        }

    evidence=action.get("evidence") or {}

    return {
        "decision":"MATERIALIZE",
        "field":field,
        "value":value,
        "source":(
            evidence.get("source")
            or evidence.get("legacy_source")
            or action.get("action")
        ),
        "confidence":evidence.get("confidence"),
        "resolution_kind":kind,
        "evidence":evidence,
    }


def current_value(db,account_id,field):
    row=db.execute(text("""
        SELECT value
        FROM factory_resolved_values
        WHERE account_id=:a
          AND field=:f
    """),{
        "a":account_id,
        "f":field,
    }).fetchone()

    return row[0] if row else None


def log_action(
    db,
    account_id,
    field,
    action,
    outcome,
    reason,
    before,
    after,
    fingerprint,
):
    db.execute(text("""
        INSERT INTO factory_action_log (
            account_id,
            field,
            action,
            outcome,
            reason,
            before_value,
            after_value,
            fingerprint
        )
        VALUES (
            :account_id,
            :field,
            :action,
            :outcome,
            :reason,
            CAST(:before_value AS jsonb),
            CAST(:after_value AS jsonb),
            :fingerprint
        )
        ON CONFLICT (
            account_id,
            field,
            action,
            fingerprint
        )
        DO NOTHING
    """),{
        "account_id":account_id,
        "field":field,
        "action":action,
        "outcome":outcome,
        "reason":reason,
        "before_value":json.dumps(
            before,
            ensure_ascii=False,
            default=str,
        ),
        "after_value":json.dumps(
            after,
            ensure_ascii=False,
            default=str,
        ),
        "fingerprint":fingerprint,
    })


def apply_resolution(db,account_id,res):
    field=res["field"]
    value=res["value"]

    before=current_value(
        db,
        account_id,
        field,
    )

    payload={
        "account_id":account_id,
        "field":field,
        "value":value,
        "source":res.get("source"),
        "confidence":res.get("confidence"),
        "resolution_kind":res["resolution_kind"],
        "evidence":res.get("evidence") or {},
    }

    fp=stable_hash(payload)

    db.execute(text("""
        INSERT INTO factory_resolved_values (
            account_id,
            field,
            value,
            source,
            confidence,
            resolution_kind,
            evidence,
            source_fingerprint,
            created_at,
            updated_at
        )
        VALUES (
            :account_id,
            :field,
            CAST(:value AS jsonb),
            :source,
            :confidence,
            :resolution_kind,
            CAST(:evidence AS jsonb),
            :source_fingerprint,
            now(),
            now()
        )
        ON CONFLICT (account_id,field)
        DO UPDATE SET
            value=EXCLUDED.value,
            source=EXCLUDED.source,
            confidence=EXCLUDED.confidence,
            resolution_kind=EXCLUDED.resolution_kind,
            evidence=EXCLUDED.evidence,
            source_fingerprint=EXCLUDED.source_fingerprint,
            updated_at=
                CASE
                    WHEN factory_resolved_values.source_fingerprint
                         IS DISTINCT FROM EXCLUDED.source_fingerprint
                    THEN now()
                    ELSE factory_resolved_values.updated_at
                END
    """),{
        "account_id":account_id,
        "field":field,
        "value":json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        ),
        "source":res.get("source"),
        "confidence":res.get("confidence"),
        "resolution_kind":res["resolution_kind"],
        "evidence":json.dumps(
            res.get("evidence") or {},
            ensure_ascii=False,
            default=str,
        ),
        "source_fingerprint":fp,
    })

    after=value

    outcome=(
        "UNCHANGED"
        if before == after
        else (
            "CREATED"
            if before is None
            else "UPDATED"
        )
    )

    log_action(
        db,
        account_id,
        field,
        res["resolution_kind"],
        outcome,
        "safe Factory materialization",
        before,
        after,
        fp,
    )

    return outcome
