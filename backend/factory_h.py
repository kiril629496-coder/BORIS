#!/usr/bin/env python3

"""BORIS Factory H runner — FACTORY_H_V1."""

import argparse
import json

from app.db.session import SessionLocal
from app.factory import h_resolver as H


FACTORY_H_V1=True


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        items=H.build(db)

        print("=== FACTORY H ===")
        print(f"input_unresolved={len(items)}")

        counts={}

        for x in items:
            counts[x["status"]]=(
                counts.get(x["status"],0)+1
            )

            print()
            print("ACCOUNT   :",x["account_id"])
            print("FIELD     :",x["field"])
            print("STATUS    :",x["status"])
            print("VALUE     :",repr(x["value"]))
            print("CONFIDENCE:",x["confidence"])
            print(
                "EVIDENCE  :",
                json.dumps(
                    x["evidence"],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        print()
        print("statuses=",counts)

        if not args.apply:
            db.rollback()
            print("DRY RUN: no DB writes")
            return 0

        result=H.apply(db)
        db.commit()

        print()
        print("inserted=",result["inserted"])
        print("updated=",result["updated"])
        print("unchanged=",result["unchanged"])
        print("unresolved=",result["unresolved"])
        print("conflicts=",result["conflicts"])
        print("APPLY=PASS")

        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    raise SystemExit(main())
