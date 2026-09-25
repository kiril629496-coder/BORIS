"""FACTORY_L_V1 — versioned schedule policy registry.

Policy is data, not guessing.

Only ACTIVE policy may be used by K/M.

No crontab mutation.
No execution.
No money.
"""

import hashlib
import json

from sqlalchemy import text


JOB_COMMANDS={
    "autopilot":
        "cd /root/BORIS/backend && set -a; . ./.env; set +a; "
        "venv/bin/python3 ads_autopilot.py {ACCOUNT_ID} "
        ">> /root/BORIS/backend/ads_autopilot.log 2>&1",

    "mop_poll":
        "cd /root/BORIS/backend && "
        "venv/bin/python3 messenger_runner.py "
        "--apply --only={ACCOUNT_ID} "
        ">> /root/BORIS/backend/mop.log 2>&1",
}


def canonical(v):
    return json.dumps(
        v,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        default=str,
    )


def content_hash(data):
    return hashlib.sha256(
        canonical(data).encode("utf-8")
    ).hexdigest()


def validate_cron(expr):
    parts=expr.split()

    if len(parts)!=5:
        return False

    # Narrow accepted policy syntax for now.
    allowed_chars=set(
        "0123456789*/,-"
    )

    for part in parts:
        if not set(part) <= allowed_chars:
            return False

    return True


def validate_policy(
    field,
    job_kind,
    cadence,
    cron_expr,
    command_template,
):
    if field not in {
        "schedule.autopilot",
        "schedule.mop_poll",
    }:
        return False,"unsupported_field"

    expected={
        "schedule.autopilot":"autopilot",
        "schedule.mop_poll":"mop_poll",
    }[field]

    if job_kind != expected:
        return False,"job_kind_mismatch"

    if cadence not in {
        "daily",
        "intraday",
    }:
        return False,"unsupported_cadence"

    if not validate_cron(cron_expr):
        return False,"invalid_cron"

    expected_cmd=JOB_COMMANDS[job_kind]

    if command_template != expected_cmd:
        return False,"command_template_mismatch"

    return True,None


def active_policy(db,field):
    return db.execute(text("""
        SELECT *
        FROM factory_schedule_policy
        WHERE field=:field
          AND status='active'
          AND enabled
        ORDER BY version DESC
        LIMIT 1
    """), {
        "field":field,
    }).mappings().first()


def create_or_get(
    db,
    policy_key,
    version,
    field,
    job_kind,
    cadence,
    cron_expr,
    command_template,
    actor="factory_l",
):
    ok,reason=validate_policy(
        field,
        job_kind,
        cadence,
        cron_expr,
        command_template,
    )

    if not ok:
        raise ValueError(reason)

    payload={
        "policy_key":policy_key,
        "version":version,
        "field":field,
        "job_kind":job_kind,
        "cadence":cadence,
        "cron_expr":cron_expr,
        "command_template":command_template,
    }

    h=content_hash(payload)

    existing=db.execute(text("""
        SELECT *
        FROM factory_schedule_policy
        WHERE policy_key=:k
          AND version=:v
    """), {
        "k":policy_key,
        "v":version,
    }).mappings().first()

    if existing:
        if existing["content_hash"] != h:
            raise ValueError(
                "policy version collision"
            )

        return dict(existing),"UNCHANGED"

    row=db.execute(text("""
        INSERT INTO factory_schedule_policy (
            policy_key,
            version,
            field,
            job_kind,
            cadence,
            cron_expr,
            command_template,
            enabled,
            status,
            source,
            content_hash,
            created_at
        )
        VALUES (
            :k,
            :v,
            :field,
            :job,
            :cadence,
            :cron,
            :cmd,
            TRUE,
            'draft',
            'factory_policy',
            :hash,
            now()
        )
        RETURNING *
    """), {
        "k":policy_key,
        "v":version,
        "field":field,
        "job":job_kind,
        "cadence":cadence,
        "cron":cron_expr,
        "cmd":command_template,
        "hash":h,
    }).mappings().first()

    db.execute(text("""
        INSERT INTO factory_schedule_policy_events (
            policy_id,
            event_type,
            before_value,
            after_value,
            actor,
            created_at
        )
        VALUES (
            :id,
            'CREATE',
            NULL,
            CAST(:after AS jsonb),
            :actor,
            now()
        )
    """), {
        "id":row["id"],
        "after":canonical(payload),
        "actor":actor,
    })

    return dict(row),"CREATED"


def activate(db,policy_id,actor):
    row=db.execute(text("""
        SELECT *
        FROM factory_schedule_policy
        WHERE id=:id
        FOR UPDATE
    """), {
        "id":policy_id,
    }).mappings().first()

    if not row:
        raise ValueError(
            "policy_not_found"
        )

    if row["status"]=="active":
        return dict(row),"UNCHANGED"

    # One active policy per field.
    db.execute(text("""
        UPDATE factory_schedule_policy
        SET
            status='retired'
        WHERE field=:field
          AND status='active'
          AND id<>:id
    """), {
        "field":row["field"],
        "id":policy_id,
    })

    db.execute(text("""
        UPDATE factory_schedule_policy
        SET
            status='active',
            activated_at=now()
        WHERE id=:id
    """), {
        "id":policy_id,
    })

    db.execute(text("""
        INSERT INTO factory_schedule_policy_events (
            policy_id,
            event_type,
            before_value,
            after_value,
            actor,
            created_at
        )
        VALUES (
            :id,
            'ACTIVATE',
            CAST(:before AS jsonb),
            CAST(:after AS jsonb),
            :actor,
            now()
        )
    """), {
        "id":policy_id,
        "before":canonical({
            "status":row["status"],
        }),
        "after":canonical({
            "status":"active",
        }),
        "actor":actor,
    })

    return {
        **dict(row),
        "status":"active",
    },"ACTIVATED"
