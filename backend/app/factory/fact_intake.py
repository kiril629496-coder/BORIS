"""
BORIS Factory J — FACTORY_J_V1.

Controlled internal fact intake.

Purpose:
    Accept trusted structured facts without manual DB editing.

V1 supported fact:
    geo.city_or_region

Flow:
    J accepted fact
      -> H structured resolver
      -> F safe internal executor
      -> G provisioning
      -> I missing-facts queue

J NEVER:
    - sends messages
    - publishes
    - changes Client Config
    - spends money
    - calls external APIs
    - guesses values
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from sqlalchemy import text


FACTORY_J_V1=True


SUPPORTED_FACTS={
    "geo.city_or_region": {
        "target_field":"profile.timezone",
    },
}


ALLOWED_SOURCE_TYPES={
    "client_confirmed",
    "onboarding",
    "operator",
    "manual_verified",
    "import_verified",
}


DDL=(
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))


def _json(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def stable_hash(value):
    raw=json.dumps(
        _json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def normalize_value(value):
    if value is None:
        raise ValueError(
            "fact value cannot be null"
        )

    if isinstance(value,str):
        value=" ".join(
            value.strip().split()
        )

        if not value:
            raise ValueError(
                "fact value cannot be empty"
            )

    return value


def validate(
    *,
    account_id,
    fact_kind,
    value,
    source_type,
    source_ref,
    actor,
    confidence,
):
    account_id=str(account_id or "").strip()
    fact_kind=str(fact_kind or "").strip()
    source_type=str(source_type or "").strip()
    source_ref=str(source_ref or "").strip()
    actor=str(actor or "").strip()

    if not account_id:
        raise ValueError(
            "account_id required"
        )

    if fact_kind not in SUPPORTED_FACTS:
        raise ValueError(
            "unsupported fact_kind: %s"
            % fact_kind
        )

    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError(
            "unsupported source_type: %s"
            % source_type
        )

    if not source_ref:
        raise ValueError(
            "source_ref required"
        )

    if not actor:
        raise ValueError(
            "actor required"
        )

    value=normalize_value(value)

    confidence=float(confidence)

    if not 0.90 <= confidence <= 1.0:
        raise ValueError(
            "controlled intake requires confidence 0.90..1.00"
        )

    return {
        "account_id":account_id,
        "fact_kind":fact_kind,
        "target_field":
            SUPPORTED_FACTS[fact_kind]["target_field"],
        "value":value,
        "source_type":source_type,
        "source_ref":source_ref,
        "actor":actor,
        "confidence":confidence,
    }


def submit(
    db,
    *,
    account_id,
    fact_kind,
    value,
    source_type,
    source_ref,
    actor,
    confidence=1.0,
):
    ensure_schema(db)

    data=validate(
        account_id=account_id,
        fact_kind=fact_kind,
        value=value,
        source_type=source_type,
        source_ref=source_ref,
        actor=actor,
        confidence=confidence,
    )

    provenance={
        "source_type":data["source_type"],
        "source_ref":data["source_ref"],
        "actor":data["actor"],
        "controlled_intake":True,
    }

    fingerprint=stable_hash({
        **data,
        "provenance":provenance,
    })

    old=db.execute(text("""
        SELECT
            status,
            value,
            fact_fingerprint
        FROM factory_fact_intake_state
        WHERE account_id=:a
          AND fact_kind=:k
    """),{
        "a":data["account_id"],
        "k":data["fact_kind"],
    }).mappings().first()

    if (
        old
        and old["fact_fingerprint"]==fingerprint
        and old["status"]=="ACCEPTED"
    ):
        return {
            "result":"UNCHANGED",
            "fingerprint":fingerprint,
        }

    before_status=(
        old["status"]
        if old
        else None
    )

    before_value=(
        old["value"]
        if old
        else None
    )

    db.execute(text("""
        INSERT INTO factory_fact_intake_state (
            account_id,
            fact_kind,
            target_field,
            value,
            status,
            source_type,
            source_ref,
            actor,
            confidence,
            provenance,
            fact_fingerprint,
            created_at,
            updated_at
        )
        VALUES (
            :a,
            :k,
            :f,
            CAST(:v AS JSONB),
            'ACCEPTED',
            :st,
            :sr,
            :actor,
            :c,
            CAST(:p AS JSONB),
            :fp,
            NOW(),
            NOW()
        )
        ON CONFLICT (account_id,fact_kind)
        DO UPDATE SET
            target_field=EXCLUDED.target_field,
            value=EXCLUDED.value,
            status='ACCEPTED',
            source_type=EXCLUDED.source_type,
            source_ref=EXCLUDED.source_ref,
            actor=EXCLUDED.actor,
            confidence=EXCLUDED.confidence,
            provenance=EXCLUDED.provenance,
            fact_fingerprint=EXCLUDED.fact_fingerprint,
            updated_at=NOW()
    """),{
        "a":data["account_id"],
        "k":data["fact_kind"],
        "f":data["target_field"],
        "v":json.dumps(
            data["value"],
            ensure_ascii=False,
            sort_keys=True,
        ),
        "st":data["source_type"],
        "sr":data["source_ref"],
        "actor":data["actor"],
        "c":data["confidence"],
        "p":json.dumps(
            provenance,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "fp":fingerprint,
    })

    db.execute(text("""
        INSERT INTO factory_fact_intake_events (
            account_id,
            fact_kind,
            event_type,
            before_status,
            after_status,
            before_value,
            after_value,
            source_type,
            source_ref,
            actor,
            confidence,
            fact_fingerprint,
            payload,
            created_at
        )
        VALUES (
            :a,
            :k,
            :e,
            :bs,
            'ACCEPTED',
            CAST(:bv AS JSONB),
            CAST(:av AS JSONB),
            :st,
            :sr,
            :actor,
            :c,
            :fp,
            CAST(:p AS JSONB),
            NOW()
        )
    """),{
        "a":data["account_id"],
        "k":data["fact_kind"],
        "e":(
            "FACT_ACCEPTED"
            if old is None
            else "FACT_REPLACED"
        ),
        "bs":before_status,
        "bv":json.dumps(
            before_value,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "av":json.dumps(
            data["value"],
            ensure_ascii=False,
            sort_keys=True,
        ),
        "st":data["source_type"],
        "sr":data["source_ref"],
        "actor":data["actor"],
        "c":data["confidence"],
        "fp":fingerprint,
        "p":json.dumps(
            {
                **data,
                "provenance":provenance,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    })

    return {
        "result":(
            "CREATED"
            if old is None
            else "UPDATED"
        ),
        "fingerprint":fingerprint,
    }


def get_fact(
    db,
    account_id,
    fact_kind,
):
    ensure_schema(db)

    row=db.execute(text("""
        SELECT
            account_id,
            fact_kind,
            target_field,
            value,
            status,
            source_type,
            source_ref,
            actor,
            confidence,
            provenance,
            fact_fingerprint
        FROM factory_fact_intake_state
        WHERE account_id=:a
          AND fact_kind=:k
          AND status='ACCEPTED'
    """),{
        "a":account_id,
        "k":fact_kind,
    }).mappings().first()

    return dict(row) if row else None


def list_state(db):
    ensure_schema(db)

    return [
        dict(x)
        for x in db.execute(text("""
            SELECT
                account_id,
                fact_kind,
                target_field,
                value,
                status,
                source_type,
                source_ref,
                actor,
                confidence,
                updated_at
            FROM factory_fact_intake_state
            ORDER BY account_id,fact_kind
        """)).mappings()
    ]
