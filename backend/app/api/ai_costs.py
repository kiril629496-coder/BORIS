from fastapi import APIRouter, Query, Depends
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.ai_costs_private_access import require_ai_costs_private_owner

router = APIRouter(
    prefix="/api/ai-costs",
    tags=["ai-costs"],
 dependencies=[Depends(require_ai_costs_private_owner)])

def rows(db, sql, params=None):
    return [
        dict(x)
        for x in db.execute(
            text(sql),
            params or {},
        ).mappings().all()
    ]

def one(db, sql, params=None):
    x = db.execute(
        text(sql),
        params or {},
    ).mappings().first()
    return dict(x) if x else {}


@router.get("/overview")
def overview(days: int = Query(7, ge=1, le=90)):
    db = SessionLocal()
    try:
        return {
            "status": "ok",

            "totals": one(db, """
                select
                    count(*) calls,
                    round(coalesce(sum(cost_rub),0)::numeric,2) cost_rub,
                    coalesce(sum(images),0) images,
                    round(
                        (coalesce(sum(audio_seconds),0)/60.0)::numeric,
                        2
                    ) audio_minutes
                from api_usage
                where created_at >=
                    now() - make_interval(days => :d)
            """, {"d": days}),

            "today": one(db, """
                select
                    count(*) calls,
                    round(coalesce(sum(cost_rub),0)::numeric,2) cost_rub
                from api_usage
                where created_at >= current_date
            """),

            "month": one(db, """
                select
                    count(*) calls,
                    round(coalesce(sum(cost_rub),0)::numeric,2) cost_rub
                from api_usage
                where created_at >= date_trunc('month', now())
            """),

            "qa": one(db, """
                select
                    coalesce(sum(calls),0) calls,
                    round(coalesce(sum(cost_rub),0)::numeric,2) cost_rub
                from ai_cc_qa
                where day >= current_date - (:d - 1)
            """, {"d": days}),

            "alerts": one(db, """
                select
                    count(*) alerts,
                    count(*) filter(where severity='critical') critical,
                    count(*) filter(where severity='high') high
                from ai_cc_alerts
            """),
        }
    finally:
        db.close()


@router.get("/operations")
def operations(days: int = Query(7, ge=1, le=90)):
    db = SessionLocal()
    try:
        return {
            "status": "ok",
            "items": rows(db, """
                select
                    coalesce(operation,'(NULL)') operation,
                    coalesce(model,'unknown') model,
                    count(*) calls,
                    round(sum(cost_rub)::numeric,2) cost_rub,
                    coalesce(sum(images),0) images,
                    round(
                        (coalesce(sum(audio_seconds),0)/60.0)::numeric,
                        2
                    ) audio_minutes
                from api_usage
                where created_at >=
                    now() - make_interval(days => :d)
                group by operation,model
                order by cost_rub desc
                limit 100
            """, {"d": days})
        }
    finally:
        db.close()


@router.get("/accounts")
def accounts(days: int = Query(7, ge=1, le=90)):
    db = SessionLocal()
    try:
        return {
            "status": "ok",
            "items": rows(db, """
                select
                    u.account_id,
                    a.owner_user_id,
                    count(*) calls,
                    round(sum(u.cost_rub)::numeric,2) cost_rub,
                    count(*) filter(
                        where u.user_id is null
                    ) missing_user_rows
                from api_usage u
                left join accounts a
                  on a.account_id::text=u.account_id::text
                where u.created_at >=
                    now() - make_interval(days => :d)
                group by
                    u.account_id,
                    a.owner_user_id
                order by cost_rub desc
                limit 200
            """, {"d": days})
        }
    finally:
        db.close()


@router.get("/images")
def images(days: int = Query(7, ge=1, le=90)):
    db = SessionLocal()
    try:
        return {
            "status": "ok",
            "items": rows(db, """
                select
                    model,
                    operation,
                    sum(images) images,
                    round(sum(cost_rub)::numeric,2) cost_rub,
                    round(
                        (
                            sum(cost_rub)
                            / nullif(sum(images),0)
                        )::numeric,
                        2
                    ) rub_per_image
                from api_usage
                where created_at >=
                    now() - make_interval(days => :d)
                  and coalesce(images,0)>0
                group by model,operation
                order by cost_rub desc
            """, {"d": days})
        }
    finally:
        db.close()


@router.get("/whisper")
def whisper(days: int = Query(7, ge=1, le=90)):
    db = SessionLocal()
    try:
        return {
            "status": "ok",
            "items": rows(db, """
                select
                    account_id,
                    operation,
                    count(*) calls,
                    round(
                        (sum(audio_seconds)/60.0)::numeric,
                        2
                    ) audio_minutes,
                    round(sum(cost_rub)::numeric,2) cost_rub
                from api_usage
                where created_at >=
                    now() - make_interval(days => :d)
                  and model ilike '%whisper%'
                group by account_id,operation
                order by cost_rub desc
            """, {"d": days})
        }
    finally:
        db.close()


@router.get("/guards")
def guards():
    db = SessionLocal()
    try:
        return {
            "status": "ok",
            "items": rows(db, """
                select *
                from ai_cc_guard
                where day=current_date
                order by used_percent desc
            """)
        }
    finally:
        db.close()


@router.get("/alerts")
def alerts():
    db = SessionLocal()
    try:
        return {
            "status": "ok",
            "items": rows(db, """
                select *
                from ai_cc_alerts
                order by
                    case severity
                        when 'critical' then 1
                        when 'high' then 2
                        when 'warning' then 3
                        else 4
                    end,
                    rub desc
            """)
        }
    finally:
        db.close()


@router.get("/guard-events")
def guard_events(
    days: int = Query(7, ge=1, le=90),
    account_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
):
    """Private owner view: why BORIS blocked/reused/isolated paid AI work."""
    from app.services.ai_guard_audit import recent_events, event_summary
    return {
        "status": "ok",
        "summary": event_summary(days=days, account_id=account_id),
        "items": recent_events(days=days, account_id=account_id, limit=limit),
    }
