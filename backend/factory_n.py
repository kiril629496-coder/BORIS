#!/usr/bin/env python3

"""BORIS Factory N runner — FACTORY_N_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import auto_internal_executor as N


FACTORY_N_V1=True


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        items=N.build(db)

        print("=== FACTORY N ===")
        print(
            f"auto_internal_items={len(items)}"
        )

        print()
        print("--- ACTION TYPES ---")

        counts=Counter(
            x["action_type"]
            for x in items
        )

        if not counts:
            print("NONE                     0")

        for action,n in sorted(
            counts.items()
        ):
            print(
                "%-28s %s"
                % (action,n)
            )

        print()
        print("--- EXECUTION PREVIEW ---")

        for x in items:
            print(
                "%-44s %-26s %-22s %s"
                % (
                    x["account_id"][:44],
                    x["issue_type"],
                    x["action_type"],
                    x["result"].get(
                        "assessment"
                    ),
                )
            )

        if not args.apply:
            db.rollback()

            print()
            print(
                "DRY RUN: no DB writes"
            )

            return 0

        result=N.apply(db)
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
