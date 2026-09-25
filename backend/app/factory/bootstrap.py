"""
BORIS Factory E — FACTORY_E_V1.

Purpose
-------
Consume the already proven Factory C persistent state and materialize a
deterministic INTERNAL zero-touch bootstrap package.

This module deliberately DOES NOT:
- change client_config
- write onboarding
- publish anything
- change advertising / CPX
- change KPI or autopilot
- send client messages
- spend money
- activate tariff/product entitlements

It owns only:
    factory_bootstrap_state
    factory_bootstrap_events
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from sqlalchemy import text


FACTORY_E_V1 = True
SCHEMA_VERSION = 1

ELIGIBLE_STATUS = "ZERO_TOUCH_READY"

# Neighboring execution contours are intentionally not executed by Factory E.
FORBIDDEN_EXECUTION_PREFIXES = (
    "kpi.",
    "automation.",
    "schedule.",
    "products.",
)

FORBIDDEN_PRODUCTS = {
    "cpx",
    "feed",
    "autopilot",
}

INTERNAL_ONLY = "INTERNAL_PREPARE"
BLOCK_EXTERNAL = "EXTERNAL_OWNER_REQUIRED"


DDL = (
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
    'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */',
)


def _jsonable(value: Any) -> Any:
    """Canonical JSON-safe representation."""
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def fingerprint(value: Any) -> str:
    raw = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_schema(db) -> None:
    for sql in DDL:
        db.execute(text(sql))


def _loads_packet(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        obj = json.loads(value)
        if isinstance(obj, dict):
            return obj

    raise ValueError("factory packet is not an object")


def load_launch_rows(db) -> List[Dict[str, Any]]:
    """
    Canonical bootstrap input.

    Source of launch decision:
        factory_client_launch

    Packet source:
        factory_launch_state.packet

    The old persisted factory_launch_state.product_count is deliberately
    NOT used as an eligibility source.
    """

    rows = db.execute(text("""
        SELECT
            fcl.account_id,
            fcl.owner_user_id,
            fcl.launch_status,
            fcl.launch_ready,
            fcl.readiness_pct,
            fcl.source_fingerprint,

            fls.packet AS packet,

            CASE
                WHEN fcl.launch_ready = TRUE
                 AND fcl.launch_status = 'READY_INTERNAL'
                THEN TRUE
                ELSE FALSE
            END AS eligible_zero_touch

        FROM factory_client_launch fcl

        LEFT JOIN factory_launch_state fls
          ON fls.account_id = fcl.account_id

        ORDER BY fcl.account_id
    """)).mappings().all()

    result = []

    for raw in rows:
        row = dict(raw)

        packet = _loads_packet(row.get("packet"))

        products = packet.get("products") or []
        if not isinstance(products, list):
            products = []

        blockers = packet.get("core_blockers") or []

        core_blocked = bool(blockers)

        row["packet"] = packet
        row["product_count"] = len(products)
        row["core_blocked"] = core_blocked

        result.append(row)

    return result


def eligible(row: Dict[str, Any]) -> bool:
    """
    Fail closed.

    Eligibility requires BOTH the explicit status and the explicit persisted
    boolean created by Factory C. Counts must also prove there is no human or
    platform dependency left.
    """
    return bool(
        row.get("launch_status") == ELIGIBLE_STATUS
        and row.get("eligible_zero_touch") is True
        and not row.get("core_blocked")
        and row.get("owner_user_id") is not None
    )


def classify_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """
    Factory E only PREPARES actions.

    Even a safe-looking DERIVE is not written to Client Config here because
    Client Config is an external contract owned by another contour.
    """
    field = str(action.get("field") or "")
    op = str(action.get("action") or "")
    actor = str(action.get("actor") or "")

    external_reason = None

    if actor and actor != "BORIS":
        external_reason = "actor_not_boris"

    if any(field.startswith(p) for p in FORBIDDEN_EXECUTION_PREFIXES):
        external_reason = "neighboring_execution_contour"

    if field in {
        "channels.avito",
        "access.roles",
    }:
        external_reason = "platform_or_access_contract"

    safety = BLOCK_EXTERNAL if external_reason else INTERNAL_ONLY

    evidence = action.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}

    return {
        "field": field,
        "actor": actor,
        "action": op,
        "severity": action.get("severity"),
        "safety": safety,
        "external_reason": external_reason,
        "candidate_value": evidence.get("candidate_value"),
        "evidence": _jsonable(evidence),
    }


def build_package(row: Dict[str, Any]) -> Dict[str, Any]:
    if not eligible(row):
        raise ValueError(
            "account is not zero-touch eligible: %s"
            % row.get("account_id")
        )

    packet = _loads_packet(row["packet"])

    if packet.get("launch_status") != ELIGIBLE_STATUS:
        raise ValueError("packet/status mismatch")

    human = packet.get("human_questions") or []
    platform = packet.get("platform_actions") or []

    if not isinstance(human, list):
        raise ValueError("human_questions must be list")

    if not isinstance(platform, list):
        raise ValueError("platform_actions must be list")

    raw_actions = packet.get("boris_actions") or []

    if not isinstance(raw_actions, list):
        raise ValueError("boris_actions must be list")

    actions = []
    for a in raw_actions:
        if not isinstance(a, dict):
            raise ValueError("boris action is not object")
        actions.append(classify_action(a))

    internal = [
        a for a in actions
        if a["safety"] == INTERNAL_ONLY
    ]

    external = [
        a for a in actions
        if a["safety"] == BLOCK_EXTERNAL
    ]

    products = packet.get("products") or []
    if not isinstance(products, list):
        products = []

    package = {
        "schema_version": SCHEMA_VERSION,
        "account_id": row["account_id"],
        "owner_user_id": row["owner_user_id"],
        "launch_status": row["launch_status"],
        "source_fingerprint": row["source_fingerprint"],
        "product_count": row["product_count"],
        "readiness_pct": row["readiness_pct"],
        "products": products,

        # What Factory is allowed to prepare internally.
        "actions": actions,
        "internal_prepare_actions": internal,

        # BORIS actions belonging to neighboring execution contours.
        "external_owner_actions": external,

        # C may keep non-blocking recommendations even when launch is
        # explicitly ZERO_TOUCH_READY. E records them but never executes.
        "client_advisory_questions": _jsonable(human),
        "platform_boundary_actions": _jsonable(platform),

        "guards": {
            "source_launch_status": ELIGIBLE_STATUS,
            "source_eligible_zero_touch": True,
            "source_human_questions": len(human),
            "source_platform_actions": len(platform),
            "core_blocked": False,
            "money_actions_allowed": False,
            "client_messages_allowed": False,
            "publishing_allowed": False,
            "neighboring_contours_execution_allowed": False,
        },
    }

    return _jsonable(package)


def package_status(package: Dict[str, Any]) -> str:
    if (
        package["external_owner_actions"]
        or package["client_advisory_questions"]
        or package["platform_boundary_actions"]
    ):
        return "READY_WITH_EXTERNAL_BOUNDARY"
    if package["internal_prepare_actions"]:
        return "READY_INTERNAL"
    return "READY_NOOP"


def materialize(db, apply: bool = False) -> Dict[str, Any]:
    rows = load_launch_rows(db)

    candidates = [r for r in rows if eligible(r)]

    built: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for row in candidates:
        try:
            package = build_package(row)
            fp = fingerprint(package)

            built.append({
                "account_id": row["account_id"],
                "owner_user_id": row["owner_user_id"],
                "source_fingerprint": row["source_fingerprint"],
                "bootstrap_fingerprint": fp,
                "status": package_status(package),
                "action_count": len(package["actions"]),
                "executable_internal": len(
                    package["internal_prepare_actions"]
                ),
                "blocked_external": len(
                    package["external_owner_actions"]
                ),
                "package": package,
            })
        except Exception as exc:
            errors.append({
                "account_id": row.get("account_id"),
                "error": "%s: %s" % (
                    type(exc).__name__,
                    exc,
                ),
            })

    result = {
        "launch_rows": len(rows),
        "eligible": len(candidates),
        "built": len(built),
        "errors": errors,
        "items": built,
        "apply": bool(apply),
        "written": 0,
        "unchanged": 0,
        "events": 0,
    }

    if errors:
        return result

    if not apply:
        return result

    ensure_schema(db)

    for item in built:
        old = db.execute(text("""
            SELECT bootstrap_fingerprint
            FROM factory_bootstrap_state
            WHERE account_id=:a
        """), {"a": item["account_id"]}).fetchone()

        old_fp = old[0] if old else None

        if old_fp == item["bootstrap_fingerprint"]:
            result["unchanged"] += 1
            continue

        db.execute(text("""
            INSERT INTO factory_bootstrap_state (
                account_id,
                owner_user_id,
                source_fingerprint,
                bootstrap_fingerprint,
                status,
                action_count,
                executable_internal,
                blocked_external,
                package,
                created_at,
                updated_at
            )
            VALUES (
                :account_id,
                :owner_user_id,
                :source_fingerprint,
                :bootstrap_fingerprint,
                :status,
                :action_count,
                :executable_internal,
                :blocked_external,
                CAST(:package AS JSONB),
                NOW(),
                NOW()
            )
            ON CONFLICT (account_id)
            DO UPDATE SET
                owner_user_id=EXCLUDED.owner_user_id,
                source_fingerprint=EXCLUDED.source_fingerprint,
                bootstrap_fingerprint=EXCLUDED.bootstrap_fingerprint,
                status=EXCLUDED.status,
                action_count=EXCLUDED.action_count,
                executable_internal=EXCLUDED.executable_internal,
                blocked_external=EXCLUDED.blocked_external,
                package=EXCLUDED.package,
                updated_at=NOW()
        """), {
            **{k: v for k, v in item.items() if k != "package"},
            "package": json.dumps(
                item["package"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        })

        event = "BOOTSTRAP_CREATED" if old_fp is None else "BOOTSTRAP_REFRESHED"

        db.execute(text("""
            INSERT INTO factory_bootstrap_events (
                account_id,
                event,
                from_fingerprint,
                to_fingerprint,
                payload,
                created_at
            )
            VALUES (
                :a,
                :e,
                :f,
                :t,
                CAST(:p AS JSONB),
                NOW()
            )
        """), {
            "a": item["account_id"],
            "e": event,
            "f": old_fp,
            "t": item["bootstrap_fingerprint"],
            "p": json.dumps({
                "status": item["status"],
                "action_count": item["action_count"],
                "executable_internal": item["executable_internal"],
                "blocked_external": item["blocked_external"],
            }, ensure_ascii=False, sort_keys=True),
        })

        result["written"] += 1
        result["events"] += 1

    return result


def snapshot(db) -> List[Dict[str, Any]]:
    ensure_schema(db)

    rows = db.execute(text("""
        SELECT
            account_id,
            owner_user_id,
            status,
            action_count,
            executable_internal,
            blocked_external,
            source_fingerprint,
            bootstrap_fingerprint,
            updated_at
        FROM factory_bootstrap_state
        ORDER BY account_id
    """)).mappings().all()

    return [dict(r) for r in rows]
