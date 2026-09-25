"""BORIS Factory D runner — FACTORY_D_V1.

Default mode = DRY RUN.

--apply writes only factory_launch_state.

Designed to be run globally for all accounts.
No client-specific cron entries.
"""

import argparse
import fcntl
import json
import os
import sys
from collections import Counter

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import bridge as BR
from app.factory import state as FS


LOCK_PATH = "/tmp/boris_factory_d.lock"


def acquire_lock():
    fh = open(
        LOCK_PATH,
        "w",
        encoding="utf-8",
    )

    try:
        fcntl.flock(
            fh.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError:
        print(
            "SKIP: Factory D already running"
        )
        return None

    fh.write(str(os.getpid()))
    fh.flush()

    return fh


def current_map(db):
    rows=db.execute(text("""
        SELECT
            account_id,
            source_fingerprint,
            launch_status,
            eligible_zero_touch
        FROM factory_launch_state
    """)).mappings().all()

    return {
        r["account_id"]: dict(r)
        for r in rows
    }


def change_set(db, packets):
    before=current_map(db)

    changes=[]

    for packet in packets:
        fp=FS.fingerprint(packet)
        old=before.get(packet["account_id"])

        # Lossless E -> D handoff.
        #
        # IMPORTANT:
        # The legacy status fields remain unchanged so existing D consumers
        # keep their contract.
        #
        # The complete E decision projection is carried alongside them.
        # D must not silently discard policy decisions.

        decision_projection = {
            "decisions": list(packet.get("decisions") or []),
            "auto_allowed": list(packet.get("auto_allowed") or []),
            "confirm_required": list(
                packet.get("confirm_required") or []
            ),
            "mandate_required": list(
                packet.get("mandate_required") or []
            ),
            "blocked": list(packet.get("blocked") or []),
            "noop": list(packet.get("noop") or []),
        }

        handoff = {
            "source_fingerprint": fp,
            "eligible_zero_touch":
                packet.get("eligible_zero_touch"),
            **decision_projection,
            "packet": packet,
        }

        if not old:
            changes.append({
                "account_id": packet["account_id"],
                "change": "NEW",
                "before": None,
                "after": packet["launch_status"],
                **handoff,
            })
            continue

        if old["source_fingerprint"] != fp:
            changes.append({
                "account_id": packet["account_id"],
                "change": "CHANGED",
                "before": old["launch_status"],
                "after": packet["launch_status"],
                **handoff,
            })

    return changes


def print_summary(packets, changes):
    statuses=Counter(
        p["launch_status"]
        for p in packets
    )

    cohorts=Counter(
        p.get("cohort")
        for p in packets
    )

    eligible=[
        p for p in packets
        if FS.eligible(p)
    ]

    print("=== FACTORY D ===")
    print("accounts_seen:", len(packets))
    print("changes:", len(changes))

    print("\n--- STATUSES ---")

    for k,v in statuses.most_common():
        print(
            "%-22s %3d"
            % (str(k),v)
        )

    print("\n--- COHORTS ---")

    for k,v in cohorts.most_common():
        print(
            "%-24s %3d"
            % (str(k),v)
        )

    print("\n--- ZERO TOUCH ELIGIBLE ---")
    print(len(eligible))

    for p in eligible:
        print(
            "  %-36s readiness=%s%%"
            % (
                p["account_id"][:36],
                p.get("self_pct",0),
            )
        )

    print("\n--- CHANGES ---")

    if not changes:
        print("  none")

    for c in changes:
        print(
            "  %-36s %-8s %s -> %s"
            % (
                c["account_id"][:36],
                c["change"],
                c["before"],
                c["after"],
            )
        )


def main():
    parser=argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--account",
        default=None,
    )

    args=parser.parse_args()

    lock=acquire_lock()

    if lock is None:
        return 0

    db=SessionLocal()

    try:
        if args.account:
            packet=BR.build_one(
                db,
                args.account,
            )

            if packet is None:
                print(
                    "NOT_FOUND:",
                    args.account,
                )
                return 2

            packets=[packet]

        else:
            packets=BR.build_all(db)

        changes=change_set(
            db,
            packets,
        )

        print_summary(
            packets,
            changes,
        )

        with open(
            "/tmp/factory_d_last.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                {
                    "packets": packets,
                    "changes": changes,
                },
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        if not args.apply:
            print(
                "\nDRY RUN: no DB write"
            )
            return 0

        BR.apply_all(
            db,
            packets,
        )

        db.commit()

        print(
            "\nAPPLY: factory_launch_state updated"
        )

        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
