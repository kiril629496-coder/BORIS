"""BORIS Factory scheduler watchdog — FACTORY_F_WATCHDOG_V1.

Checks expected schedule executions against factory_schedule_runs.

Does NOT:
- execute a business job
- retry a failed job
- send client messages
- spend money
- alter Factory schedules

Only writes:
    factory_schedule_health
"""

import argparse

from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.session import SessionLocal

import factory_schedule_dispatcher as DISP


GRACE_MINUTES = 3
LOOKBACK_MINUTES = 180


def expected_minutes(
    cron_expr,
    start,
    end,
):
    """Generate minute buckets in server-local timezone."""

    out=[]

    cur=start.replace(
        second=0,
        microsecond=0,
    )

    end=end.replace(
        second=0,
        microsecond=0,
    )

    while cur <= end:
        if DISP.cron_matches(
            cron_expr,
            cur,
        ):
            out.append(
                DISP.minute_key(cur)
            )

        cur += timedelta(minutes=1)

    return out


def classify(run):
    if run is None:
        return (
            "MISSED",
            "no execution-ledger row after grace period",
        )

    status=run.get("status")
    rc=run.get("return_code")

    if status=="SUCCESS" and rc==0:
        return (
            "SUCCESS",
            "execution completed successfully",
        )

    if status=="FAILED":
        return (
            "FAILED",
            "business command returned non-zero",
        )

    if status=="RUNNING":
        return (
            "STUCK",
            "execution remained RUNNING after grace period",
        )

    if status=="RESERVED":
        return (
            "STUCK",
            "execution remained RESERVED after grace period",
        )

    return (
        "UNKNOWN",
        "unexpected ledger status %r" % status,
    )


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--lookback",
        type=int,
        default=LOOKBACK_MINUTES,
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        now=datetime.now().astimezone()

        observation=db.execute(text("""
            SELECT observation_started_at
            FROM factory_scheduler_state
            WHERE singleton=TRUE
        """)).scalar()

        if observation is None:
            raise SystemExit(
                "STOP: observation watermark missing"
            )

        # Do not judge the last GRACE_MINUTES yet.
        eligible_end=(
            now
            - timedelta(
                minutes=GRACE_MINUTES
            )
        )

        requested_start=(
            eligible_end
            - timedelta(
                minutes=max(
                    args.lookback,
                    1,
                )
            )
        )

        observation_local=(
            observation.astimezone(
                now.tzinfo
            )
        )

        start=max(
            requested_start,
            observation_local,
        )

        # Observation may begin inside the grace period.
        empty_window = start > eligible_end

        schedules=db.execute(text("""
            SELECT
                account_id,
                job_kind,
                cron_expr,
                created_at
            FROM factory_schedule
            WHERE enabled
            ORDER BY job_kind,account_id
        """)).mappings().all()

        checks=[]

        for schedule in schedules:
            if empty_window:
                expected=[]
            else:
                # BORIS_K8_1_TEMPORAL_BOUNDARY
                #
                # A schedule cannot have missed an execution
                # before the schedule itself existed.
                schedule_created_at = schedule["created_at"]

                if (
                    schedule_created_at is not None
                    and schedule_created_at.tzinfo is None
                ):
                    schedule_created_at = (
                        schedule_created_at.replace(
                            tzinfo=start.tzinfo
                        )
                    )

                schedule_start = max(
                    start,
                    schedule_created_at
                    if schedule_created_at is not None
                    else start,
                )

                expected=expected_minutes(
                    schedule["cron_expr"],
                    schedule_start,
                    eligible_end,
                )

            for minute in expected:
                run=db.execute(text("""
                    SELECT
                        status,
                        return_code,
                        started_at,
                        finished_at
                    FROM factory_schedule_runs
                    WHERE account_id=:a
                      AND job_kind=:k
                      AND scheduled_minute=:m
                """),{
                    "a":schedule["account_id"],
                    "k":schedule["job_kind"],
                    "m":minute,
                }).mappings().first()

                health,reason=classify(
                    dict(run)
                    if run
                    else None
                )

                checks.append({
                    "account_id":
                        schedule["account_id"],
                    "job_kind":
                        schedule["job_kind"],
                    "scheduled_minute":
                        minute,
                    "run_status":
                        run["status"]
                        if run else None,
                    "return_code":
                        run["return_code"]
                        if run else None,
                    "health_status":
                        health,
                    "reason":
                        reason,
                })

        for c in checks:
            db.execute(text("""
                INSERT INTO factory_schedule_health (
                    account_id,
                    job_kind,
                    scheduled_minute,
                    expected,
                    run_status,
                    return_code,
                    health_status,
                    reason,
                    checked_at
                )
                VALUES (
                    :account_id,
                    :job_kind,
                    :scheduled_minute,
                    TRUE,
                    :run_status,
                    :return_code,
                    :health_status,
                    :reason,
                    now()
                )
                ON CONFLICT(
                    account_id,
                    job_kind,
                    scheduled_minute
                )
                DO UPDATE SET
                    run_status=EXCLUDED.run_status,
                    return_code=EXCLUDED.return_code,
                    health_status=EXCLUDED.health_status,
                    reason=EXCLUDED.reason,
                    checked_at=now()
            """),c)

        db.commit()

        from collections import Counter

        counts=Counter(
            x["health_status"]
            for x in checks
        )

        print("=== FACTORY SCHEDULE WATCHDOG ===")
        print("observation_started=",observation)
        print("window_start=",start)
        print("window_end=",eligible_end)
        print("empty_window=",empty_window)
        print("expected_checks=",len(checks))

        print("\n--- HEALTH ---")

        for k in (
            "SUCCESS",
            "FAILED",
            "MISSED",
            "STUCK",
            "UNKNOWN",
        ):
            print(
                "%-12s %s"
                % (
                    k,
                    counts[k],
                )
            )

        bad=[
            x for x in checks
            if x["health_status"]
            not in ("SUCCESS",)
        ]

        print("\n--- PROBLEMS ---")
        print(len(bad))

        for x in bad[:50]:
            print(
                "%-36s %-10s %s %s"
                % (
                    x["account_id"][:36],
                    x["job_kind"],
                    x["scheduled_minute"],
                    x["health_status"],
                )
            )

        # Watchdog itself reports facts.
        # Nonzero exit only for internal/watchdog failure,
        # NOT because a business schedule failed.
        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    raise SystemExit(main())
