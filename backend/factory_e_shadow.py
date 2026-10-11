"""Factory E Stage 2 shadow runner — FACTORY_E_SHADOW_V1."""

import json
from collections import Counter,defaultdict

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import shadow as SH


def main():
    db=SessionLocal()

    try:
        rows=db.execute(text("""
            SELECT
                account_id,
                field,
                value,
                source,
                confidence
            FROM factory_resolved_values
            ORDER BY field,account_id
        """)).mappings().all()

        results=[]

        total=Counter()
        by_field=defaultdict(Counter)

        for row in rows:
            field=row["field"]

            if field not in SH.FIELDS:
                continue

            leg=SH.legacy(
                db,
                row["account_id"],
                field,
            )

            result=SH.compare(
                row["value"],
                leg,
            )

            item={
                "account_id":row["account_id"],
                "field":field,
                "resolved":row["value"],
                "resolved_source":row["source"],
                "confidence":(
                    float(row["confidence"])
                    if row["confidence"] is not None
                    else None
                ),
                "legacy_state":leg.get("state"),
                "legacy_value":leg.get("value"),
                "legacy_source":leg.get("source"),
                "result":result,
            }

            results.append(item)

            total[result]+=1
            by_field[field][result]+=1

        print("=== FACTORY E SHADOW ===")
        print("comparisons:",len(results))

        print("\n--- TOTAL ---")

        for k in (
            "same",
            "different",
            "legacy_unknown",
            "resolved_unknown",
        ):
            print("%-20s %s"%(k,total[k]))

        print("\n--- BY FIELD ---")

        for field in sorted(by_field):
            c=by_field[field]

            print(
                "%-34s same=%d different=%d legacy_unknown=%d resolved_unknown=%d"
                % (
                    field,
                    c["same"],
                    c["different"],
                    c["legacy_unknown"],
                    c["resolved_unknown"],
                )
            )

        diffs=[
            x for x in results
            if x["result"]=="different"
        ]

        print("\n--- DIFFERENT ---")
        print(len(diffs))

        for x in diffs:
            print(
                "%-34s %-28s resolved=%r legacy=%r"
                % (
                    x["account_id"][:34],
                    x["field"][:28],
                    x["resolved"],
                    x["legacy_value"],
                )
            )

        # Cutover candidates are only fields whose complete
        # comparable population has zero differences.
        gates={}

        for field,c in by_field.items():
            gates[field]={
                "same":c["same"],
                "different":c["different"],
                "legacy_unknown":c["legacy_unknown"],
                "resolved_unknown":c["resolved_unknown"],
                "cutover_candidate":(
                    c["different"]==0
                    and c["resolved_unknown"]==0
                    and c["same"]>0
                ),
            }

        print("\n--- CUTOVER GATES ---")

        for field in sorted(gates):
            g=gates[field]

            print(
                "%-34s %s"
                % (
                    field,
                    "CANDIDATE"
                    if g["cutover_candidate"]
                    else "BLOCKED",
                )
            )

        with open(
            "/tmp/factory_e_shadow.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                {
                    "results":results,
                    "gates":gates,
                },
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        print(
            "\nreport=/tmp/factory_e_shadow.json"
        )

    finally:
        db.close()


if __name__=="__main__":
    main()
