"""BORIS Factory G — FACTORY_G_V1.

Stage 1:
    provisioning planner + persistent state.

Default = DRY RUN.

--apply writes ONLY:
    factory_provisioning_state
    factory_provisioning_events
"""

import argparse
import fcntl
import json
import os
import sys

from collections import Counter

from app.db.session import SessionLocal
from app.factory import provisioning as P


LOCK="/tmp/boris_factory_g.lock"


def lock():
    fh=open(
        LOCK,
        "w",
        encoding="utf-8",
    )

    try:
        fcntl.flock(
            fh.fileno(),
            fcntl.LOCK_EX
            | fcntl.LOCK_NB,
        )

    except BlockingIOError:
        print(
            "SKIP: Factory G already running"
        )
        return None

    fh.write(
        str(os.getpid())
    )

    fh.flush()

    return fh


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    ap.add_argument(
        "--account",
        default=None,
    )

    args=ap.parse_args()

    lk=lock()

    if lk is None:
        return 0

    db=SessionLocal()

    try:
        rows=P.launch_rows(db)

        if args.account:
            rows=[
                row
                for row in rows
                if row["account_id"]
                == args.account
            ]

        plans=[
            P.build_plan(
                db,
                row,
            )
            for row in rows
        ]

        statuses=Counter(
            x["provisioning_status"]
            for x in plans
        )

        print("=== FACTORY G ===")
        print(
            "accounts_seen=",
            len(plans),
        )

        print("\n--- PROVISIONING STATUS ---")

        for status,n in (
            statuses.most_common()
        ):
            print(
                "%-22s %s"
                % (
                    status,
                    n,
                )
            )

        ready=[
            x for x in plans
            if x[
                "ready_internal"
            ]
        ]

        print("\n--- READY INTERNAL ---")
        print(len(ready))

        for x in ready:
            print(
                "%-38s products=%d resolved=%d schedules=%d readiness=%d%%"
                % (
                    x["account_id"][:38],
                    x["product_count"],
                    x["resolved_values"],
                    x["active_schedules"],
                    x["readiness_pct"],
                )
            )

        blocked=[
            x for x in plans
            if x["blockers"]
        ]

        print("\n--- BLOCKED ---")
        print(len(blocked))

        for x in blocked[:30]:
            print(
                "%-38s %-18s %s"
                % (
                    x["account_id"][:38],
                    x["provisioning_status"],
                    ",".join(
                        "%s:%s"
                        % (
                            b["kind"],
                            b["field"],
                        )
                        for b in x[
                            "blockers"
                        ]
                    ),
                )
            )

        with open(
            "/tmp/factory_g_plan.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                plans,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        if not args.apply:
            print(
                "\nDRY RUN: no DB writes"
            )
            return 0

        outcomes=Counter()

        for plan in plans:
            result=P.upsert_state(
                db,
                plan,
            )

            outcomes[
                result
            ] += 1

        db.commit()

        print("\n--- APPLY OUTCOMES ---")

        for k,v in outcomes.most_common():
            print(
                "%-15s %s"
                % (k,v)
            )

        print("APPLY=PASS")

        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
        lk.close()


if __name__=="__main__":
    sys.exit(main())
