"""
BORIS Factory F — FACTORY_F_V1.

Executes ONLY explicitly allowlisted, internal, reversible preparation actions.

V1 allowlist:
    profile.timezone

Target:
    storage key = factory_profile_timezone

This deliberately does NOT modify Client Config.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from sqlalchemy import text

from app.factory import h_resolver as H

FACTORY_H_CONSUMER_V1 = True


FACTORY_F_V1 = True

ALLOWLIST = {
        "expand_inventory",
    "profile.timezone",
}

FIELD_TARGET = {
    "profile.timezone": "factory_profile_timezone",
}


DDL = (
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


def _json(v):
    return json.loads(
        json.dumps(
            v,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
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


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))


def load_actions(db):
    rows=db.execute(text("""
        SELECT
            account_id,
            bootstrap_fingerprint,
            package
        FROM factory_bootstrap_state b
        WHERE NOT EXISTS (
            SELECT 1 FROM reliability_kill_switches r
             WHERE r.account_id=b.account_id AND r.blocked=true
               AND r.module IN ('actions','background')
        )
        ORDER BY b.account_id
    """)).mappings().all()

    out=[]

    for row in rows:
        for a in row["package"].get(
            "internal_prepare_actions"
        ) or []:

            field=a.get("field")

            if field not in ALLOWLIST:
                raise ValueError(
                    "field outside Factory F allowlist: %s" % field
                )

            out.append({
                "account_id":row["account_id"],
                "field":field,
                "candidate_value":a.get("candidate_value"),
                "action":a.get("action"),
                "evidence":a.get("evidence") or {},
                "bootstrap_fingerprint":row[
                    "bootstrap_fingerprint"
                ],
            })

    return out


def current_value(db, account_id, field):
    key=FIELD_TARGET[field]

    row=db.execute(text("""
        SELECT value
        FROM storage
        WHERE account_id=:a
          AND key=:k
        ORDER BY id DESC
        LIMIT 1
    """),{
        "a":account_id,
        "k":key,
    }).fetchone()

    if not row:
        return None

    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def plan(db):
    actions=load_actions(db)

    result=[]

    for a in actions:

        unresolved = (
            a["candidate_value"] in (None, "")
            or bool((a.get("evidence") or {}).get("needs_confirmation"))
        )


        h_resolution_used=False

        if unresolved:
            h_value=H.get_value(
                db,
                a["account_id"],
                a["field"],
            )

            if h_value is not None:
                a=dict(a)

                a["candidate_value"]=h_value["value"]

                a["evidence"]={
                    "factory_h_resolution":True,
                    "source":h_value.get("source"),
                    "confidence":float(
                        h_value.get("confidence")
                        or 0
                    ),
                    "resolution_kind":
                        h_value.get(
                            "resolution_kind"
                        ),
                    "source_fingerprint":
                        h_value.get(
                            "source_fingerprint"
                        ),
                    "source_evidence":
                        h_value.get("evidence"),
                }

                unresolved=False
                h_resolution_used=True

        current=current_value(
            db,
            a["account_id"],
            a["field"],
        )

        execution_fp=fp({
            "account_id":a["account_id"],
            "field":a["field"],
            "target":a["candidate_value"],
            "source":a["bootstrap_fingerprint"],
            "h_resolution":(
                (a.get("evidence") or {}).get(
                    "source_fingerprint"
                )
                if h_resolution_used
                else None
            ),
        })

        result.append({
            **a,
            "target_key":FIELD_TARGET[a["field"]],
            "current_value":current,
            "target_value":a["candidate_value"],
            "execution_fingerprint":execution_fp,
            "unresolved":unresolved,
            "needs_write":(
                False
                if unresolved
                else current != a["candidate_value"]
            ),
        })

    return result


def execute(db):
    ensure_schema(db)

    p=plan(db)

    changed=0
    unchanged=0
    events=0

    for item in p:

        existing=db.execute(text("""
            SELECT execution_fingerprint,status
            FROM factory_execution_state
            WHERE account_id=:a
              AND field=:f
        """),{
            "a":item["account_id"],
            "f":item["field"],
        }).fetchone()

        if item.get("unresolved"):
            if (
                existing
                and existing[0] == item["execution_fingerprint"]
                and existing[1] == "SKIPPED_UNRESOLVED"
            ):
                unchanged += 1
                continue

            db.execute(text("""
                INSERT INTO factory_execution_state (
                    account_id,
                    field,
                    source_bootstrap_fingerprint,
                    target_key,
                    target_value,
                    status,
                    execution_fingerprint,
                    created_at,
                    updated_at
                )
                VALUES (
                    :a,
                    :f,
                    :s,
                    :k,
                    NULL,
                    'SKIPPED_UNRESOLVED',
                    :e,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (account_id,field)
                DO UPDATE SET
                    source_bootstrap_fingerprint=
                        EXCLUDED.source_bootstrap_fingerprint,
                    target_key=EXCLUDED.target_key,
                    target_value=NULL,
                    status='SKIPPED_UNRESOLVED',
                    execution_fingerprint=
                        EXCLUDED.execution_fingerprint,
                    updated_at=NOW()
            """),{
                "a":item["account_id"],
                "f":item["field"],
                "s":item["bootstrap_fingerprint"],
                "k":item["target_key"],
                "e":item["execution_fingerprint"],
            })

            db.execute(text("""
                INSERT INTO factory_execution_events (
                    account_id,
                    field,
                    event,
                    before_value,
                    after_value,
                    source_bootstrap_fingerprint,
                    execution_fingerprint,
                    created_at
                )
                VALUES (
                    :a,
                    :f,
                    'SKIPPED_UNRESOLVED',
                    CAST(:b AS JSONB),
                    NULL,
                    :s,
                    :e,
                    NOW()
                )
            """),{
                "a":item["account_id"],
                "f":item["field"],
                "b":json.dumps(
                    item["current_value"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "s":item["bootstrap_fingerprint"],
                "e":item["execution_fingerprint"],
            })

            events += 1
            continue

        if (
            existing
            and existing[0] == item["execution_fingerprint"]
            and not item["needs_write"]
        ):
            unchanged += 1
            continue

        before=item["current_value"]
        after=item["target_value"]

        row=db.execute(text("""
            SELECT id
            FROM storage
            WHERE account_id=:a
              AND key=:k
            ORDER BY id DESC
            LIMIT 1
        """),{
            "a":item["account_id"],
            "k":item["target_key"],
        }).fetchone()

        encoded=json.dumps(
            after,
            ensure_ascii=False,
            sort_keys=True,
        )

        if row:
            db.execute(text("""
                UPDATE storage
                SET value=:v
                WHERE id=:id
            """),{
                "v":encoded,
                "id":row[0],
            })
        else:
            db.execute(text("""
                INSERT INTO storage (
                    account_id,
                    key,
                    value
                )
                VALUES (
                    :a,
                    :k,
                    :v
                )
            """),{
                "a":item["account_id"],
                "k":item["target_key"],
                "v":encoded,
            })

        db.execute(text("""
            INSERT INTO factory_execution_state (
                account_id,
                field,
                source_bootstrap_fingerprint,
                target_key,
                target_value,
                status,
                execution_fingerprint,
                created_at,
                updated_at
            )
            VALUES (
                :a,
                :f,
                :s,
                :k,
                CAST(:v AS JSONB),
                'APPLIED',
                :e,
                NOW(),
                NOW()
            )
            ON CONFLICT (account_id,field)
            DO UPDATE SET
                source_bootstrap_fingerprint=
                    EXCLUDED.source_bootstrap_fingerprint,
                target_key=EXCLUDED.target_key,
                target_value=EXCLUDED.target_value,
                status='APPLIED',
                execution_fingerprint=
                    EXCLUDED.execution_fingerprint,
                updated_at=NOW()
        """),{
            "a":item["account_id"],
            "f":item["field"],
            "s":item["bootstrap_fingerprint"],
            "k":item["target_key"],
            "v":json.dumps(
                after,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "e":item["execution_fingerprint"],
        })

        db.execute(text("""
            INSERT INTO factory_execution_events (
                account_id,
                field,
                event,
                before_value,
                after_value,
                source_bootstrap_fingerprint,
                execution_fingerprint,
                created_at
            )
            VALUES (
                :a,
                :f,
                'APPLIED',
                CAST(:b AS JSONB),
                CAST(:v AS JSONB),
                :s,
                :e,
                NOW()
            )
        """),{
            "a":item["account_id"],
            "f":item["field"],
            "b":json.dumps(
                before,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "v":json.dumps(
                after,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "s":item["bootstrap_fingerprint"],
            "e":item["execution_fingerprint"],
        })

        changed += 1
        events += 1

    skipped=sum(
        1 for x in p
        if x.get("unresolved")
    )

    return {
        "items":p,
        "changed":changed,
        "unchanged":unchanged,
        "skipped_unresolved":skipped,
        "events":events,
    }
