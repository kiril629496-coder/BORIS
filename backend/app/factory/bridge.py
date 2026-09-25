"""BORIS Factory automatic bridge — FACTORY_D_V1.

Purpose:
    account state -> Factory A evidence -> Factory B plan
    -> Factory C packet -> factory_launch_state.

Allowed write target:
    factory_launch_state ONLY.

Forbidden:
    client_config writes
    storage writes
    accounts writes
    money_mandates writes
    tg_routes writes
    cron writes
    external API calls
    client messages
    money/bid actions
"""

from sqlalchemy import text

import factory_readiness as A
import factory_b as B
import factory_c as C

from app.factory import state as FS

from app.factory.authoritative_snapshot import build_snapshot


def _account_rows(db):
    return db.execute(text("""
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


def _meta_map(db):
    return {
        row["account_id"]: dict(row)
        for row in _account_rows(db)
    }


def _blocked_no_owner(meta):
    """Explicit packet for accounts A used to skip."""

    account_id = meta["account_id"]

    return {
        "account_id": account_id,
        "owner_user_id": None,

        "products": [],
        "product_count": 0,

        "source_launch_status": "BLOCKED_NO_OWNER",
        "launch_status": "BLOCKED_NO_OWNER",

        "self_pct": 0,

        "human_questions": [],
        "boris_actions": [],
        "platform_actions": [],

        "core_blockers": [
            {
                "field": "owner_user_id",
                "class": "OPERATIONAL_CONFIG",
                "evidence": {
                    "reason": "account has no owner_user_id"
                },
            }
        ],

        "account_meta": {
            "name": meta.get("name"),
            "billing_mode": meta.get("billing_mode"),
            "company_niche": meta.get("company_niche"),
            "company_website": meta.get("company_website"),
            "has_avito_user_id": bool(
                meta.get("avito_user_id")
            ),
            "has_avito_client_id": bool(
                meta.get("avito_client_id")
            ),
        },

        "activity": {
            "messenger_messages": 0,
            "client_facts": 0,
            "storage_rows": 0,
            "has_activity": False,
        },

        "looks_test_account": C.looks_test_account(
            account_id
        ),

        "cohort": (
            "QA_OR_TEST"
            if C.looks_test_account(account_id)
            else "BLOCKED_NO_OWNER"
        ),
    }


def build_all(db):
    """Build packets from live DB state.

    No writes.
    """

    metas = _meta_map(db)
    account_ids = sorted(metas)

    cron_map = A.client_cron(account_ids)

    packets = []

    for account_id in account_ids:
        meta = metas[account_id]
        owner_id = meta.get("owner_user_id")

        if owner_id is None:
            packets.append(
                _blocked_no_owner(meta)
            )
            continue

        report = A.account_report(
            db,
            account_id,
            owner_id,
            cron_map,
        )

        plan = B.make_plan(report)
        plan["owner_user_id"] = owner_id
        plan["authoritative_snapshot"] = build_snapshot(
            db,
            account_id,
        )

        packet = C.build_packet(
            db,
            plan,
            meta,
        )

        packet["authoritative_snapshot"] = build_snapshot(
            db,
            account_id,
        )

        packets.append(packet)

    return packets


def build_one(db, account_id):
    metas = _meta_map(db)

    meta = metas.get(account_id)

    if not meta:
        return None

    if meta.get("owner_user_id") is None:
        return _blocked_no_owner(meta)

    cron_map = A.client_cron(
        [account_id]
    )

    report = A.account_report(
        db,
        account_id,
        meta["owner_user_id"],
        cron_map,
    )

    plan = B.make_plan(report)
    plan["owner_user_id"] = meta["owner_user_id"]
    plan["authoritative_snapshot"] = build_snapshot(
        db,
        account_id,
    )

    packet = C.build_packet(
        db,
        plan,
        meta,
    )

    packet["authoritative_snapshot"] = build_snapshot(
        db,
        account_id,
    )

    return packet


def apply_packet(db, packet):
    """Single allowed write."""

    return FS.upsert(
        db,
        packet,
    )


def apply_all(db, packets):
    ids=[]

    for packet in packets:
        ids.append(
            apply_packet(db, packet)
        )

    return ids
