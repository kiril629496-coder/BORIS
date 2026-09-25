"""BORIS Factory exact schedule dispatcher — FACTORY_F_V1.

Exact cron scheduler with:
- global non-overlap lock
- per-minute deduplication
- execution ledger
- fail-closed duplicate suppression

Business command remains the migrated legacy command template.
"""

import fcntl
import json
import os
import shlex
import subprocess
import sys

from datetime import datetime, timezone

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory.schedule_executor import _mop_owner_enabled


LOCK="/tmp/boris_factory_schedule.lock"


def _number_match(value,expr):
    for token in expr.split(","):
        token=token.strip()

        if token=="*":
            return True

        if token.startswith("*/"):
            step=int(token[2:])

            if step<=0:
                return False

            if value % step == 0:
                return True

            continue

        if "-" in token:
            a,b=token.split("-",1)

            if int(a)<=value<=int(b):
                return True

            continue

        if int(token)==value:
            return True

    return False


def cron_matches(expr,dt):
    p=expr.split()

    if len(p)!=5:
        return False

    minute,hour,dom,month,dow=p

    cron_dow=(dt.weekday()+1)%7

    return (
        _number_match(dt.minute,minute)
        and _number_match(dt.hour,hour)
        and _number_match(dt.day,dom)
        and _number_match(dt.month,month)
        and _number_match(cron_dow,dow)
    )


def acquire_lock():
    fh=open(
        LOCK,
        "w",
        encoding="utf-8",
    )

    try:
        fcntl.flock(
            fh.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError:
        print(
            "SKIP: Factory schedule dispatcher already running"
        )
        return None

    fh.write(str(os.getpid()))
    fh.flush()

    return fh


def minute_key(dt):
    """UTC-aware minute identity for ledger dedup."""

    if dt.tzinfo is None:
        local=dt.astimezone()
    else:
        local=dt

    return local.astimezone(
        timezone.utc
    ).replace(
        second=0,
        microsecond=0,
    )


def reserve_run(
    db,
    account_id,
    job_kind,
    scheduled_minute,
    cron_expr,
    command_template,
):
    row=db.execute(text("""
        INSERT INTO factory_schedule_runs (
            account_id,
            job_kind,
            scheduled_minute,
            cron_expr,
            command_template,
            status,
            created_at
        )
        VALUES (
            :a,:k,:m,:c,:t,
            'RESERVED',
            now()
        )
        ON CONFLICT (
            account_id,
            job_kind,
            scheduled_minute
        )
        DO NOTHING
        RETURNING id
    """),{
        "a":account_id,
        "k":job_kind,
        "m":scheduled_minute,
        "c":cron_expr,
        "t":command_template,
    }).fetchone()

    db.commit()

    return row[0] if row else None


def mark_running(db,run_id):
    db.execute(text("""
        UPDATE factory_schedule_runs
        SET
            status='RUNNING',
            started_at=now()
        WHERE id=:id
          AND status='RESERVED'
    """),{"id":run_id})

    db.commit()


def mark_finished(
    db,
    run_id,
    rc,
    error_text=None,
):
    status=(
        "SUCCESS"
        if rc==0
        else "FAILED"
    )

    db.execute(text("""
        UPDATE factory_schedule_runs
        SET
            status=:s,
            return_code=:rc,
            error_text=:e,
            finished_at=now()
        WHERE id=:id
    """),{
        "id":run_id,
        "s":status,
        "rc":rc,
        "e":error_text,
    })

    db.commit()


def main(now=None):
    lock=acquire_lock()

    if lock is None:
        return 0

    db=SessionLocal()

    try:
        now=now or datetime.now().astimezone()

        rows=db.execute(text("""
            SELECT
                account_id,
                job_kind,
                cron_expr,
                command_template
            FROM factory_schedule
            WHERE enabled
            ORDER BY job_kind,account_id
        """)).mappings().all()

        due=[]
        for row in rows:
            if not cron_matches(row["cron_expr"], now):
                continue
            if row["job_kind"] == "mop_poll" and not _mop_owner_enabled(db, row["account_id"]):
                print("BLOCKED", row["job_kind"], row["account_id"], "mop_owner_disabled")
                continue
            due.append(row)

        key=minute_key(now)

        print(
            "factory_schedule rows=%d due=%d minute=%s"
            % (
                len(rows),
                len(due),
                key.isoformat(),
            )
        )

        final_rc=0

        for row in due:
            account=row["account_id"]
            kind=row["job_kind"]

            run_id=reserve_run(
                db,
                account,
                kind,
                key,
                row["cron_expr"],
                row["command_template"],
            )

            if run_id is None:
                print(
                    "DEDUP",
                    kind,
                    account,
                    key.isoformat(),
                )
                continue

            safe_account=shlex.quote(
                account
            )

            command=row[
                "command_template"
            ].replace(
                "{ACCOUNT_ID}",
                safe_account,
            )

            if "{ACCOUNT_ID}" in command:
                mark_finished(
                    db,
                    run_id,
                    97,
                    "unresolved ACCOUNT_ID template",
                )

                final_rc=1
                continue

            mark_running(
                db,
                run_id,
            )

            print(
                "RUN",
                kind,
                account,
            )

            try:
                r=subprocess.run(
                    command,
                    shell=True,
                )

                rc=r.returncode
                err=None

            except Exception as exc:
                rc=98
                err=(
                    type(exc).__name__
                    + ": "
                    + str(exc)
                )[:1000]

            mark_finished(
                db,
                run_id,
                rc,
                err,
            )

            print(
                "RC",
                kind,
                account,
                rc,
            )

            if rc:
                final_rc=1

        return final_rc

    finally:
        db.close()
        lock.close()


if __name__=="__main__":
    sys.exit(main())
