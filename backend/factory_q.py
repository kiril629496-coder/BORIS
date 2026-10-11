#!/usr/bin/env python3
"""BORIS Factory Q runner."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import outcome_loop as Q


FACTORY_Q_V1=True


def main():
    parser=argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args=parser.parse_args()

    db=SessionLocal()

    try:
        decisions=Q.plan(db)

        print("=== FACTORY Q ===")
        print(
            "attention_items=",
            len(decisions),
        )

        counts=Counter(
            x["result"]
            for x in decisions
        )

        print()
        print("--- OUTCOMES ---")

        if counts:
            for key in sorted(counts):
                print(
                    f"{key:28s}",
                    counts[key],
                )
        else:
            print("NONE                         0")

        print()
        print("--- ITEMS ---")

        for d in decisions:
            print(
                f'{d["account_id"][:44]:44s} '
                f'{d["issue_type"][:22]:22s} '
                f'{d["result"]:28s} '
                f'reason={d["reason"]}'
            )

        if not args.apply:
            print()
            print(
                "DRY RUN: no persistent Q writes"
            )
            return

        result=Q.apply(
            db,
            decisions,
        )

        db.commit()

        print()
        print("--- APPLY ---")

        for key in (
            "inserted",
            "updated",
            "unchanged",
            "events",
        ):
            print(
                f"{key}=",
                result[key],
            )

        print("APPLY=PASS")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
