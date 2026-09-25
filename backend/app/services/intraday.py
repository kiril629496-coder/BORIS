# -*- coding: utf-8 -*-
"""
Внутридневной слой управления ставками BORIS.

Отвечает на три вопроса и ничего не исполняет:
  1. Какое сейчас рабочее окно клиента           -> business_day_window()
  2. Успеваем ли мы к дневной цели                -> kpi_pace()
  3. Кого можно усилить и почему именно его       -> select_intraday_raise_candidates()

Семидневный советник не трогается. Три источника сигнала хранятся раздельно:
historical_signal (7 полных дней) · intraday_signal (сегодня) · kpi_pace (темп).
Решение о применении принимает вызывающий, разрешение — autonomy guard.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from app.services.marketing_clock import marketing_today_iso

import os as _os
import sys as _sys
from app.services.exception_observability import observe_suppressed

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:  # чтобы импорт работал и при запуске файла как скрипта
    _sys.path.insert(0, _ROOT)

try:  # правила оптимизации: отсутствие модуля не ломает отбор кандидатов
    from app.services import marketing_rules as _MR
    from app.services import optimization_state as _OS
except Exception:  # pragma: no cover
    _MR = None
    _OS = None

# --------------------------------------------------------------- настройки
INTRADAY_GRACE_SHARE = 0.15     # первые 15% рабочего окна — только наблюдаем
BEHIND_THRESHOLD = 1.0          # любое отставание от плана — повод действовать
SMOOTHING_K = 30                # сглаживание конверсии к среднему по аккаунту
TOP_N = 60                      # aggressive CPL-headroom mode: до 50 действий/цикл + запас
W_CONVERSION = 0.40
W_SAMPLE = 0.25
W_INTRADAY = 0.20
W_HEADROOM = 0.15
SAMPLE_FULL_VIEWS = 300         # при скольких просмотрах доверие к выборке = 1

REACH_RESCUE_MAX_ACTIVE = 5
REACH_RESCUE_MAX_ACTIONS = 3
REACH_RESCUE_MAX_VIEWS_7D = 5
REACH_RESCUE_MAX_VIEWS_TODAY_NO_CONTACT = 10
REACH_RESCUE_MIN_MEASURE_MINUTES = 1440  # REACH_RESCUE_DAILY_EVIDENCE_V2

FALLBACK_WINDOW = {
    "start": "09:00", "end": "20:00", "timezone": "Europe/Moscow",
    "tz_offset_hours": 3,
    "source": "historical_activity_30d",
    "window_semantics": "customer_inbound_activity",
    "evidence": "344 входящих за 30 дней, p10 10:00, p90 19:00, 91.6% в 09-19",
}


def _st(db, account_id, key):
    r = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k"),
                   {"a": account_id, "k": key}).fetchone()
    if not r:
        return None
    try:
        return json.loads(r[0])
    except Exception:
        return None


# --------------------------------------------------------------- 1. окно
def business_day_window(db, account_id):
    """Явный график клиента приоритетнее исторической активности."""
    try:
        rows = db.execute(text(
            "SELECT name, value FROM client_facts"
            " WHERE account_id=:a AND status='confirmed'"
            " AND (lower(name) LIKE '%график работы%' OR lower(name) LIKE '%часы работы%'"
            "      OR lower(name) LIKE '%время работы%')"
            " LIMIT 1"), {"a": account_id}).fetchall()
        if rows:
            return {**FALLBACK_WINDOW, "source": "company_profile",
                    "window_semantics": "declared_working_hours",
                    "raw_value": rows[0][1], "raw_name": rows[0][0],
                    "note": "график найден в базе знаний, часы требуют разбора вручную"}
    except Exception as e:
        print(f"[intraday] client_facts недоступны: {str(e)[:100]}")
    return dict(FALLBACK_WINDOW)


def _local_now(window, now=None):
    # All persisted daily_stats keys use the server/UTC calendar day. Keep
    # business-local time only for pace/reporting; money guards must not switch
    # to a future local date before the collector has created that day's facts.
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return now + timedelta(hours=int(window.get("tz_offset_hours") or 0))


def _stats_day(db, account_id, local_day):
    """Resolve the freshest canonical stats day without crossing into stale data."""
    local_key = f"daily_stats:{local_day}"
    local = _st(db, account_id, local_key) or {}
    if local:
        return local_day, local
    utc_day = marketing_today_iso()
    utc = _st(db, account_id, f"daily_stats:{utc_day}") or {}
    return (utc_day, utc) if utc else (local_day, {})


def _hhmm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------- 2. темп
def _historical_demand_cdf(db, account_id, local_hour: int, *, lookback_days=60):
    """Account-scoped historical useful-demand share by hour.

    Uses first-seen Avito messenger leads as the closest timestamped demand fact
    available across the installed production accounts. Prefer the same weekday
    when the sample is large enough, otherwise fall back to all recent lead days.
    No current-day events are used, so today's KPI result cannot leak into its own
    expectation curve. If evidence is thin, return a conservative linear fallback.
    """
    linear = max(0.0, min(1.0, (int(local_hour) + 1) / 24.0))
    try:
        rows = db.execute(text("""
            SELECT created_at
              FROM messenger_leads
             WHERE account_id=:a
               AND created_at >= now() - (:days * interval '1 day')
               AND created_at < date_trunc('day', now())
             ORDER BY created_at DESC
             LIMIT 5000
        """), {"a": account_id, "days": int(lookback_days)}).fetchall()
    except Exception:
        return {"share": round(linear, 4), "source": "linear_fallback", "sample": 0}
    stamps = [r[0] for r in rows if r and r[0] is not None]
    if not stamps:
        return {"share": round(linear, 4), "source": "linear_fallback", "sample": 0}
    # messenger_leads.created_at is stored without timezone in this legacy table.
    # Compare hour-of-day in the same stored clock rather than inventing a TZ
    # conversion that the historical rows cannot prove.
    now_weekday = datetime.now().weekday()
    same_weekday = [x for x in stamps if getattr(x, "weekday", lambda: -1)() == now_weekday]
    chosen = same_weekday if len(same_weekday) >= 20 else stamps
    source = "messenger_leads_same_weekday_60d" if chosen is same_weekday else "messenger_leads_60d"
    if len(chosen) < 30:
        return {"share": round(linear, 4), "source": "linear_fallback_low_sample", "sample": len(chosen)}
    by_now = sum(1 for x in chosen if int(getattr(x, "hour", 0)) <= int(local_hour))
    empirical = by_now / max(1, len(chosen))
    # Shrink empirical CDF toward linear time so one client's unusual historical
    # burst cannot create an extreme spend instruction. Confidence reaches 1 at
    # 200 observations; below that the fallback keeps significant weight.
    confidence = min(1.0, len(chosen) / 200.0)
    share = empirical * confidence + linear * (1.0 - confidence)
    share = max(0.02, min(1.0, share))
    return {"share": round(share, 4), "source": source,
            "sample": len(chosen), "empirical_share": round(empirical, 4),
            "confidence": round(confidence, 3)}


def kpi_pace(db, account_id, now=None, window=None):
    """24/7 KPI pace.

    Declared business hours remain metadata for reports, but never stop the AI
    marketer. Progress is measured across the whole calendar day, so BORIS can
    optimize bids at night as well as during customer-facing hours.
    """
    window = window or business_day_window(db, account_id)
    loc = _local_now(window, now)
    minutes_now = loc.hour * 60 + loc.minute
    elapsed_share = max(0.0, min(1.0, minutes_now / 1440.0))
    demand = _historical_demand_cdf(db, account_id, loc.hour)
    demand_share = float(demand.get("share") or elapsed_share)

    kpi = _st(db, account_id, "kpi_settings") or {}
    target = kpi.get("target_leads_per_day")
    stats_day, today = _stats_day(db, account_id, loc.date().isoformat())
    actual = sum(int(i.get("contacts") or 0) for i in (today.get("items") or []))
    expected = round(float(target) * demand_share, 2) if target else None

    if target is None:
        state = "NO_TARGET"
    elif actual >= float(target):
        state = "AHEAD"
    elif expected is not None and actual < expected * BEHIND_THRESHOLD:
        state = "BEHIND"
    else:
        state = "ON_TRACK"

    return {"state": state, "target": target, "actual": actual,
            "expected_by_now": expected, "elapsed_share": round(elapsed_share, 3),
            "demand_share": round(demand_share, 4), "demand_curve": demand,
            "local_time": loc.strftime("%Y-%m-%d %H:%M"), "business_window": window,
            "execution_window": "24/7", "grace_share": 0.0}


# --------------------------------------------------------------- бюджет
def budget_signal(db, account_id):
    """Canonical money signal from Avito daily spending facts; fail closed."""
    kpi = _st(db, account_id, "kpi_settings") or {}
    limit = float(kpi.get("daily_budget_limit_rub") or 0)
    utc_day = marketing_today_iso()
    stats = _st(db, account_id, f"daily_stats:{utc_day}") or {}
    spending = stats.get("spending") if isinstance(stats.get("spending"), dict) else {}
    status = str(spending.get("status") or "unknown")
    raw = spending.get("all_spend_rub")
    try:
        spent = float(raw) if raw is not None else None
    except Exception:
        spent = None
    proven = status == "ok" and spent is not None
    budget_configured = limit > 0
    pct = round(spent * 100.0 / limit, 1) if proven and budget_configured else None
    near = bool(proven and limit > 0 and spent >= limit * 0.90)
    exceeded = bool(proven and limit > 0 and spent >= limit)
    return {"source": "avito_daily_spending", "spent_today_rub": spent,
            "limit": limit, "limit_pct": pct, "spend_proven": proven,
            "state": ("BUDGET_EXCEEDED" if exceeded else
                      "BUDGET_NEAR_LIMIT" if near else
                      "BUDGET_OK" if proven and limit > 0 else "BUDGET_UNKNOWN"),
            "near_limit": near, "exceeded": exceeded,
            "budget_configured": budget_configured,
            "note": ("достоверный расход Avito за день" if proven and budget_configured else
                     "дневной бюджет не задан — повышение запрещено" if not budget_configured else
                     "нет достоверных данных расхода — повышение запрещено")}


# --------------------------------------------------------------- мандат
def _mandate(db, account_id):
    # MONEY_MANDATE_SOURCE_ISOLATION_V1: intraday KPI planning must use the
    # paid_tariff_kpi envelope, never the newer one-item feed-launch envelope.
    r = db.execute(text(
        "SELECT id, version, max_bid_rub, max_bid_delta_pct, max_actions_run,"
        " max_actions_day FROM money_mandates"
        " WHERE :a = ANY(account_scope) AND status='active' AND revoked_at IS NULL"
        "   AND 'cpx.raise_bid'=ANY(allowed_operations)"
        "   AND (valid_from IS NULL OR valid_from<=now())"
        "   AND (valid_until IS NULL OR valid_until>now())"
        " ORDER BY CASE WHEN source='paid_tariff_kpi' THEN 0 WHEN source='new_feed_launch' THEN 1 ELSE 2 END, id DESC LIMIT 1"),
        {"a": account_id}).fetchone()
    if not r:
        return None
    return {"id": r[0], "version": r[1], "max_bid_rub": float(r[2]),
            "max_bid_delta_pct": int(r[3]), "max_actions_run": int(r[4]),
            "max_actions_day": int(r[5])}


def _raise_feedback_state(db, account_id, item_id):
    """Latest raise feedback only; old losers do not blacklist forever."""
    rows = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k ORDER BY id DESC"), {"a":account_id,"k":f"cpx_measure:{item_id}:raise"}).fetchall()
    states=[]
    for (v,) in rows:
        try:
            st=json.loads(v or "{}")
            if isinstance(st,dict): states.append(st)
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=259)
    if not states: return "none"
    states.sort(key=lambda x:str(x.get("finished_at") or x.get("started_at") or ""),reverse=True)
    st=states[0]
    if st.get("status")=="waiting_measurement": return "waiting"
    if st.get("status")!="measured": return "none"
    return {"worsened":"loser","improved":"winner",
            "inconclusive_no_signal":"no_signal"}.get(str(st.get("effect") or ""),"observe")


def _negative_raise_measurement(db, account_id, item_id):
    return _raise_feedback_state(db,account_id,item_id)=="loser"

def _measure_locked(db, account_id, item_id):
    rows = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key LIKE :k"),
        {"a": account_id, "k": f"cpx_measure:{item_id}:%"}).fetchall()
    for (v,) in rows:
        try:
            if json.loads(v).get("status") == "waiting_measurement":
                return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=280)
    return False


def _mutated_recently(db, account_id, item_id, minutes=50):
    """Не дёргаем одну ставку чаще часового цикла, но и не морозим её на сутки."""
    r = db.execute(text(
        "SELECT count(*) FROM action_log WHERE account_id=:a AND object_name=:i"
        " AND ts >= now() - (:mins * interval '1 minute')"
        " AND object_kind LIKE '%объявлен%'"),
        {"a": account_id, "i": str(item_id), "mins": int(minutes)}).fetchone()
    return bool(r and int(r[0]) > 0)


def _hypothesis_for(db, account_id, rule, item_id, current_bid, views_7d, contacts_7d):
    """Накладывает правило оптимизации ПОВЕРХ готового ранжирования.

    Ничего не решает и ничего не меняет: помечает кандидата и считает target
    от baseline первого применения, а не от текущей ставки. Fail-closed:
    нет правила, модуля или данных -> None, поведение остаётся прежним.
    """
    if not rule or _MR is None or _OS is None:
        return None
    try:
        matched, why = _MR.matches(rule, views_7d, contacts_7d)
        states_any = _OS.load_any_version(db, account_id, item_id,
                                          rule["optimization_rule_id"])
        vsuf = ":v%s" % rule["rule_version"]
        states_same = [s for k, s in states_any if k.endswith(vsuf)]
        allowed, state_why = _OS.can_propose(states_same, states_any)
        baseline, base_why = _OS.effective_baseline(current_bid, states_any)
        target = (_MR.proposed_bid(rule, baseline)
                  if (matched and allowed and baseline) else None)
        return {"optimization_rule_id": rule["optimization_rule_id"],
                "rule_version": rule["rule_version"],
                "rule_name": rule["name"],
                "matched": bool(matched and allowed),
                "rule_reason": why,
                "state_reason": state_why,
                "baseline_bid": baseline,
                "baseline_source": base_why,
                "target_bid": target,
                "params": dict(rule["params"]),
                "note": ("рекомендация; изменение ставки проходит мандат, "
                         "лимиты, guard и подтверждение")}
    except Exception as e:
        print("[intraday] гипотеза не рассчитана для %s: %s" % (item_id, str(e)[:100]))
        return None


# --------------------------------------------------------------- 3. отбор
def _intraday_cpl_signal(db, account_id, day, red_cpl_fallback=0.0):
    """Canonical intraday CPL from Avito daily spending facts."""
    # KPI_INTRADAY_CPL_CANONICAL_SPEND_V1: wallet balance movements are not ad
    # spend. Use the same stats/v2 spending snapshot as kpi_check/budget_signal.
    kpi = _st(db, account_id, "kpi_settings") or {}
    red = float(kpi.get("max_cost_per_lead_rub") or red_cpl_fallback or 0)
    today = _st(db, account_id, f"daily_stats:{day}") or {}
    contacts = sum(int(i.get("contacts") or 0) for i in (today.get("items") or []))
    spending = today.get("spending") if isinstance(today.get("spending"), dict) else {}
    raw = spending.get("all_spend_rub") if spending.get("status") == "ok" else None
    try: spent = float(raw) if raw is not None else None
    except Exception: spent = None
    actual = round(spent / contacts, 2) if spent is not None and contacts > 0 else None
    return {"red_cpl_rub": red, "actual_cpl_rub": actual,
            "spent_today_rub": round(spent, 2) if spent is not None else None,
            "contacts_today": contacts, "source": "avito_daily_spending"}


def select_intraday_raise_candidates(db, account_id, now=None):
    pace = kpi_pace(db, account_id, now)
    budget = budget_signal(db, account_id)
    _budget_limit = float(budget.get("limit") or 0)
    _spent_today = budget.get("spent_today_rub")
    _demand_share = float(pace.get("demand_share") or pace.get("elapsed_share") or 0)
    # Keep a 10 percentage-point operational buffer over historical demand; this
    # is a pacing gate, not permission to spend. The final money guard remains
    # authoritative and independently enforces the hard daily ceiling.
    _allowed_share = min(1.0, max(0.10, _demand_share + 0.10))
    _allowed_spend_now = round(_budget_limit * _allowed_share, 2) if _budget_limit > 0 else None
    _remaining_budget = (round(max(0.0, _budget_limit - float(_spent_today)), 2)
                         if _budget_limit > 0 and _spent_today is not None else None)
    _spend_pace_exceeded = bool(_allowed_spend_now is not None and _spent_today is not None
                                and float(_spent_today) > _allowed_spend_now)
    budget.update({"demand_share": round(_demand_share,4),
                   "allowed_spend_share": round(_allowed_share,4),
                   "allowed_spend_by_now_rub": _allowed_spend_now,
                   "remaining_budget_rub": _remaining_budget,
                   "spend_pace_exceeded": _spend_pace_exceeded})
    mandate = _mandate(db, account_id)
    window = pace["business_window"]
    local_day = _local_now(window, now).date().isoformat()
    day, _today_stats = _stats_day(db, account_id, local_day)

    adv = _st(db, account_id, "cpx_advice") or {}
    recs = adv.get("recommendations") or {}
    target_cfg = adv.get("target") or {}
    fact_cfg = adv.get("fact") or {}
    red_cpl = float(target_cfg.get("max_cpl_rub") or 0)
    actual_cpl = fact_cfg.get("account_cpl_rub")
    try:
        actual_cpl = float(actual_cpl) if actual_cpl is not None else None
    except Exception:
        actual_cpl = None
    live_cpl = _intraday_cpl_signal(db, account_id, day, red_cpl)
    if live_cpl.get("red_cpl_rub"):
        red_cpl = float(live_cpl["red_cpl_rub"])
    if live_cpl.get("actual_cpl_rub") is not None:
        actual_cpl = float(live_cpl["actual_cpl_rub"])
    # Пока доказанный/рассчитанный CPL аккаунта ниже красной цены, задача не
    # «сберечь ставку», а масштабировать объём. Headroom mode не зависит от
    # выполнения дневного количества лидов: дешёвые лиды продолжаем выжимать.
    cpl_headroom = bool(red_cpl > 0 and actual_cpl is not None and actual_cpl >= 0 and actual_cpl < red_cpl)
    hist = {}
    for group in ("raise", "lower", "watching", "archive_candidates", "lower_or_archive"):
        for e in (recs.get(group) or []):
            if isinstance(e, dict) and e.get("id") is not None:
                hist[str(e["id"])] = {"group": group, "bid_rub": e.get("bid_rub"),
                                      "views_7d": e.get("views_7d") or 0,
                                      "contacts_7d": e.get("contacts_7d") or 0,
                                      "reason_code": e.get("reason_code"),
                                      "promotion_active": bool(e.get("promotion_active")),
                                      "promotion_mode": e.get("promotion_mode") or "off",
                                      "title": e.get("title") or ""}

    today = _today_stats
    intraday = {}
    for i in (today.get("items") or []):
        iid = str(i.get("id"))
        intraday[iid] = {"contacts_today": int(i.get("contacts") or 0),
                         "views_today": int(i.get("views") or 0),
                         "title": i.get("title") or "", "status": i.get("status")}

    tot_c = sum(h["contacts_7d"] for h in hist.values())
    tot_v = sum(h["views_7d"] for h in hist.values())
    if tot_v > 0:
        base_conv, base_src = tot_c / tot_v, "account_7d"
    else:
        base_conv, base_src = 0.0, "fallback_zero"

    max_bid = mandate["max_bid_rub"] if mandate else None
    # CPX_ITEM_CAPABILITY_COOLDOWN_V1: skip item-level Avito CPX 403s for 24h.
    capability_unavailable = set()
    try:
        _cd = _st(db, account_id, "cpx_item_capability_cooldown") or {}
        _items = _cd.get("items") if isinstance(_cd, dict) else {}
        _now = datetime.now(timezone.utc)
        for _iid,_raw in (_items.items() if isinstance(_items,dict) else []):
            try:
                _ts = datetime.fromisoformat(str(_raw).replace("Z", "+00:00"))
                if (_now - _ts).total_seconds() < 24*3600:
                    capability_unavailable.add(str(_iid))
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=433)
    except Exception:
        capability_unavailable = set()
    rule = None
    if _MR is not None:
        try:
            rule = _MR.get_optimization_rule(db, "cpx_raise")
        except Exception as e:
            print("[intraday] правило оптимизации недоступно: %s" % str(e)[:100])
    out = []
    for iid in set(list(hist) + list(intraday)):
        h = hist.get(iid, {"views_7d": 0, "contacts_7d": 0, "bid_rub": None,
                           "reason_code": None, "promotion_active": False,
                           "promotion_mode": "off", "title": "", "group": None})
        t = intraday.get(iid, {"contacts_today": 0, "views_today": 0,
                               "title": "", "status": None})
        bid = h.get("bid_rub")
        v7, c7 = int(h["views_7d"] or 0), int(h["contacts_7d"] or 0)
        ct, vt = t["contacts_today"], t["views_today"]

        smoothed = (c7 + base_conv * SMOOTHING_K) / (v7 + SMOOTHING_K)
        sample = min(1.0, math.log10(1 + v7) / math.log10(1 + SAMPLE_FULL_VIEWS))
        intr = min(1.0, ct / 3.0)
        headroom = (max(0.0, (max_bid - float(bid)) / max_bid)
                    if (max_bid and bid is not None) else 0.0)
        # конверсия учитывается ПРОПОРЦИОНАЛЬНО доверию к выборке:
        # 100% на одном просмотре не должны обгонять 5% на семистах
        rel_conv = (smoothed / base_conv) if base_conv > 0 else 0.0
        conv_component = min(rel_conv, 2.0) / 2.0 * sample
        score = round(100 * (W_CONVERSION * conv_component
                             + W_SAMPLE * sample + W_INTRADAY * intr
                             + W_HEADROOM * headroom), 1)

        # причины фильтрации, по порядку строгости
        status = None
        _raise_feedback = _raise_feedback_state(db, account_id, iid)
        if _raise_feedback == "loser":
            status = "negative_feedback"
        elif _raise_feedback == "no_signal":
            status = "no_signal_feedback"
        if status is None and str(iid) in capability_unavailable:
            status = "cpx_unavailable"
        if status is None and bid is None and not ((c7 > 0 or ct > 0) and (pace["state"] == "BEHIND" or cpl_headroom)):
            # Без текущего продвижения разрешаем старт только доказанно работающим
            # объявлениям при отставании, либо более широкую разведку при дешёвом CPL.
            if not (cpl_headroom and (vt > 0 or v7 > 0)):
                status = "insufficient_signal"
        if status is None and ct == 0 and c7 == 0 and not (cpl_headroom and (vt > 0 or v7 > 0)):
            status = "insufficient_signal"
        if (status is None and vt == 0 and ct == 0
                and not (cpl_headroom and v7 > 0)
                and not (pace["state"] == "BEHIND" and c7 > 0)):
            status = "insufficient_signal"
        if status is None and bid is not None and max_bid is not None and float(bid) >= max_bid:
            status = "max_bid_reached"
        if status is None and _mutated_recently(db, account_id, iid):
            status = "recently_mutated"
        # Observation window is mandatory after every mutation. KPI lag or CPL
        # headroom may justify choosing a candidate, but must never bypass the
        # measurement lock; otherwise the runner can stack raises before the
        # previous hypothesis has a measurable result.
        if status is None and _measure_locked(db, account_id, iid):
            status = "measure_locked"
        if status is None and (not budget.get("budget_configured") or budget["exceeded"] or budget.get("near_limit") or not budget.get("spend_proven")):
            status = "blocked_budget"
        if status is None and budget.get("spend_pace_exceeded"):
            status = "blocked_spend_pacing"
        if status is None and pace["state"] != "BEHIND" and not cpl_headroom:
            status = "blocked_by_pace"
        if status is None:
            status = "eligible"

        hyp = _hypothesis_for(db, account_id, rule, iid, bid, v7, c7)

        out.append({
            "item_id": iid, "title": (h.get("title") or t.get("title") or "")[:60],
            "hypothesis": hyp,
            "status": status, "final_score": score,
            "components": {"smoothed_conversion": round(smoothed, 4),
                           "sample_confidence": round(sample, 3),
                           "intraday_signal": round(intr, 3),
                           "bid_headroom": round(headroom, 3),
                           "conversion_component": round(conv_component, 4),
                           "base_conversion": round(base_conv, 4),
                           "base_source": base_src},
            "historical_signal": {"window": "7 полных дней, без сегодняшнего",
                                  "views_7d": v7, "contacts_7d": c7,
                                  "conversion_7d": (round(c7 * 100.0 / v7, 2) if v7 else None),
                                  "reason_code": h.get("reason_code"),
                                  "advisor_group": h.get("group")},
            "intraday_signal": {"window": f"{day} 00:00 -> now",
                                "contacts_today": ct, "views_today": vt},
            "current_bid_rub": bid,
            "promotion_active": bool(h.get("promotion_active")),
            "promotion_mode": h.get("promotion_mode") or "off",
            "raise_feedback": _raise_feedback,
            "winner_memory": bool(_raise_feedback == "winner"),
        })

    # WINNER_MEMORY_RANK_V1: among candidates that passed all current money guards,
    # prefer an already measured winner. This is ranking only; red CPL, budget,
    # mandate, measurement lock and hard bid caps remain authoritative.
    out.sort(key=lambda x: (
        x["status"] != "eligible",
        not bool(x.get("winner_memory")),
        -x["final_score"],
    ))
    eligible = [c for c in out if c["status"] == "eligible"]
    ranked = out[:TOP_N] if not eligible else eligible[:TOP_N]

    reasons = []
    # KPI_INTRADAY_CPL_REDLINE_BLOCK_V1: being behind KPI never overrides an
    # already-proven CPL violation.  The growth lane must hold; the KPI goal
    # state machine can lower weak paid bids and work on conversion instead.
    if actual_cpl is not None and red_cpl > 0 and actual_cpl > red_cpl:
        reasons.append("cpl_above_redline")
    if pace["state"] != "BEHIND" and not cpl_headroom:
        reasons.append("kpi_" + pace["state"].lower())
    if not budget.get("budget_configured"):
        reasons.append("advisor_daily_budget_missing")
    elif budget["exceeded"]:
        reasons.append("advisor_daily_limit_exceeded")
    elif budget.get("near_limit"):
        reasons.append("advisor_daily_limit_near")
    elif not budget.get("spend_proven"):
        reasons.append("advisor_spend_unknown")
    elif budget.get("spend_pace_exceeded"):
        reasons.append("advisor_spend_pace_exceeded")
    if mandate is None:
        reasons.append("no_active_mandate")

    # REACH_STARVATION_RESCUE_V1: when KPI is behind because the portfolio is
    # barely shown at all, lack of historical contacts is not evidence that a
    # bid increase is bad. Expose a tiny test cohort; execution remains subject
    # to explicit budget, spend pacing, hard bid cap and canonical money guard.
    active_items_today = [x for x in (today.get("items") or []) if x.get("status") == "active"]
    total_views_today = sum(int(x.get("views") or 0) for x in active_items_today)
    total_contacts_today = sum(int(x.get("contacts") or 0) for x in active_items_today)
    reach_threshold = max(10, min(50, int(max(1, len(active_items_today)) * 0.03)))
    reach_starved = bool(
        pace.get("state") == "BEHIND" and total_contacts_today == 0
        and total_views_today < reach_threshold
        and budget.get("budget_configured") and budget.get("spend_proven")
        and not budget.get("near_limit") and not budget.get("exceeded")
        and not budget.get("spend_pace_exceeded")
    )
    active_paid_ids = {str(iid) for iid,h in hist.items() if h.get("bid_rub") is not None}
    # REACH_RESCUE_COHORT_DURABLE_V1: provider/advice snapshots can omit a newly
    # changed item for a while. Persist cohort membership in the canonical
    # action log so each hourly cycle continues the same tiny experiment instead
    # of silently adding three fresh paid listings every hour.
    try:
        rescue_started_ids = {str(x[0]) for x in db.execute(text(
            "SELECT DISTINCT object_name FROM action_log"
            " WHERE account_id=:a AND source='reach_rescue'"
            " AND ts >= now() - interval '24 hours'"
            " AND action LIKE '%ставк%'"), {"a":account_id}).fetchall() if x and x[0]}
    except Exception:
        rescue_started_ids = set()
    # REACH_RESCUE_COHORT_DURABLE_V2: only items explicitly admitted by
    # reach_rescue belong to the experiment. Existing manual/legacy paid items
    # are candidates to fill empty slots first, but they do not automatically
    # turn a 15-item paid portfolio into a 15-item rescue cohort.
    cohort_ids = set(rescue_started_ids)
    active_paid_count = len(active_paid_ids)
    rescue = []
    if reach_starved:
        for c in out:
            h = c.get("historical_signal") or {}
            if int(h.get("contacts_7d") or 0) != 0:
                continue
            if int(h.get("views_7d") or 0) > REACH_RESCUE_MAX_VIEWS_7D:
                continue
            i = c.get("intraday_signal") or {}
            if int(i.get("contacts_today") or 0) == 0 and int(i.get("views_today") or 0) >= REACH_RESCUE_MAX_VIEWS_TODAY_NO_CONTACT:
                continue
            if str(c.get("status") or "") in {"negative_feedback", "no_signal_feedback", "cpx_unavailable"}:
                continue
            if c.get("current_bid_rub") is not None and max_bid is not None and float(c.get("current_bid_rub")) >= float(max_bid):
                continue
            iid = c.get("item_id")
            if _mutated_recently(db, account_id, iid, minutes=REACH_RESCUE_MIN_MEASURE_MINUTES):
                continue
            # REACH_RESCUE_DAILY_EVIDENCE_V2: one exploratory money hypothesis gets
            # a full-day observation before another raise on the same listing.
            # Negative completed feedback remains binding at apply_one as well.
            rescue.append(c)
        rescue.sort(key=lambda c: (str(c.get("item_id")) not in cohort_ids,
                                   str(c.get("item_id")) not in active_paid_ids,
                                   c.get("current_bid_rub") is None,
                                   -int((c.get("historical_signal") or {}).get("views_7d") or 0),
                                   -float(c.get("final_score") or 0)))
        slots = max(0, REACH_RESCUE_MAX_ACTIVE - len(cohort_ids))
        chosen=[]
        for c in rescue:
            if len(chosen) >= REACH_RESCUE_MAX_ACTIONS:
                break
            iid_s = str(c.get("item_id"))
            already_in_cohort = iid_s in cohort_ids
            if not already_in_cohort:
                if slots <= 0:
                    continue
                slots -= 1
                cohort_ids.add(iid_s)
            chosen.append(c)
        rescue = chosen

    return {"account_id": account_id,
            "decision": ("RAISE_CANDIDATES" if eligible and not reasons else "HOLD_RAISE"),
            "reasons": reasons,
            "kpi_pace": pace, "budget_signal": budget,
            "cpl_headroom": {"active": cpl_headroom, "actual_cpl_rub": actual_cpl,
                             "red_cpl_rub": red_cpl,
                             "headroom_pct": (round((red_cpl-actual_cpl)*100/red_cpl, 1)
                                              if cpl_headroom else None),
                             "spent_today_rub": live_cpl.get("spent_today_rub"),
                             "contacts_today": live_cpl.get("contacts_today"),
                             "source": live_cpl.get("source")},
            "mandate": mandate,
            "optimization_rule": (_MR.snapshot(rule) if (rule and _MR) else None),
            "hypothesis_matched": len([c for c in out
                                       if (c.get("hypothesis") or {}).get("matched")]),
            "candidates_total": len(out),
            "eligible_candidates": len(eligible),
            "reach_signal": {"starved": reach_starved, "views_today": total_views_today,
                             "contacts_today": total_contacts_today, "active_items": len(active_items_today),
                             "threshold_views": reach_threshold, "active_paid_count": active_paid_count,
                             "durable_reach_cohort_count": len(rescue_started_ids),
                             "effective_reach_cohort_count": len(cohort_ids),
                             "max_active_cohort": REACH_RESCUE_MAX_ACTIVE,
                             "max_views_today_no_contact": REACH_RESCUE_MAX_VIEWS_TODAY_NO_CONTACT,
                             "max_actions_cycle": REACH_RESCUE_MAX_ACTIONS},
            "reach_rescue_candidates": rescue,
            "selected_for_execution": (len(eligible[:mandate["max_actions_run"]])
                                       if (eligible and mandate and not reasons) else 0),
            "top": ranked}


def explain(c):
    """Человеческая причина выбора — для отчёта владельцу, без score."""
    h, i = c["historical_signal"], c["intraday_signal"]
    parts = []
    if h["contacts_7d"]:
        parts.append(f"{h['contacts_7d']} обращений на {h['views_7d']} просмотров за неделю")
    if i["contacts_today"]:
        parts.append(f"{i['contacts_today']} сегодня")
    if c["current_bid_rub"] is not None:
        parts.append(f"ставка {c['current_bid_rub']} руб")
    return f"{c['item_id']}: " + ", ".join(parts) if parts else str(c["item_id"])


if __name__ == "__main__":
    import argparse, os, sys
    sys.path.insert(0, os.environ.get("BORIS_RUNTIME_BACKEND_ROOT", "/root/BORIS/backend"))
    from app.db.session import SessionLocal

    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    a = ap.parse_args()

    db = SessionLocal()
    try:
        r = select_intraday_raise_candidates(db, a.account)
        p, b = r["kpi_pace"], r["budget_signal"]
        w = p["business_window"]
        print("=" * 72)
        print(f"РАБОЧЕЕ ОКНО: {w['start']}-{w['end']} {w['timezone']}"
              f"  source={w['source']}  semantics={w['window_semantics']}")
        print(f"МЕСТНОЕ ВРЕМЯ: {p['local_time']}  прошло {p['elapsed_share']*100:.0f}% окна")
        print(f"KPI: цель {p['target']}, факт {p['actual']}, ожидалось к этому часу"
              f" {p['expected_by_now']}  ->  PACE = {p['state']}")
        print(f"БЮДЖЕТ: net_wallet_change={b['net_wallet_change']} лимит={b['limit']}"
              f" ({b['limit_pct']}%)  exceeded={b['exceeded']}")
        print(f"МАНДАТ: {r['mandate']}")
        print(f"ПРАВИЛО: {r.get('optimization_rule')}")
        print(f"ПОД ПРАВИЛО ПОДХОДИТ ОБЪЯВЛЕНИЙ: {r.get('hypothesis_matched')}")
        print("=" * 72)
        print(f"РЕШЕНИЕ: {r['decision']}   причины: {r['reasons']}")
        print(f"кандидатов всего {r['candidates_total']},"
              f" eligible {r['eligible_candidates']},"
              f" к исполнению {r['selected_for_execution']}")
        print("=" * 72)
        print(f"{'item':12s} {'status':22s} {'score':>6s} {'bid':>6s} {'7д':>10s} {'сегодня':>8s}")
        for c in r["top"]:
            h, i = c["historical_signal"], c["intraday_signal"]
            print(f"{c['item_id']:12s} {c['status']:22s} {c['final_score']:6.1f}"
                  f" {str(c['current_bid_rub']):>6s}"
                  f" {str(h['contacts_7d']) + '/' + str(h['views_7d']):>10s}"
                  f" {str(i['contacts_today']) + '/' + str(i['views_today']):>8s}")
        print()
        for c in r["top"][:3]:
            print("  " + explain(c))
        print("\nSAFETY: только чтение, мутаций и вызовов Avito нет")
    finally:
        db.close()
