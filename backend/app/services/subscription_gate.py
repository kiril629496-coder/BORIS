"""Shared base-subscription gate for account-scoped background/live work.

An explicit expired billing.paid_until is authoritative. Missing/invalid legacy
billing stays "unknown_allowed" so onboarding and old accounts are not
accidentally disabled. Platform-owned accounts (accounts.is_own) bypass the
customer subscription clock.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text


def _utc_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def base_subscription_access(db, account_id: str, now=None) -> dict:
    """Return conservative access evidence for the account's base paid period."""
    aid = str(account_id or "").strip()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    if not aid:
        return {"allowed": False, "state": "invalid_account", "paid_until": None}

    own = db.execute(
        text("SELECT coalesce(is_own,false) FROM accounts WHERE account_id=:a LIMIT 1"),
        {"a": aid},
    ).scalar()
    if bool(own):
        return {"allowed": True, "state": "platform_owned", "paid_until": None}

    raw = db.execute(
        text("SELECT value FROM storage WHERE account_id=:a AND key='billing' ORDER BY id DESC LIMIT 1"),
        {"a": aid},
    ).scalar()
    if not raw:
        return {"allowed": True, "state": "unknown_allowed", "paid_until": None}

    try:
        billing = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {"allowed": True, "state": "invalid_billing_allowed", "paid_until": None}
    if not isinstance(billing, dict):
        return {"allowed": True, "state": "invalid_billing_allowed", "paid_until": None}

    raw_until = str(billing.get("paid_until") or "").strip()
    if not raw_until:
        return {"allowed": True, "state": "unknown_allowed", "paid_until": None}

    until = _utc_dt(raw_until)
    if until is None:
        return {"allowed": True, "state": "invalid_paid_until_allowed", "paid_until": raw_until}

    allowed = until > now
    return {
        "allowed": allowed,
        "state": "active" if allowed else "expired",
        "paid_until": until.isoformat(),
    }
