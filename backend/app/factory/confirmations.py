"""FACTORY_J_V1 — durable human confirmation engine.

Supported lifecycle:
    pending
    approved
    rejected
    stale

This layer DOES NOT execute the approved operation.

Its job:
- create durable requests from E CONFIRM_REQUIRED decisions
- deduplicate by fingerprint
- preserve human decisions
- mark obsolete pending requests stale
- audit every state transition

No money.
No cron mutation.
No messages.
No external API.
"""

import hashlib
import json

from sqlalchemy import text


VALID_STATUS={
    "pending",
    "approved",
    "rejected",
    "stale",
}


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",",":"),
    )


def fingerprint(account_id,field,operation,payload):
    raw=canonical({
        "account_id":account_id,
        "field":field,
        "operation":operation,
        "payload":payload,
    }).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def _request_payload(db, packet, decision):
    """Return stable evidence for the human decision being confirmed.

    FACTORY_J_STABLE_SCHEDULE_FINGERPRINT_V1
    Schedule confirmation must not churn when unrelated readiness/onboarding
    counters change. Bind it to the actual schedule decision and active policy
    instead. A policy version/content change intentionally creates a new
    fingerprint and therefore requires a fresh confirmation.
    """
    operation=decision.get("operation")
    field=decision.get("field")

    if (
        operation=="migrate.schedule"
        and field in {"schedule.autopilot","schedule.mop_poll"}
    ):
        policy_evidence=None
        if db is not None:
            try:
                from app.factory import schedule_policy as SP
                row=SP.active_policy(db,field)
                if row:
                    policy_evidence={
                        k:row.get(k)
                        for k in (
                            "policy_key",
                            "version",
                            "field",
                            "job_kind",
                            "cadence",
                            "cron_expr",
                            "command_template",
                            "content_hash",
                        )
                    }
            except Exception:
                policy_evidence=None

        stable_decision={
            k:decision.get(k)
            for k in (
                "field",
                "operation",
                "base_policy",
                "policy",
                "risk",
                "money",
                "visible",
                "reversible",
                "source_action",
            )
        }

        return {
            "decision":stable_decision,
            "schedule_policy":policy_evidence,
        }

    return {
        "readiness_pct":packet.get("readiness_pct"),
        "launch_status":packet.get("launch_status"),
        "decision":decision,
        "onboarding":packet.get("onboarding"),
    }


def _schedule_migration_already_satisfied(
    db,
    account_id,
    field,
    operation,
):
    """True only for an exact ACTIVE central Factory schedule.

    No policy inference here.
    No cadence inference.
    No cron guessing.

    Existing enabled factory_schedule is factual proof that migration for
    this concrete job has already happened.
    """

    if db is None:
        return False

    if operation != "migrate.schedule":
        return False

    job_kind={
        "schedule.autopilot":"autopilot",
        "schedule.mop_poll":"mop_poll",
    }.get(field)

    if not job_kind:
        return False

    row=db.execute(text("""
        SELECT
            id,
            cron_expr,
            command_template,
            source,
            source_fingerprint,
            enabled
        FROM factory_schedule
        WHERE account_id=:a
          AND job_kind=:j
          AND enabled
        ORDER BY id DESC
        LIMIT 1
    """), {
        "a":account_id,
        "j":job_kind,
    }).mappings().first()

    return bool(row)


def _assignment_already_satisfied(db, account_id, field, operation):
    """Return True only when the requested internal assignment already exists.

    ZERO_TOUCH_SATISFIED_ASSIGNMENT_V1
    A derived fact must not create an owner confirmation for an operation whose
    material result is already present in canonical production tables.
    """
    if db is None:
        return False

    if operation == "assign.avito_connection" and field == "channels.avito":
        row = db.execute(text("""
            SELECT length(coalesce(avito_client_id,'')) AS client_id_len
            FROM accounts
            WHERE account_id=:a
            LIMIT 1
        """), {"a": account_id}).mappings().first()
        return bool(row and int(row.get("client_id_len") or 0) > 0)

    if operation == "assign.access_roles" and field == "access.roles":
        row = db.execute(text("""
            SELECT
                a.owner_user_id,
                EXISTS (
                    SELECT 1
                    FROM user_account_access u
                    WHERE u.account_id=a.account_id
                      AND u.can_view=TRUE
                ) AS has_view_grant
            FROM accounts a
            WHERE a.account_id=:a
            LIMIT 1
        """), {"a": account_id}).mappings().first()
        return bool(
            row and (
                row.get("owner_user_id") is not None
                or row.get("has_view_grant") is True
            )
        )

    return False


def current_requests(packets, db=None):
    """Build CURRENT unresolved confirmation requests from E packets.

    FACTORY_J_SATISFIED_SCHEDULE_V1

    Fail closed:
    - only eligible account
    - owner must exist
    - only effective CONFIRM_REQUIRED

    Important reconciliation rule:
    migrate.schedule is NOT a current human decision when the exact required
    job already exists and is enabled in factory_schedule.

    That means a legacy migration which is already materially complete cannot
    keep generating a fake pending confirmation forever.

    When db is omitted we preserve the old pure-packet behavior for backwards
    compatibility. Production sync and K fingerprint validation pass db.
    """

    out=[]
    active_schedule_cache={}

    for packet in packets:
        if packet.get("owner_user_id") is None:
            continue

        account_id=packet.get("account_id")
        launch_status=str(packet.get("launch_status") or "").upper()
        fully_eligible=bool(packet.get("eligible_zero_touch"))

        # FACTORY_J_ACTIVE_SCHEDULE_DEADLOCK_FIX_V1
        # Ordinary confirmations retain the historical fail-closed eligibility
        # fence. The only pre-eligible exception is a schedule migration for a
        # canonically ACTIVE client. This breaks the circular dependency where
        # schedule approval is needed on the path to eligibility, while keeping
        # expired/inactive/billing-gap tenants and all non-schedule operations
        # out of the queue. K still refuses execution until eligible_zero_touch.
        allow_preeligible_schedule=False
        if (
            not fully_eligible
            and launch_status in {"NEEDS_CLIENT","NEEDS_PLATFORM"}
            and db is not None
            and account_id
        ):
            if account_id not in active_schedule_cache:
                try:
                    from app.services.client_supervisor import build_client_state
                    active_schedule_cache[account_id]=bool(
                        build_client_state(db,str(account_id)).get("client_state")=="active"
                    )
                except Exception:
                    active_schedule_cache[account_id]=False
            allow_preeligible_schedule=active_schedule_cache[account_id]

        if not fully_eligible and not allow_preeligible_schedule:
            continue

        for decision in packet.get(
            "confirm_required",
            []
        ):
            if decision.get("policy") != "CONFIRM_REQUIRED":
                continue

            operation=decision.get("operation")
            field=decision.get("field")

            if not operation or not field:
                continue

            # FACTORY_HUMAN_INPUT_NOT_APPROVAL_V1
            # ask.client is a data/question workflow, not an authorization to
            # mutate production. Keep it in factory_launch_state.human_questions
            # and out of the durable approval queue so the owner is not turned
            # into an operator for optional profile/KPI clarification.
            if operation == "ask.client":
                continue

            is_schedule_migration=(
                operation=="migrate.schedule"
                and field in {"schedule.autopilot","schedule.mop_poll"}
            )

            if is_schedule_migration and db is not None:
                if account_id not in active_schedule_cache:
                    try:
                        from app.services.client_supervisor import build_client_state
                        active_schedule_cache[account_id]=bool(
                            build_client_state(db,str(account_id)).get("client_state")=="active"
                        )
                    except Exception:
                        active_schedule_cache[account_id]=False
                if not active_schedule_cache[account_id]:
                    continue

            if not fully_eligible:
                if not (
                    allow_preeligible_schedule
                    and is_schedule_migration
                ):
                    continue

            # FACTORY_J_SATISFIED_SCHEDULE_V1
            # Existing exact enabled central schedule means there is no
            # unresolved migration left for the owner to approve.
            if _schedule_migration_already_satisfied(
                db,
                packet["account_id"],
                field,
                operation,
            ):
                continue

            if _assignment_already_satisfied(
                db,
                packet["account_id"],
                field,
                operation,
            ):
                continue

            payload=_request_payload(
                db,
                packet,
                decision,
            )

            fp=fingerprint(
                packet["account_id"],
                field,
                operation,
                payload,
            )

            out.append({
                "account_id":
                    packet["account_id"],

                "field":
                    field,

                "operation":
                    operation,

                "fingerprint":
                    fp,

                "payload":
                    payload,
            })

    return out


def _event(
    db,
    confirmation_id,
    account_id,
    event_type,
    before_status,
    after_status,
    payload,
    actor,
):
    db.execute(text("""
        INSERT INTO factory_confirmation_events (
            confirmation_id,
            account_id,
            event_type,
            before_status,
            after_status,
            payload,
            actor,
            created_at
        )
        VALUES (
            :cid,
            :a,
            :event,
            :before,
            :after,
            CAST(:payload AS jsonb),
            :actor,
            now()
        )
    """), {
        "cid":confirmation_id,
        "a":account_id,
        "event":event_type,
        "before":before_status,
        "after":after_status,
        "payload":canonical(payload or {}),
        "actor":actor,
    })


def sync(db,packets,dry_run=False):
    current=current_requests(
        packets,
        db=db,
    )

    current_keys={
        (
            x["account_id"],
            x["field"],
            x["operation"],
            x["fingerprint"],
        )
        for x in current
    }

    existing=db.execute(text("""
        SELECT
            id,
            account_id,
            field,
            operation,
            fingerprint,
            status,
            payload
        FROM factory_confirmations
        ORDER BY id
    """)).mappings().all()

    existing_keys={
        (
            x["account_id"],
            x["field"],
            x["operation"],
            x["fingerprint"],
        ):x
        for x in existing
    }

    create=[
        x
        for x in current
        if (
            x["account_id"],
            x["field"],
            x["operation"],
            x["fingerprint"],
        ) not in existing_keys
    ]

    # FACTORY_CONFIRMATION_REACTIVATE_V1
    # A request may disappear temporarily when runtime evidence becomes healthy
    # and later become current again with the exact same fingerprint. A stale
    # row must not suppress that current request forever.
    reactivate=[]
    for x in current:
        key=(
            x["account_id"],
            x["field"],
            x["operation"],
            x["fingerprint"],
        )
        old=existing_keys.get(key)
        if old and old["status"]=="stale":
            reactivate.append({
                **x,
                "id":old["id"],
                "before_status":"stale",
            })

    stale=[
        x
        for x in existing
        if x["status"]=="pending"
        and (
            x["account_id"],
            x["field"],
            x["operation"],
            x["fingerprint"],
        ) not in current_keys
    ]

    result={
        "current":len(current),
        "create":len(create),
        "reactivate":len(reactivate),
        "stale":len(stale),
    }

    if dry_run:
        result["create_items"]=create
        result["reactivate_items"]=[
            dict(x) for x in reactivate
        ]
        result["stale_items"]=[
            dict(x) for x in stale
        ]
        return result

    for item in create:
        row=db.execute(text("""
            INSERT INTO factory_confirmations (
                account_id,
                field,
                operation,
                fingerprint,
                status,
                payload,
                created_at,
                updated_at
            )
            VALUES (
                :a,
                :f,
                :o,
                :fp,
                'pending',
                CAST(:payload AS jsonb),
                now(),
                now()
            )
            RETURNING id
        """), {
            "a":item["account_id"],
            "f":item["field"],
            "o":item["operation"],
            "fp":item["fingerprint"],
            "payload":canonical(item["payload"]),
        }).scalar()

        _event(
            db,
            row,
            item["account_id"],
            "CREATE",
            None,
            "pending",
            item["payload"],
            "factory",
        )

    for item in reactivate:
        db.execute(text("""
            UPDATE factory_confirmations
            SET
                status='pending',
                payload=CAST(:payload AS jsonb),
                decided_at=NULL,
                decided_by=NULL,
                updated_at=now()
            WHERE id=:id
              AND status='stale'
        """), {
            "id":item["id"],
            "payload":canonical(item["payload"]),
        })

        _event(
            db,
            item["id"],
            item["account_id"],
            "REACTIVATE",
            "stale",
            "pending",
            item["payload"],
            "factory",
        )

    for item in stale:
        db.execute(text("""
            UPDATE factory_confirmations
            SET
                status='stale',
                updated_at=now()
            WHERE id=:id
              AND status='pending'
        """), {
            "id":item["id"],
        })

        _event(
            db,
            item["id"],
            item["account_id"],
            "STALE",
            "pending",
            "stale",
            item["payload"],
            "factory",
        )

    return result


def decide(db,confirmation_id,status,actor):
    if status not in {
        "approved",
        "rejected",
    }:
        raise ValueError(
            "decision must be approved or rejected"
        )

    row=db.execute(text("""
        SELECT *
        FROM factory_confirmations
        WHERE id=:id
        FOR UPDATE
    """), {
        "id":confirmation_id,
    }).mappings().first()

    if not row:
        raise ValueError(
            "confirmation not found"
        )

    before=row["status"]

    # Human decision can only be made on a current pending request.
    if before != "pending":
        raise ValueError(
            "confirmation is not pending: %s"
            % before
        )

    db.execute(text("""
        UPDATE factory_confirmations
        SET
            status=:status,
            decided_at=now(),
            decided_by=:actor,
            updated_at=now()
        WHERE id=:id
    """), {
        "status":status,
        "actor":actor,
        "id":confirmation_id,
    })

    _event(
        db,
        confirmation_id,
        row["account_id"],
        status.upper(),
        before,
        status,
        row["payload"],
        actor,
    )

    return {
        "id":confirmation_id,
        "account_id":row["account_id"],
        "field":row["field"],
        "operation":row["operation"],
        "before":before,
        "after":status,
    }


def list_pending(db,account_id=None):
    sql="""
        SELECT
            id,
            account_id,
            field,
            operation,
            status,
            payload,
            created_at
        FROM factory_confirmations
        WHERE status='pending'
    """

    params={}

    if account_id:
        sql += " AND account_id=:a"
        params["a"]=account_id

    sql += " ORDER BY account_id,field,id"

    return [
        dict(x)
        for x in db.execute(
            text(sql),
            params,
        ).mappings().all()
    ]
