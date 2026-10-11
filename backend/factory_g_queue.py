"""BORIS Factory G setup queue — FACTORY_G_QUEUE_V1.

Consumes lifecycle evidence and writes only:

    factory_setup_queue
    factory_orphan_registry

No owner assignment.
No product grant.
No deletion.
No business command.
"""

import hashlib
import json

from sqlalchemy import text

from app.db.session import SessionLocal


def fp(obj):
    raw=json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",",":"),
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def priority_for(row):
    status=row["provisioning_status"]
    action=row["resolution"]["action"]

    if status=="BLOCKED_OWNER":
        return 100

    if action=="CONFIGURE_PRODUCT":
        return 90

    return 50


def issue_type(row):
    if row["provisioning_status"]=="BLOCKED_OWNER":
        return "OWNER_MISSING"

    if row["provisioning_status"]=="BLOCKED_PRODUCTS":
        return "PRODUCT_MISSING"

    return "UNKNOWN"


def setup_payload(row):
    resolution=row["resolution"]

    payload={
        "account_id":
            row["account_id"],

        "issue_type":
            issue_type(row),

        "action_required":
            resolution["action"],

        "lifecycle":
            resolution["lifecycle"],

        "priority":
            priority_for(row),

        "reason":
            resolution["reason"],

        "evidence":{
            "account":
                row["account"],

            "activity":
                row["activity"],

            "provisioning_status":
                row["provisioning_status"],
        },
    }

    payload["source_fingerprint"]=fp(
        payload
    )

    return payload


def orphan_payload(row):
    payload={
        "account_id":
            row["account_id"],

        "lifecycle":
            row["resolution"]["lifecycle"],

        "reason":
            row["resolution"]["reason"],

        "activity":
            row["activity"],
    }

    payload["source_fingerprint"]=fp(
        payload
    )

    return payload


def load_lifecycle():
    return json.load(
        open(
            "/tmp/factory_g_lifecycle.json",
            encoding="utf-8",
        )
    )


def main():
    rows=load_lifecycle()

    setup=[
        setup_payload(x)
        for x in rows
        if x["resolution"]["lifecycle"]
        =="REAL_NEEDS_SETUP"
    ]

    orphans=[
        orphan_payload(x)
        for x in rows
        if x["resolution"]["lifecycle"]
        in (
            "ACTIVE_ORPHAN",
            "DORMANT_ORPHAN",
        )
    ]

    print("=== FACTORY G SETUP QUEUE ===")
    print("setup=",len(setup))
    print("orphans=",len(orphans))

    print("\n--- SETUP ---")

    for x in sorted(
        setup,
        key=lambda r:(-r["priority"],r["account_id"])
    ):
        print(
            "%-38s %-18s priority=%d action=%s"
            % (
                x["account_id"][:38],
                x["issue_type"],
                x["priority"],
                x["action_required"],
            )
        )

    print("\n--- ORPHANS ---")

    for x in orphans:
        print(
            "%-38s %-18s"
            % (
                x["account_id"][:38],
                x["lifecycle"],
            )
        )

    db=SessionLocal()

    try:
        for x in setup:
            db.execute(text("""
                INSERT INTO factory_setup_queue (
                    account_id,
                    issue_type,
                    action_required,
                    lifecycle,
                    priority,
                    status,
                    reason,
                    evidence,
                    source_fingerprint,
                    created_at,
                    updated_at
                )
                VALUES (
                    :account_id,
                    :issue_type,
                    :action_required,
                    :lifecycle,
                    :priority,
                    'OPEN',
                    :reason,
                    CAST(:evidence AS jsonb),
                    :source_fingerprint,
                    now(),
                    now()
                )
                ON CONFLICT(account_id)
                DO UPDATE SET
                    issue_type=
                        EXCLUDED.issue_type,
                    action_required=
                        EXCLUDED.action_required,
                    lifecycle=
                        EXCLUDED.lifecycle,
                    priority=
                        EXCLUDED.priority,
                    reason=
                        EXCLUDED.reason,
                    evidence=
                        EXCLUDED.evidence,
                    source_fingerprint=
                        EXCLUDED.source_fingerprint,
                    status=
                        CASE
                        WHEN factory_setup_queue.status='RESOLVED'
                             AND factory_setup_queue.source_fingerprint
                                 IS DISTINCT FROM EXCLUDED.source_fingerprint
                        THEN 'OPEN'
                        ELSE factory_setup_queue.status
                        END,
                    resolved_at=
                        CASE
                        WHEN factory_setup_queue.status='RESOLVED'
                             AND factory_setup_queue.source_fingerprint
                                 IS DISTINCT FROM EXCLUDED.source_fingerprint
                        THEN NULL
                        ELSE factory_setup_queue.resolved_at
                        END,
                    updated_at=
                        CASE
                        WHEN factory_setup_queue.source_fingerprint
                             IS DISTINCT FROM EXCLUDED.source_fingerprint
                        THEN now()
                        ELSE factory_setup_queue.updated_at
                        END
            """),{
                **x,
                "evidence":
                    json.dumps(
                        x["evidence"],
                        ensure_ascii=False,
                        default=str,
                    ),
            })

        setup_ids={
            x["account_id"]
            for x in setup
        }

        # If an old OPEN issue is no longer REAL_NEEDS_SETUP,
        # close the queue item; do not delete history.
        db.execute(text("""
            UPDATE factory_setup_queue q
            SET
                status='RESOLVED',
                resolved_at=COALESCE(
                    resolved_at,
                    now()
                ),
                updated_at=now()
            WHERE status='OPEN'
              AND NOT (
                  account_id = ANY(
                      CAST(:ids AS varchar[])
                  )
              )
        """),{
            "ids":list(setup_ids)
            if setup_ids
            else ["__none__"],
        })

        for x in orphans:
            db.execute(text("""
                INSERT INTO factory_orphan_registry (
                    account_id,
                    lifecycle,
                    reason,
                    activity,
                    source_fingerprint,
                    created_at,
                    updated_at
                )
                VALUES (
                    :account_id,
                    :lifecycle,
                    :reason,
                    CAST(:activity AS jsonb),
                    :source_fingerprint,
                    now(),
                    now()
                )
                ON CONFLICT(account_id)
                DO UPDATE SET
                    lifecycle=
                        EXCLUDED.lifecycle,
                    reason=
                        EXCLUDED.reason,
                    activity=
                        EXCLUDED.activity,
                    source_fingerprint=
                        EXCLUDED.source_fingerprint,
                    updated_at=
                        CASE
                        WHEN factory_orphan_registry.source_fingerprint
                             IS DISTINCT FROM EXCLUDED.source_fingerprint
                        THEN now()
                        ELSE factory_orphan_registry.updated_at
                        END
            """),{
                **x,
                "activity":
                    json.dumps(
                        x["activity"],
                        ensure_ascii=False,
                        default=str,
                    ),
            })

        db.commit()

        print("\nAPPLY=PASS")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    main()
