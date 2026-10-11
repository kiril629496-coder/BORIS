"""BORIS Factory G controlled resolver — FACTORY_G_RESOLVE_V1.

Default: DRY RUN.

Supported explicit operations:

    --owner ACCOUNT_ID:USER_ID
    --product ACCOUNT_ID:PRODUCT

AUTO-GUESSING IS FORBIDDEN.

Owner:
    updates accounts.owner_user_id only.

Product:
    creates account_slots row only after validating PRODUCT
    against an already-existing account_slots.product value.

No money.
No paid_until invention.
No tariff_ok invention.
No business command execution.
"""

import argparse
import json
import sys

from sqlalchemy import text

from app.db.session import SessionLocal


def parse_pair(value,name):
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            "%s must be ACCOUNT_ID:VALUE" % name
        )

    account,value=value.split(":",1)

    account=account.strip()
    value=value.strip()

    if not account or not value:
        raise argparse.ArgumentTypeError(
            "%s must contain non-empty account and value"
            % name
        )

    return account,value


def queue_item(db,account_id):
    row=db.execute(text("""
        SELECT
            account_id,
            issue_type,
            status
        FROM factory_setup_queue
        WHERE account_id=:a
    """),{"a":account_id}).mappings().first()

    return dict(row) if row else None


def validate_owner(db,account_id,user_id):
    item=queue_item(
        db,
        account_id,
    )

    if not item:
        raise ValueError(
            "account is not in setup queue"
        )

    if item["status"]!="OPEN":
        raise ValueError(
            "setup queue item is not OPEN"
        )

    if item["issue_type"]!="OWNER_MISSING":
        raise ValueError(
            "queue issue is not OWNER_MISSING"
        )

    acc=db.execute(text("""
        SELECT owner_user_id
        FROM accounts
        WHERE account_id=:a
    """),{"a":account_id}).fetchone()

    if not acc:
        raise ValueError(
            "account does not exist"
        )

    if acc[0] is not None:
        raise ValueError(
            "account already has owner_user_id=%s"
            % acc[0]
        )

    user=db.execute(text("""
        SELECT
            id,
            email,
            role,
            is_active,
            status
        FROM users
        WHERE id=:u
    """),{"u":user_id}).mappings().first()

    if not user:
        raise ValueError(
            "user does not exist"
        )

    if user["is_active"] is False:
        raise ValueError(
            "user is inactive"
        )

    return {
        "account_id":account_id,
        "user_id":user_id,
        "user_email":user["email"],
        "user_role":user["role"],
        "user_status":user["status"],
    }


def validate_product(db,account_id,product):
    item=queue_item(
        db,
        account_id,
    )

    if not item:
        raise ValueError(
            "account is not in setup queue"
        )

    if item["status"]!="OPEN":
        raise ValueError(
            "setup queue item is not OPEN"
        )

    if item["issue_type"]!="PRODUCT_MISSING":
        raise ValueError(
            "queue issue is not PRODUCT_MISSING"
        )

    acc=db.execute(text("""
        SELECT
            owner_user_id,
            name,
            avito_user_id
        FROM accounts
        WHERE account_id=:a
    """),{"a":account_id}).mappings().first()

    if not acc:
        raise ValueError(
            "account does not exist"
        )

    if acc["owner_user_id"] is None:
        raise ValueError(
            "product cannot be assigned without owner"
        )

    existing=db.execute(text("""
        SELECT count(*)
        FROM account_slots
        WHERE account_id=:a
          AND product=:p
    """),{
        "a":account_id,
        "p":product,
    }).scalar()

    if existing:
        raise ValueError(
            "account already has this product slot"
        )

    known=db.execute(text("""
        SELECT count(*)
        FROM account_slots
        WHERE product=:p
    """),{
        "p":product,
    }).scalar()

    if not known:
        raise ValueError(
            "unknown product; product must already exist in account_slots"
        )

    next_slot=db.execute(text("""
        SELECT
            COALESCE(max(slot_no),0)+1
        FROM account_slots
        WHERE owner_user_id=:u
          AND product=:p
    """),{
        "u":acc["owner_user_id"],
        "p":product,
    }).scalar()

    return {
        "account_id":account_id,
        "owner_user_id":
            acc["owner_user_id"],
        "account_name":
            acc["name"],
        "avito_user_id":
            acc["avito_user_id"],
        "product":
            product,
        "slot_no":
            int(next_slot or 1),
    }


def apply_owner(db,plan):
    result=db.execute(text("""
        UPDATE accounts
        SET owner_user_id=:u
        WHERE account_id=:a
          AND owner_user_id IS NULL
    """),{
        "a":plan["account_id"],
        "u":plan["user_id"],
    })

    if result.rowcount!=1:
        raise RuntimeError(
            "owner update rowcount=%s"
            % result.rowcount
        )


def apply_product(db,plan):
    db.execute(text("""
        INSERT INTO account_slots (
            owner_user_id,
            product,
            slot_no,
            status,
            account_id,
            avito_user_id,
            account_name,
            tariff_ok,
            created_at,
            updated_at
        )
        VALUES (
            :owner_user_id,
            :product,
            :slot_no,
            'pending',
            :account_id,
            :avito_user_id,
            :account_name,
            FALSE,
            now(),
            now()
        )
    """),plan)


def main():
    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--owner",
        action="append",
        default=[],
        help="ACCOUNT_ID:USER_ID",
    )

    ap.add_argument(
        "--product",
        action="append",
        default=[],
        help="ACCOUNT_ID:PRODUCT",
    )

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    args=ap.parse_args()

    db=SessionLocal()

    try:
        plans=[]

        for raw in args.owner:
            account,user=parse_pair(
                raw,
                "owner",
            )

            try:
                user_id=int(user)
            except ValueError:
                raise SystemExit(
                    "owner USER_ID must be integer"
                )

            plan=validate_owner(
                db,
                account,
                user_id,
            )

            plans.append({
                "type":"OWNER",
                "plan":plan,
            })

        for raw in args.product:
            account,product=parse_pair(
                raw,
                "product",
            )

            plan=validate_product(
                db,
                account,
                product,
            )

            plans.append({
                "type":"PRODUCT",
                "plan":plan,
            })

        print("=== FACTORY G RESOLVE ===")
        print("operations=",len(plans))

        for x in plans:
            print(
                x["type"],
                json.dumps(
                    x["plan"],
                    ensure_ascii=False,
                    default=str,
                )
            )

        if not plans:
            print(
                "DRY RUN: no operations supplied"
            )
            return 0

        if not args.apply:
            print(
                "DRY RUN: validation passed; DB unchanged"
            )
            return 0

        for x in plans:
            if x["type"]=="OWNER":
                apply_owner(
                    db,
                    x["plan"],
                )

            elif x["type"]=="PRODUCT":
                apply_product(
                    db,
                    x["plan"],
                )

        db.commit()

        print("APPLY=PASS")
        return 0

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__=="__main__":
    sys.exit(main())
