"""BORIS Factory G Evidence Resolver — FACTORY_G_EVIDENCE_V1.

READ ONLY.

Purpose:
    explain BLOCKED_OWNER / BLOCKED_PRODUCTS using existing DB evidence.

No writes.
No guessed owner.
No guessed product.
No side effects.
"""

import json
from collections import defaultdict

from sqlalchemy import text

from app.db.session import SessionLocal


def table_columns(db):
    rows=db.execute(text("""
        SELECT
            table_name,
            column_name
        FROM information_schema.columns
        WHERE table_schema='public'
        ORDER BY table_name,ordinal_position
    """)).fetchall()

    out=defaultdict(list)

    for table,col in rows:
        out[table].append(col)

    return dict(out)


def table_exists(cols,table):
    return table in cols


def safe_count(db,table,account_id,cols):
    if table not in cols:
        return None

    if "account_id" not in cols[table]:
        return None

    try:
        return int(
            db.execute(text(
                'SELECT count(*) '
                'FROM "%s" '
                'WHERE account_id=:a'
                % table
            ),{
                "a":account_id,
            }).scalar()
            or 0
        )
    except Exception:
        db.rollback()
        return None


def account_row(db,account_id):
    return db.execute(text("""
        SELECT *
        FROM accounts
        WHERE account_id=:a
    """),{
        "a":account_id,
    }).mappings().first()


def owner_candidates(db,account_id,cols):
    """Only factual owner references from existing rows."""

    candidates=[]

    acc=account_row(
        db,
        account_id,
    )

    if acc and acc.get("owner_user_id") is not None:
        candidates.append({
            "owner_user_id":
                acc["owner_user_id"],
            "source":
                "accounts.owner_user_id",
            "strength":
                "DIRECT",
        })

    # Search only tables that actually contain BOTH
    # account_id and an owner/user-like integer reference.
    for table,columns in cols.items():

        if table=="accounts":
            continue

        if "account_id" not in columns:
            continue

        possible=[
            c for c in columns
            if c in (
                "owner_user_id",
                "user_id",
                "granted_by_user_id",
                "created_by_user_id",
            )
        ]

        for col in possible:
            try:
                rows=db.execute(text(
                    'SELECT DISTINCT "%s" '
                    'FROM "%s" '
                    'WHERE account_id=:a '
                    'AND "%s" IS NOT NULL '
                    'LIMIT 20'
                    % (
                        col,
                        table,
                        col,
                    )
                ),{
                    "a":account_id,
                }).fetchall()
            except Exception:
                db.rollback()
                continue

            for row in rows:
                candidates.append({
                    "owner_user_id":
                        row[0],
                    "source":
                        "%s.%s"%(table,col),
                    "strength":
                        "INDIRECT",
                })

    return candidates


def normalize_owner_candidates(rows):
    grouped=defaultdict(list)

    for row in rows:
        grouped[
            row["owner_user_id"]
        ].append(
            row["source"]
        )

    return [
        {
            "owner_user_id":owner,
            "sources":sorted(set(sources)),
        }
        for owner,sources
        in grouped.items()
    ]


def product_evidence(db,account_id,cols):
    """Collect product-like evidence without assuming its meaning."""

    out=[]

    tokens=(
        "product",
        "entitl",
        "subscription",
        "tariff",
        "access",
    )

    for table,columns in cols.items():

        if "account_id" not in columns:
            continue

        if not any(
            token in table.lower()
            for token in tokens
        ):
            continue

        try:
            count=db.execute(text(
                'SELECT count(*) '
                'FROM "%s" '
                'WHERE account_id=:a'
                % table
            ),{
                "a":account_id,
            }).scalar()
        except Exception:
            db.rollback()
            continue

        if not count:
            continue

        sample_cols=[
            c for c in columns
            if c in (
                "product",
                "product_code",
                "product_name",
                "name",
                "type",
                "status",
                "tariff",
                "tariff_code",
                "subscription_type",
                "is_active",
                "active",
            )
        ]

        sample=[]

        if sample_cols:
            quoted=",".join(
                '"%s"'%c
                for c in sample_cols
            )

            try:
                rows=db.execute(text(
                    'SELECT %s '
                    'FROM "%s" '
                    'WHERE account_id=:a '
                    'LIMIT 10'
                    % (
                        quoted,
                        table,
                    )
                ),{
                    "a":account_id,
                }).fetchall()

                sample=[
                    list(r)
                    for r in rows
                ]

            except Exception:
                db.rollback()

        out.append({
            "table":table,
            "count":int(count),
            "columns":sample_cols,
            "sample":sample,
        })

    return out


def classify_owner(evidence):
    normalized=normalize_owner_candidates(
        evidence
    )

    if len(normalized)==1:
        return (
            "AUTO",
            normalized,
            "exactly one factual owner candidate",
        )

    if len(normalized)>1:
        return (
            "REVIEW",
            normalized,
            "multiple factual owner candidates",
        )

    return (
        "NO_EVIDENCE",
        [],
        "no owner candidate found",
    )


def classify_products(evidence):
    if not evidence:
        return (
            "NO_EVIDENCE",
            "no product-like account rows found",
        )

    # Stage 3 deliberately does NOT infer product mapping
    # from arbitrary tables.
    return (
        "REVIEW",
        "product-like evidence exists but semantic mapping is not yet proven",
    )


def main():
    db=SessionLocal()

    try:
        cols=table_columns(db)

        blocked=db.execute(text("""
            SELECT
                account_id,
                provisioning_status,
                blockers
            FROM factory_provisioning_state
            WHERE provisioning_status IN (
                'BLOCKED_OWNER',
                'BLOCKED_PRODUCTS'
            )
            ORDER BY provisioning_status,account_id
        """)).mappings().all()

        reports=[]

        for row in blocked:
            account=row["account_id"]
            status=row["provisioning_status"]

            result={
                "account_id":account,
                "provisioning_status":status,
            }

            if status=="BLOCKED_OWNER":
                evidence=owner_candidates(
                    db,
                    account,
                    cols,
                )

                decision,candidates,reason=(
                    classify_owner(
                        evidence
                    )
                )

                result.update({
                    "decision":decision,
                    "reason":reason,
                    "owner_candidates":
                        candidates,
                })

            elif status=="BLOCKED_PRODUCTS":
                evidence=product_evidence(
                    db,
                    account,
                    cols,
                )

                decision,reason=(
                    classify_products(
                        evidence
                    )
                )

                result.update({
                    "decision":decision,
                    "reason":reason,
                    "product_evidence":
                        evidence,
                })

            reports.append(result)

        print("=== FACTORY G EVIDENCE ===")
        print("accounts=",len(reports))

        from collections import Counter

        decisions=Counter(
            x["decision"]
            for x in reports
        )

        print("\n--- DECISIONS ---")

        for k,v in decisions.most_common():
            print(
                "%-15s %s"
                % (k,v)
            )

        print("\n--- DETAILS ---")

        for r in reports:
            print(
                "\n%-38s %-18s %s"
                % (
                    r["account_id"][:38],
                    r["provisioning_status"],
                    r["decision"],
                )
            )

            print(
                " reason:",
                r["reason"],
            )

            if "owner_candidates" in r:
                print(
                    " owners:",
                    json.dumps(
                        r["owner_candidates"],
                        ensure_ascii=False,
                        default=str,
                    )
                )

            if "product_evidence" in r:
                for e in r[
                    "product_evidence"
                ]:
                    print(
                        " table=%s count=%s columns=%s sample=%s"
                        % (
                            e["table"],
                            e["count"],
                            e["columns"],
                            e["sample"],
                        )
                    )

        with open(
            "/tmp/factory_g_evidence.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                reports,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        print(
            "\nreport=/tmp/factory_g_evidence.json"
        )

    finally:
        db.close()


if __name__=="__main__":
    main()
