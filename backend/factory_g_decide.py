"""BORIS Factory G setup decision layer — FACTORY_G_DECIDE_V1.

READ ONLY.

Decision outputs:
    READY_FOR_EXPLICIT_OWNER
    READY_FOR_EXPLICIT_PRODUCT
    NEEDS_HUMAN_OWNER
    NEEDS_HUMAN_PRODUCT
    BLOCKED_AMBIGUOUS

Never writes configuration.
"""

import json

from sqlalchemy import text

from app.db.session import SessionLocal


OWNER_CASES=(
    "evgeniy_peregorodki_12447",
    "marketing_pod_klyuch_20035",
)

PRODUCT_CASE="samvel_almaznoe_burenie_64222"


def active_owner_users(db):
    rows=db.execute(text("""
        SELECT
            id,
            email,
            role,
            account_id,
            status,
            is_active
        FROM users
        WHERE is_active IS DISTINCT FROM FALSE
          AND role IN (
              'owner',
              'client'
          )
        ORDER BY id
    """)).mappings().all()

    return [
        dict(x)
        for x in rows
    ]


def owner_case(db,account_id):
    acc=db.execute(text("""
        SELECT
            account_id,
            name,
            owner_user_id,
            billing_mode
        FROM accounts
        WHERE account_id=:a
    """),{"a":account_id}).mappings().first()

    access=db.execute(text("""
        SELECT DISTINCT
            user_id,
            role
        FROM user_account_access
        WHERE account_id=:a
    """),{"a":account_id}).fetchall()

    direct_users=db.execute(text("""
        SELECT
            id,
            email,
            role,
            status,
            is_active
        FROM users
        WHERE account_id=:a
    """),{"a":account_id}).mappings().all()

    if acc is None:
        return {
            "account_id":account_id,
            "decision":"BLOCKED_AMBIGUOUS",
            "reason":"account missing",
        }

    if acc["owner_user_id"] is not None:
        return {
            "account_id":account_id,
            "decision":"BLOCKED_AMBIGUOUS",
            "reason":"owner already assigned",
        }

    factual=set()

    for r in access:
        factual.add(
            int(r[0])
        )

    for r in direct_users:
        factual.add(
            int(r["id"])
        )

    if len(factual)==1:
        user_id=next(
            iter(factual)
        )

        return {
            "account_id":account_id,
            "decision":
                "READY_FOR_EXPLICIT_OWNER",
            "suggested_user_id":
                user_id,
            "reason":
                "exactly one account-linked user exists",
        }

    if len(factual)>1:
        return {
            "account_id":account_id,
            "decision":
                "BLOCKED_AMBIGUOUS",
            "candidate_user_ids":
                sorted(factual),
            "reason":
                "multiple account-linked users exist",
        }

    return {
        "account_id":account_id,
        "decision":
            "NEEDS_HUMAN_OWNER",
        "reason":
            "no account-linked owner evidence; explicit owner selection required",
        "allowed_users":
            active_owner_users(db),
    }


def product_case(db,account_id):
    acc=db.execute(text("""
        SELECT
            account_id,
            name,
            owner_user_id
        FROM accounts
        WHERE account_id=:a
    """),{"a":account_id}).mappings().first()

    if not acc:
        return {
            "account_id":account_id,
            "decision":"BLOCKED_AMBIGUOUS",
            "reason":"account missing",
        }

    owner=acc["owner_user_id"]

    if owner is None:
        return {
            "account_id":account_id,
            "decision":"NEEDS_HUMAN_PRODUCT",
            "reason":"owner missing",
        }

    global_products=[
        r[0]
        for r in db.execute(text("""
            SELECT DISTINCT product
            FROM account_slots
            WHERE product IS NOT NULL
              AND btrim(product)<>''
            ORDER BY product
        """)).fetchall()
    ]

    owner_products=[
        r[0]
        for r in db.execute(text("""
            SELECT DISTINCT product
            FROM account_slots
            WHERE owner_user_id=:u
              AND product IS NOT NULL
              AND btrim(product)<>''
            ORDER BY product
        """),{"u":owner}).fetchall()
    ]

    if len(owner_products)==1:
        return {
            "account_id":account_id,
            "decision":
                "READY_FOR_EXPLICIT_PRODUCT",
            "suggested_product":
                owner_products[0],
            "reason":
                "owner has exactly one existing product family",
            "owner_user_id":
                owner,
        }

    if len(owner_products)>1:
        return {
            "account_id":account_id,
            "decision":
                "BLOCKED_AMBIGUOUS",
            "owner_user_id":
                owner,
            "candidate_products":
                owner_products,
            "reason":
                "owner has multiple product families",
        }

    return {
        "account_id":account_id,
        "decision":
            "NEEDS_HUMAN_PRODUCT",
        "owner_user_id":
            owner,
        "known_products":
            global_products,
        "reason":
            "owner has no previous product family; explicit product choice required",
    }


def main():
    db=SessionLocal()

    try:
        results=[]

        for a in OWNER_CASES:
            results.append(
                owner_case(
                    db,
                    a,
                )
            )

        results.append(
            product_case(
                db,
                PRODUCT_CASE,
            )
        )

        print("=== FACTORY G DECISIONS ===")

        for r in results:
            print(
                "\naccount=",
                r["account_id"],
            )

            print(
                "decision=",
                r["decision"],
            )

            print(
                "reason=",
                r["reason"],
            )

            if "suggested_user_id" in r:
                print(
                    "suggested_user_id=",
                    r["suggested_user_id"],
                )

            if "suggested_product" in r:
                print(
                    "suggested_product=",
                    r["suggested_product"],
                )

            if "candidate_user_ids" in r:
                print(
                    "candidate_user_ids=",
                    r["candidate_user_ids"],
                )

            if "candidate_products" in r:
                print(
                    "candidate_products=",
                    r["candidate_products"],
                )

            if "known_products" in r:
                print(
                    "known_products=",
                    r["known_products"],
                )

        with open(
            "/tmp/factory_g_decisions.json",
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
            "\nreport=/tmp/factory_g_decisions.json"
        )

    finally:
        db.close()


if __name__=="__main__":
    main()
