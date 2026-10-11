#!/usr/bin/env python3

"""BORIS Factory O runner — FACTORY_O_V1."""

import argparse
from collections import Counter

from app.db.session import SessionLocal
from app.factory import remediation_policy as O


FACTORY_O_V1=True


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        items=O.build(db)

        decisions=Counter(
            x["decision"]
            for x in items
        )

        print("=== FACTORY O ===")
        print(
            f"attention_items={len(items)}"
        )

        print()
        print("--- POLICY DECISIONS ---")

        for decision,n in sorted(
            decisions.items()
        ):
            print(
                "%-20s %s"
                % (decision,n)
            )

        if not decisions:
            print(
                "NONE                 0"
            )

        print()
        print("--- GUARDS ---")

        for x in items:
            print(
                "%-44s %-22s %-16s attempts=%s rollback=%s external=%s reason=%s"
                % (
                    x["account_id"][:44],
                    x["issue_type"],
                    x["decision"],
                    x["attempts"],
                    x["rollback_required"],
                    x["external_effects_allowed"],
                    x["reason"],
                )
            )

        if not args.apply:
            db.rollback()

            print()
            print(
                "DRY RUN: no persistent policy/guard writes"
            )

            return 0

        policy=O.sync_policy(db)
        guards=O.apply_guard_state(db)

        db.commit()

        print()
        print("--- POLICY APPLY ---")

        print(
            f"policy_inserted={policy['inserted']}"
        )

        print(
            f"policy_updated={policy['updated']}"
        )

        print(
            f"policy_unchanged={policy['unchanged']}"
        )

        print(
            f"policy_events={policy['events']}"
        )

        print()
        print("--- GUARD APPLY ---")

        print(
            f"guard_inserted={guards['inserted']}"
        )

        print(
            f"guard_updated={guards['updated']}"
        )

        print(
            f"guard_unchanged={guards['unchanged']}"
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
