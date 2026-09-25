"""FACTORY_E_V2 — dynamic Factory decision planner.

READ ONLY.

Reads:
    factory_launch_state

Writes:
    /root/BORIS/backend/run/factory_e_packets.json
    /root/BORIS/backend/run/factory_e_summary.json

Does NOT:
    update DB
    call external APIs
    send messages
    publish
    change bids
    change money
    change Client Config
"""

import json
from collections import Counter

from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import executors as X


FIELD_MAP = {
    # DERIVE
    "profile.timezone":
        "derive.timezone",

    "kpi.business_hours":
        "derive.business_hours_candidate",

    "profile.city":
        "derive.city",


    # FACTORY_I_TONE_MAPPING_V1
    "profile.tone_of_voice":
        "derive.tone_of_voice",

    "knowledge.prompt_ref":
        "derive.knowledge_refs",

    "knowledge.facts_scope":
        "derive.knowledge_refs",

    "products.mop":
        "derive.product_entitlement",

    "products.rop":
        "derive.product_entitlement",

    "products.feed":
        "derive.product_entitlement",

    "products.cpx":
        "derive.product_entitlement",

    # SCHEDULE
    "schedule.autopilot":
        "migrate.schedule",

    "schedule.mop_poll":
        "migrate.schedule",

    "schedule.memory_extract":
        "migrate.schedule",

    "schedule.reports":
        "assign.report_schedule",

    # PLATFORM
    "channels.avito":
        "assign.avito_connection",

    "channels.telegram_routes":
        "assign.telegram_route",

    "access.roles":
        "assign.access_roles",

    "automation.mode":
        "assign.product_mode",

    # MONEY
    "automation.money_actions":
        "money.enable_actions",

    "limits.max_bid_delta_pct":
        "money.change_bid",

    "limits.max_actions_run":
        "money.enable_actions",

    "limits.daily_budget_rub":
        "money.change_budget",

    "kpi.daily_budget_limit_rub":
        "money.change_budget",
}


def operation_for(action):
    actor = action.get("actor")
    kind = action.get("action")

    # CLIENT ASK is a human decision request.
    # It MUST override FIELD_MAP.
    if actor == "CLIENT" and kind == "ASK":
        return "ask.client"

    field = action.get("field")

    # FACTORY_EXISTING_PLATFORM_TRUTH_V1
    # A canonical existing fact must stay a read-only derivation. Do not turn
    # BORIS verification into a platform assignment that asks for confirmation.
    if actor == "BORIS" and kind == "DERIVE":
        if field == "channels.avito":
            return "derive.avito_connection"
        if field == "access.roles":
            return "derive.access_roles"
        if field in (
            "automation.money_actions",
            "kpi.daily_budget_limit_rub",
            "limits.daily_budget_rub",
            "limits.max_actions_run",
            "limits.max_bid_delta_pct",
        ):
            return "derive.money_policy"

    if actor == "BORIS" and kind == "MIGRATE" and field in (
        "limits.daily_budget_rub",
        "limits.max_actions_run",
        "limits.max_bid_delta_pct",
    ):
        return "derive.money_policy"

    if field in FIELD_MAP:
        return FIELD_MAP[field]

    actor = action.get("actor")
    kind = action.get("action")

    # Generic safe fallback only where semantics are already explicit.
    if actor == "BORIS" and kind == "DERIVE":
        return "derive.knowledge_refs"

    if actor == "BORIS" and kind == "MIGRATE":
        return "migrate.schedule"

    return None


def decision(action, account_eligible):
    operation = operation_for(action)

    if operation is None:
        return {
            "field": action.get("field"),
            "operation": None,
            "base_policy": X.BLOCKED,
            "policy": X.BLOCKED,
            "risk": "unknown",
            "reason": "no_executor_mapping",
            "source_action": action,
        }

    base = X.policy_for(operation)

    effective = X.effective_policy(
        operation,
        account_eligible,
    )

    out = {
        "field": action.get("field"),
        "operation": operation,
        "base_policy": base["policy"],
        "policy": effective["policy"],
        "risk": effective["risk"],
        "money": effective.get("money", False),
        "visible": effective.get("visible", False),
        "reversible": effective.get("reversible", False),
        "source_action": action,
    }

    if effective.get("reason"):
        out["reason"] = effective["reason"]

    return out


def packet_for(row):
    packet = row["packet"] or {}

    if isinstance(packet, str):
        packet = json.loads(packet)

    account_eligible = bool(
        row["eligible_zero_touch"]
    )

    source_actions = []

    for key in (
        "boris_actions",
        "platform_actions",
        "human_questions",
    ):
        for action in packet.get(key) or []:
            item = dict(action)
            item["_source_bucket"] = key
            source_actions.append(item)

    decisions = [
        decision(a, account_eligible)
        for a in source_actions
    ]

    buckets = {
        X.AUTO_ALLOWED: [],
        X.CONFIRM_REQUIRED: [],
        X.MANDATE_REQUIRED: [],
        X.BLOCKED: [],
        X.NOOP: [],
    }

    for d in decisions:
        buckets.setdefault(
            d["policy"],
            [],
        ).append(d)

    return {
        "account_id":
            row["account_id"],

        "owner_user_id":
            row["owner_user_id"],

        "launch_status":
            row["launch_status"],

        "readiness_pct":
            row["readiness_pct"],

        "eligible_zero_touch":
            account_eligible,

        "onboarding":
            packet.get("onboarding"),

        "source_actions_count":
            len(source_actions),

        "decisions":
            decisions,

        "auto_allowed":
            buckets[X.AUTO_ALLOWED],

        "confirm_required":
            buckets[X.CONFIRM_REQUIRED],

        "mandate_required":
            buckets[X.MANDATE_REQUIRED],

        "blocked":
            buckets[X.BLOCKED],

        "noop":
            buckets[X.NOOP],
    }


def main():
    db = SessionLocal()

    try:
        rows = db.execute(text("""
            SELECT
                account_id,
                owner_user_id,
                launch_status,
                readiness_pct,
                eligible_zero_touch,
                packet
            FROM factory_launch_state f
            WHERE NOT EXISTS (
                SELECT 1 FROM reliability_kill_switches r
                 WHERE r.account_id=f.account_id AND r.blocked=true
                   AND r.module IN ('actions','background')
            )
            ORDER BY f.account_id
        """)).mappings().all()

        packets = [
            packet_for(row)
            for row in rows
        ]

        db_eligible = sum(
            1
            for row in rows
            if row["eligible_zero_touch"]
        )

        packet_eligible = sum(
            1
            for p in packets
            if p["eligible_zero_touch"]
        )

        statuses = Counter(
            p["launch_status"]
            for p in packets
        )

        effective = Counter()
        base = Counter()

        for p in packets:
            for d in p["decisions"]:
                effective[d["policy"]] += 1
                base[d["base_policy"]] += 1

        print("=== FACTORY E v2 ===")
        print("accounts:", len(packets))
        print(
            "eligible:",
            packet_eligible,
        )

        print()
        print("--- ACCOUNT STATES ---")

        for status,n in statuses.most_common():
            print(
                "%-22s %3d"
                % (status,n)
            )

        print()
        print("--- BASE OPERATION POLICIES ---")

        for policy,n in base.most_common():
            print(
                "%-22s %3d"
                % (policy,n)
            )

        print()
        print("--- EFFECTIVE POLICIES ---")

        for policy,n in effective.most_common():
            print(
                "%-22s %3d"
                % (policy,n)
            )

        print()
        print("--- ZERO-TOUCH EXECUTOR PLAN ---")

        for p in packets:
            if not p["eligible_zero_touch"]:
                continue

            print()
            print(
                "%s readiness=%s%%"
                % (
                    p["account_id"],
                    p["readiness_pct"],
                )
            )

            print(
                "  AUTO     %d"
                % len(p["auto_allowed"])
            )

            for d in p["auto_allowed"]:
                print(
                    "    %-32s <- %s"
                    % (
                        d["operation"],
                        d["field"],
                    )
                )

            print(
                "  CONFIRM  %d"
                % len(p["confirm_required"])
            )

            for d in p["confirm_required"]:
                print(
                    "    %-32s <- %s"
                    % (
                        d["operation"],
                        d["field"],
                    )
                )

            print(
                "  MANDATE  %d"
                % len(p["mandate_required"])
            )

            for d in p["mandate_required"]:
                print(
                    "    %-32s <- %s"
                    % (
                        d["operation"],
                        d["field"],
                    )
                )

            print(
                "  BLOCKED  %d"
                % len(p["blocked"])
            )

            for d in p["blocked"]:
                print(
                    "    %-32s <- %s (%s)"
                    % (
                        str(
                            d["operation"]
                            or "NO_MAPPING"
                        ),
                        d["field"],
                        d.get(
                            "reason",
                            "blocked",
                        ),
                    )
                )

        with open(
            "/root/BORIS/backend/run/factory_e_packets.json",
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

        summary = {
            "accounts": len(packets),
            "eligible": packet_eligible,
            "db_eligible": db_eligible,
            "statuses": dict(statuses),
            "base_policies": dict(base),
            "effective_policies": dict(effective),
        }

        with open(
            "/root/BORIS/backend/run/factory_e_summary.json",
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                summary,
                fh,
                ensure_ascii=False,
                indent=2,
            )

        print()
        print(
            "packets=/root/BORIS/backend/run/factory_e_packets.json"
        )

        print(
            "summary=/root/BORIS/backend/run/factory_e_summary.json"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
