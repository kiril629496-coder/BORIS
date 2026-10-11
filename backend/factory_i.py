#!/usr/bin/env python3

"""BORIS Factory I runner — FACTORY_I_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import missing_facts as I


FACTORY_I_V1=True


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        items=I.build(db)

        statuses=Counter(
            x["status"]
            for x in items
        )

        print("=== FACTORY I ===")
        print(f"tracked_fields={len(items)}")

        print()
        print("--- DESIRED STATUS ---")

        for status,n in statuses.items():
            print(
                "%-20s %s"
                % (status,n)
            )

        print()
        print("--- WAITING FACTS ---")

        waiting=[
            x
            for x in items
            if x["status"]=="WAITING_FACT"
        ]

        print(len(waiting))

        for x in waiting:
            print()
            print("ACCOUNT :",x["account_id"])
            print("FIELD   :",x["field"])
            print("FACT    :",x["fact_kind"])
            print("QUESTION:",x["question_text"])
            print("REASON  :",x["reason"])

        if not args.apply:
            db.rollback()
            print()
            print("DRY RUN: no DB writes")
            return 0

        result=I.apply(db)
        db.commit()

        print()
        print("--- APPLY ---")
        print(f"inserted={result['inserted']}")
        print(f"updated={result['updated']}")
        print(f"unchanged={result['unchanged']}")
        print(f"events={result['events']}")

        q=I.queue(db)

        final=Counter(
            x["status"]
            for x in q
        )

        print()
        print("--- PERSISTENT QUEUE ---")

        for status,n in final.items():
            print(
                "%-20s %s"
                % (status,n)
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
