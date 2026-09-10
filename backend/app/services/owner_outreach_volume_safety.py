"""Database-authoritative safety boundary for BORIS owner cold email.

This module is deliberately isolated from prospect_campaigns.py and UI code.
It is invoked by the canonical minute email worker before any prospect feeder
or SMTP processing. PostgreSQL remains the final enforcement layer.
"""
from __future__ import annotations

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.owner_outreach_policy import (
    OWNER_OUTREACH_HARD_MAX_DAILY,
    OWNER_OUTREACH_STAGE1_DAILY,
    OWNER_OUTREACH_STAGE1_DAYS,
    OWNER_OUTREACH_STAGE2_DAILY,
    OWNER_OUTREACH_STAGE2_END_DAY,
    canonical_daily_cap,
)

SNAPSHOT_TRIGGER = "trg_zzz_owner_daily_limit_safety_v3"
QUEUE_TRIGGER = "trg_zzz_owner_outreach_queue_volume_v3"


def canonical_cap(age_days:int)->int:
    """Backend-final policy mirrored by both PostgreSQL guards."""
    return canonical_daily_cap(max(0, int(age_days or 0)))


def _snapshot_guard_sql() -> str:
    return f"""
      CREATE OR REPLACE FUNCTION boris_owner_daily_limit_safety_v3()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $guard$
      DECLARE
        first_service_day date;
        age_days integer;
        allowed_cap integer;
        tz_name text;
      BEGIN
        tz_name := COALESCE(NULLIF(NEW.timezone,''),'Europe/Moscow');

        SELECT min(timezone(tz_name,m.queued_at)::date)
          INTO first_service_day
        FROM prospect_campaign_members m
        JOIN prospect_campaigns c ON c.id=m.campaign_id
        WHERE c.owner_id=NEW.owner_id
          AND c.account_id='__owner_outreach__'
          AND m.queued_at IS NOT NULL;

        age_days := CASE
          WHEN first_service_day IS NULL THEN 0
          ELSE GREATEST(0,NEW.service_date-first_service_day)
        END;
        allowed_cap := CASE
          WHEN age_days < {OWNER_OUTREACH_STAGE1_DAYS} THEN {OWNER_OUTREACH_STAGE1_DAILY}
          WHEN age_days < {OWNER_OUTREACH_STAGE2_END_DAY} THEN {OWNER_OUTREACH_STAGE2_DAILY}
          ELSE {OWNER_OUTREACH_HARD_MAX_DAILY}
        END;

        IF TG_OP='UPDATE' THEN
          NEW.daily_cap := LEAST(NEW.daily_cap,OLD.daily_cap,allowed_cap);
        ELSE
          NEW.daily_cap := LEAST(NEW.daily_cap,allowed_cap);
        END IF;
        NEW.timezone := tz_name;
        NEW.updated_at := NOW();
        RETURN NEW;
      END
      $guard$
    """


def _queue_guard_sql() -> str:
    return f"""
      CREATE OR REPLACE FUNCTION boris_owner_outreach_queue_volume_v3()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $guard$
      DECLARE
        member_id bigint;
        owner_id_v bigint;
        account_id_v text;
        tz_name text;
        service_day date;
        first_service_day date;
        age_days integer;
        canonical_cap integer;
        snapshot_cap integer;
        effective_cap integer;
        active_today integer;
      BEGIN
        IF NEW.ref_type IS DISTINCT FROM 'prospect_campaign_member'
           OR NEW.ref_id IS NULL
           OR NEW.ref_id !~ '^[0-9]+$'
        THEN
          RETURN NEW;
        END IF;

        member_id := NEW.ref_id::bigint;
        SELECT c.owner_id,c.account_id
          INTO owner_id_v,account_id_v
        FROM prospect_campaign_members m
        JOIN prospect_campaigns c ON c.id=m.campaign_id
        WHERE m.id=member_id;

        IF account_id_v IS DISTINCT FROM '__owner_outreach__' THEN
          RETURN NEW;
        END IF;

        -- Serialize only owner cold-email queue admissions. No external call is
        -- made while this transaction-scoped lock is held.
        PERFORM pg_advisory_xact_lock(884422922,owner_id_v::integer);

        tz_name := 'Europe/Moscow';
        -- OWNER_OUTREACH_SERVICE_DAY_BY_EXECUTION_V1: a row deferred to the
        -- next window must consume the day on which it will actually be sent,
        -- not the historical day on which it was first queued.
        service_day := CASE
          WHEN NEW.status='sending' THEN timezone(tz_name,NOW())::date
          ELSE timezone(tz_name,COALESCE(NEW.next_attempt_at,NEW.created_at,NOW()))::date
        END;

        SELECT min(timezone(tz_name,m.queued_at)::date)
          INTO first_service_day
        FROM prospect_campaign_members m
        JOIN prospect_campaigns c ON c.id=m.campaign_id
        WHERE c.owner_id=owner_id_v
          AND c.account_id='__owner_outreach__'
          AND m.queued_at IS NOT NULL;

        age_days := CASE
          WHEN first_service_day IS NULL THEN 0
          ELSE GREATEST(0,service_day-first_service_day)
        END;
        canonical_cap := CASE
          WHEN age_days < {OWNER_OUTREACH_STAGE1_DAYS} THEN {OWNER_OUTREACH_STAGE1_DAILY}
          WHEN age_days < {OWNER_OUTREACH_STAGE2_END_DAY} THEN {OWNER_OUTREACH_STAGE2_DAILY}
          ELSE {OWNER_OUTREACH_HARD_MAX_DAILY}
        END;

        SELECT daily_cap INTO snapshot_cap
        FROM prospect_owner_daily_limits
        WHERE owner_id=owner_id_v AND service_date=service_day;

        -- OWNER_OUTREACH_CURRENT_ROW_EXCLUDED_V1: on UPDATE the current
        -- queued row already exists in email_queue. Excluding NEW.id prevents
        -- the 20th allowed row from being mistaken for a 21st row.
        SELECT count(*)::integer INTO active_today
        FROM email_queue q
        JOIN prospect_campaign_members m ON m.id=CASE
          WHEN q.ref_type='prospect_campaign_member' AND q.ref_id ~ '^[0-9]+$'
          THEN q.ref_id::bigint ELSE -1 END
        JOIN prospect_campaigns c ON c.id=m.campaign_id
        WHERE c.owner_id=owner_id_v
          AND c.account_id='__owner_outreach__'
          AND q.status IN ('queued','sending','sent')
          AND q.id IS DISTINCT FROM NEW.id
          AND CASE
                WHEN q.status='sent' THEN timezone(tz_name,q.sent_at)::date
                WHEN q.status='sending' THEN timezone(tz_name,q.updated_at)::date
                ELSE timezone(tz_name,COALESCE(q.next_attempt_at,q.created_at))::date
              END = service_day;

        -- SNAPSHOT_MISSING_FAIL_LOWER: if today's durable snapshot disappeared
        -- after traffic already started, do not reconstruct a higher same-day cap.
        -- The next service day may advance normally before its first queue row.
        effective_cap := LEAST(
          canonical_cap,
          CASE
            WHEN snapshot_cap IS NULL AND active_today > 0 THEN {OWNER_OUTREACH_STAGE1_DAILY}
            ELSE COALESCE(snapshot_cap,canonical_cap)
          END
        );

        IF active_today >= effective_cap THEN
          RAISE EXCEPTION 'OWNER_OUTREACH_DAILY_VOLUME_GUARD_V3 cap=% active=%',
            effective_cap,active_today
            USING ERRCODE='check_violation';
        END IF;

        RETURN NEW;
      END
      $guard$
    """


def _healthy(db) -> bool:
    return bool(db.execute(text("""
      SELECT
        EXISTS(
          SELECT 1
          FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
          WHERE t.tgrelid='public.prospect_owner_daily_limits'::regclass
            AND t.tgname=:snapshot
            AND t.tgenabled <> 'D' AND NOT t.tgisinternal
            AND pg_get_functiondef(p.oid) LIKE '%age_days < 6%'
            AND pg_get_functiondef(p.oid) LIKE '%THEN 10%'
            AND pg_get_functiondef(p.oid) LIKE '%age_days < 7%'
            AND pg_get_functiondef(p.oid) LIKE '%THEN 15%'
            AND pg_get_functiondef(p.oid) LIKE '%ELSE 20%'
            AND pg_get_functiondef(p.oid) LIKE '%OLD.daily_cap%'
        )
        AND EXISTS(
          SELECT 1
          FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
          WHERE t.tgrelid='public.email_queue'::regclass
            AND t.tgname=:queue
            AND t.tgenabled <> 'D' AND NOT t.tgisinternal
            AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_DAILY_VOLUME_GUARD_V3%'
            AND pg_get_functiondef(p.oid) LIKE '%pg_advisory_xact_lock%'
            AND pg_get_functiondef(p.oid) LIKE '%age_days < 6%'
            AND pg_get_functiondef(p.oid) LIKE '%THEN 10%'
            AND pg_get_functiondef(p.oid) LIKE '%age_days < 7%'
            AND pg_get_functiondef(p.oid) LIKE '%THEN 15%'
            AND pg_get_functiondef(p.oid) LIKE '%ELSE 20%'
            AND pg_get_functiondef(p.oid) LIKE '%q.status IN (%'
            AND pg_get_functiondef(p.oid) LIKE '%SNAPSHOT_MISSING_FAIL_LOWER%'
            AND pg_get_functiondef(p.oid) LIKE '%snapshot_cap IS NULL AND active_today > 0%'
            AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_CURRENT_ROW_EXCLUDED_V1%'
            AND pg_get_functiondef(p.oid) LIKE '%q.id IS DISTINCT FROM NEW.id%'
            AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_SERVICE_DAY_BY_EXECUTION_V1%'
            AND pg_get_functiondef(p.oid) LIKE '%COALESCE(q.next_attempt_at,q.created_at)%'
            AND pg_get_functiondef(p.oid) LIKE '%queued%'
            AND pg_get_functiondef(p.oid) LIKE '%sending%'
            AND pg_get_functiondef(p.oid) LIKE '%sent%'
        )
        AND NOT EXISTS(
          SELECT 1 FROM pg_trigger t
          WHERE t.tgrelid='public.prospect_owner_daily_limits'::regclass
            AND NOT t.tgisinternal
            AND t.tgname IN (
              'trg_owner_daily_limit_guard_v1',
              'trg_zz_owner_daily_limit_guard_v2'
            )
        )
    """), {"snapshot": SNAPSHOT_TRIGGER, "queue": QUEUE_TRIGGER}).scalar())


def _snapshot_drift_rows(db) -> list[dict]:
    rows = db.execute(text("""
      SELECT l.owner_id,l.service_date,l.daily_cap,l.timezone,
             (
               SELECT min(timezone(COALESCE(NULLIF(l.timezone,''),'Europe/Moscow'),m.queued_at)::date)
               FROM prospect_campaign_members m
               JOIN prospect_campaigns c ON c.id=m.campaign_id
               WHERE c.owner_id=l.owner_id
                 AND c.account_id='__owner_outreach__'
                 AND m.queued_at IS NOT NULL
             ) AS first_service_day
      FROM prospect_owner_daily_limits l
      ORDER BY l.service_date,l.owner_id
    """)).mappings().all()
    out=[]
    for raw in rows:
        row=dict(raw)
        first=row.get("first_service_day")
        service=row.get("service_date")
        age=max(0,(service-first).days) if first and service else 0
        expected=canonical_cap(age)
        if int(row.get("daily_cap") or 0) != int(expected):
            row["age_days"]=age
            row["expected_cap"]=expected
            out.append(row)
    return out


def _ensure_active_daily_snapshots(db) -> list[dict]:
    """Create today's fail-lower quota snapshot before any feeder can enqueue mail.

    The DB queue trigger is still authoritative even when a snapshot is absent,
    but persisting the service-day cap up front makes plan/fact health deterministic
    and removes the historical window where the snapshot appeared only after sends.
    Existing rows are never raised or rewritten here.
    """
    rows=db.execute(text("""
      SELECT DISTINCT c.owner_id,
             timezone('Europe/Moscow',NOW())::date AS service_date,
             (
               SELECT min(timezone('Europe/Moscow',m.queued_at)::date)
               FROM prospect_campaign_members m
               JOIN prospect_campaigns c2 ON c2.id=m.campaign_id
               WHERE c2.owner_id=c.owner_id
                 AND c2.account_id='__owner_outreach__'
                 AND m.queued_at IS NOT NULL
             ) AS first_service_day,
             (
               SELECT count(*)
               FROM prospect_campaign_members m
               JOIN prospect_campaigns c2 ON c2.id=m.campaign_id
               WHERE c2.owner_id=c.owner_id
                 AND c2.account_id='__owner_outreach__'
                 AND m.queued_at IS NOT NULL
                 AND timezone('Europe/Moscow',m.queued_at)::date=timezone('Europe/Moscow',NOW())::date
             ) AS activity_today
      FROM prospect_campaigns c
      WHERE c.status='active' AND c.account_id='__owner_outreach__'
      ORDER BY c.owner_id
    """)).mappings().all()
    created=[]
    for raw in rows:
        row=dict(raw)
        service=row.get('service_date')
        first=row.get('first_service_day')
        age=max(0,(service-first).days) if service and first else 0
        cap=canonical_cap(age)
        # If today's snapshot disappeared after activity already started, rebuild
        # conservatively. A missing row must never increase the same-day ceiling.
        if int(row.get('activity_today') or 0)>0:
            cap=min(cap,OWNER_OUTREACH_STAGE1_DAILY)
        inserted=db.execute(text("""
          INSERT INTO prospect_owner_daily_limits
            (owner_id,service_date,daily_cap,timezone,created_at,updated_at)
          VALUES(:o,:d,:cap,'Europe/Moscow',NOW(),NOW())
          ON CONFLICT(owner_id,service_date) DO NOTHING
          RETURNING owner_id,service_date,daily_cap
        """),{'o':int(row['owner_id']),'d':service,'cap':int(cap)}).mappings().first()
        if inserted:
            created.append(dict(inserted))
    return created


def _missing_active_snapshot_count(db) -> int:
    return int(db.execute(text("""
      SELECT count(DISTINCT c.owner_id)
      FROM prospect_campaigns c
      WHERE c.status='active' AND c.account_id='__owner_outreach__'
        AND NOT EXISTS (
          SELECT 1 FROM prospect_owner_daily_limits l
          WHERE l.owner_id=c.owner_id
            AND l.service_date=timezone('Europe/Moscow',NOW())::date
        )
    """)).scalar() or 0)


def ensure() -> dict:
    db = SessionLocal()
    changed = False
    try:
        if _healthy(db):
            created=_ensure_active_daily_snapshots(db)
            if created:
                db.commit()
            else:
                db.rollback()
            return {"state": "ok", "changed": bool(created),
                    "snapshots_created": created,
                    "snapshot_trigger": SNAPSHOT_TRIGGER, "queue_trigger": QUEUE_TRIGGER}

        # V3 is the only DB owner of volume policy. Remove historical
        # triggers/functions before reinstalling it so PostgreSQL cannot execute
        # multiple competing BEFORE guards on the same row.
        for trigger_name in (
            "trg_owner_daily_limit_guard_v1",
            "trg_zz_owner_daily_limit_guard_v2",
        ):
            db.execute(text(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON prospect_owner_daily_limits"
            ))
        for fn_name in (
            "boris_owner_daily_limit_guard_v1()",
            "boris_owner_daily_limit_guard_v2()",
        ):
            db.execute(text(f"DROP FUNCTION IF EXISTS {fn_name}"))

        db.execute(text(_snapshot_guard_sql()))
        db.execute(text(f"DROP TRIGGER IF EXISTS {SNAPSHOT_TRIGGER} ON prospect_owner_daily_limits"))
        db.execute(text(f"""
          CREATE TRIGGER {SNAPSHOT_TRIGGER}
          BEFORE INSERT OR UPDATE OF daily_cap,service_date,owner_id,timezone
          ON prospect_owner_daily_limits
          FOR EACH ROW EXECUTE FUNCTION boris_owner_daily_limit_safety_v3()
        """))

        db.execute(text(_queue_guard_sql()))
        db.execute(text(f"DROP TRIGGER IF EXISTS {QUEUE_TRIGGER} ON email_queue"))
        db.execute(text(f"""
          CREATE TRIGGER {QUEUE_TRIGGER}
          BEFORE INSERT OR UPDATE OF ref_type,ref_id,status,created_at
          ON email_queue
          FOR EACH ROW EXECUTE FUNCTION boris_owner_outreach_queue_volume_v3()
        """))

        # Clamp existing snapshots through the authoritative final trigger, then
        # create today's row for every active owner-outreach campaign before the
        # worker can reach the feeder.
        db.execute(text("UPDATE prospect_owner_daily_limits SET daily_cap=daily_cap"))
        created=_ensure_active_daily_snapshots(db)
        db.commit()
        changed = True

        if not _healthy(db):
            raise RuntimeError("owner_outreach_volume_safety_not_healthy_after_install")
        db.rollback()
        return {"state": "ok", "changed": changed,
                "snapshots_created": created,
                "snapshot_trigger": SNAPSHOT_TRIGGER, "queue_trigger": QUEUE_TRIGGER}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def health() -> dict:
    db = SessionLocal()
    try:
        ok = _healthy(db)
        missing_snapshots=_missing_active_snapshot_count(db) if ok else 0
        cap_rows = [dict(r) for r in db.execute(text("""
          SELECT owner_id,service_date,daily_cap,timezone,updated_at
          FROM prospect_owner_daily_limits
          ORDER BY service_date DESC,owner_id
          LIMIT 20
        """)).mappings().all()]
        db.rollback()
        return {
            "state": "ok" if ok and missing_snapshots==0 else ("degraded" if ok else "critical"),
            # Missing snapshot is self-healable and queue safety is still enforced
            # by the DB-final canonical guard, so do not wake the owner for the
            # sub-minute window before the worker creates today's row.
            "healthy": ok,
            "snapshot_ready": missing_snapshots==0,
            "missing_active_snapshots": missing_snapshots,
            "daily_limits": cap_rows,
        }
    finally:
        db.close()
