"""BORIS Factory G authoritative resolver — FACTORY_G_AUTHORITATIVE_V1.

READ ONLY.

Strong owner sources:
    accounts.owner_user_id
    account_slots.owner_user_id
    catalog_products.owner_user_id
    users.account_id -> users.id

Supporting owner source:
    user_account_access.user_id

Strong product sources:
    account_slots.product
    catalog_products.kind/category/status

Explicitly NOT product evidence:
    user_account_access

AUTO rules are intentionally strict.
"""

import json

from collections import defaultdict,Counter

from sqlalchemy import text

from app.db.session import SessionLocal


ACTIVE_SLOT_STATUSES={
    "active",
    "enabled",
    "paid",
    "ready",
    "ok",
}

ACTIVE_PRODUCT_STATUSES={
    "active",
    "enabled",
    "published",
    "ready",
    "draft",
}


def _uniq(rows,key):
    out={}
    for row in rows:
        out[row[key]]=row
    return list(out.values())


def account_row(db,account_id):
    row=db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            name,
            avito_user_id,
            billing_mode
        FROM accounts
        WHERE account_id=:a
    """),{"a":account_id}).mappings().first()

    return dict(row) if row else None


def owner_evidence(db,account_id):
    evidence=[]

    acc=account_row(
        db,
        account_id,
    )

    if acc and acc.get(
        "owner_user_id"
    ) is not None:
        evidence.append({
            "owner_user_id":
                acc["owner_user_id"],
            "source":
                "accounts.owner_user_id",
            "strength":
                "STRONG",
        })

    for row in db.execute(text("""
        SELECT DISTINCT
            owner_user_id
        FROM account_slots
        WHERE account_id=:a
          AND owner_user_id IS NOT NULL
    """),{"a":account_id}).fetchall():

        evidence.append({
            "owner_user_id":
                row[0],
            "source":
                "account_slots.owner_user_id",
            "strength":
                "STRONG",
        })

    for row in db.execute(text("""
        SELECT DISTINCT
            owner_user_id
        FROM catalog_products
        WHERE account_id=:a
          AND owner_user_id IS NOT NULL
          AND deleted_at IS NULL
    """),{"a":account_id}).fetchall():

        evidence.append({
            "owner_user_id":
                row[0],
            "source":
                "catalog_products.owner_user_id",
            "strength":
                "STRONG",
        })

    for row in db.execute(text("""
        SELECT
            id,
            is_active,
            status
        FROM users
        WHERE account_id=:a
    """),{"a":account_id}).fetchall():

        evidence.append({
            "owner_user_id":
                row[0],
            "source":
                "users.account_id",
            "strength":
                "STRONG",
            "user_is_active":
                row[1],
            "user_status":
                row[2],
        })

    # Supporting only: access may represent manager/member,
    # therefore never sufficient on its own for AUTO.
    for row in db.execute(text("""
        SELECT DISTINCT
            user_id,
            role
        FROM user_account_access
        WHERE account_id=:a
          AND user_id IS NOT NULL
    """),{"a":account_id}).fetchall():

        evidence.append({
            "owner_user_id":
                row[0],
            "source":
                "user_account_access.user_id",
            "strength":
                "SUPPORTING",
            "role":
                row[1],
        })

    return evidence


def classify_owner(evidence):
    strong=defaultdict(list)
    supporting=defaultdict(list)

    for e in evidence:
        owner=e["owner_user_id"]

        if e["strength"]=="STRONG":
            strong[owner].append(
                e["source"]
            )
        else:
            supporting[owner].append(
                e["source"]
            )

    strong_ids=sorted(
        strong
    )

    if len(strong_ids)==1:
        owner=strong_ids[0]

        return {
            "decision":"AUTO",
            "owner_user_id":owner,
            "reason":
                "one unique owner across authoritative sources",
            "strong_sources":
                sorted(set(strong[owner])),
            "supporting_sources":
                sorted(set(
                    supporting.get(owner,[])
                )),
        }

    if len(strong_ids)>1:
        return {
            "decision":"REVIEW",
            "owner_user_id":None,
            "reason":
                "authoritative owner sources disagree",
            "strong_candidates":{
                str(k):
                    sorted(set(v))
                for k,v in strong.items()
            },
        }

    support_ids=sorted(
        supporting
    )

    if len(support_ids)==1:
        return {
            "decision":"REVIEW",
            "owner_user_id":
                support_ids[0],
            "reason":
                "only supporting access evidence exists; not sufficient for AUTO",
            "supporting_sources":
                sorted(set(
                    supporting[
                        support_ids[0]
                    ]
                )),
        }

    if len(support_ids)>1:
        return {
            "decision":"REVIEW",
            "owner_user_id":None,
            "reason":
                "multiple supporting user candidates",
            "supporting_candidates":{
                str(k):
                    sorted(set(v))
                for k,v in supporting.items()
            },
        }

    return {
        "decision":"NO_EVIDENCE",
        "owner_user_id":None,
        "reason":
            "no owner evidence in authoritative or supporting sources",
    }


def product_evidence(db,account_id):
    slots=[]

    for row in db.execute(text("""
        SELECT
            id,
            owner_user_id,
            product,
            status,
            tariff_ok,
            paid_until,
            slot_no,
            sync_status
        FROM account_slots
        WHERE account_id=:a
        ORDER BY id
    """),{"a":account_id}).mappings().all():

        slots.append(
            dict(row)
        )

    catalog=[]

    for row in db.execute(text("""
        SELECT
            id,
            owner_user_id,
            sku,
            name,
            kind,
            category,
            status,
            source,
            deleted_at
        FROM catalog_products
        WHERE account_id=:a
        ORDER BY id
    """),{"a":account_id}).mappings().all():

        catalog.append(
            dict(row)
        )

    return {
        "account_slots":
            slots,

        "catalog_products":
            catalog,
    }


def normalize_product_tokens(evidence):
    slot_products=[]

    for row in evidence[
        "account_slots"
    ]:
        product=(
            row.get("product")
            or ""
        ).strip()

        if product:
            slot_products.append(
                product
            )

    catalog_tokens=[]

    for row in evidence[
        "catalog_products"
    ]:
        if row.get(
            "deleted_at"
        ) is not None:
            continue

        for field in (
            "kind",
            "category",
        ):
            value=(
                row.get(field)
                or ""
            ).strip()

            if value:
                catalog_tokens.append(
                    value
                )

    return {
        "slot_products":
            sorted(set(
                slot_products
            )),

        "catalog_tokens":
            sorted(set(
                catalog_tokens
            )),
    }


def classify_products(evidence):
    tokens=normalize_product_tokens(
        evidence
    )

    slot_products=tokens[
        "slot_products"
    ]

    catalog_tokens=tokens[
        "catalog_tokens"
    ]

    # account_slots.product is the strongest entitlement-like
    # source because it binds owner/product/account/slot.
    if len(slot_products)==1:
        return {
            "decision":"AUTO",
            "product":
                slot_products[0],
            "reason":
                "one unique product in account_slots",
            "source":
                "account_slots.product",
            "catalog_tokens":
                catalog_tokens,
        }

    if len(slot_products)>1:
        return {
            "decision":"REVIEW",
            "product":None,
            "reason":
                "multiple account_slots products",
            "slot_products":
                slot_products,
            "catalog_tokens":
                catalog_tokens,
        }

    # catalog_products describe sellable/catalog content,
    # not necessarily BORIS entitlement. Never AUTO from this alone.
    if catalog_tokens:
        return {
            "decision":"REVIEW",
            "product":None,
            "reason":
                "catalog product evidence exists but no account_slots entitlement",
            "catalog_tokens":
                catalog_tokens,
        }

    return {
        "decision":"NO_EVIDENCE",
        "product":None,
        "reason":
            "no account_slots product and no catalog product evidence",
    }


def main():
    db=SessionLocal()

    try:
        blocked=db.execute(text("""
            SELECT
                account_id,
                provisioning_status
            FROM factory_provisioning_state
            WHERE provisioning_status IN (
                'BLOCKED_OWNER',
                'BLOCKED_PRODUCTS'
            )
            ORDER BY provisioning_status,account_id
        """)).mappings().all()

        results=[]

        for row in blocked:
            account=row[
                "account_id"
            ]

            status=row[
                "provisioning_status"
            ]

            item={
                "account_id":
                    account,

                "provisioning_status":
                    status,
            }

            if status=="BLOCKED_OWNER":

                evidence=owner_evidence(
                    db,
                    account,
                )

                decision=classify_owner(
                    evidence
                )

                item[
                    "owner_evidence"
                ]=evidence

                item[
                    "resolution"
                ]=decision

            else:
                evidence=product_evidence(
                    db,
                    account,
                )

                decision=classify_products(
                    evidence
                )

                item[
                    "product_evidence"
                ]=evidence

                item[
                    "normalized"
                ]=normalize_product_tokens(
                    evidence
                )

                item[
                    "resolution"
                ]=decision

            results.append(
                item
            )

        counts=Counter(
            x[
                "resolution"
            ][
                "decision"
            ]
            for x in results
        )

        print(
            "=== FACTORY G AUTHORITATIVE ==="
        )

        print(
            "accounts=",
            len(results),
        )

        print(
            "\n--- DECISIONS ---"
        )

        for k,v in (
            counts.most_common()
        ):
            print(
                "%-15s %s"
                % (
                    k,
                    v,
                )
            )

        print(
            "\n--- DETAILS ---"
        )

        for x in results:

            r=x[
                "resolution"
            ]

            print(
                "\n%-38s %-18s %s"
                % (
                    x["account_id"][:38],
                    x["provisioning_status"],
                    r["decision"],
                )
            )

            print(
                " reason:",
                r["reason"],
            )

            if x[
                "provisioning_status"
            ]=="BLOCKED_OWNER":

                print(
                    " owner=",
                    r.get(
                        "owner_user_id"
                    ),
                )

                for e in x[
                    "owner_evidence"
                ]:
                    print(
                        "  %-35s owner=%s strength=%s"
                        % (
                            e["source"],
                            e["owner_user_id"],
                            e["strength"],
                        )
                    )

            else:
                print(
                    " product=",
                    r.get(
                        "product"
                    ),
                )

                print(
                    " slot_products=",
                    x[
                        "normalized"
                    ][
                        "slot_products"
                    ],
                )

                print(
                    " catalog_tokens=",
                    x[
                        "normalized"
                    ][
                        "catalog_tokens"
                    ],
                )

                for slot in x[
                    "product_evidence"
                ][
                    "account_slots"
                ]:
                    print(
                        "  SLOT",
                        json.dumps(
                            slot,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )

                for product in x[
                    "product_evidence"
                ][
                    "catalog_products"
                ]:
                    print(
                        "  CATALOG",
                        json.dumps(
                            product,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )

        with open(
            "/tmp/factory_g_authoritative.json",
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

        print(
            "\nreport=/tmp/factory_g_authoritative.json"
        )

    finally:
        db.close()


if __name__=="__main__":
    main()
