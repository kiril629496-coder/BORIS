"""
BORIS Factory M — FACTORY_M_V1.

Internal attention / decision queue.

Consumes Factory L health state.

Writes ONLY:
    factory_attention_state
    factory_attention_events

It does NOT:
    send messages
    contact clients
    spend money
    publish
    change schedules
    approve confirmations
    edit Client Config
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text


FACTORY_M_V1=True


DDL = (
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',

    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


def ensure_schema(db):
    for sql in DDL:
        db.execute(text(sql))


def fingerprint(payload):
    raw=json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        default=str,
    ).encode()

    return hashlib.sha256(raw).hexdigest()


def issue(
    account_id,
    issue_type,
    route,
    priority,
    title,
    detail,
    source_health,
    source_fingerprint,
):
    key="%s:%s" % (
        account_id,
        issue_type,
    )

    payload={
        "attention_key":key,
        "account_id":account_id,
        "source":"FACTORY_L",
        "issue_type":issue_type,
        "route":route,
        "priority":priority,
        "status":"OPEN",
        "title":title,
        "detail":detail,
        "source_health":source_health,
        "source_fingerprint":
            source_fingerprint,
    }

    payload["attention_fingerprint"] = \
        fingerprint(payload)

    return payload


def build(db):
    # FACTORY_ATTENTION_ACTIVE_CLIENT_ONLY_V1
    # Reuse the canonical client-period truth instead of duplicating billing
    # heuristics here. Factory attention is a service-work queue, not the place
    # to reactivate expired/billing-gap/inactive clients or make the owner clean
    # up historical launch gaps.
    active_accounts = None
    try:
        from app.services.client_supervisor import build_fleet_state
        fleet = build_fleet_state(db)
        active_accounts = {
            str(x.get("account_id"))
            for x in (fleet.get("accounts") or [])
            if x.get("client_state") == "active"
            and x.get("account_id")
        }
    except Exception:
        # Fail safe for routing: if canonical activity truth is temporarily
        # unavailable, keep the previous behavior rather than silently hiding
        # a potentially real owner decision.
        active_accounts = None

    rows=db.execute(text("""
        SELECT
            account_id,
            health_status,
            unresolved_internal,
            waiting_facts,
            pending_confirmations,
            provisioning_status,
            scheduler_stale,
            hard_blockers,
            health_fingerprint
        FROM factory_launch_health
        ORDER BY account_id
    """)).mappings().all()

    result=[]

    for r in rows:
        aid=r["account_id"]

        if (
            active_accounts is not None
            and str(aid) not in active_accounts
        ):
            continue

        health=r["health_status"]
        src_fp=r["health_fingerprint"]

        # Missing structured fact:
        # owner/client knowledge is required.
        if int(r["waiting_facts"] or 0) > 0:
            result.append(issue(
                aid,
                "MISSING_FACT",
                "OWNER_REQUIRED",
                60,
                "Нужен недостающий факт",
                "%s ожидающих подтверждённых фактов"
                % int(r["waiting_facts"]),
                health,
                src_fp,
            ))

        # Confirmation is explicitly NOT auto-approved here.
        if int(r["pending_confirmations"] or 0) > 0:
            result.append(issue(
                aid,
                "PENDING_CONFIRMATION",
                "OWNER_REQUIRED",
                70,
                "Требуется решение",
                "%s действий ожидают подтверждения"
                % int(r["pending_confirmations"]),
                health,
                src_fp,
            ))

        # Operational degradation is highest priority.
        if bool(r["scheduler_stale"]):
            result.append(issue(
                aid,
                "SCHEDULER_STALE",
                "AUTO_INTERNAL",
                90,
                "Расписание требует диагностики",
                "Factory L обнаружил устаревшее выполнение scheduler",
                health,
                src_fp,
            ))

        if int(r["hard_blockers"] or 0) > 0:
            result.append(issue(
                aid,
                "HARD_BLOCKER",
                "OWNER_REQUIRED",
                100,
                "Запуск заблокирован",
                "Обнаружен критический blocker после запуска",
                health,
                src_fp,
            ))

        # Internal unresolved is represented separately only when
        # no WAITING_FACT exists, otherwise one root cause would
        # produce duplicate owner tasks.
        if (
            int(r["unresolved_internal"] or 0) > 0
            and int(r["waiting_facts"] or 0) == 0
        ):
            result.append(issue(
                aid,
                "INTERNAL_GAP",
                "AUTO_INTERNAL",
                50,
                "Внутренний пробел конфигурации",
                "%s внутренних значений не разрешены"
                % int(r["unresolved_internal"]),
                health,
                src_fp,
            ))

    return sorted(
        result,
        key=lambda x:(
            -x["priority"],
            x["account_id"],
            x["issue_type"],
        ),
    )


def apply(db):
    ensure_schema(db)

    current=build(db)
    active_keys={
        x["attention_key"]
        for x in current
    }

    inserted=0
    updated=0
    unchanged=0
    resolved=0
    events=0

    for x in current:

        old=db.execute(text("""
            SELECT
                status,
                attention_fingerprint
            FROM factory_attention_state
            WHERE attention_key=:k
        """),{
            "k":x["attention_key"],
        }).fetchone()

        if (
            old
            and old[0]=="OPEN"
            and old[1]
                == x["attention_fingerprint"]
        ):
            unchanged += 1
            continue

        before=old[0] if old else None

        db.execute(text("""
            INSERT INTO factory_attention_state (
                attention_key,
                account_id,
                source,
                issue_type,
                route,
                priority,
                status,
                title,
                detail,
                source_health,
                source_fingerprint,
                attention_fingerprint,
                created_at,
                updated_at,
                resolved_at
            )
            VALUES (
                :attention_key,
                :account_id,
                :source,
                :issue_type,
                :route,
                :priority,
                'OPEN',
                :title,
                :detail,
                :source_health,
                :source_fingerprint,
                :attention_fingerprint,
                NOW(),
                NOW(),
                NULL
            )
            ON CONFLICT (attention_key)
            DO UPDATE SET
                account_id=EXCLUDED.account_id,
                source=EXCLUDED.source,
                issue_type=EXCLUDED.issue_type,
                route=EXCLUDED.route,
                priority=EXCLUDED.priority,
                status='OPEN',
                title=EXCLUDED.title,
                detail=EXCLUDED.detail,
                source_health=EXCLUDED.source_health,
                source_fingerprint=EXCLUDED.source_fingerprint,
                attention_fingerprint=EXCLUDED.attention_fingerprint,
                updated_at=NOW(),
                resolved_at=NULL
        """),x)

        db.execute(text("""
            INSERT INTO factory_attention_events (
                attention_key,
                account_id,
                before_status,
                after_status,
                route,
                priority,
                attention_fingerprint,
                created_at
            )
            VALUES (
                :attention_key,
                :account_id,
                :before_status,
                'OPEN',
                :route,
                :priority,
                :attention_fingerprint,
                NOW()
            )
        """),{
            **x,
            "before_status":before,
        })

        if old:
            updated += 1
        else:
            inserted += 1

        events += 1

    # Close issues whose source condition disappeared.
    open_rows=db.execute(text("""
        SELECT
            attention_key,
            account_id,
            route,
            priority,
            attention_fingerprint
        FROM factory_attention_state
        WHERE status='OPEN'
    """)).mappings().all()

    for old in open_rows:
        if old["attention_key"] in active_keys:
            continue

        db.execute(text("""
            UPDATE factory_attention_state
            SET
                status='RESOLVED',
                updated_at=NOW(),
                resolved_at=NOW()
            WHERE attention_key=:k
              AND status='OPEN'
        """),{
            "k":old["attention_key"],
        })

        db.execute(text("""
            INSERT INTO factory_attention_events (
                attention_key,
                account_id,
                before_status,
                after_status,
                route,
                priority,
                attention_fingerprint,
                created_at
            )
            VALUES (
                :attention_key,
                :account_id,
                'OPEN',
                'RESOLVED',
                :route,
                :priority,
                :attention_fingerprint,
                NOW()
            )
        """),dict(old))

        resolved += 1
        events += 1

    return {
        "items":current,
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
        "resolved":resolved,
        "events":events,
    }
