"""Fail-closed validation of persisted Avito money signals.

Reporting may preserve a last-known spend snapshot across provider throttling.
Money-increasing decisions must not treat that degraded fallback as fresh proof.
"""
from __future__ import annotations

import json
import math
import time
from sqlalchemy import text
from app.services.marketing_clock import marketing_today_iso


def money_spend_signal_eligible(db, account_id: str, signal: dict, *, max_age_seconds: float = 900.0, max_future_skew_sec: float = 60.0) -> bool:
    """True only for an exact, non-degraded persisted spend sample.

    `latest_confirmed_spend()` intentionally serves reporting-compatible data.
    This stricter boundary re-opens the exact selected storage row and rejects
    fallback/degraded snapshots, corrupt numbers, and materially future clocks.
    """
    if not isinstance(signal, dict) or signal.get("status") != "ok":
        return False
    try:
        ts = float(signal.get("timestamp") or 0)
        spent = float(signal.get("spent_today_rub"))
    except Exception:
        return False
    now = time.time()
    # MARKETER_SPEND_ELIGIBILITY_MAX_AGE_15M_V1: this money-only helper enforces
    # freshness itself, not merely by trusting the caller to have used a bounded
    # reporting selector first. A syntactically "ok" stale sample is never enough.
    if (not math.isfinite(ts) or not math.isfinite(spent) or ts <= 0 or spent < 0
            or ts > now + float(max_future_skew_sec)
            or now - ts > float(max_age_seconds)):
        return False
    key = str(signal.get("storage_key") or "")
    if not key or not (key.startswith("daily_spending:") or key.startswith("daily_stats:")):
        return False
    row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
                     {"a": account_id, "k": key}).fetchone()
    if not row:
        return False
    try:
        raw = json.loads(row[0] or "{}")
        spending = raw if key.startswith("daily_spending:") else raw.get("spending")
        if not isinstance(spending, dict) or spending.get("status") != "ok":
            return False
        if spending.get("fallback_status") or spending.get("fallback_at"):
            return False
        raw_ts = float(spending.get("timestamp") or 0)
        raw_spent = float(spending.get("all_spend_rub"))
        if not math.isfinite(raw_ts) or not math.isfinite(raw_spent):
            return False
        if abs(raw_ts - ts) > 1e-6 or abs(raw_spent - spent) > 1e-6:
            return False

        # MARKETER_DEDICATED_SPEND_NOT_DEGRADED_V1: reporting may select an
        # older clean daily_stats row with the same provider timestamp. Money
        # authorization additionally requires the dedicated same-day spending
        # row to be current and non-degraded; a 429 fallback there blocks raise.
        day = str(signal.get("spending_date") or spending.get("date") or "")
        # MARKETER_SPEND_SAME_DAY_MONEY_PROOF_V1: timestamp freshness alone is
        # insufficient for a daily budget. A late/yesterday provider total with
        # a newly persisted timestamp must remain reporting-only until today's
        # dedicated spending sample exists.
        from datetime import datetime, timezone
        today = marketing_today_iso()
        if not day or day != today or str(spending.get("date") or day) != today:
            return False
        # MARKETER_SPEND_STORAGE_DAY_BINDING_V1: the selected storage identity
        # must belong to the same provider day as its payload. A mismatched key
        # is retained for diagnostics/reporting but cannot become money proof.
        key_day = key.split(":", 1)[1] if ":" in key else ""
        if key_day != day:
            return False
        dedicated = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"),
                               {"a": account_id, "k": "daily_spending:" + day}).fetchone()
        if not dedicated:
            return False
        dedicated_spend = json.loads(dedicated[0] or "{}")
        if (not isinstance(dedicated_spend, dict) or dedicated_spend.get("status") != "ok"
                or dedicated_spend.get("fallback_status") or dedicated_spend.get("fallback_at")):
            return False
        dts = float(dedicated_spend.get("timestamp") or 0)
        dval = float(dedicated_spend.get("all_spend_rub"))
        if (not math.isfinite(dts) or not math.isfinite(dval) or dts <= 0
                or dts > now + float(max_future_skew_sec) or dval < 0
                or now - dts > float(max_age_seconds)):
            return False
        if abs(dval - spent) > 1e-6 or abs(dts - ts) > 1e-6:
            return False
    except Exception:
        return False
    return True


def money_stats_snapshot_eligible(db, account_id: str, *, max_age_seconds: float = 900.0,
                                  max_future_skew_sec: float = 60.0) -> tuple[bool, dict]:
    """Return whether today's persisted Avito stats are fresh enough for a raise.

    Reporting may keep an older complete snapshot when Avito is throttled or
    degraded. Money-increasing actions require a complete same-day snapshot
    collected locally within the strict freshness window. This helper performs
    no provider I/O and never mutates storage.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    key = "daily_stats:" + marketing_today_iso()
    row = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC LIMIT 1"
    ), {"a": account_id, "k": key}).fetchone()
    if not row:
        return False, {"reason":"stats_missing", "storage_key":key}
    try:
        snap = json.loads(row[0] or "{}")
        if not isinstance(snap, dict):
            return False, {"reason":"stats_invalid", "storage_key":key}
        complete = bool((snap.get("completeness") or {}).get("complete") is True)
        # MARKETER_PROVIDER_STATS_DAY_MONEY_FENCE_V1:
        # Right after Moscow midnight Avito can return a fresh snapshot persisted
        # under today's key while item counters still belong to yesterday.
        # Such rows are fine for reporting/history but can never authorize a
        # money-increasing decision until provider stats_date catches up.
        marketing_day = marketing_today_iso()
        provider_stats_day = str(snap.get("stats_date") or "")
        provider_day_current = (provider_stats_day == marketing_day)
        collected_raw = str(snap.get("collected_at") or "")
        collected = datetime.fromisoformat(collected_raw.replace("Z", "+00:00"))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        age = (now - collected.astimezone(timezone.utc)).total_seconds()
        fresh = bool(complete and -float(max_future_skew_sec) <= age <= float(max_age_seconds))
        ok = bool(fresh and provider_day_current)
        reason = (
            "ok" if ok else
            "provider_stats_day_lagged" if fresh and not provider_day_current else
            "stats_stale_or_incomplete"
        )
        return ok, {"reason":reason,
                    "storage_key":key, "age_seconds":age, "complete":complete,
                    "collected_at":collected_raw,
                    "marketing_day":marketing_day,
                    "provider_stats_day":provider_stats_day,
                    "provider_day_current":bool(provider_day_current)}
    except Exception:
        return False, {"reason":"stats_invalid", "storage_key":key}


def money_raise_signals_eligible(db, account_id: str, *, max_age_seconds: float = 900.0, max_snapshot_skew_seconds: float = 300.0) -> tuple[bool, dict]:
    """Final DB-only gate for any money-increasing Avito mutation.

    Re-validates the dedicated spend sample and today's complete stats snapshot
    immediately before the provider write. Reporting fallbacks remain readable,
    but they never authorize a raise.
    """
    from app.services.marketing_money_policy import latest_confirmed_spend
    signal = latest_confirmed_spend(db, account_id, max_age_seconds=max_age_seconds) or {}
    spend_ok = money_spend_signal_eligible(
        db, account_id, signal, max_age_seconds=max_age_seconds
    )
    stats_ok, stats_evidence = money_stats_snapshot_eligible(
        db, account_id, max_age_seconds=max_age_seconds
    )
    try:
        spend_ts = float(signal.get("timestamp") or 0)
        age = time.time() - spend_ts
    except Exception:
        spend_ts = 0.0
        age = None
    # MARKETER_FINAL_SPEND_STATS_COHERENCE_V1: the final provider-boundary guard
    # must not authorize a raise by mixing a newly refreshed spend sample with an
    # older (though individually still <=15m) item-statistics snapshot. Reuse the
    # rollout's five-minute coherence rule here for every money lane.
    snapshot_skew_seconds = None
    coherent = False
    try:
        from datetime import datetime, timezone
        collected_raw = str((stats_evidence or {}).get("collected_at") or "")
        collected = datetime.fromisoformat(collected_raw.replace("Z", "+00:00"))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        stats_ts = collected.astimezone(timezone.utc).timestamp()
        snapshot_skew_seconds = abs(stats_ts - spend_ts)
        coherent = bool(spend_ts > 0 and snapshot_skew_seconds <= float(max_snapshot_skew_seconds))
    except Exception:
        coherent = False
    # MONEY_RAISE_SHARED_THROTTLE_FAIL_CLOSED_V1: freshness/coherence alone is
    # not permission to increase money while Avito has an active tenant-wide
    # Retry-After. Put the provider cooldown in the canonical final DB/local
    # signal gate so every raise lane inherits the same fail-closed rule, even
    # if a caller's earlier provider-throttle preflight happened before a sibling
    # worker observed 429.
    from app.services.avito_account_throttle import account_throttle_remaining
    throttle_remaining = int(account_throttle_remaining(account_id) or 0)
    throttle_clear = throttle_remaining <= 0
    ok = bool(spend_ok and stats_ok and coherent and throttle_clear)
    return ok, {
        "reason": "ok" if ok else ("avito_account_throttled" if not throttle_clear else "money_signal_stale_or_degraded"),
        "spend_ok": bool(spend_ok),
        "stats_ok": bool(stats_ok),
        "coherent": bool(coherent),
        "throttle_clear": bool(throttle_clear),
        "retry_after_seconds": int(throttle_remaining),
        "snapshot_skew_seconds": snapshot_skew_seconds,
        "spend_age_seconds": age,
        "stats": stats_evidence,
    }
