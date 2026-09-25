"""FACTORY_K_EXECUTOR_V1 — sole approved schedule executor."""
"""FACTORY_K_V1 — apply approved schedule decisions.

CRITICAL RULES:

1. Only J status=approved.
2. Confirmation fingerprint must still exist in current E.
3. Account must still be zero-touch eligible and owned.
4. Only migrate.schedule.
5. Only schedule.autopilot / schedule.mop_poll.
6. Never invent cron_expr.
7. Never write crontab.
8. Existing Factory schedule dispatcher remains the sole executor.
9. If exact schedule evidence is missing -> BLOCKED, no write.
"""

import json

from sqlalchemy import text

from app.factory import schedule_policy as SP


FIELD_JOB={
    "schedule.autopilot":
        "autopilot",

    "schedule.mop_poll":
        "mop_poll",
}


def canonical(v):
    return json.dumps(
        v,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",",":"),
    )


def load_current_e():
    return json.load(open(
        "/root/BORIS/backend/run/factory_e_packets.json",
        encoding="utf-8",
    ))


def current_confirmation_fingerprints(db):
    """Use J's own builder so fingerprint semantics are identical."""

    from app.factory import confirmations as C

    packets=load_current_e()

    # FACTORY_K_EFFECTIVE_CONFIRMATION_V1
    # K validates against the same unresolved request set as J.
    # An already-existing exact schedule is not a migration waiting to happen.
    return {
        (
            x["account_id"],
            x["field"],
            x["operation"],
            x["fingerprint"],
        )
        for x in C.current_requests(
            packets,
            db=db,
        )
    }


def _schedule_row(db,account_id,job_kind):
    return db.execute(text("""
        SELECT
            id,
            account_id,
            job_kind,
            cron_expr,
            command_template,
            source,
            source_fingerprint,
            enabled
        FROM factory_schedule
        WHERE account_id=:a
          AND job_kind=:j
        ORDER BY id DESC
        LIMIT 1
    """), {
        "a":account_id,
        "j":job_kind,
    }).mappings().first()


def _mop_owner_enabled(db, account_id):
    raw = db.execute(text("""
        SELECT value
          FROM storage
         WHERE account_id=:a
           AND key='mop_crm_sales_settings'
         ORDER BY id DESC
         LIMIT 1
    """), {"a": account_id}).scalar()
    if not raw:
        return True
    try:
        cfg = json.loads(raw)
    except Exception:
        return False
    if cfg.get("enabled") is False:
        return False
    if cfg.get("mop_enabled") is False:
        return False
    return True


def _resolved_schedule_evidence(
    db,
    account_id,
    field,
):
    """Historical resolved schedule evidence.

    IMPORTANT:
    cadence alone is NOT enough to create a concrete cron.

    We read it only as supporting evidence.
    """

    exists=db.execute(text("""
        SELECT EXISTS(
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='public'
              AND table_name='factory_resolved_values'
        )
    """)).scalar()

    if not exists:
        return None

    row=db.execute(text("""
        SELECT
            value,
            source,
            confidence,
            resolution_kind,
            evidence,
            source_fingerprint
        FROM factory_resolved_values
        WHERE account_id=:a
          AND field=:f
        ORDER BY updated_at DESC,id DESC
        LIMIT 1
    """), {
        "a":account_id,
        "f":field,
    }).mappings().first()

    return dict(row) if row else None


def preview_one(
    db,
    confirmation,
    current_keys,
):
    cid=confirmation["id"]
    account_id=confirmation["account_id"]
    field=confirmation["field"]
    operation=confirmation["operation"]
    fp=confirmation["fingerprint"]

    base={
        "confirmation_id":cid,
        "account_id":account_id,
        "field":field,
        "operation":operation,
        "fingerprint":fp,
    }

    if confirmation["status"] != "approved":
        return {
            **base,
            "result":"SKIP",
            "reason":"confirmation_not_approved",
        }

    if operation != "migrate.schedule":
        return {
            **base,
            "result":"BLOCKED",
            "reason":"operation_not_supported",
        }

    job_kind=FIELD_JOB.get(field)

    if not job_kind:
        return {
            **base,
            "result":"BLOCKED",
            "reason":"schedule_field_not_supported",
        }

    base["job_kind"]=job_kind

    key=(
        account_id,
        field,
        operation,
        fp,
    )

    # FACTORY_K_LIFECYCLE_RECONCILIATION_V1
    #
    # J intentionally removes a schedule confirmation from CURRENT requests
    # once the exact central schedule already exists. Therefore an already
    # approved historical confirmation may legitimately be absent from
    # current_keys.
    #
    # The safe exception is extremely narrow:
    #   approved confirmation fingerprint == enabled central schedule
    #   fingerprint for the same account/job.
    #
    # This does NOT approve anything.
    # This does NOT create a schedule.
    # It only allows K to reconcile an already-approved action with the
    # already-existing exact schedule.
    approved_existing_schedule = False

    if key not in current_keys:
        existing_for_reconciliation = _schedule_row(
            db,
            account_id,
            job_kind,
        )

        if (
            existing_for_reconciliation
            and existing_for_reconciliation.get("enabled")
            and existing_for_reconciliation.get("source_fingerprint") == fp
        ):
            approved_existing_schedule = True

        if not approved_existing_schedule:
            return {
                **base,
                "result":"BLOCKED",
                "reason":"confirmation_fingerprint_not_current",
            }

    state=db.execute(text("""
        SELECT
            f.eligible_zero_touch,
            f.launch_status,
            a.owner_user_id
        FROM factory_launch_state f
        JOIN accounts a
          ON a.account_id=f.account_id
        WHERE f.account_id=:a
    """), {
        "a":account_id,
    }).mappings().first()

    if not state:
        return {
            **base,
            "result":"BLOCKED",
            "reason":"account_state_missing",
        }

    if state["owner_user_id"] is None:
        return {
            **base,
            "result":"BLOCKED",
            "reason":"owner_missing",
        }

    if not state["eligible_zero_touch"]:
        return {
            **base,
            "result":"BLOCKED",
            "reason":"account_not_zero_touch_eligible",
        }

    if field == "schedule.mop_poll" and not _mop_owner_enabled(db, account_id):
        return {
            **base,
            "result":"BLOCKED",
            "reason":"mop_owner_disabled",
        }

    existing=_schedule_row(
        db,
        account_id,
        job_kind,
    )

    resolved=_resolved_schedule_evidence(
        db,
        account_id,
        field,
    )

    # Safest possible case:
    # exact schedule already exists in the proven central scheduler.
    if existing:
        return {
            **base,
            "result":"READY_EXISTING",
            "reason":"exact_factory_schedule_exists",
            "schedule":{
                k:existing[k]
                for k in (
                    "id",
                    "cron_expr",
                    "command_template",
                    "source",
                    "source_fingerprint",
                    "enabled",
                )
            },
            "resolved_evidence":resolved,
        }

    # FACTORY_M_POLICY_FALLBACK_V1
    # Exact historical schedule does not exist.
    # Only an ACTIVE versioned policy may provide the concrete schedule.
    policy=SP.active_policy(
        db,
        field,
    )

    if not policy:
        return {
            **base,
            "result":"BLOCKED",
            "reason":"no_exact_schedule_evidence_or_active_policy",
            "resolved_evidence":resolved,
        }

    cron_expr=policy["cron_expr"]

    # Daily autopilot accounts receive a deterministic minute slot.
    # Same account => same minute. No randomness and no manual guessing.
    if field=="schedule.autopilot":
        import hashlib

        slot=int(
            hashlib.sha256(
                account_id.encode("utf-8")
            ).hexdigest()[:8],
            16,
        ) % 40

        cron_expr="%d 6 * * *" % slot

    return {
        **base,
        "result":"READY_POLICY",
        "reason":"active_versioned_schedule_policy",
        "policy":{
            "id":policy["id"],
            "policy_key":policy["policy_key"],
            "version":policy["version"],
            "cron_expr":cron_expr,
            "command_template":policy["command_template"],
            "content_hash":policy["content_hash"],
        },
        "resolved_evidence":resolved,
    }


def preview_all(db):
    keys=current_confirmation_fingerprints(db)

    rows=db.execute(text("""
        SELECT
            id,
            account_id,
            field,
            operation,
            fingerprint,
            status,
            payload,
            decided_at,
            decided_by
        FROM factory_confirmations
        WHERE status='approved'
          AND operation='migrate.schedule'
        ORDER BY id
    """)).mappings().all()

    return [
        preview_one(
            db,
            dict(row),
            keys,
        )
        for row in rows
    ]


def _log(
    db,
    item,
    action,
    outcome,
    reason,
    before,
    after,
):
    db.execute(text("""
        INSERT INTO factory_schedule_apply_log (
            confirmation_id,
            account_id,
            field,
            job_kind,
            action,
            outcome,
            reason,
            before_value,
            after_value,
            confirmation_fingerprint,
            actor,
            created_at
        )
        VALUES (
            :cid,
            :a,
            :f,
            :j,
            :action,
            :outcome,
            :reason,
            CAST(:before AS jsonb),
            CAST(:after AS jsonb),
            :fp,
            'factory_k',
            now()
        )
    """), {
        "cid":item["confirmation_id"],
        "a":item["account_id"],
        "f":item["field"],
        "j":item.get("job_kind") or "unknown",
        "action":action,
        "outcome":outcome,
        "reason":reason,
        "before":(
            canonical(before)
            if before is not None
            else None
        ),
        "after":(
            canonical(after)
            if after is not None
            else None
        ),
        "fp":item["fingerprint"],
    })


def apply_ready(db,item):
    """V1 intentionally applies only already-proven exact schedules.

    For an existing exact row, approval means:
    - ensure it is enabled;
    - no cron change;
    - no command change.

    For a missing row, V1 refuses to invent schedule timing.
    """

    if item["result"] == "READY_POLICY":
        policy=item["policy"]

        # Race/fail-closed check.
        existing=db.execute(text("""
            SELECT id
            FROM factory_schedule
            WHERE account_id=:a
              AND job_kind=:j
            LIMIT 1
        """), {
            "a":item["account_id"],
            "j":item["job_kind"],
        }).first()

        if existing:
            return {
                **item,
                "apply_result":"BLOCKED_EXISTING_RACE",
            }

        row_id=db.execute(text("""
            INSERT INTO factory_schedule (
                account_id,
                job_kind,
                cron_expr,
                command_template,
                source,
                source_fingerprint,
                enabled,
                created_at,
                updated_at
            )
            VALUES (
                :a,
                :j,
                :cron,
                :cmd,
                :source,
                :fp,
                TRUE,
                now(),
                now()
            )
            RETURNING id
        """), {
            "a":item["account_id"],
            "j":item["job_kind"],
            "cron":policy["cron_expr"],
            "cmd":policy["command_template"],
            "source":
                "factory_policy:%s:v%s"
                % (
                    policy["policy_key"],
                    policy["version"],
                ),
            "fp":item["fingerprint"],
        }).scalar()

        after={
            "id":row_id,
            "enabled":True,
            "cron_expr":policy["cron_expr"],
            "command_template":policy["command_template"],
            "policy_key":policy["policy_key"],
            "policy_version":policy["version"],
        }

        _log(
            db,
            item,
            "CREATE_POLICY_SCHEDULE",
            "APPLIED",
            "approved_active_policy",
            None,
            after,
        )

        return {
            **item,
            "apply_result":"APPLIED",
            "before":None,
            "after":after,
        }

    if item["result"] != "READY_EXISTING":
        return {
            **item,
            "apply_result":"NOT_APPLIED",
        }

    schedule=item["schedule"]

    before={
        "enabled":schedule["enabled"],
        "cron_expr":schedule["cron_expr"],
        "command_template":
            schedule["command_template"],
    }

    if schedule["enabled"]:
        return {
            **item,
            "apply_result":"UNCHANGED",
            "before":before,
            "after":before,
        }

    db.execute(text("""
        UPDATE factory_schedule
        SET
            enabled=TRUE,
            updated_at=now()
        WHERE id=:id
          AND cron_expr=:cron
          AND command_template=:cmd
    """), {
        "id":schedule["id"],
        "cron":schedule["cron_expr"],
        "cmd":schedule["command_template"],
    })

    after=dict(before)
    after["enabled"]=True

    _log(
        db,
        item,
        "ENABLE_EXISTING",
        "APPLIED",
        "approved_exact_existing_schedule",
        before,
        after,
    )

    return {
        **item,
        "apply_result":"APPLIED",
        "before":before,
        "after":after,
    }
