#!/usr/bin/env python3

"""BORIS Factory J runner — FACTORY_J_V1."""

import argparse
import subprocess
import sys

from app.db.session import SessionLocal
from app.factory import fact_intake as J


FACTORY_J_V1=True


def cascade():
    py=sys.executable

    for cmd in (
        [py,"factory_h.py","--apply"],
        [py,"factory_f.py","--apply"],
        [py,"factory_g.py","--apply"],
        [py,"factory_i.py","--apply"],
    ):
        print(
            "$",
            " ".join(cmd),
            flush=True,
        )

        p=subprocess.run(cmd)

        if p.returncode:
            raise SystemExit(
                p.returncode
            )


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    ap.add_argument(
        "--submit",
        action="store_true",
    )

    ap.add_argument("--account")
    ap.add_argument("--fact-kind")
    ap.add_argument("--value")
    ap.add_argument("--source-type")
    ap.add_argument("--source-ref")
    ap.add_argument("--actor")

    ap.add_argument(
        "--confidence",
        type=float,
        default=1.0,
    )

    ap.add_argument(
        "--cascade",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        if args.submit:
            if not args.apply:
                raise SystemExit(
                    "STOP: --submit requires --apply"
                )

            result=J.submit(
                db,
                account_id=args.account,
                fact_kind=args.fact_kind,
                value=args.value,
                source_type=args.source_type,
                source_ref=args.source_ref,
                actor=args.actor,
                confidence=args.confidence,
            )

            db.commit()

            print("=== FACTORY J SUBMIT ===")
            print("result=",result["result"])
            print(
                "fingerprint=",
                result["fingerprint"],
            )

            if args.cascade:
                cascade()

            print("SUBMIT=PASS")
            return 0

        rows=J.list_state(db)

        print("=== FACTORY J ===")
        print(f"accepted_facts={len(rows)}")

        for x in rows:
            print(
                "%-46s %-22s %-20s %r conf=%s"
                % (
                    x["account_id"],
                    x["fact_kind"],
                    x["status"],
                    x["value"],
                    x["confidence"],
                )
            )

        if args.apply:
            db.commit()
            print("APPLY=PASS")
        else:
            db.rollback()
            print("DRY RUN: no DB writes")

        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    raise SystemExit(main())
