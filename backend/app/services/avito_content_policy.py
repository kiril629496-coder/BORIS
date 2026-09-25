from __future__ import annotations

import os

CONTENT_POLICY_VERSION = "avito-content-autonomy-v1"


def _flag(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _nonnegative_int(name: str, default: int = 0) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return max(0, value)


def policy() -> dict:
    per_item = _nonnegative_int("BORIS_CONTENT_PAID_PER_ITEM_KOPEKS", 0)
    daily = _nonnegative_int("BORIS_CONTENT_PAID_DAILY_KOPEKS", 0)
    paid_switch = _flag("BORIS_CONTENT_PAID_FALLBACK", False)
    paid_requested = bool(paid_switch and per_item > 0 and daily > 0)
    # The current provider-specific ai_budget guards are real, but these new
    # content-wide per-item/day numbers are not yet a single cross-provider
    # enforcing ledger. Never advertise a decorative limit as financial safety.
    # Until such an adapter proves the exact upper bound, content paid fallback
    # remains disabled. Zero-cost production continues normally.
    paid_enforcing_adapter = False
    return {
        "version": CONTENT_POLICY_VERSION,
        "mode": "local_first",
        "local_text_first": _flag("BORIS_CONTENT_LOCAL_TEXT_FIRST", True),
        "local_banner_first": _flag("BORIS_CONTENT_LOCAL_BANNER_FIRST", True),
        "paid_competitor_audit": False,
        "paid_fallback_requested": paid_requested,
        "paid_fallback_enabled": bool(paid_requested and paid_enforcing_adapter),
        "paid_limit_adapter": "disabled_until_cross_provider_hard_cap_exists",
        "paid_per_item_kopeks": per_item,
        "paid_daily_kopeks": daily,
        "local_llm_required": False,
        "local_llm_role": "optional_rewrite_only",
        "facts": "canonical campaign/account evidence only; never invent commercial claims",
        "text": {
            "title_max_chars": 50,
            "description_target_chars": [1200, 2000],
            "description_hard_max_chars": 2400,
            "format": "plain_text",
            "cta_required": True,
            "uniqueness": "semantic variation from verified facts; no fake padding",
        },
        "media": {
            "owner_media_first": True,
            "approved_same_account_media_second": True,
            "stock_if_needed": True,
            "paid_image_generation": "fallback_only",
            "banner_text_renderer": "local_pillow",
            "no_rotation": True,
            "no_fake_product": True,
        },
        "xml": "canonical Feed Factory projection only",
        "publication": "canonical BORIS lifecycle only; content preparation never bypasses it",
        "idempotency": "required for every paid fallback and canonical job replay",
    }


def paid_fallback_enabled() -> bool:
    return bool(policy()["paid_fallback_enabled"])


def paid_competitor_audit_enabled() -> bool:
    return bool(policy()["paid_competitor_audit"])


def local_text_first_enabled() -> bool:
    return bool(policy()["local_text_first"])


def local_banner_first_enabled() -> bool:
    return bool(policy()["local_banner_first"])
