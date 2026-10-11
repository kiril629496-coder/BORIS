#!/usr/bin/env python3
"""BORIS Factory P runner."""

import argparse
import fcntl
from collections import Counter

from app.db.session import SessionLocal
from app.factory import recovery_supervisor as P


FACTORY_P_V1=True
LOCK="/tmp/boris_factory_p.lock"


def lock():
    fh=open(
        LOCK,
        "w",
        encoding="utf-8",
    )

    fcntl.flock(
        fh.fileno(),
        fcntl.LOCK_EX,
    )

    return fh


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument(
        "--apply",
        action="store_true",
    )
    args=ap.parse_args()

    _lock=lock()
    db=SessionLocal()

    try:
        if args.apply:
            P.ensure_schema(db)

        rows=P.plan(db)

        print("=== FACTORY P ===")
        print("attention_items=",len(rows))

        counts=Counter(
            r["recovery_status"]
            for r in rows
        )

        print()
        print("--- RECOVERY DECISIONS ---")

        if not counts:
            print("NONE                     0")
        else:
            for k,v in sorted(counts.items()):
                print("%-24s %d" % (k,v))

        print()
        print("--- ITEMS ---")

        for r in rows:
            print(
                "%-45s %-22s %-24s target=%s attempts=%s reason=%s"
                % (
                    r["account_id"][:45],
                    r["issue_type"][:22],
                    r["recovery_status"],
                    r["target_route"],
                    r["attempts"],
                    r["reason"],
                )
            )

        # Absolute runtime invariants.
        for r in rows:
            if r["source_route"]=="OWNER_REQUIRED":
                assert (
                    r["recovery_status"]
                    == "PROTECTED_OWNER_REQUIRED"
                )

                assert r["target_route"] is None

            assert r["external_effect"] is False

        if not args.apply:
            print()
            print("DRY RUN: no DB writes")
            return

        result=P.apply(db,rows)

        print()
        print("--- APPLY ---")

        for k,v in result.items():
            print("%s=%s" % (k,v))

        print("APPLY=PASS")

    finally:
        db.close()


if __name__=="__main__":
    main()
