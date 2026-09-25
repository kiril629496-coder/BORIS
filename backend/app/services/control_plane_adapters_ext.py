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


def marketing_service_entitlement(db, account_id: str, now=None) -> dict:
    """Return conservative service-period evidence without inventing entitlement.

    Explicit future paid_until => active. Explicit payment_status paid_at +
    period_days => active/expired. Legacy unlimited rows without a current period
    are unknown rather than active, preventing Brain from treating old setup rows
    as proof that an owner currently owes KPI service.
    """
    now = now or datetime.now(timezone.utc)
    rows = db.query(Storage).filter(
        Storage.account_id == str(account_id),
        Storage.key.in_(("billing", "payment_status")),
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
    if paid_until:
        return {"state": "active" if paid_until > now else "expired", "source": "billing.paid_until", "until": paid_until.isoformat()}
    paid_at = _dt(payment.get("paid_at"))
    try:
        days = int(payment.get("period_days") or 0)
    except Exception:
        days = 0
    if paid_at and days > 0:
        from datetime import timedelta
        until = paid_at + timedelta(days=days)
        return {"state": "active" if until > now else "expired", "source": "payment_status", "until": until.isoformat()}
    return {"state": "unknown", "source": "no_explicit_current_paid_period", "until": None}
