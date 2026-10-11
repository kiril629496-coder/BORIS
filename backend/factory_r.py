#!/usr/bin/env python3

"""BORIS Factory R runner — FACTORY_R_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import outcome_bridge as R


FACTORY_R_V1=True


def main():
    parser=argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args=parser.parse_args()

    db=SessionLocal()

    try:
        items=R.plan(db)

        print("=== FACTORY R ===")
        print(
            f"outcome_items={len(items)}"
        )

        counts=Counter(
            x["bridge_action"]
            for x in items
        )

        print()
        print("--- BRIDGE ACTIONS ---")

        if not counts:
            print(
                "NONE                         0"
            )

        for action,n in sorted(
            counts.items()
        ):
            print(
                "%-32s %s"
                % (action,n)
            )

        print()
        print("--- ITEMS ---")

        for x in items:
            print(
                "%-44s %-22s %-28s route=%s outcome=%s"
                % (
                    x["account_id"][:44],
                    x["issue_type"][:22],
                    x["bridge_action"],
                    x["route"],
                    x["outcome"],
                )
            )

        if not args.apply:
            db.rollback()

            print()
            print(
                "DRY RUN: no persistent R writes"
            )

            return 0

        result=R.apply(db)
        db.commit()

        print()
        print("--- APPLY ---")

        for key in (
            "inserted",
            "updated",
            "unchanged",
            "actions",
            "events",
        ):
            print(
                f"{key}={result[key]}"
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
