"""Entitlement-aware helpers for Control Plane marketing portfolio.

Kept separate from immutable canonical adapter so deployment protection is not
bypassed. Safe to import from canonical source through the normal release path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models.storage import Storage


def _dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _owner_ai_marketer_trial(autopilot, now) -> dict | None:
    """Read an already authorized finite AI-marketer trial; never grant one."""
    import re
    if not isinstance(autopilot, dict):
        return None
    if autopilot.get("full_ai_marketer") is not True or autopilot.get("trial_scope") != "ai_marketer_only":
        return None
    if not re.fullmatch(r"owner_direct_instruction_[0-9]{8}", str(autopilot.get("trial_source") or "")):
        return None
    # The DB uses the same finite ISO instant grammar. Do not accept locale
    # dates, relative PostgreSQL dates, naive time zones, or fractional rounding.
    def trial_dt(value):
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{1,6})?(Z|[+-](0[0-9]|1[0-5]):[0-5][0-9])", value
        ):
            return None
        return _dt(value)

    started = trial_dt(autopilot.get("full_ai_marketer_activated_at"))
    until = trial_dt(autopilot.get("full_ai_marketer_trial_until"))
    if not started or not until or not started <= now < until:
        return None
    return {
        "state": "active", "source": "autopilot_settings.owner_ai_marketer_trial",
        "until": until.isoformat(), "entitlement_kind": "trial", "scope": "ai_marketer_only",
    }


def marketing_service_entitlement(db, account_id: str, now=None) -> dict:
    """Return conservative service-period evidence without inventing entitlement.

    Explicit future paid_until => active. Explicit payment_status paid_at +
    period_days => active/expired. Legacy unlimited rows without a current period
    are unknown rather than active, preventing Brain from treating old setup rows
    as proof that an owner currently owes KPI service.
    """
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    rows = db.query(Storage).filter(
        Storage.account_id == str(account_id),
        Storage.key.in_(("billing", "payment_status", "autopilot_settings")),
    ).order_by(Storage.id.desc()).all()
    vals = {}
    for row in rows:
        if row.key in vals:
            continue
        try:
            vals[row.key] = json.loads(row.value or "{}")
        except Exception:
            vals[row.key] = {}
    billing = vals.get("billing") or {}
    payment = vals.get("payment_status") or {}
    # Marketing entitlement must not be inferred from a different purchased
    # module (MOP/ROP). Only the account/product paid_until is authoritative for
    # the marketing portfolio.
    paid_until = _dt(billing.get("paid_until"))
    trial = _owner_ai_marketer_trial(vals.get("autopilot_settings"), now)
    # Product-scoped owner pilot supplies service rights only. Existing money
    # mandate admission remains independent, including its required paid/trial source.
    if trial is None:
        from app.api.billing import owner_pilot_access
        pilot = owner_pilot_access(billing, "marketing", now=now)
        if pilot.get("allowed"):
            trial = {"state": "active", "source": "storage:billing.owner_pilot.marketing",
                     "until": pilot.get("until"), "money_authority": False,
                     "money_admission": "independent_existing_mandate_required"}
    if paid_until:
        if paid_until > now:
            return {"state": "active", "source": "billing.paid_until", "until": paid_until.isoformat()}
        return trial or {"state": "expired", "source": "billing.paid_until", "until": paid_until.isoformat()}
    paid_at = _dt(payment.get("paid_at"))
    try:
        days = int(payment.get("period_days") or 0)
    except Exception:
        days = 0
    if paid_at and days > 0:
        from datetime import timedelta
        until = paid_at + timedelta(days=days)
        if until > now:
            return {"state": "active", "source": "payment_status", "until": until.isoformat()}
        return trial or {"state": "expired", "source": "payment_status", "until": until.isoformat()}
    return trial or {"state": "unknown", "source": "no_explicit_current_paid_period", "until": None}
