#!/usr/bin/env python3
"""BORIS Factory E runner — FACTORY_E_V1."""

from __future__ import annotations

import argparse
import json

from app.db.session import SessionLocal
from app.factory import bootstrap as E


FACTORY_E_V1 = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()

    try:
        result = E.materialize(db, apply=args.apply)

        print("=== FACTORY E ===")
        print("launch_rows:", result["launch_rows"])
        print("eligible:", result["eligible"])
        print("built:", result["built"])
        print("errors:", len(result["errors"]))

        if result["errors"]:
            for x in result["errors"]:
                print("ERROR", x["account_id"], x["error"])
            db.rollback()
            return 2

        if result["eligible"] != 13:
            print(
                "STOP: proven zero-touch cohort changed:",
                result["eligible"],
            )
            db.rollback()
            return 3

        print()
        print("--- BOOTSTRAP PACKAGES ---")

        for item in result["items"]:
            print(
                "%-44s %-28s actions=%d internal=%d boundary=%d"
                % (
                    item["account_id"][:44],
                    item["status"],
                    item["action_count"],
                    item["executable_internal"],
                    item["blocked_external"],
                )
            )

        if args.apply:
            db.commit()

            print()
            print("--- APPLY ---")
            print("written:", result["written"])
            print("unchanged:", result["unchanged"])
            print("events:", result["events"])

            snap = E.snapshot(db)

            print()
            print("--- PERSISTENT STATE ---")
            print("rows:", len(snap))

            for x in snap:
                print(
                    "%-44s %-28s actions=%s internal=%s boundary=%s"
                    % (
                        x["account_id"][:44],
                        x["status"],
                        x["action_count"],
                        x["executable_internal"],
                        x["blocked_external"],
                    )
                )

        else:
            db.rollback()
            print()
            print("DRY RUN: database not changed")

        print()
        print(
            "report=/tmp/factory_e_report.json"
        )

        with open(
            "/tmp/factory_e_report.json",
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

        return 0

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
