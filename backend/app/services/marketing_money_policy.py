"""Authoritative BORIS money-safety policy for Avito CPX marketing.

One place owns the global red-line constraints used by the reconciler,
mandate creator and direct CPX endpoints. Account settings may only make the
policy stricter; they can never expand the global ceiling.
"""
from __future__ import annotations

import json

from sqlalchemy import text

GLOBAL_HARD_MAX_BID_RUB = 150.0
# OWNER_MAX_CPX_150_RUB_V2: canonical owner ceiling; explicit daily budget remains mandatory for every raise.
GLOBAL_MAX_BID_DELTA_PCT = 10
# STRICT_STEP10_SOURCE_INVARIANT_V1: every autonomous bid write, including
# late-day catch-up, is bounded by the same <=10% causal step. Pacing may
# choose which proven item to test, but never expands the money red line.
GLOBAL_MAX_AUTO_ACTIONS_RUN = 5
GLOBAL_MAX_AUTO_ACTIONS_DAY = 120


def configured_account_hard_cap(db, account_id: str) -> float | None:
    row = db.execute(text("""
        SELECT value
          FROM storage
         WHERE account_id=:a AND key='kpi_settings'
         ORDER BY id DESC
         LIMIT 1
    """), {"a": account_id}).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0] or "{}")
        value = float((data or {}).get("hard_max_bid_rub") or 0)
    except Exception:
        return None
    return value if value > 0 else None


def effective_hard_bid_cap(db, account_id: str) -> float:
    configured = configured_account_hard_cap(db, account_id)
    if configured is None:
        return GLOBAL_HARD_MAX_BID_RUB
    return min(float(configured), GLOBAL_HARD_MAX_BID_RUB)


def clamp_bid_step_pct(value) -> int:
    try:
        requested = int(float(value))
    except Exception:
        requested = GLOBAL_MAX_BID_DELTA_PCT
    if requested <= 0:
        requested = GLOBAL_MAX_BID_DELTA_PCT
    return min(requested, GLOBAL_MAX_BID_DELTA_PCT)


def effective_bid_step_cap_pct(source: str | None = None) -> int:
    """Return the single global autonomous per-write bid step ceiling."""
    return GLOBAL_MAX_BID_DELTA_PCT


def auto_promotion_allowed(db, account_id: str) -> bool:
    # Avito auto-promotion controls the effective bid internally. While BORIS
    # operates under an explicit per-action red line, this mode cannot prove it
    # will remain below the hard cap, so it is fail-closed.
    return effective_hard_bid_cap(db, account_id) <= 0


def effective_daily_budget_limit(db, account_id: str) -> float | None:
    """Return the active daily advertising red line for one Avito account.

    The latest explicit KPI value is authoritative, including zero. A configured
    zero means the owner has not authorized daily advertising spend, so legacy
    mandates must not resurrect an older positive limit. Only when the setting
    is genuinely absent may an already active explicit mandate be reused.
    """
    configured = None
    configured_present = False
    row = db.execute(text("""
        SELECT value
          FROM storage
         WHERE account_id=:a AND key='kpi_settings'
         ORDER BY id DESC
         LIMIT 1
    """), {"a": account_id}).fetchone()
    if row:
        try:
            data = json.loads(row[0] or "{}")
            if isinstance(data, dict) and "daily_budget_limit_rub" in data:
                configured_present = True
                value = float(data.get("daily_budget_limit_rub") or 0)
                configured = max(0.0, value)
        except Exception:
            configured = None
            configured_present = False
    if configured_present:
        return float(configured) if configured and configured > 0 else None

    mandate = db.execute(text("""
        SELECT daily_budget_rub
          FROM money_mandates
         WHERE :a=ANY(account_scope)
           AND status='active' AND revoked_at IS NULL
           AND (valid_from IS NULL OR valid_from<=now())
           AND (valid_until IS NULL OR valid_until>now())
           AND daily_budget_rub IS NOT NULL AND daily_budget_rub>0
         ORDER BY id DESC
         LIMIT 1
    """), {"a": account_id}).fetchone()
    if not mandate:
        return None
    try:
        value = float(mandate[0] or 0)
    except Exception:
        return None
    return value if value > 0 else None



def adaptive_budget_brake_policy(db, account_id: str, daily_limit_rub: float,
                                 spent_today_rub: float | None = None) -> dict:
    """Return a conservative pre-limit cutoff based on observed account burn rate.

    CPX delivery/reporting is not instantaneous. A fixed 10% reserve is unsafe on
    accounts that can spend more than that between hourly control cycles. We keep
    at least 10% reserve and enlarge it to four hours of evidenced burn, capped at
    50% of the owner's daily limit. Historical evidence uses provider-confirmed
    completed days only; today's pace can only make the reserve stricter.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    limit = float(daily_limit_rub or 0)
    if limit <= 0:
        return {"cutoff_ratio": 0.0, "reserve_ratio": 1.0,
                "reason": "daily_limit_missing", "evidence": {}}
    historical = []
    try:
        rows = db.execute(text("""
            SELECT key,value FROM storage
             WHERE account_id=:a AND key LIKE 'daily_spending:%'
             ORDER BY id DESC LIMIT 8
        """), {"a": account_id}).fetchall()
        today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        for key, raw in rows:
            try:
                d = json.loads(raw or "{}")
                if str(d.get("status") or "") != "ok":
                    continue
                day = str(d.get("date") or str(key).split(":", 1)[-1])
                value = float(d.get("all_spend_rub"))
                if day != today and value >= 0:
                    historical.append(value)
            except Exception:
                continue
    except Exception:
        historical = []
    # Four hours of evidenced burn protects an hourly worker against provider
    # reporting lag plus one delayed/failed control cycle. Use the max of the
    # latest three completed days so a recent burst is not averaged away.
    hist_peak = max(historical[:3], default=0.0)
    hist_reserve = hist_peak / 6.0
    today_reserve = 0.0
    try:
        now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
        elapsed_h = max(1.0, now_msk.hour + now_msk.minute / 60.0)
        current = max(0.0, float(spent_today_rub or 0.0))
        today_reserve = (current / elapsed_h) * 4.0
    except Exception:
        today_reserve = 0.0
    reserve_rub = max(limit * 0.10, hist_reserve, today_reserve)
    reserve_rub = min(limit * 0.50, reserve_rub)
    cutoff_ratio = max(0.50, min(0.90, 1.0 - reserve_rub / limit))
    return {
        "cutoff_ratio": round(cutoff_ratio, 4),
        "cutoff_pct": round(cutoff_ratio * 100.0, 1),
        "reserve_ratio": round(1.0 - cutoff_ratio, 4),
        "reserve_rub": round(limit * (1.0 - cutoff_ratio), 2),
        "reason": "adaptive_four_hour_evidenced_burn_reserve",
        "evidence": {
            "historical_peak_daily_spend_rub": round(hist_peak, 2),
            "historical_days_used": len(historical[:3]),
            "today_spend_rub": (round(float(spent_today_rub), 2) if spent_today_rub is not None else None),
            "fixed_min_reserve_pct": 10.0,
            "max_reserve_pct": 50.0,
            "protected_burn_hours": 4.0,
        },
    }

def pacing_budget_brake_policy(db, account_id: str, daily_limit_rub: float,
                               spent_today_rub: float | None = None) -> dict:
    """DAILY_BUDGET_PACING_BRAKE_V1: keep pacing inside 95-100%, target ~98%.

    Unlike the conservative generic raise brake, this lane exists specifically
    to deliver the owner's daily budget. It still reserves 2-5% for provider
    reporting/delivery lag and never relaxes the absolute 100% hard ceiling.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    limit=float(daily_limit_rub or 0)
    if limit<=0:
        return {"cutoff_ratio":0.0,"reserve_ratio":1.0,"reason":"daily_limit_missing","evidence":{}}
    historical=[]
    try:
        rows=db.execute(text("""
            SELECT key,value FROM storage
             WHERE account_id=:a AND key LIKE 'daily_spending:%'
             ORDER BY id DESC LIMIT 8
        """),{"a":account_id}).fetchall()
        today=datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        for key,raw in rows:
            try:
                d=json.loads(raw or "{}")
                if str(d.get("status") or "")!="ok":continue
                day=str(d.get("date") or str(key).split(":",1)[-1])
                value=float(d.get("all_spend_rub"))
                if day!=today and value>=0:historical.append(value)
            except Exception:continue
    except Exception:
        historical=[]
    hist_peak=max(historical[:3],default=0.0)
    try:
        now=datetime.now(ZoneInfo("Europe/Moscow")); elapsed=max(1.0,now.hour+now.minute/60.0)
        current=max(0.0,float(spent_today_rub or 0.0)); today_hourly=current/elapsed
    except Exception:
        current=0.0; today_hourly=0.0
    observed_hourly=max(hist_peak/24.0,today_hourly)
    reserve_rub=max(limit*0.02,min(limit*0.05,observed_hourly))
    cutoff=max(0.95,min(0.98,1.0-reserve_rub/limit))
    return {
        "cutoff_ratio":round(cutoff,4),"cutoff_pct":round(cutoff*100.0,1),
        "reserve_ratio":round(1.0-cutoff,4),"reserve_rub":round(limit*(1.0-cutoff),2),
        "target_ratio":0.98,"minimum_acceptable_ratio":0.95,
        "reason":"daily_budget_target_with_provider_lag_reserve",
        "evidence":{"historical_peak_daily_spend_rub":round(hist_peak,2),
                    "today_spend_rub":round(current,2),"observed_hourly_burn_rub":round(observed_hourly,2),
                    "hard_ceiling_ratio":1.0}
    }


def latest_confirmed_spend(db, account_id: str, max_age_seconds: int = 7200) -> dict:
    """Return freshest trustworthy Avito daily-spend signal without guessing.

    Storage key dates can follow server/API day boundaries, so freshness is
    determined by the provider timestamp, not by local calendar assumptions.
    """
    import time
    rows = db.execute(text("""
        SELECT key,value
          FROM storage
         WHERE account_id=:a
           AND (key LIKE 'daily_stats:%' OR key LIKE 'daily_spending:%')
         ORDER BY id DESC
         LIMIT 8
    """), {"a": account_id}).fetchall()
    best = None
    for key, raw in rows:
        try:
            snap = json.loads(raw or "{}")
            spending = snap if str(key).startswith("daily_spending:") else (snap.get("spending") if isinstance(snap, dict) else None)
            if not isinstance(spending, dict) or spending.get("status") != "ok":
                continue
            ts = float(spending.get("timestamp") or 0)
            spent = float(spending.get("all_spend_rub"))
        except Exception:
            continue
        if ts <= 0 or spent < 0:
            continue
        age = max(0.0, time.time() - ts)
        row = {"status":"ok","storage_key":key,"timestamp":ts,"age_seconds":age,
               "spent_today_rub":spent,"spending_date":spending.get("date")}
        if best is None or ts > best["timestamp"]:
            best = row
    if best is None:
        return {"status":"unknown","reason":"no_confirmed_spending"}
    if best["age_seconds"] > float(max_age_seconds):
        return {**best,"status":"stale","reason":"spending_stale"}
    return best


# PRESENCE_BUDGET_PRESSURE_V1: Avito reports placement/presence spend separately
# from CPX promotion. If recent completed days prove that presence alone already
# consumes the owner's daily red line, adding/restoring CPX is economically
# unsafe even when today's provider counter has not posted the next lump yet.
def presence_budget_pressure(db, account_id: str, daily_limit_rub: float, recent_days: int = 3) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    limit = float(daily_limit_rub or 0)
    if limit <= 0:
        return {"blocked": False, "reason": "daily_limit_missing", "daily_budget_limit_rub": limit}
    today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    rows = db.execute(text("""
        SELECT key,value
          FROM storage
         WHERE account_id=:a AND key LIKE 'daily_spending:%'
         ORDER BY id DESC
         LIMIT 12
    """), {"a": account_id}).fetchall()
    by_day = {}
    for key, raw in rows:
        try:
            data = json.loads(raw or "{}")
            if not isinstance(data, dict) or str(data.get("status") or "") != "ok":
                continue
            day = str(data.get("date") or str(key).split(":", 1)[-1])[:10]
            breakdown = data.get("breakdown") if isinstance(data.get("breakdown"), dict) else {}
            presence = float(breakdown.get("presence") or 0)
            total = float(data.get("all_spend_rub") or 0)
            if presence < 0 or total < 0 or not day:
                continue
        except Exception:
            continue
        if day not in by_day:
            by_day[day] = {"date": day, "presence_rub": round(presence, 2),
                           "all_spend_rub": round(total, 2)}
    current = by_day.get(today)
    completed = [v for k, v in sorted(by_day.items(), reverse=True) if k != today][:max(1, int(recent_days or 3))]
    over_days = [x for x in completed if float(x.get("presence_rub") or 0) >= limit]
    latest = completed[0] if completed else None
    latest_ratio = (float(latest.get("presence_rub") or 0) / limit) if latest else 0.0
    current_ratio = (float(current.get("presence_rub") or 0) / limit) if current else 0.0
    # A repeated breach is enough evidence. A single latest-day >=125% breach is
    # also strong enough to prevent an unsafe next-morning CPX resume while the
    # provider's new-day presence accounting is still delayed.
    blocked = bool(
        current_ratio >= 1.0
        or len(over_days) >= 2
        or (latest is not None and latest_ratio >= 1.25)
    )
    return {
        "blocked": blocked,
        "reason": "presence_spend_exceeds_daily_budget_history" if blocked else "presence_budget_compatible",
        "daily_budget_limit_rub": round(limit, 2),
        "today": current,
        "completed_days": completed,
        "completed_over_budget_days": len(over_days),
        "latest_completed_presence_ratio": round(latest_ratio, 4),
        "current_presence_ratio": round(current_ratio, 4),
        "policy": {"repeat_days_required": 2, "single_latest_ratio_block": 1.25,
                   "spend_type": "presence", "cpx_promotion_separate": True},
    }


# PRESENCE_BUDGET_GROWTH_SELF_HEAL_V1: freeze only additive inventory growth
# when provider-confirmed presence spend is structurally incompatible with the
# owner's daily advertising red line. Existing listings stay untouched. The
# previous policy is restored only after the pressure clears AND the frozen
# policy fingerprint is unchanged, so an owner's later edit always wins.
def reconcile_presence_budget_growth_policy(db, account_id: str, daily_limit_rub: float, mutate: bool = True) -> dict:
    import hashlib
    from datetime import datetime, timezone

    pressure = presence_budget_pressure(db, account_id, daily_limit_rub)
    now = datetime.now(timezone.utc).isoformat()

    row = db.execute(text("""
        SELECT id,value FROM storage
         WHERE account_id=:a AND key='inventory_growth_policy'
         ORDER BY id DESC LIMIT 1
    """), {"a": account_id}).mappings().first()
    try:
        policy = json.loads((row or {}).get("value") or "{}") if row else {}
    except Exception:
        policy = {}
    if not isinstance(policy, dict):
        policy = {}

    def _fingerprint(data: dict) -> str:
        clean = dict(data or {})
        clean.pop("money_guard", None)
        raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    marker = policy.get("money_guard") if isinstance(policy.get("money_guard"), dict) else {}
    changed = False
    action = "no_change"

    if pressure.get("blocked"):
        if row and bool(policy.get("enabled")):
            frozen = dict(policy)
            frozen["enabled"] = False
            frozen.pop("money_guard", None)
            frozen_fp = _fingerprint(frozen)
            if mutate:
                policy["enabled"] = False
                policy["money_guard"] = {
                    "source": "PRESENCE_BUDGET_PRESSURE_V1",
                    "status": "active",
                    "disabled_at": now,
                    "restore_enabled": True,
                    "frozen_policy_fingerprint": frozen_fp,
                    "reason": pressure.get("reason"),
                    "daily_budget_limit_rub": pressure.get("daily_budget_limit_rub"),
                }
                db.execute(text("UPDATE storage SET value=:v WHERE id=:id"), {
                    "v": json.dumps(policy, ensure_ascii=False), "id": int(row["id"]),
                })
                changed = True
                action = "additive_growth_frozen"
            else:
                # PRESENCE_BUDGET_DRY_RUN_NO_MUTATION_V1
                action = "would_freeze_additive_growth"
        elif row and str(marker.get("source") or "") == "PRESENCE_BUDGET_PRESSURE_V1" and str(marker.get("status") or "") == "active":
            action = "additive_growth_already_frozen"
        elif row:
            action = "additive_growth_already_disabled"
        else:
            # Missing policy is already fail-closed in _kpi_additive_growth_tick.
            action = "additive_growth_policy_absent_already_safe"
    else:
        if (row and not bool(policy.get("enabled"))
                and str(marker.get("source") or "") == "PRESENCE_BUDGET_PRESSURE_V1"
                and str(marker.get("status") or "") == "active"
                and bool(marker.get("restore_enabled"))):
            current_fp = _fingerprint(policy)
            if current_fp == str(marker.get("frozen_policy_fingerprint") or ""):
                if mutate:
                    policy["enabled"] = True
                    policy["money_guard"] = {
                        **marker,
                        "status": "recovered",
                        "recovered_at": now,
                        "reason": "presence_budget_pressure_cleared",
                    }
                    db.execute(text("UPDATE storage SET value=:v WHERE id=:id"), {
                        "v": json.dumps(policy, ensure_ascii=False), "id": int(row["id"]),
                    })
                    changed = True
                    action = "additive_growth_restored"
                else:
                    action = "would_restore_additive_growth"
            else:
                action = "owner_or_other_policy_change_preserved"
        elif row and bool(policy.get("enabled")):
            action = "additive_growth_enabled_compatible"
        else:
            action = "additive_growth_disabled_compatible"

    # PRESENCE_BUDGET_OWNER_ESCALATION_V1: once structural pressure is proven,
    # additive-growth freeze is only the safe automatic part. Existing paid
    # placement remains a business choice and can keep spending above the limit,
    # so the owner must see the exact unresolved decision instead of a false
    # "no action required" state.
    # PRESENCE_BUDGET_OWNER_HISTORY_TRUTH_V1:
    # Explain the evidence that actually caused the structural freeze. When the
    # block comes from completed days, never tell the owner that "today already
    # spent 0" merely because the new Moscow day has just started.
    _p_today = pressure.get("today") if isinstance(pressure.get("today"), dict) else {}
    _p_completed = pressure.get("completed_days") if isinstance(pressure.get("completed_days"), list) else []
    _limit_owner = float(pressure.get("daily_budget_limit_rub") or 0)
    _owner_action = None
    if bool(pressure.get("blocked")):
        _today_presence = float(_p_today.get("presence_rub") or 0)
        if _limit_owner > 0 and _today_presence >= _limit_owner:
            _evidence_text = (
                f"Сегодня базовое платное размещение Avito уже списало {_today_presence:.0f} ₽ "
                f"при суточном лимите {_limit_owner:.0f} ₽."
            )
        else:
            _over_completed = [
                x for x in _p_completed
                if isinstance(x, dict)
                and float(x.get("presence_rub") or 0) >= _limit_owner > 0
            ]
            _examples = ", ".join(
                f"{str(x.get('date') or '')}: {float(x.get('presence_rub') or 0):.0f} ₽"
                for x in _over_completed[:2]
            )
            if _over_completed:
                _evidence_text = (
                    f"Базовое платное размещение Avito превышало суточный лимит {_limit_owner:.0f} ₽ "
                    f"в {len(_over_completed)} из последних {len(_p_completed)} завершённых дней"
                    + (f" ({_examples})." if _examples else ".")
                )
            else:
                _evidence_text = (
                    f"Базовое платное размещение Avito недавно превысило безопасный суточный лимит "
                    f"{_limit_owner:.0f} ₽; текущий рост остаётся заморожен до подтверждения устойчивого расхода."
                )
        _owner_action = (
            _evidence_text + " "
            "BORIS уже остановил добавление новых объявлений и новые повышения ставок. "
            "Чтобы безопасно возобновить рост, нужно уменьшить/отключить часть действующего платного размещения "
            "или сознательно изменить суточный лимит."
        )
    runtime = {
        "policy_version": "PRESENCE_BUDGET_GROWTH_SELF_HEAL_V1",
        "account_id": account_id,
        "checked_at": now,
        "pressure": pressure,
        "action": action,
        "changed_policy": changed,
        "inventory_growth_enabled": bool(policy.get("enabled")) if row else False,
        "owner_action_required": bool(_owner_action),
        "owner_action": _owner_action,
    }
    runtime["dry_run"] = not bool(mutate)
    if mutate:
        runtime_row = db.execute(text("""
            SELECT id FROM storage
             WHERE account_id=:a AND key='presence_budget_pressure_runtime'
             ORDER BY id DESC LIMIT 1
        """), {"a": account_id}).mappings().first()
        raw_runtime = json.dumps(runtime, ensure_ascii=False)
        if runtime_row:
            db.execute(text("UPDATE storage SET value=:v WHERE id=:id"), {
                "v": raw_runtime, "id": int(runtime_row["id"]),
            })
        else:
            db.execute(text("INSERT INTO storage(account_id,key,value) VALUES(:a,'presence_budget_pressure_runtime',:v)"), {
                "a": account_id, "v": raw_runtime,
            })
        db.commit()
    return runtime
