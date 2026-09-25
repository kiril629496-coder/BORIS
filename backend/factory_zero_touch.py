"""ZERO_TOUCH_LAUNCH_V1 — product launch state runner."""

import argparse
import json
from collections import Counter

from app.db.session import SessionLocal
from app.factory import client_launch as L


def main():
    p=argparse.ArgumentParser()

    p.add_argument(
        "--apply",
        action="store_true",
    )

    args=p.parse_args()

    db=SessionLocal()

    try:
        rows=L.build_all(
            db
        )

        print(
            "=== ZERO-TOUCH CLIENT LAUNCH ==="
        )

        print(
            "accounts:",
            len(rows),
        )

        statuses=Counter(
            x["launch_status"]
            for x in rows
        )

        print()
        print("--- STATUSES ---")

        for status,n in sorted(
            statuses.items(),
            key=lambda x:
                (-x[1],x[0])
        ):
            print(
                "%-32s %d"
                % (
                    status,
                    n,
                )
            )

        print()
        print("--- ACCOUNTS ---")

        for row in rows:
            human=sum(
                1
                for x in row[
                    "blockers"
                ]
                if x.get(
                    "human_required"
                )
            )

            system=sum(
                1
                for x in row[
                    "blockers"
                ]
                if not x.get(
                    "human_required"
                )
            )

            print(
                "%-42s %-28s ready=%-5s "
                "readiness=%3s%% pending=%-2s "
                "sched=%s/%s human=%s system=%s"
                % (
                    row[
                        "account_id"
                    ][:42],

                    row[
                        "launch_status"
                    ],

                    row[
                        "launch_ready"
                    ],

                    row[
                        "readiness_pct"
                    ],

                    row[
                        "pending_decisions"
                    ],

                    row[
                        "active_schedules"
                    ],

                    row[
                        "required_schedules"
                    ],

                    human,
                    system,
                )
            )

        with open(
            "/root/BORIS/backend/run/factory_zero_touch_preview.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                rows,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        print()
        print(
            "summary=",
            json.dumps(
                L.summary(rows),
                ensure_ascii=False,
                default=str,
            )
        )

        if not args.apply:
            print()
            print(
                "DRY RUN: launch DB unchanged"
            )
            return

        result=L.apply_all(
            db,
            rows,
        )

        db.commit()

        print()
        print(
            "apply=",
            json.dumps(
                result,
                ensure_ascii=False,
            )
        )

        with open(
            "/root/BORIS/backend/run/factory_zero_touch_result.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                {
                    "apply":
                        result,

                    "summary":
                        L.summary(
                            rows
                        ),
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    main()
