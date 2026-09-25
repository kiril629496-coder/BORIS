from datetime import timezone
"""has_entitlement(): the single read-only answer to "is this product allowed?".

Proven source of truth (measured 15.08): storage:billing - untyped JSON.
ai_licenses is referenced only by app/api/ai_bindings.py and is empty, so it is
NOT a gate. product_limits has the right shape but is filled for two resources
only; it is consulted as an owner-level fallback.

This module NEVER writes and NEVER changes billing. It only reads what the
production gates (calltracking.py, messenger.py) already read, in one place.
"""

import datetime
import json

# product -> (purchased key, paid_until key, legacy gate that owns it)
BILLING_KEYS = {
    "mop": ("manager_msgs_purchased", "manager_paid_until", "app/api/messenger.py:591"),
    "rop": ("rop_minutes_purchased", "rop_paid_until", "app/api/calltracking.py:1074"),
    # ЕЦС (единый центр сопровождения): отдельный штатный gate по аналогии с mop/rop.
    # В billing должно появляться ecs_purchased > 0 и ecs_paid_until >= now.
    # Точка записи: app/api/admin_clients.py:ProvisionBody + provision()
    # с вызовом app/api/billing.py:add_ecs_package(...).
    "ecs": ("ecs_purchased", "ecs_paid_until", "app/api/billing.py:add_ecs_package"),
}

UNKNOWN = "unknown"


def _parse(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {}


def _as_date(value):
    if not value:
        return None
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value if isinstance(value, datetime.datetime) else datetime.datetime(
            value.year, value.month, value.day)
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "").strip())
    except Exception:
        return None



def _utc_aware_datetime(value):
    """Normalize PostgreSQL/legacy naive datetime to UTC-aware datetime."""
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        from datetime import timezone
        return value.replace(tzinfo=timezone.utc)
    return value



def _utc_epoch(value):
    """Return UTC epoch for naive or timezone-aware datetime."""
    if value is None:
        return None

    tzinfo = getattr(value, "tzinfo", None)

    if tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.timestamp()


def decide(product, billing, now=None):
    """Pure decision. Returns allowed/state/evidence without touching the DB."""
    now = now or datetime.datetime.now()
    keys = BILLING_KEYS.get(product)
    if not keys:
        return {"product": product, "allowed": False, "state": UNKNOWN,
                "reason": "unknown_product", "source": None, "evidence": {}}
    purchased_key, until_key, owner = keys
    if not billing:
        return {"product": product, "allowed": False, "state": UNKNOWN,
                "reason": "no_billing_record", "source": "storage:billing",
                "gate_owner": owner, "evidence": {}}
    if purchased_key not in billing:
        # untyped JSON: absence proves nothing, so this is unknown, not "off"
        return {"product": product, "allowed": False, "state": UNKNOWN,
                "reason": "key_absent_in_untyped_json", "source": "storage:billing",
                "gate_owner": owner, "evidence": {"looked_for": purchased_key}}
    try:
        purchased = float(billing.get(purchased_key) or 0)
    except (TypeError, ValueError):
        purchased = 0.0
    until = _as_date(billing.get(until_key))
    if until is not None and getattr(until, "tzinfo", None) is None:
        # PostgreSQL/legacy values may arrive without timezone.
        # The application clock is UTC-aware, therefore normalize
        # naive timestamps before comparison.
        until = until.replace(tzinfo=timezone.utc)

    expired = bool(
        until is not None
        and _utc_epoch(until) < _utc_epoch(now)
    )
    allowed = purchased > 0 and not expired
    return {"product": product, "allowed": allowed, "state": "known",
            "reason": ("ok" if allowed else ("expired" if expired else "nothing_purchased")),
            "source": "storage:billing", "gate_owner": owner,
            "evidence": {purchased_key: purchased, until_key: str(until) if until else None}}


def has_entitlement(db, account_id, product):
    """Read-only entitlement check for one account."""
    from sqlalchemy import text
    row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='billing'"),
                     {"a": account_id}).fetchone()
    billing = _parse(row[0]) if row else None
    out = decide(product, billing)
    out["account_id"] = account_id
    return out


def owner_pool(db, billing_owner_id, resource, billing_owner_kind="user"):
    """Typed owner-level pool from product_limits. Absence here IS provable."""
    from sqlalchemy import text
    row = db.execute(text(
        "SELECT purchased, used, paid_until FROM product_limits"
        " WHERE billing_owner_kind=:k AND billing_owner_id=:o AND resource=:r"),
        {"k": billing_owner_kind, "o": billing_owner_id, "r": resource}).fetchone()
    if not row:
        return {"resource": resource, "state": "known", "allowed": False,
                "reason": "no_row_in_product_limits", "source": "product_limits"}
    purchased, used, until = row
    left = (purchased or 0) - (used or 0)
    return {"resource": resource, "state": "known", "allowed": left > 0,
            "reason": "ok" if left > 0 else "exhausted", "source": "product_limits",
            "evidence": {"purchased": purchased, "used": used, "left": left,
                         "paid_until": str(until) if until else None}}
