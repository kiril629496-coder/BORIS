from __future__ import annotations

from copy import deepcopy
from datetime import date

# Runtime contract for Social posting plans. This module belongs to the Social
# reliability workstream and intentionally does not own billing/payment code.
# Billing may persist these fields directly; when it does not, Social runtime
# deterministically reconstructs them from the paid plan code.
SOCIAL_POSTING_PLAN_CONTRACT = {
    "post_tg": {
        "platforms": "tg",
        "posts_per_day": 3,
    },
    "post_vk": {
        "platforms": "vk",
        "posts_per_day": 3,
    },
    "post_both": {
        "platforms": "both",
        "posts_per_day": 3,
    },
}


def normalize_posting_subscription(subscription: dict | None) -> tuple[dict, dict]:
    """Return normalized entitlement + a field-level change report.

    Only deterministic fields derivable from the existing paid plan code are
    repaired. Unknown/custom plans are never guessed.
    """
    original = deepcopy(subscription) if isinstance(subscription, dict) else {}
    out = deepcopy(original)
    plan = str(out.get("plan") or "").strip()
    contract = SOCIAL_POSTING_PLAN_CONTRACT.get(plan)
    changes = {}
    if not contract:
        return out, changes

    for key, expected in contract.items():
        current = out.get(key)
        if current in (None, "", 0):
            out[key] = expected
            changes[key] = {"before": current, "after": expected}
            continue

        # For a known paid plan, an impossible platform mismatch is deterministic
        # drift; the plan code is the source of truth.
        if key == "platforms" and str(current).strip().lower() != str(expected):
            out[key] = expected
            changes[key] = {"before": current, "after": expected}

        # posts_per_day is a package cap, not an implicit schedule. Existing
        # explicit positive custom limits are preserved to avoid inventing policy.
        if key == "posts_per_day":
            try:
                current_int = int(current)
            except Exception:
                current_int = 0
            if current_int <= 0:
                out[key] = int(expected)
                changes[key] = {"before": current, "after": int(expected)}

    return out, changes


def posting_subscription_active(subscription: dict | None, today: date | None = None) -> bool:
    """SOCIAL_PAID_PERIOD_CANONICAL_V1: deterministic paid-period truth.

    Explicit terminal lifecycle states always win. Otherwise the paid date and
    period length are the source of truth; missing/invalid evidence never
    resurrects a subscription.
    """
    normalized, _ = normalize_posting_subscription(subscription)
    if not isinstance(normalized, dict) or not normalized:
        return False
    status = str(normalized.get("status") or "").strip().lower()
    if status in {"expired", "cancelled", "canceled", "suspended", "blocked", "expired_owner_confirmed"}:
        return False
    try:
        paid = date.fromisoformat(str(normalized.get("paid_at") or ""))
        period_days = int(normalized.get("period_days") or 30)
        current = today or date.today()
        return period_days > 0 and (current - paid).days < period_days
    except Exception:
        return False


def posting_entitlement_view(subscription: dict | None) -> dict:
    normalized, changes = normalize_posting_subscription(subscription)
    try:
        per_day = int(normalized.get("posts_per_day") or 0)
    except Exception:
        per_day = 0
    return {
        "plan": normalized.get("plan"),
        "platforms": normalized.get("platforms"),
        "posts_per_day_limit": per_day,
        "normalized": normalized,
        "changes": changes,
        "known_plan": str(normalized.get("plan") or "") in SOCIAL_POSTING_PLAN_CONTRACT,
    }
