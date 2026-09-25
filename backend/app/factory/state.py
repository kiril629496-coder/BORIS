from app.factory.product_truth import reconcile_packet as reconcile_product_truth
"""BORIS Factory state — FACTORY_C_V1.

Internal control-plane state only.

This module must never:
- spend money
- change bids
- send client messages
- change cron
- edit Client Config
- activate paid products
"""

import hashlib
import json

from sqlalchemy import text


VALID_STATUSES = {
    "ZERO_TOUCH_READY",
    "NEEDS_PLATFORM",
    "NEEDS_CLIENT",
    "NO_PRODUCTS",
    "BLOCKED_NO_OWNER",
    "BLOCKED_CORE",
}


def fingerprint(packet):
    raw = json.dumps(
        packet,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def normalize_status(status):
    status = str(status or "").strip().upper()

    if status not in VALID_STATUSES:
        raise ValueError(
            "unknown Factory launch status: %r" % status
        )

    return status


def _resolved_business_niche(packet):
    """Return only persisted/Factory-resolved business direction evidence."""
    meta = packet.get("account_meta") or {}
    onboarding = packet.get("onboarding") or {}
    business = onboarding.get("business") or {}

    for value in (
        meta.get("company_niche"),
        business.get("niche"),
    ):
        value = str(value or "").strip()
        if value:
            return value

    return None


def eligible(packet):
    """Fail closed.

    Zero-touch means:
    - explicit ZERO_TOUCH_READY
    - owner exists
    - at least one actual product
    - no core blockers
    - no human blockers
    """
    if normalize_status(packet["launch_status"]) != "ZERO_TOUCH_READY":
        return False

    if packet.get("owner_user_id") is None:
        return False

    if int(packet.get("product_count") or 0) <= 0:
        return False

    if packet.get("core_blockers"):
        return False

    if packet.get("human_questions"):
        blockers = [
            q for q in packet["human_questions"]
            if q.get("severity") == "blocker"
        ]
        if blockers:
            return False

    return True


def upsert(db, packet):
    packet = reconcile_product_truth(db, packet)

    # ACCOUNT_MASTER_OFF_V1 — authoritative fail-closed fence.
    # No caller of Factory state may resurrect a tenant disabled by the master
    # actions/background kill switch. Enforce here, at the single persistence
    # boundary, rather than relying only on individual runners to remember it.
    _account_id = str(packet.get("account_id") or "").strip()

    # FACTORY_ONBOARDING_TRUTH_MERGE_V1
    # C is refreshed before D in the periodic cycle. Reuse the last persisted
    # onboarding evidence while computing readiness so C cannot transiently
    # resurrect a client that D has already proved incomplete.
    if _account_id and not packet.get("onboarding"):
        _existing_onboarding = db.execute(text("""
            SELECT packet->'onboarding'
            FROM factory_launch_state
            WHERE account_id=:a
        """), {"a": _account_id}).scalar()

        if isinstance(_existing_onboarding, dict):
            packet = dict(packet)
            packet["onboarding"] = _existing_onboarding

    if _account_id:
        _disabled = db.execute(text("""
            SELECT 1 FROM reliability_kill_switches
             WHERE account_id=:a AND blocked=true
               AND module IN ('actions','background')
             LIMIT 1
        """), {"a": _account_id}).first()
        if _disabled:
            packet = dict(packet)
            packet["source_launch_status"] = packet.get("source_launch_status") or packet.get("launch_status")
            packet["launch_status"] = "BLOCKED_CORE"
            blockers = [x for x in (packet.get("core_blockers") or [])
                        if not (isinstance(x, dict) and x.get("code") == "account_disabled")]
            blockers.append({
                "code": "account_disabled",
                "class": "SYSTEM_POLICY",
                "severity": "blocker",
                "human_required": False,
                "reason": "BORIS actions disabled for this account",
            })
            packet["core_blockers"] = blockers
            packet["account_disabled"] = True

    # K7.2 — status must agree with authoritative product truth.
    if (
        packet.get("launch_status") == "NO_PRODUCTS"
        and int(packet.get("product_count") or 0) > 0
    ):
        old_status = packet.get("launch_status")
    
        human_questions = (
            packet.get("human_questions")
            or []
        )
    
        platform_actions = (
            packet.get("platform_actions")
            or []
        )
    
        core_blockers = (
            packet.get("core_blockers")
            or []
        )
    
        human_blocked = bool(
            human_questions
        )
    
        platform_blocked = any(
            isinstance(item, dict)
            and (
                item.get("severity") == "blocker"
                or item.get("action") == "ASSIGN"
            )
            for item in platform_actions
        )
    
        if not platform_blocked:
            platform_blocked = any(
                isinstance(item, dict)
                and item.get("class")
                    == "OPERATIONAL_CONFIG"
                for item in core_blockers
            )
    
        if human_blocked:
            new_status = "NEEDS_CLIENT"
        elif platform_blocked:
            new_status = "NEEDS_PLATFORM"
        else:
            new_status = "ZERO_TOUCH_READY"
    
        packet.setdefault(
            "source_launch_status",
            old_status,
        )
    
        packet["launch_status"] = new_status
    
        packet["product_truth_status_reconciled"] = {
            "from": old_status,
            "to": new_status,
            "reason": "authoritative_product_count_gt_zero",
        }

    # ZERO_TOUCH_BUSINESS_PROFILE_GATE_V1
    # A client cannot be called zero-touch ready without a concrete business
    # direction. This is an internal fail-closed gate only; it never invents
    # the niche and never performs an external action.
    if (
        packet.get("launch_status") == "ZERO_TOUCH_READY"
        and not _resolved_business_niche(packet)
    ):
        questions = list(packet.get("human_questions") or [])

        if not any(
            isinstance(item, dict)
            and item.get("field") == "business.niche"
            for item in questions
        ):
            questions.append({
                "actor": "CLIENT",
                "field": "business.niche",
                "action": "ASK",
                "screen": "Укажите направление бизнеса",
                "evidence": {
                    "reason": "business niche absent from profile and Factory onboarding",
                },
                "severity": "blocker",
            })

        packet["human_questions"] = questions
        packet["launch_status"] = "NEEDS_CLIENT"
        packet["business_profile_status_reconciled"] = {
            "from": "ZERO_TOUCH_READY",
            "to": "NEEDS_CLIENT",
            "reason": "business_niche_required_for_zero_touch",
        }

    status = normalize_status(packet["launch_status"])
    fp = fingerprint(packet)

    result = db.execute(text("""
        INSERT INTO factory_launch_state (
            account_id,
            owner_user_id,
            launch_status,
            product_count,
            readiness_pct,
            human_questions,
            boris_actions,
            platform_actions,
            core_blocked,
            eligible_zero_touch,
            packet,
            source_fingerprint,
            created_at,
            updated_at
        )
        VALUES (
            :account_id,
            :owner_user_id,
            :launch_status,
            :product_count,
            :readiness_pct,
            :human_questions,
            :boris_actions,
            :platform_actions,
            :core_blocked,
            :eligible_zero_touch,
            CAST(:packet AS jsonb),
            :source_fingerprint,
            now(),
            now()
        )
        ON CONFLICT (account_id)
        DO UPDATE SET
            owner_user_id = EXCLUDED.owner_user_id,
            launch_status = EXCLUDED.launch_status,
            product_count = EXCLUDED.product_count,
            readiness_pct = EXCLUDED.readiness_pct,
            human_questions = EXCLUDED.human_questions,
            boris_actions = EXCLUDED.boris_actions,
            platform_actions = EXCLUDED.platform_actions,
            core_blocked = EXCLUDED.core_blocked,
            eligible_zero_touch = EXCLUDED.eligible_zero_touch,
            -- FACTORY_PACKET_PRESERVE_V1
            -- C owns the base launch packet. Enrichers such as Factory D own
            -- their namespaced sections. A C refresh must never erase them.
            packet =
                CASE
                    WHEN factory_launch_state.packet ? 'onboarding'
                    THEN EXCLUDED.packet ||
                         jsonb_build_object(
                             'onboarding',
                             factory_launch_state.packet->'onboarding'
                         )
                    ELSE EXCLUDED.packet
                END,
            source_fingerprint = EXCLUDED.source_fingerprint,
            updated_at =
                CASE
                    WHEN factory_launch_state.source_fingerprint
                         IS DISTINCT FROM EXCLUDED.source_fingerprint
                    THEN now()
                    ELSE factory_launch_state.updated_at
                END
        RETURNING id
    """), {
        "account_id": packet["account_id"],
        "owner_user_id": packet.get("owner_user_id"),
        "launch_status": status,
        "product_count": int(packet.get("product_count") or 0),
        "readiness_pct": int(packet.get("self_pct") or 0),
        "human_questions": len(packet.get("human_questions") or []),
        "boris_actions": len(packet.get("boris_actions") or []),
        "platform_actions": len(packet.get("platform_actions") or []),
        "core_blocked": bool(packet.get("core_blockers")),
        "eligible_zero_touch": eligible(packet),
        "packet": json.dumps(
            packet,
            ensure_ascii=False,
            default=str,
        ),
        "source_fingerprint": fp,
    })

    return result.scalar()


def get(db, account_id):
    row = db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            launch_status,
            product_count,
            readiness_pct,
            human_questions,
            boris_actions,
            platform_actions,
            core_blocked,
            eligible_zero_touch,
            packet,
            source_fingerprint,
            created_at,
            updated_at
        FROM factory_launch_state
        WHERE account_id=:a
    """), {"a": account_id}).mappings().first()

    return dict(row) if row else None
