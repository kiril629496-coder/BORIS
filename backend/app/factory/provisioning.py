"""BORIS Factory provisioning core — FACTORY_G_V1.

Stage 1 scope:
    READ:
        factory_launch_state
        factory_resolved_values
        factory_schedule

    WRITE:
        factory_provisioning_state
        factory_provisioning_events

Absolutely no:
    client_config writes
    accounts writes
    storage writes
    billing writes
    money mandate writes
    external API calls
    business command execution
    cron mutation
"""

import hashlib
import json

from sqlalchemy import text
# BORIS_FACTORY_N21_1_ENTITLEMENT_HELPER
#
# catalog_products is intentionally NOT consulted here.
#
# Only typed/known BORIS entitlements can become products.
# Unknown => False.
#
# This helper is read-only.

def _n21_1_entitled_products(db, account_id):
    products = {
        "mop": False,
        "rop": False,
        "cpx": False,
    }

    try:
        from app.client_config.entitlements import has_entitlement
    except Exception:
        return products

    for product in ("mop", "rop"):
        try:
            result = has_entitlement(
                db,
                account_id,
                product,
            )

            if (
                isinstance(result, dict)
                and result.get("allowed") is True
                and result.get("state") == "known"
            ):
                products[product] = True

        except Exception:
            # Fail closed.
            products[product] = False

    return products


def _n21_1_product_count(products):
    return sum(
        1
        for key in ("mop", "rop", "cpx")
        if products.get(key) is True
    )


def stable_hash(obj):
    raw=json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",",":"),
    ).encode("utf-8")

    return hashlib.sha256(
        raw
    ).hexdigest()


def launch_rows(db):
    return db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            launch_status,
            product_count,
            readiness_pct,
            human_questions,
            boris_actions,
            platform_actions,
            core_blocked,
            eligible_zero_touch,
            packet,
            source_fingerprint
        FROM factory_launch_state
        ORDER BY account_id
    """)).mappings().all()


def resolved_count(
    db,
    account_id,
):
    return int(
        db.execute(text("""
            SELECT count(*)
            FROM factory_resolved_values
            WHERE account_id=:a
        """),{
            "a":account_id,
        }).scalar()
        or 0
    )


def schedule_count(
    db,
    account_id,
):
    return int(
        db.execute(text("""
            SELECT count(*)
            FROM factory_schedule
            WHERE account_id=:a
              AND enabled
        """),{
            "a":account_id,
        }).scalar()
        or 0
    )


def packet_test_cohort(packet):
    packet=packet or {}

    return bool(
        packet.get(
            "looks_test_account"
        )
        or packet.get(
            "cohort"
        )=="QA_OR_TEST"
    )


def blocker_rows(
    packet,
    launch_status,
):
    packet=packet or {}

    out=[]

    for x in (
        packet.get(
            "core_blockers"
        )
        or []
    ):
        out.append({
            "kind":"CORE",
            "field":x.get("field"),
        })

    for x in (
        packet.get(
            "human_questions"
        )
        or []
    ):
        if x.get("severity")=="blocker":
            out.append({
                "kind":"CLIENT",
                "field":x.get("field"),
            })

    for x in (
        packet.get(
            "platform_actions"
        )
        or []
    ):
        if x.get("severity")=="blocker":
            out.append({
                "kind":"PLATFORM",
                "field":x.get("field"),
            })

    if launch_status=="BLOCKED_NO_OWNER":
        if not any(
            x["field"]=="owner_user_id"
            for x in out
        ):
            out.append({
                "kind":"CORE",
                "field":"owner_user_id",
            })

    if launch_status=="NO_PRODUCTS":
        out.append({
            "kind":"PRODUCT",
            "field":"products",
        })

    return out




# N21_4_ENTITLEMENT_PRODUCT_COUNT
#
# Product count for Factory eligibility is derived from the real
# entitlement contract. Unknown / absent entitlement is fail-closed.
#
# READ ONLY. No DB writes.

def _n21_4_entitled_product_count(db, account_id):
    from app.client_config.entitlements import has_entitlement

    count = 0

    for product in ("mop", "rop", "cpx", "feed"):
        result = has_entitlement(
            db,
            account_id,
            product,
        )

        if (
            isinstance(result, dict)
            and result.get("allowed") is True
            and result.get("state") == "known"
        ):
            count += 1

    return count

def build_plan(
    db,
    row,
):
    packet=row["packet"] or {}

    account_id=row["account_id"]
    # N21.4: entitlement is the product source of truth.
    entitlement_product_count = _n21_4_entitled_product_count(
        db,
        account_id,
    )
    # BORIS_K10_V4_LAUNCH_PRODUCT_COUNT
    #
    # The launch row has already passed Factory product reconciliation.
    # Provisioning must never project fewer confirmed products than
    # factory_launch_state already contains.
    launch_product_count = int(
        row["product_count"]
        or 0
    )
    
    entitlement_product_count = max(
        int(
            entitlement_product_count
            or 0
        ),
        launch_product_count,
    )

    resolved=resolved_count(
        db,
        account_id,
    )

    schedules=schedule_count(
        db,
        account_id,
    )

    blockers=blocker_rows(
        packet,
        row["launch_status"],
    )

    test=packet_test_cohort(
        packet
    )

    human=sum(
        1 for x in blockers
        if x["kind"]=="CLIENT"
    )

    platform=sum(
        1 for x in blockers
        if x["kind"]=="PLATFORM"
    )

    core=sum(
        1 for x in blockers
        if x["kind"]=="CORE"
    )

    base_eligible=bool(
        row["eligible_zero_touch"]
    )

    eligible=bool(
        base_eligible
        and not test
        and not human
        and not platform
        and not core
        and int(
            entitlement_product_count
            or 0
        ) > 0
    )

    if test:
        status="TEST_COHORT"

    elif row[
        "launch_status"
    ]=="BLOCKED_NO_OWNER":
        status="BLOCKED_OWNER"

    elif row[
        "launch_status"
    ]=="NO_PRODUCTS":
        status="BLOCKED_PRODUCTS"

    elif human:
        status="BLOCKED_CLIENT"

    elif platform or core:
        status="BLOCKED_PLATFORM"

    elif eligible:
        status="READY_INTERNAL"

    else:
        status="WAITING"

    ready_internal=(
        status=="READY_INTERNAL"
    )

    # Existing active schedule is proof only for the
    # account/job already migrated through E/F.
    # G Stage 1 never invents or activates schedules.
    ready_schedule=bool(
        ready_internal
        and schedules > 0
    )

    plan={
        "account_id":
            account_id,

        "launch_status":
            row["launch_status"],

        "provisioning_status":
            status,

        "eligible_zero_touch":
            eligible,

        "test_cohort":
            test,

        "product_count":
            int(
                entitlement_product_count
                or 0
            ),

        "readiness_pct":
            int(
                row["readiness_pct"]
                or 0
            ),

        "resolved_values":
            resolved,

        "active_schedules":
            schedules,

        "human_blockers":
            human,

        "platform_blockers":
            platform,

        "core_blockers":
            core,

        "ready_internal":
            ready_internal,

        "ready_schedule":
            ready_schedule,

        "blockers":
            blockers,

        "launch_fingerprint":
            row[
                "source_fingerprint"
            ],
    }

    plan[
        "source_fingerprint"
    ]=stable_hash(
        plan
    )

    return plan


def existing_state(
    db,
    account_id,
):
    row=db.execute(text("""
        SELECT
            provisioning_status,
            source_fingerprint
        FROM factory_provisioning_state
        WHERE account_id=:a
    """),{
        "a":account_id,
    }).mappings().first()

    return (
        dict(row)
        if row
        else None
    )


def upsert_state(
    db,
    plan,
):
    before=existing_state(
        db,
        plan["account_id"],
    )

    db.execute(text("""
        INSERT INTO factory_provisioning_state (
            account_id,
            launch_status,
            provisioning_status,
            eligible_zero_touch,
            test_cohort,
            product_count,
            resolved_values,
            active_schedules,
            human_blockers,
            platform_blockers,
            core_blockers,
            ready_internal,
            ready_schedule,
            blockers,
            plan,
            source_fingerprint,
            created_at,
            updated_at
        )
        VALUES (
            :account_id,
            :launch_status,
            :provisioning_status,
            :eligible_zero_touch,
            :test_cohort,
            :product_count,
            :resolved_values,
            :active_schedules,
            :human_blockers,
            :platform_blockers,
            :core_blockers,
            :ready_internal,
            :ready_schedule,
            CAST(:blockers AS jsonb),
            CAST(:plan AS jsonb),
            :source_fingerprint,
            now(),
            now()
        )
        ON CONFLICT(account_id)
        DO UPDATE SET
            launch_status=
                EXCLUDED.launch_status,
            provisioning_status=
                EXCLUDED.provisioning_status,
            eligible_zero_touch=
                EXCLUDED.eligible_zero_touch,
            test_cohort=
                EXCLUDED.test_cohort,
            product_count=
                EXCLUDED.product_count,
            resolved_values=
                EXCLUDED.resolved_values,
            active_schedules=
                EXCLUDED.active_schedules,
            human_blockers=
                EXCLUDED.human_blockers,
            platform_blockers=
                EXCLUDED.platform_blockers,
            core_blockers=
                EXCLUDED.core_blockers,
            ready_internal=
                EXCLUDED.ready_internal,
            ready_schedule=
                EXCLUDED.ready_schedule,
            blockers=
                EXCLUDED.blockers,
            plan=
                EXCLUDED.plan,
            source_fingerprint=
                EXCLUDED.source_fingerprint,
            updated_at=
                CASE
                WHEN factory_provisioning_state.source_fingerprint
                     IS DISTINCT FROM EXCLUDED.source_fingerprint
                THEN now()
                ELSE factory_provisioning_state.updated_at
                END
    """),{
        "account_id":
            plan["account_id"],

        "launch_status":
            plan["launch_status"],

        "provisioning_status":
            plan["provisioning_status"],

        "eligible_zero_touch":
            plan["eligible_zero_touch"],

        "test_cohort":
            plan["test_cohort"],

        "product_count":
            plan["product_count"],

        "resolved_values":
            plan["resolved_values"],

        "active_schedules":
            plan["active_schedules"],

        "human_blockers":
            plan["human_blockers"],

        "platform_blockers":
            plan["platform_blockers"],

        "core_blockers":
            plan["core_blockers"],

        "ready_internal":
            plan["ready_internal"],

        "ready_schedule":
            plan["ready_schedule"],

        "blockers":
            json.dumps(
                plan["blockers"],
                ensure_ascii=False,
            ),

        "plan":
            json.dumps(
                plan,
                ensure_ascii=False,
                default=str,
            ),

        "source_fingerprint":
            plan[
                "source_fingerprint"
            ],
    })

    after=plan[
        "provisioning_status"
    ]

    if before is None:
        event="CREATED"
        before_status=None

    elif before[
        "source_fingerprint"
    ] != plan[
        "source_fingerprint"
    ]:
        event="CHANGED"
        before_status=before[
            "provisioning_status"
        ]

    else:
        return "UNCHANGED"

    event_payload={
        "event":
            event,

        "before_status":
            before_status,

        "after_status":
            after,

        "source_fingerprint":
            plan[
                "source_fingerprint"
            ],
    }

    event_fp=stable_hash(
        event_payload
    )

    db.execute(text("""
        INSERT INTO factory_provisioning_events (
            account_id,
            event_type,
            before_status,
            after_status,
            fingerprint,
            details
        )
        VALUES (
            :account_id,
            :event_type,
            :before_status,
            :after_status,
            :fingerprint,
            CAST(:details AS jsonb)
        )
        ON CONFLICT(
            account_id,
            event_type,
            fingerprint
        )
        DO NOTHING
    """),{
        "account_id":
            plan["account_id"],

        "event_type":
            event,

        "before_status":
            before_status,

        "after_status":
            after,

        "fingerprint":
            event_fp,

        "details":
            json.dumps(
                event_payload,
                ensure_ascii=False,
            ),
    })

    return event
