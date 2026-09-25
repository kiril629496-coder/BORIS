#!/usr/bin/env python3
"""BORIS Factory F runner — FACTORY_F_V1."""

import argparse
import json

from app.db.session import SessionLocal
from app.factory import executor as F


FACTORY_F_V1=True


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--apply",action="store_true")
    args=ap.parse_args()

    db=SessionLocal()

    try:
        plan=F.plan(db)

        print("=== FACTORY F ===")
        print("actions:",len(plan))

        for x in plan:
            print(
                "%-44s %-24s before=%r -> after=%r write=%s"
                % (
                    x["account_id"][:44],
                    x["field"],
                    x["current_value"],
                    x["target_value"],
                    (
                        "SKIP_UNRESOLVED"
                        if x.get("unresolved")
                        else x["needs_write"]
                    ),
                )
            )

        # Fleet size is dynamic. Disabled/new accounts can legitimately change
        # the number of safe internal actions, so correctness is defined by the
        # executor allowlist and account master-OFF contract, not a stale magic count.

        if not args.apply:
            db.rollback()
            print("DRY RUN: database not changed")
            return

        result=F.execute(db)
        db.commit()

        print()
        print("changed:",result["changed"])
        print("unchanged:",result["unchanged"])
        print(
            "skipped_unresolved:",
            result["skipped_unresolved"],
        )
        print("events:",result["events"])

        with open(
            "/root/BORIS/backend/run/factory_f_report.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                result,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
