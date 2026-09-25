"""ZERO_TOUCH_LAUNCH_V1.

Final product-level account launch state over BORIS Factory.

This module is conservative by design.

It MAY write only:
    factory_client_launch
    factory_client_launch_events

It NEVER:
    approves confirmation
    edits client_config
    edits accounts
    edits factory_schedule
    edits crontab
    spends money
    changes bids
    sends messages
    publishes ads
    calls external APIs
"""

import hashlib
import json
from pathlib import Path

from sqlalchemy import text


SCHEDULE_FIELD_JOB={
    "schedule.autopilot":"autopilot",
    "schedule.mop_poll":"mop_poll",
}


FINAL_READY_STATUSES={
    "LAUNCHED",
    "READY_NO_AUTOMATION",
}


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",",":"),
    )


def hash_payload(payload):
    return hashlib.sha256(
        canonical(payload).encode("utf-8")
    ).hexdigest()


def load_e_packets():
    path=Path(
        "/root/BORIS/backend/run/factory_e_packets.json"
    )

    if not path.exists():
        return {}

    try:
        rows=json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}

    return {
        x.get("account_id"):x
        for x in rows
        if x.get("account_id")
    }


def table_exists(db,table):
    return bool(
        db.execute(text("""
            SELECT EXISTS(
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public'
                  AND table_name=:t
            )
        """), {
            "t":table,
        }).scalar()
    )


def current_schedule_requirements(e_packet):
    """Determine current required schedule fields from CURRENT E evidence.

    We do NOT invent a product requirement.

    A schedule is considered required by the launch state only if the
    current E packet contains a decision for that schedule field.

    Rejected confirmation means the human explicitly declined that optional
    migration, so it does not block launch.
    """

    if not e_packet:
        return []

    found=[]

    decisions=e_packet.get("decisions") or []

    if not decisions:
        # Compatibility with packet buckets.
        for bucket in (
            "auto_allowed",
            "confirm_required",
            "mandate_required",
            "blocked",
        ):
            decisions.extend(
                e_packet.get(bucket) or []
            )

    seen=set()

    for decision in decisions:
        field=decision.get("field")

        if field not in SCHEDULE_FIELD_JOB:
            continue

        if field in seen:
            continue

        seen.add(field)

        found.append({
            "field":field,
            "job_kind":
                SCHEDULE_FIELD_JOB[field],

            "policy":
                decision.get("policy"),

            "operation":
                decision.get("operation"),
        })

    return found


def confirmation_state(db,account_id):
    rows=db.execute(text("""
        SELECT
            id,
            field,
            operation,
            status,
            fingerprint,
            payload
        FROM factory_confirmations
        WHERE account_id=:a
        ORDER BY id
    """), {
        "a":account_id,
    }).mappings().all()

    latest={}

    for row in rows:
        key=(
            row["field"],
            row["operation"],
        )

        latest[key]=dict(row)

    counts={
        "pending":0,
        "approved":0,
        "rejected":0,
        "stale":0,
    }

    for row in latest.values():
        status=row["status"]

        if status in counts:
            counts[status]+=1

    return latest,counts


def active_schedule_state(db,account_id):
    rows=db.execute(text("""
        SELECT
            id,
            job_kind,
            cron_expr,
            command_template,
            source,
            source_fingerprint,
            enabled
        FROM factory_schedule
        WHERE account_id=:a
        ORDER BY job_kind,id
    """), {
        "a":account_id,
    }).mappings().all()

    result={}

    for row in rows:
        if row["enabled"]:
            result[row["job_kind"]]=dict(row)

    return result


def derived_state(db,account_id):
    rows=db.execute(text("""
        SELECT
            field,
            operation,
            value,
            evidence,
            source_fingerprint
        FROM factory_derived_state
        WHERE account_id=:a
        ORDER BY field
    """), {
        "a":account_id,
    }).mappings().all()

    return [
        dict(x)
        for x in rows
    ]


def knowledge_state(db,account_id):
    """Useful health information only.

    Knowledge is NOT a hard blocker here because niches/products may not
    require client_facts at launch and current architecture can use other
    knowledge sources.

    Never guess if client_facts table does not exist.
    """

    if not table_exists(
        db,
        "client_facts",
    ):
        return {
            "available":False,
            "count":None,
        }

    count=db.execute(text("""
        SELECT count(*)
        FROM client_facts
        WHERE account_id=:a
          AND (
              status IS NULL
              OR status NOT IN (
                  'deleted',
                  'superseded'
              )
          )
    """), {
        "a":account_id,
    }).scalar()

    return {
        "available":True,
        "count":int(count or 0),
    }


def build_account_health(
    db,
    account_row,
    factory_row,
    e_packet,
):
    account_id=account_row["account_id"]
    owner_user_id=account_row["owner_user_id"]

    blockers=[]

    # -----------------------------------------------------------------------
    # 1. Base Factory / owner.
    # -----------------------------------------------------------------------

    if owner_user_id is None:
        blockers.append({
            "code":"NO_OWNER",
            "title":"Не назначен владелец",
            "action":"Назначить владельца аккаунта",
            "human_required":True,
        })

    if factory_row is None:
        blockers.append({
            "code":"FACTORY_STATE_MISSING",
            "title":"Factory ещё не собрал состояние",
            "action":"Дождаться полного Factory cycle",
            "human_required":False,
        })

        eligible=False
        readiness_pct=0
        launch_status="BLOCKED_FACTORY"

    else:
        eligible=bool(
            factory_row[
                "eligible_zero_touch"
            ]
        )

        readiness_pct=int(
            factory_row[
                "readiness_pct"
            ] or 0
        )

        factory_status=(
            factory_row[
                "launch_status"
            ]
        )

        if factory_status=="BLOCKED_NO_OWNER":
            if not any(
                x["code"]=="NO_OWNER"
                for x in blockers
            ):
                blockers.append({
                    "code":"NO_OWNER",
                    "title":"Не назначен владелец",
                    "action":"Назначить владельца аккаунта",
                    "human_required":True,
                })

        elif factory_status=="BLOCKED_CORE":
            blockers.append({
                "code":"CORE_BLOCKED",
                "title":"Не выполнен обязательный системный контракт",
                "action":"Проверить канал / доступ / обязательные данные",
                "human_required":True,
            })

        elif factory_status=="NO_PRODUCTS":
            blockers.append({
                "code":"NO_PRODUCTS",
                "title":"Нет подключённого продукта",
                "action":"Подключить продукт BORIS для аккаунта",
                "human_required":True,
            })

        launch_status=None

    # -----------------------------------------------------------------------
    # 2. Confirmation state.
    # -----------------------------------------------------------------------

    confirmations,confirmation_counts=confirmation_state(
        db,
        account_id,
    )

    # -----------------------------------------------------------------------
    # 3. Current E schedule requirements.
    # -----------------------------------------------------------------------

    requirements=current_schedule_requirements(
        e_packet
    )

    schedules=active_schedule_state(
        db,
        account_id,
    )

    schedule_health=[]

    required_active=0
    pending_schedule_decisions=0

    for req in requirements:
        field=req["field"]
        job_kind=req["job_kind"]

        current=schedules.get(
            job_kind
        )

        confirm=confirmations.get(
            (
                field,
                "migrate.schedule",
            )
        )

        decision_status=(
            confirm["status"]
            if confirm
            else None
        )

        if current:
            required_active+=1

            state="ACTIVE"

        elif decision_status=="pending":
            state="NEEDS_DECISION"

            pending_schedule_decisions+=1

            blockers.append({
                "code":
                    "SCHEDULE_CONFIRMATION_REQUIRED",

                "field":
                    field,

                "title":
                    "Нужно подтвердить автоматизацию",

                "action":
                    "Открыть «Требуют решения»",

                "human_required":
                    True,
            })

        elif decision_status=="approved":
            state="APPROVED_NOT_APPLIED"

            blockers.append({
                "code":
                    "APPROVED_SCHEDULE_NOT_APPLIED",

                "field":
                    field,

                "title":
                    "Решение подтверждено, но расписание ещё не применено",

                "action":
                    "Factory применит решение в ближайшем cycle",

                "human_required":
                    False,
            })

        elif decision_status=="rejected":
            # Explicit human rejection => do not block base account launch.
            state="REJECTED_BY_OWNER"

        else:
            state="SCHEDULE_MISSING"

            blockers.append({
                "code":
                    "SCHEDULE_MISSING",

                "field":
                    field,

                "title":
                    "Автоматизация ожидается, но расписание не доказано",

                "action":
                    "Проверить Factory E/J/K policy",

                "human_required":
                    False,
            })

        schedule_health.append({
            **req,
            "state":state,
            "confirmation_status":
                decision_status,
            "active":
                bool(current),
            "cron_expr":
                current.get("cron_expr")
                if current
                else None,
            "source":
                current.get("source")
                if current
                else None,
        })

    # -----------------------------------------------------------------------
    # 4. Derived + knowledge.
    # -----------------------------------------------------------------------

    derived=derived_state(
        db,
        account_id,
    )

    knowledge=knowledge_state(
        db,
        account_id,
    )

    # -----------------------------------------------------------------------
    # 5. Final state machine.
    # -----------------------------------------------------------------------

    if owner_user_id is None:
        launch_status="BLOCKED_NO_OWNER"

    elif factory_row is None:
        launch_status="BLOCKED_FACTORY"

    elif factory_row["launch_status"]=="BLOCKED_CORE":
        launch_status="BLOCKED_CORE"

    elif factory_row["launch_status"]=="NO_PRODUCTS":
        launch_status="NO_PRODUCTS"

    elif pending_schedule_decisions > 0:
        launch_status="NEEDS_DECISION"

    elif any(
        x["code"]=="APPROVED_SCHEDULE_NOT_APPLIED"
        for x in blockers
    ):
        launch_status="APPLYING"

    elif any(
        x["code"]=="SCHEDULE_MISSING"
        for x in blockers
    ):
        launch_status="BLOCKED_AUTOMATION"

    elif eligible:
        # If current E expects no schedule, launch can still be valid:
        # account may have a product whose runtime requires no scheduler.
        launch_status="LAUNCHED"

    else:
        launch_status="NOT_ZERO_TOUCH_READY"

    launch_ready=(
        launch_status in FINAL_READY_STATUSES
    )

    health={
        "account":{
            "account_id":account_id,
            "owner_user_id":
                owner_user_id,
        },

        "factory":{
            "status":
                factory_row["launch_status"]
                if factory_row
                else None,

            "eligible_zero_touch":
                eligible,

            "readiness_pct":
                readiness_pct,
        },

        "onboarding":{
            "present":
                bool(
                    factory_row
                    and (
                        factory_row["packet"]
                        or {}
                    ).get("onboarding")
                ),
        },

        "derived":{
            "count":
                len(derived),

            "fields":[
                x["field"]
                for x in derived
            ],
        },

        "confirmations":{
            **confirmation_counts,
        },

        "automation":{
            "required":
                len(requirements),

            "active":
                required_active,

            "items":
                schedule_health,
        },

        "knowledge":
            knowledge,

        "launch":{
            "status":
                launch_status,

            "ready":
                launch_ready,
        },
    }

    payload={
        "account_id":
            account_id,

        "owner_user_id":
            owner_user_id,

        "launch_status":
            launch_status,

        "launch_ready":
            launch_ready,

        "readiness_pct":
            readiness_pct,

        "pending_decisions":
            confirmation_counts[
                "pending"
            ],

        "approved_decisions":
            confirmation_counts[
                "approved"
            ],

        "rejected_decisions":
            confirmation_counts[
                "rejected"
            ],

        "required_schedules":
            len(requirements),

        "active_schedules":
            required_active,

        "derived_values":
            len(derived),

        "health":
            health,

        "blockers":
            blockers,
    }

    payload[
        "source_fingerprint"
    ]=hash_payload(
        payload
    )

    return payload


def build_all(db):
    e_packets=load_e_packets()

    accounts=db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            company_niche,
            client_goal,
            billing_mode
        FROM accounts a
        WHERE NOT EXISTS (
            SELECT 1 FROM reliability_kill_switches r
             WHERE r.account_id=a.account_id AND r.blocked=true
               AND r.module IN ('actions','background')
        )
        ORDER BY account_id
    """)).mappings().all()

    factory={
        x["account_id"]:dict(x)
        for x in db.execute(text("""
            SELECT *
            FROM factory_launch_state
            ORDER BY account_id
        """)).mappings().all()
    }

    result=[]

    for account in accounts:
        account_id=account[
            "account_id"
        ]

        result.append(
            build_account_health(
                db,
                dict(account),
                factory.get(
                    account_id
                ),
                e_packets.get(
                    account_id
                ),
            )
        )

    return result


def _event(
    db,
    account_id,
    event_type,
    before_status,
    after_status,
    before_value,
    after_value,
    fingerprint,
):
    db.execute(text("""
        INSERT INTO factory_client_launch_events (
            account_id,
            event_type,
            before_status,
            after_status,
            before_value,
            after_value,
            source_fingerprint,
            created_at
        )
        VALUES (
            :a,
            :event,
            :before_status,
            :after_status,
            CAST(:before_value AS jsonb),
            CAST(:after_value AS jsonb),
            :fp,
            now()
        )
    """), {
        "a":account_id,
        "event":event_type,
        "before_status":
            before_status,
        "after_status":
            after_status,
        "before_value":
            canonical(before_value)
            if before_value is not None
            else None,
        "after_value":
            canonical(after_value)
            if after_value is not None
            else None,
        "fp":fingerprint,
    })


def apply_all(db,rows):
    changed=0
    unchanged=0
    created=0

    for row in rows:
        existing=db.execute(text("""
            SELECT *
            FROM factory_client_launch
            WHERE account_id=:a
            FOR UPDATE
        """), {
            "a":row["account_id"],
        }).mappings().first()

        if (
            existing
            and existing[
                "source_fingerprint"
            ]==row[
                "source_fingerprint"
            ]
        ):
            unchanged+=1
            continue

        if existing:
            before={
                "launch_status":
                    existing[
                        "launch_status"
                    ],

                "launch_ready":
                    existing[
                        "launch_ready"
                    ],

                "readiness_pct":
                    existing[
                        "readiness_pct"
                    ],

                "health":
                    existing[
                        "health"
                    ],

                "blockers":
                    existing[
                        "blockers"
                    ],
            }

            event_type=(
                "STATUS_CHANGE"
                if existing[
                    "launch_status"
                ] != row[
                    "launch_status"
                ]
                else "HEALTH_CHANGE"
            )

            db.execute(text("""
                UPDATE factory_client_launch
                SET
                    owner_user_id=:owner,
                    launch_status=:status,
                    launch_ready=:ready,
                    readiness_pct=:readiness,
                    pending_decisions=:pending,
                    approved_decisions=:approved,
                    rejected_decisions=:rejected,
                    required_schedules=:required,
                    active_schedules=:active,
                    derived_values=:derived,
                    health=CAST(:health AS jsonb),
                    blockers=CAST(:blockers AS jsonb),
                    source_fingerprint=:fp,
                    updated_at=now(),
                    launched_at=
                        CASE
                            WHEN :ready
                                 AND launched_at IS NULL
                            THEN now()
                            ELSE launched_at
                        END
                WHERE account_id=:a
            """), {
                "a":
                    row["account_id"],

                "owner":
                    row["owner_user_id"],

                "status":
                    row["launch_status"],

                "ready":
                    row["launch_ready"],

                "readiness":
                    row["readiness_pct"],

                "pending":
                    row["pending_decisions"],

                "approved":
                    row["approved_decisions"],

                "rejected":
                    row["rejected_decisions"],

                "required":
                    row["required_schedules"],

                "active":
                    row["active_schedules"],

                "derived":
                    row["derived_values"],

                "health":
                    canonical(
                        row["health"]
                    ),

                "blockers":
                    canonical(
                        row["blockers"]
                    ),

                "fp":
                    row[
                        "source_fingerprint"
                    ],
            })

            _event(
                db,
                row["account_id"],
                event_type,
                existing[
                    "launch_status"
                ],
                row[
                    "launch_status"
                ],
                before,
                row,
                row[
                    "source_fingerprint"
                ],
            )

            changed+=1

        else:
            db.execute(text("""
                INSERT INTO factory_client_launch (
                    account_id,
                    owner_user_id,
                    launch_status,
                    launch_ready,
                    readiness_pct,
                    pending_decisions,
                    approved_decisions,
                    rejected_decisions,
                    required_schedules,
                    active_schedules,
                    derived_values,
                    health,
                    blockers,
                    source_fingerprint,
                    created_at,
                    updated_at,
                    launched_at
                )
                VALUES (
                    :a,
                    :owner,
                    :status,
                    :ready,
                    :readiness,
                    :pending,
                    :approved,
                    :rejected,
                    :required,
                    :active,
                    :derived,
                    CAST(:health AS jsonb),
                    CAST(:blockers AS jsonb),
                    :fp,
                    now(),
                    now(),
                    CASE
                        WHEN :ready
                        THEN now()
                        ELSE NULL
                    END
                )
            """), {
                "a":
                    row["account_id"],

                "owner":
                    row["owner_user_id"],

                "status":
                    row["launch_status"],

                "ready":
                    row["launch_ready"],

                "readiness":
                    row["readiness_pct"],

                "pending":
                    row["pending_decisions"],

                "approved":
                    row["approved_decisions"],

                "rejected":
                    row["rejected_decisions"],

                "required":
                    row["required_schedules"],

                "active":
                    row["active_schedules"],

                "derived":
                    row["derived_values"],

                "health":
                    canonical(
                        row["health"]
                    ),

                "blockers":
                    canonical(
                        row["blockers"]
                    ),

                "fp":
                    row[
                        "source_fingerprint"
                    ],
            })

            _event(
                db,
                row["account_id"],
                "CREATE",
                None,
                row[
                    "launch_status"
                ],
                None,
                row,
                row[
                    "source_fingerprint"
                ],
            )

            created+=1

    return {
        "created":created,
        "changed":changed,
        "unchanged":unchanged,
        "total":len(rows),
    }


def summary(rows):
    statuses={}

    ready=0
    human_blocked=0
    system_blocked=0

    for row in rows:
        status=row[
            "launch_status"
        ]

        statuses[
            status
        ]=statuses.get(
            status,
            0
        )+1

        if row[
            "launch_ready"
        ]:
            ready+=1

        if any(
            x.get(
                "human_required"
            )
            for x in row[
                "blockers"
            ]
        ):
            human_blocked+=1

        if any(
            not x.get(
                "human_required"
            )
            for x in row[
                "blockers"
            ]
        ):
            system_blocked+=1

    return {
        "accounts":len(rows),
        "ready":ready,
        "human_blocked":
            human_blocked,
        "system_blocked":
            system_blocked,
        "statuses":
            statuses,
    }
