#!/usr/bin/env python3

"""BORIS Factory L runner — FACTORY_L_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import launch_supervisor as L


FACTORY_L_V1=True


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        items=L.build(db)

        statuses=Counter(
            x["health_status"]
            for x in items
        )

        print("=== FACTORY L ===")
        print(
            f"launched_accounts={len(items)}"
        )

        print()
        print("--- HEALTH ---")

        for status,n in sorted(
            statuses.items()
        ):
            print(
                "%-24s %s"
                % (status,n)
            )

        print()
        print("--- ACCOUNTS ---")

        for x in items:
            print(
                "%-46s %-20s sched=%s/%s unresolved=%s waiting=%s confirm=%s prov=%s reasons=%s"
                % (
                    x["account_id"][:46],
                    x["health_status"],
                    x["schedule_present"],
                    x["schedule_required"],
                    x["unresolved_internal"],
                    x["waiting_facts"],
                    x["pending_confirmations"],
                    x["provisioning_status"],
                    ",".join(x["reasons"])
                    or "-",
                )
            )

        if not args.apply:
            db.rollback()
            print()
            print(
                "DRY RUN: no DB writes"
            )
            return 0

        result=L.apply(db)
        db.commit()

        print()
        print("--- APPLY ---")
        print(
            f"inserted={result['inserted']}"
        )
        print(
            f"updated={result['updated']}"
        )
        print(
            f"unchanged={result['unchanged']}"
        )
        print(
            f"events={result['events']}"
        )
        print("APPLY=PASS")

        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    raise SystemExit(main())
