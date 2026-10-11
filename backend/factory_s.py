#!/usr/bin/env python3

"""BORIS Factory S runner — FACTORY_S_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import supervisor_dashboard as S


FACTORY_S_V1=True


def main():
    parser=argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args=parser.parse_args()

    db=SessionLocal()

    try:
        items=S.plan(db)

        print("=== FACTORY S ===")
        print(
            "supervised_accounts=",
            len(items),
        )

        statuses=Counter(
            x["operational_status"]
            for x in items
        )

        print()
        print("--- OPERATIONAL STATUS ---")

        for status,n in sorted(
            statuses.items()
        ):
            print(
                "%-24s %s"
                % (
                    status,
                    n,
                )
            )

        print()
        print("--- ACCOUNTS ---")

        for x in sorted(
            items,
            key=lambda y:(
                -y["priority"],
                y["account_id"],
            ),
        ):
            print(
                "%-44s %-18s P%-3s owner=%s auto=%s facts=%s recovery=%s esc=%s reason=%s"
                % (
                    x["account_id"][:44],
                    x["operational_status"],
                    x["priority"],
                    x["open_owner_items"],
                    x["open_auto_items"],
                    x["waiting_facts"],
                    x["active_recovery"],
                    x["escalations"],
                    x["reason"],
                )
            )

        if not args.apply:
            db.rollback()

            print()
            print(
                "DRY RUN: no DB writes"
            )

            return 0

        result=S.apply(db)

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
