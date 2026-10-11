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


def _base_subscription_access(db, account_id: str, now=None) -> dict:
    """Return conservative access evidence for the account's base paid period."""
    aid = str(account_id or "").strip()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    if not aid:
        return {"allowed": False, "state": "invalid_account", "paid_until": None}

    account = db.execute(
        text(
            "SELECT coalesce(is_own,false) AS is_own,"
            " coalesce(billing_mode,'manual') AS billing_mode,owner_user_id "
            "FROM accounts WHERE account_id=:a LIMIT 1"
        ),
        {"a": aid},
    ).mappings().first()
    if account and bool(account.get("is_own")):
        return {"allowed": True, "state": "platform_owned", "paid_until": None, "source": "platform_owned"}

    raw = db.execute(
        text("SELECT value FROM storage WHERE account_id=:a AND key='billing' ORDER BY id DESC LIMIT 1"),
        {"a": aid},
    ).scalar()
    billing = {}
    if raw:
        try:
            billing = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            return {"allowed": True, "state": "invalid_billing_allowed", "paid_until": None, "source": "account_billing_invalid"}
        if not isinstance(billing, dict):
            return {"allowed": True, "state": "invalid_billing_allowed", "paid_until": None, "source": "account_billing_invalid"}

    source = "account_billing"
    if account and str(account.get("billing_mode") or "manual") == "auto" and account.get("owner_user_id"):
        owner_key = "user:%s" % int(account["owner_user_id"])
        owner_raw = db.execute(
            text("SELECT value FROM storage WHERE account_id=:a AND key='billing' ORDER BY id DESC LIMIT 1"),
            {"a": owner_key},
        ).scalar()
        if owner_raw:
            try:
                owner_billing = json.loads(owner_raw) if isinstance(owner_raw, str) else (owner_raw or {})
            except Exception:
                owner_billing = {}
            if isinstance(owner_billing, dict):
                for field in ("tier", "period_start", "unlimited", "paid_until"):
                    if field in owner_billing:
                        billing[field] = owner_billing[field]
                source = "owner_billing"

    if not billing:
        return {"allowed": True, "state": "unknown_allowed", "paid_until": None, "source": "billing_unknown"}

    raw_until = str(billing.get("paid_until") or "").strip()
    if not raw_until:
        return {"allowed": True, "state": "unknown_allowed", "paid_until": None, "source": source}

    until = _utc_dt(raw_until)
    if until is None:
        return {"allowed": True, "state": "invalid_paid_until_allowed", "paid_until": raw_until, "source": source}

    allowed = until > now
    return {
        "allowed": allowed,
        "state": "active" if allowed else "expired",
        "paid_until": until.isoformat(),
        "source": source,
    }



def pilot_product_for_path(path):
    """Trusted server route mapping; user-provided product names are not authority."""
    path = str(path or "").rstrip("/")
    for prefix, product in (
        ("/api/messenger", "mop"), ("/api/calltracking", "rop"),
        ("/api/crm", "crm"), ("/api/boris-crm", "crm"),
        ("/api/reactivation", "reactivation"), ("/api/campaign", "campaign"),
        ("/api/campaigns", "campaign"), ("/api/banners", "campaign"),
        ("/api/inbox", "ecs"),
    ):
        if path == prefix or path.startswith(prefix + "/"):
            return product
    return None


def base_subscription_access(db, account_id: str, now=None, *, product=None) -> dict:
    base = _base_subscription_access(db, account_id, now=now)
    if product is None or base.get("state") in {"active", "platform_owned"}:
        return base
    from app.api.billing import owner_pilot_product_access, owner_pilot_paid_scope_readback, _load_billing
    trial = owner_pilot_product_access(db, account_id, product, now=now)
    if not trial.get("configured"):
        return base
    # A real existing paid module with legacy unknown base remains unchanged.
    paid = owner_pilot_paid_scope_readback(_load_billing(account_id, db=db) or {}, now=now)
    if base.get("allowed"):
        if paid.get(product, {}).get("covered"):
            return base
        # Preserve an independently paid product accepted by the old unknown-
        # base path; full conversion proof intentionally has a stricter scope.
        from app.client_config.entitlements import decide
        product_right = decide(product, _load_billing(account_id, db=db) or {}, now=now)
        if product_right.get("allowed") and product_right.get("source") == "storage:billing":
            return base
    return {**base, "allowed": bool(trial.get("allowed")),
            "state": "owner_pilot" if trial.get("allowed") else trial.get("state"),
            "base_state": base.get("state"), "source": "storage:billing.owner_pilot",
            "product": product, "pilot_until": trial.get("until"),
            "reason": trial.get("reason"), "money_authority": False}
