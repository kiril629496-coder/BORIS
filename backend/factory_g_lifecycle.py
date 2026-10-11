"""BORIS Factory G lifecycle resolver — FACTORY_G_LIFECYCLE_V1.

READ ONLY.

Answers:
    Is this blocked account demonstrably alive?
    Is it likely QA/test?
    Does it contain historical business data?
    Does it require real setup rather than migration?

Never deletes, mutates, assigns owner, or grants products.
"""

import json
import re
from collections import Counter

from sqlalchemy import text

from app.db.session import SessionLocal


TEST_PATTERNS=(
    r'^qa[_-]',
    r'^qa',
    r'test',
    r'reuse',
    r'dry',
    r'turnstile',
    r'landing',
)


def test_name(account_id,name):
    value=(
        (account_id or "")
        +" "
        +(name or "")
    ).lower()

    return any(
        re.search(
            pattern,
            value,
        )
        for pattern in TEST_PATTERNS
    )


def table_exists(db,table):
    return bool(
        db.execute(text("""
            SELECT EXISTS(
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public'
                  AND table_name=:t
            )
        """),{
            "t":table,
        }).scalar()
    )


def columns(db,table):
    return {
        r[0]
        for r in db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=:t
        """),{
            "t":table,
        }).fetchall()
    }


def count_account(db,table,account_id):
    if not table_exists(
        db,
        table,
    ):
        return 0

    cols=columns(
        db,
        table,
    )

    if "account_id" not in cols:
        return 0

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


def storage_count(db,account_id):
    return count_account(
        db,
        "storage",
        account_id,
    )


def activity(db,account_id):
    messages=count_account(
        db,
        "messenger_messages",
        account_id,
    )

    facts=count_account(
        db,
        "client_facts",
        account_id,
    )

    storage=storage_count(
        db,
        account_id,
    )

    catalog=count_account(
        db,
        "catalog_products",
        account_id,
    )

    slots=count_account(
        db,
        "account_slots",
        account_id,
    )

    access=count_account(
        db,
        "user_account_access",
        account_id,
    )

    events=count_account(
        db,
        "product_events",
        account_id,
    )

    return {
        "messenger_messages":messages,
        "client_facts":facts,
        "storage":storage,
        "catalog_products":catalog,
        "account_slots":slots,
        "user_account_access":access,
        "product_events":events,
    }


def account(db,account_id):
    row=db.execute(text("""
        SELECT
            account_id,
            name,
            created_at,
            owner_user_id,
            avito_client_id,
            avito_client_secret,
            avito_user_id,
            billing_mode,
            is_own,
            company_niche,
            company_description
        FROM accounts
        WHERE account_id=:a
    """),{
        "a":account_id,
    }).mappings().first()

    return (
        dict(row)
        if row
        else {}
    )


def classify(
    account_row,
    activity_row,
    provisioning_status,
):
    is_test=test_name(
        account_row.get(
            "account_id"
        ),
        account_row.get(
            "name"
        ),
    )

    has_avito=bool(
        account_row.get(
            "avito_user_id"
        )
        or account_row.get(
            "avito_client_id"
        )
        or account_row.get(
            "avito_client_secret"
        )
    )

    strong_activity=(
        activity_row[
            "messenger_messages"
        ] > 0
        or activity_row[
            "client_facts"
        ] > 0
        or activity_row[
            "catalog_products"
        ] > 0
        or activity_row[
            "account_slots"
        ] > 0
    )

    historical_activity=(
        strong_activity
        or activity_row[
            "storage"
        ] > 0
        or activity_row[
            "product_events"
        ] > 0
    )

    if is_test:
        return {
            "lifecycle":
                "TEST_OR_QA",

            "action":
                "EXCLUDE_FROM_REAL_CLIENT_RECOVERY",

            "reason":
                "account/name matches QA or test heuristic",
        }

    if provisioning_status=="BLOCKED_OWNER":

        if (
            strong_activity
            or has_avito
        ):
            return {
                "lifecycle":
                    "REAL_NEEDS_SETUP",

                "action":
                    "ASK_OR_ASSIGN_OWNER",

                "reason":
                    "real activity/Avito identity exists but owner is absent",
            }

        if historical_activity:
            return {
                "lifecycle":
                    "ACTIVE_ORPHAN",

                "action":
                    "REVIEW_OWNER",

                "reason":
                    "historical data exists but no authoritative owner",
            }

        return {
            "lifecycle":
                "DORMANT_ORPHAN",

            "action":
                "REVIEW_DO_NOT_DELETE",

            "reason":
                "no authoritative owner and no strong business activity",
        }

    if provisioning_status=="BLOCKED_PRODUCTS":

        if (
            strong_activity
            or has_avito
        ):
            return {
                "lifecycle":
                    "REAL_NEEDS_SETUP",

                "action":
                    "CONFIGURE_PRODUCT",

                "reason":
                    "real account evidence exists but no product entitlement",
            }

        if historical_activity:
            return {
                "lifecycle":
                    "ACTIVE_ORPHAN",

                "action":
                    "REVIEW_PRODUCT",

                "reason":
                    "historical data exists but product entitlement is absent",
            }

        return {
            "lifecycle":
                "DORMANT_ORPHAN",

            "action":
                "REVIEW_DO_NOT_DELETE",

            "reason":
                "no product entitlement and no strong business activity",
        }

    return {
        "lifecycle":
            "REVIEW",

        "action":
            "REVIEW",

        "reason":
            "unexpected provisioning state",
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
            account_id=row[
                "account_id"
            ]

            acc=account(
                db,
                account_id,
            )

            act=activity(
                db,
                account_id,
            )

            resolution=classify(
                acc,
                act,
                row[
                    "provisioning_status"
                ],
            )

            results.append({
                "account_id":
                    account_id,

                "provisioning_status":
                    row[
                        "provisioning_status"
                    ],

                "account":
                    acc,

                "activity":
                    act,

                "resolution":
                    resolution,
            })

        counts=Counter(
            x[
                "resolution"
            ][
                "lifecycle"
            ]
            for x in results
        )

        print(
            "=== FACTORY G LIFECYCLE ==="
        )

        print(
            "accounts=",
            len(results),
        )

        print(
            "\n--- LIFECYCLE ---"
        )

        for key,value in (
            counts.most_common()
        ):
            print(
                "%-22s %s"
                % (
                    key,
                    value,
                )
            )

        print(
            "\n--- DETAILS ---"
        )

        for x in results:
            a=x["account"]
            r=x["resolution"]

            print(
                "\n%-38s %-18s %s"
                % (
                    x["account_id"][:38],
                    x[
                        "provisioning_status"
                    ],
                    r[
                        "lifecycle"
                    ],
                )
            )

            print(
                " name=",
                a.get(
                    "name"
                ),
            )

            print(
                " owner=",
                a.get(
                    "owner_user_id"
                ),
            )

            print(
                " avito_user_id=",
                a.get(
                    "avito_user_id"
                ),
            )

            print(
                " billing_mode=",
                a.get(
                    "billing_mode"
                ),
            )

            print(
                " activity=",
                x[
                    "activity"
                ],
            )

            print(
                " action=",
                r[
                    "action"
                ],
            )

            print(
                " reason=",
                r[
                    "reason"
                ],
            )

        with open(
            "/tmp/factory_g_lifecycle.json",
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
            "\nreport=/tmp/factory_g_lifecycle.json"
        )

    finally:
        db.close()


if __name__=="__main__":
    main()
