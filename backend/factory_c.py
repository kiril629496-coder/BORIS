"""BORIS Factory C — FACTORY_C_V1.

Builds and persists INTERNAL launch packets.

Safe write scope:
    factory_launch_state only.

Explicitly forbidden:
    client_config
    storage
    money_mandates
    cron
    accounts
    tg_routes
    billing
    external APIs
"""

import argparse
import json
import re
from collections import Counter

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import state as FS


TEST_PATTERNS = (
    r"^qa_",
    r"^qa",
    r"test",
    r"dry",
    r"reuse",
    r"turnstile",
    r"lending",
    r"newbie",
    r"multiakkaunt",
)


def looks_test_account(account_id):
    low = str(account_id or "").lower()

    return any(
        re.search(p, low)
        for p in TEST_PATTERNS
    )


def account_activity(db, account_id):
    """Factual signals only. No guessed business classification."""

    def count(table):
        try:
            return int(db.execute(text(
                'SELECT count(*) FROM "%s" WHERE account_id=:a' % table
            ), {"a": account_id}).scalar() or 0)
        except Exception:
            db.rollback()
            return 0

    messages = count("messenger_messages")
    facts = count("client_facts")

    try:
        storage = int(db.execute(text("""
            SELECT count(*)
            FROM storage
            WHERE account_id=:a
        """), {"a": account_id}).scalar() or 0)
    except Exception:
        db.rollback()
        storage = 0

    return {
        "messenger_messages": messages,
        "client_facts": facts,
        "storage_rows": storage,
        "has_activity": bool(
            messages > 0
            or facts > 0
            or storage > 2
        ),
    }


def account_meta(db):
    rows = db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            name,
            billing_mode,
            company_niche,
            company_website,
            avito_user_id,
            avito_client_id
        FROM accounts
        ORDER BY account_id
    """)).mappings().all()

    return {
        r["account_id"]: dict(r)
        for r in rows
    }


def refine_status(plan, meta):
    """Only strengthen blocking status. Never upgrade readiness."""

    if not meta:
        return "BLOCKED_CORE"

    if meta.get("owner_user_id") is None:
        return "BLOCKED_NO_OWNER"

    if plan.get("core_blockers"):
        # NO_PRODUCTS remains NO_PRODUCTS because it describes
        # product absence, but it will never be eligible.
        if plan.get("launch_status") != "NO_PRODUCTS":
            return "BLOCKED_CORE"

    return plan["launch_status"]


def build_packet(db, plan, meta):
    acc = plan["account_id"]

    activity = account_activity(db, acc)

    packet = dict(plan)

    packet["account_meta"] = {
        "name": meta.get("name") if meta else None,
        "billing_mode": meta.get("billing_mode") if meta else None,
        "company_niche": meta.get("company_niche") if meta else None,
        "company_website": meta.get("company_website") if meta else None,
        "has_avito_user_id": bool(meta and meta.get("avito_user_id")),
        "has_avito_client_id": bool(meta and meta.get("avito_client_id")),
    }

    packet["owner_user_id"] = (
        meta.get("owner_user_id")
        if meta
        else None
    )

    packet["activity"] = activity
    packet["looks_test_account"] = looks_test_account(acc)

    packet["source_launch_status"] = plan["launch_status"]
    packet["launch_status"] = refine_status(plan, meta)

    # Operational cohort is evidence, not a deletion/disable instruction.
    if packet["looks_test_account"]:
        cohort = "QA_OR_TEST"
    elif packet["product_count"] > 0:
        cohort = "PRODUCT_CONNECTED"
    elif activity["has_activity"]:
        cohort = "NO_PRODUCT_BUT_ACTIVE"
    else:
        cohort = "NO_PRODUCT_INACTIVE"

    packet["cohort"] = cohort

    return packet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist ONLY to factory_launch_state",
    )
    args = parser.parse_args()

    plans = json.load(
        open(
            "/root/BORIS/backend/run/factory_b_plan.json",
            encoding="utf-8",
        )
    )

    db = SessionLocal()

    try:
        metas = account_meta(db)

        packets = []

        for plan in plans:
            meta = metas.get(plan["account_id"])
            packets.append(
                build_packet(db, plan, meta)
            )

        status = Counter(
            p["launch_status"]
            for p in packets
        )

        cohorts = Counter(
            p["cohort"]
            for p in packets
        )

        eligible = [
            p for p in packets
            if FS.eligible(p)
        ]

        print("=== FACTORY C ===")
        print("packets:", len(packets))

        print("\n--- FINAL LAUNCH STATUS ---")
        for k, v in status.most_common():
            print("%-22s %3d" % (k, v))

        print("\n--- COHORTS ---")
        for k, v in cohorts.most_common():
            print("%-24s %3d" % (k, v))

        print("\n--- ZERO TOUCH ELIGIBLE ---")
        print("%d из %d" % (
            len(eligible),
            len(packets),
        ))

        for p in eligible:
            print(
                "  %-34s products=%d cohort=%s"
                % (
                    p["account_id"][:34],
                    p["product_count"],
                    p["cohort"],
                )
            )

        suspicious = [
            p for p in packets
            if p["launch_status"] == "NO_PRODUCTS"
            and p["cohort"] == "NO_PRODUCT_BUT_ACTIVE"
        ]

        print("\n--- NO PRODUCT BUT ACTIVE — REVIEW, НЕ УДАЛЯТЬ ---")
        print(len(suspicious))

        for p in suspicious:
            a = p["activity"]
            print(
                "  %-34s msg=%d facts=%d storage=%d"
                % (
                    p["account_id"][:34],
                    a["messenger_messages"],
                    a["client_facts"],
                    a["storage_rows"],
                )
            )

        tests = [
            p for p in packets
            if p["cohort"] == "QA_OR_TEST"
        ]

        print("\n--- QA/TEST BY NAME HEURISTIC — INFO ONLY ---")
        print(len(tests))

        for p in tests:
            print("  " + p["account_id"])

        with open(
            "/root/BORIS/backend/run/factory_c_packets.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                packets,
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        if args.apply:
            print("\n--- APPLY INTERNAL FACTORY STATE ---")

            for p in packets:
                FS.upsert(db, p)

            db.commit()

            print("written:", len(packets))

        else:
            print("\nDRY RUN: database not changed")

        print(
            "\nreport=/root/BORIS/backend/run/factory_c_packets.json"
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
