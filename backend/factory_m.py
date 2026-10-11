#!/usr/bin/env python3

"""BORIS Factory M runner — FACTORY_M_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import attention_router as M


FACTORY_M_V1=True


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        items=M.build(db)

        print("=== FACTORY M ===")
        print("attention_items=",len(items))

        print()
        print("--- ROUTES ---")

        routes=Counter(
            x["route"]
            for x in items
        )

        for k,n in sorted(routes.items()):
            print("%-20s %s" % (k,n))

        print()
        print("--- ISSUE TYPES ---")

        types=Counter(
            x["issue_type"]
            for x in items
        )

        for k,n in sorted(types.items()):
            print("%-24s %s" % (k,n))

        print()
        print("--- QUEUE ---")

        for x in items:
            print(
                "P%-3s %-44s %-22s %-16s %s"
                % (
                    x["priority"],
                    x["account_id"][:44],
                    x["issue_type"],
                    x["route"],
                    x["detail"],
                )
            )

        if not args.apply:
            db.rollback()

            print()
            print("DRY RUN: no DB writes")
            return 0

        result=M.apply(db)
        db.commit()

        print()
        print("--- APPLY ---")
        print("inserted=",result["inserted"])
        print("updated=",result["updated"])
        print("unchanged=",result["unchanged"])
        print("resolved=",result["resolved"])
        print("events=",result["events"])
        print("APPLY=PASS")

        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    raise SystemExit(main())
