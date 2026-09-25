"""FACTORY_H_V1 — reconcile materialized derived state.

Purpose:
A derived value is not permanent merely because it was once valid.

Keep a row only if the CURRENT E/F cycle still produces the same
(account_id, field) as READY.

Otherwise:
- write INVALIDATE_STALE event
- remove only factory_derived_state row

Never touches:
- Client Config
- accounts
- storage
- money
- bids
- messages
- external APIs
"""

import json

from sqlalchemy import text

from app.factory import derive_executor as DX


def canonical(v):
    return json.dumps(
        v,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )


def ready_keys(packets):
    """Return current valid materialization keys from E packets."""

    keys={}

    for packet in packets:
        for decision in packet.get("auto_allowed") or []:
            preview=DX.preview(
                packet,
                decision,
            )

            if preview.get("result") != "READY":
                continue

            key=(
                preview["account_id"],
                preview["field"],
            )

            keys[key]=preview

    return keys


def reconcile(db, packets, dry_run=False):
    allowed=ready_keys(packets)

    rows=db.execute(text("""
        SELECT
            id,
            account_id,
            field,
            operation,
            value,
            source_fingerprint
        FROM factory_derived_state
        ORDER BY account_id,field
    """)).mappings().all()

    invalid=[]

    for row in rows:
        key=(
            row["account_id"],
            row["field"],
        )

        current=allowed.get(key)

        # If E/F no longer considers this field READY,
        # stale runtime state must disappear.
        if current is None:
            invalid.append({
                "row":dict(row),
                "reason":"no_longer_ready",
            })
            continue

        # Same key remains valid. F itself handles changed values/fingerprints.
        # Reconcile therefore does not delete the freshly updated row.

    if dry_run:
        return {
            "kept":len(rows)-len(invalid),
            "invalidated":len(invalid),
            "details":[
                {
                    "account_id":x["row"]["account_id"],
                    "field":x["row"]["field"],
                    "reason":x["reason"],
                }
                for x in invalid
            ],
        }

    for item in invalid:
        row=item["row"]

        db.execute(text("""
            INSERT INTO factory_executor_events (
                account_id,
                field,
                operation,
                event_type,
                before_value,
                after_value,
                source_fingerprint,
                created_at
            )
            VALUES (
                :a,
                :f,
                :o,
                'INVALIDATE_STALE',
                CAST(:before AS jsonb),
                NULL,
                :fp,
                now()
            )
        """), {
            "a":row["account_id"],
            "f":row["field"],
            "o":row["operation"],
            "before":canonical(row["value"]),
            "fp":row["source_fingerprint"],
        })

        db.execute(text("""
            DELETE FROM factory_derived_state
            WHERE id=:id
        """), {
            "id":row["id"],
        })

    return {
        "kept":len(rows)-len(invalid),
        "invalidated":len(invalid),
        "details":[
            {
                "account_id":x["row"]["account_id"],
                "field":x["row"]["field"],
                "reason":x["reason"],
            }
            for x in invalid
        ],
    }
