"""
BORIS Factory product truth reconciliation.

This module does NOT grant products.

It reads already-existing authoritative evidence and makes sure
a packet cannot silently lose a product which existing BORIS data
already proves is connected.

Rules:
- additive only
- never remove packet products
- UNKNOWN entitlement is not True
- no writes to billing/storage
- platform is infrastructure and never counts as a product
"""

from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy import text

from app.client_config import entitlements as E


_ALLOWED = (
    "mop",
    "rop",
    "feed",
    "cpx",
    "autopilot",
)


def _storage(db, account_id: str, key: str):
    """Read one storage JSON value safely."""

    row = db.execute(
        text(
            """
            SELECT value
            FROM storage
            WHERE account_id=:account
              AND key=:key
            LIMIT 1
            """
        ),
        {
            "account": account_id,
            "key": key,
        },
    ).scalar()

    if row is None:
        return None

    if isinstance(
        row,
        (
            dict,
            list,
            int,
            float,
            bool,
        ),
    ):
        return row

    if isinstance(row, str):

        value = row.strip()

        if not value:
            return None

        try:
            return json.loads(value)
        except Exception:
            return value

    return row


def detect_existing_products(
    db,
    account_id: str,
) -> Dict[str, Any]:
    """
    Mirror the proven Factory A product evidence.

    This is READ ONLY.
    """

    result: Dict[str, Any] = {
        "platform": True,
    }

    for product in (
        "mop",
        "rop",
    ):
        decision = E.has_entitlement(
            db,
            account_id,
            product,
        )

        if decision.get("allowed"):
            result[product] = True

        elif (
            decision.get("state")
            == E.UNKNOWN
        ):
            result[product] = None

        else:
            result[product] = False

    result["feed"] = bool(
        _storage(
            db,
            account_id,
            "feed_prefs",
        )
        or
        _storage(
            db,
            account_id,
            "feed_items",
        )
    )

    result["cpx"] = bool(
        _storage(
            db,
            account_id,
            "kpi_settings",
        )
        or
        _storage(
            db,
            account_id,
            "cpx_advice",
        )
    )

    autopilot = (
        _storage(
            db,
            account_id,
            "autopilot_settings",
        )
        or {}
    )

    if isinstance(
        autopilot,
        dict,
    ):
        mode = autopilot.get("mode")
    else:
        mode = None

    result["autopilot"] = bool(
        mode
        and mode not in (
            "off",
            "manual",
        )
    )

    return result


# BORIS_K9_FACTORY_A_COMPAT
def _factory_a_truth(
    db,
    account_id: str,
) -> Dict[str, Any]:
    """
    Compatibility bridge to the canonical Factory A detector.

    READ ONLY.
    No grants and no persistence.
    Local import prevents application startup dependency cycles.
    """

    import factory_a as factory_a_module

    detected = factory_a_module.detect_products(
        db,
        account_id,
    )

    if not isinstance(detected, dict):
        return {}

    allowed = {
        "mop",
        "rop",
        "feed",
        "cpx",
        "autopilot",
    }

    return {
        key: value
        for key, value in detected.items()
        if key in allowed
    }



def reconcile_packet(
    db,
    packet: dict,
) -> dict:
    """
    Add authoritative True products to packet.

    Existing packet products are preserved unchanged.
    """

    if not isinstance(packet, dict):
        return packet

    account_id = packet.get(
        "account_id"
    )

    if not account_id:
        return packet

    existing = packet.get(
        "products"
    )

    if not isinstance(
        existing,
        list,
    ):
        existing = []

    merged = list(existing)

    if "platform" not in merged:
        merged.insert(
            0,
            "platform",
        )

    detected = detect_existing_products(
        db,
        str(account_id),
    )

    canonical_detected = _factory_a_truth(
        db,
        str(account_id),
    )

    # Canonical True may add a missing product.
    # It can never remove an existing product.
    for key, value in canonical_detected.items():
        if value is True:
            detected[key] = True

    for product in _ALLOWED:

        if (
            detected.get(product)
            is True
            and product not in merged
        ):
            merged.append(product)

    packet = dict(packet)

    packet["products"] = merged

    packet["product_count"] = len(
        [
            p
            for p in merged
            if p != "platform"
        ]
    )

    packet[
        "product_truth_reconciled"
    ] = True

    return packet
