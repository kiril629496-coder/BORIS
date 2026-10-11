#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from sqlalchemy import text
from app.db.session import SessionLocal

def get_rows(db, sql):
    return db.execute(text(sql)).mappings().all()

def section(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)

db = SessionLocal()

try:
    section("BORIS AI CONTROL CENTER")

    total = db.execute(text("""
        select
            count(*) calls,
            round(coalesce(sum(cost_rub),0)::numeric,2) rub
        from api_usage
        where created_at >= now() - interval '24 hours'
    """)).mappings().one()

    print("24h calls=%s | cost=%s RUB" % (total["calls"], total["rub"]))

    section("TOP OPERATIONS — 24H")

    for r in get_rows(db, """
        select
            coalesce(operation,'(NULL)') operation,
            count(*) calls,
            round(sum(cost_rub)::numeric,2) rub
        from api_usage
        where created_at >= now() - interval '24 hours'
        group by 1
        order by rub desc
        limit 25
    """):
        print("%8s RUB | %5s calls | %s" % (r["rub"], r["calls"], r["operation"]))

    section("CLIENTS — 24H")

    for r in get_rows(db, """
        select
            account_id,
            count(*) calls,
            round(sum(cost_rub)::numeric,2) rub,
            count(*) filter (where user_id is null) missing_user
        from api_usage
        where created_at >= now() - interval '24 hours'
        group by account_id
        order by rub desc
        limit 40
    """):
        print(
            "%8s RUB | %5s calls | no_user=%s | %s"
            % (r["rub"], r["calls"], r["missing_user"], r["account_id"])
        )

    section("IMAGES — 24H")

    for r in get_rows(db, """
        select
            model,
            operation,
            sum(images) images,
            round(sum(cost_rub)::numeric,2) rub,
            round((sum(cost_rub)/nullif(sum(images),0))::numeric,2) rub_per_image
        from api_usage
        where created_at >= now() - interval '24 hours'
          and coalesce(images,0) > 0
        group by model,operation
        order by rub desc
    """):
        print(
            "%7s RUB | %4s img | %5s RUB/img | %-18s | %s"
            % (
                r["rub"],
                r["images"],
                r["rub_per_image"],
                r["model"],
                r["operation"],
            )
        )

    section("WHISPER — 24H")

    whisper = get_rows(db, """
        select *
        from ai_cc_whisper
        where day >= current_date - 1
        order by cost_rub desc
    """)

    if not whisper:
        print("NO WHISPER USAGE")

    for r in whisper:
        print(
            "%7s RUB | %7s min | %s | %s"
            % (
                r["cost_rub"],
                r["audio_minutes"],
                r["account_id"],
                r["operation"],
            )
        )

    section("GUARDS TODAY")

    guards = get_rows(db, """
        select *
        from ai_cc_guard
        where day=current_date
        order by used_percent desc
    """)

    if not guards:
        print("NO GUARD ACTIVITY TODAY")

    for r in guards:
        print(
            "%6s%% | %8s/%8s RUB | %-28s | %s"
            % (
                r["used_percent"],
                r["guard_spent_rub"],
                r["limit_rub"],
                r["scope_key"],
                r["module"],
            )
        )

    section("ATTRIBUTION PROBLEMS — 24H")

    problems = get_rows(db, """
        select *
        from ai_cc_attribution
        where attribution_state <> 'OK'
          and last_seen >= now() - interval '24 hours'
        order by cost_rub desc
        limit 50
    """)

    if not problems:
        print("NO RECENT ATTRIBUTION PROBLEMS")

    for r in problems:
        print(
            "%8s RUB | %5s rows | %-24s | %s"
            % (
                r["cost_rub"],
                r["rows"],
                r["attribution_state"],
                r["account_id"],
            )
        )

    section("ALERTS")

    alerts = get_rows(db, """
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

    if not alerts:
        print("NO ACTIVE ALERTS")

    for r in alerts:
        print(
            "%-8s | %-12s | %-30s | %s"
            % (
                r["severity"],
                r["alert_type"],
                r["subject"],
                r["reason"],
            )
        )

finally:
    db.close()
