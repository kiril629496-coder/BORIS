"""FACTORY_G_V1 — runtime shadow comparison."""

from collections import Counter

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory.runtime_config import resolve_runtime


def main():
    db=SessionLocal()

    try:
        accounts=[
            r[0]
            for r in db.execute(text("""
                SELECT account_id
                FROM accounts
                ORDER BY account_id
            """)).fetchall()
        ]

        statuses=Counter()
        layers=Counter()

        print("=== FACTORY G SHADOW ===")
        print("accounts:",len(accounts))

        for account_id in accounts:
            r=resolve_runtime(
                db,
                account_id,
            )

            tz=r["profile.timezone"]
            bh=r["kpi.business_hours"]

            statuses[
                "timezone:"+tz["status"]
            ] += 1

            statuses[
                "business_hours:"+bh["status"]
            ] += 1

            if tz.get("layer"):
                layers[
                    "timezone:"+tz["layer"]
                ] += 1

            if bh.get("layer"):
                layers[
                    "business_hours:"+bh["layer"]
                ] += 1

            if (
                tz["status"]=="ok"
                or bh["status"] in ("ok","shadow")
            ):
                print()
                print(account_id)
                print(
                    "  timezone:",
                    tz,
                )
                print(
                    "  business_hours:",
                    bh,
                )

        print()
        print("--- statuses ---")

        for k,v in sorted(statuses.items()):
            print("%-45s %d" % (k,v))

        print()
        print("--- layers ---")

        for k,v in sorted(layers.items()):
            print("%-45s %d" % (k,v))

    finally:
        db.close()


if __name__=="__main__":
    main()
