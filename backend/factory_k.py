"""FACTORY_K_V1 — approved schedule executor CLI."""

import argparse
import json
from collections import Counter

from app.db.session import SessionLocal
from app.factory import schedule_executor as S


def main():
    p=argparse.ArgumentParser()

    p.add_argument(
        "--apply",
        action="store_true",
    )

    args=p.parse_args()

    db=SessionLocal()

    try:
        items=S.preview_all(db)

        print("=== FACTORY K ===")
        print("approved=",len(items))

        counts=Counter(
            x["result"]
            for x in items
        )

        print("\n--- PREVIEW ---")

        for k,v in counts.items():
            print(
                "%-30s %d"
                % (k,v)
            )

        for x in items:
            print(
                "%4s %-40s %-24s %-22s %s"
                % (
                    x["confirmation_id"],
                    x["account_id"][:40],
                    x["field"][:24],
                    x.get("job_kind","-"),
                    x["result"]
                    + ":"
                    + x.get("reason",""),
                )
            )

            if x.get("schedule"):
                print(
                    "     cron=%s"
                    % x["schedule"]["cron_expr"]
                )

        with open(
            "/root/BORIS/backend/run/factory_k_preview.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                items,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        if not args.apply:
            print(
                "\nDRY RUN: no schedule writes"
            )
            return

        results=[]

        for item in items:
            results.append(
                S.apply_ready(
                    db,
                    item,
                )
            )

        db.commit()

        print("\n--- APPLY ---")

        apply_counts=Counter(
            x["apply_result"]
            for x in results
        )

        for k,v in apply_counts.items():
            print(
                "%-30s %d"
                % (k,v)
            )

        with open(
            "/root/BORIS/backend/run/factory_k_results.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                results,
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


if __name__=="__main__":
    main()
