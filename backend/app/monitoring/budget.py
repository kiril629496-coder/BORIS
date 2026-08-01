# -*- coding: utf-8 -*-
"""
Бюджет и лимиты модуля «Поиск клиентов».

Три независимых предохранителя перед КАЖДЫМ вызовом LLM:

  1. Финансовый — резерв под ХУДШИЙ вызов, а не средняя цена.
     если (бюджет − потрачено_за_месяц) < MONITOR_MAX_ANALYSIS_COST_RUB → стоп.
     Гарантия: один длинный дорогой анализ не выскочит за бюджет.

  2. Месячный технический — analyses_this_month ≥ MONITOR_MAX_ANALYSES_PER_MONTH.

  3. Дневной технический — analyses_today ≥ MONITOR_MAX_ANALYSES_PER_DAY.

Проверка ДО вызова. Фактическая стоимость пишется в api_usage ПОСЛЕ (пулом).
Средняя цена считается ТОЛЬКО для показа и прогноза — как финансовый гейт
не используется (по прямому указанию: средняя смотрит назад и пропустит
дорогой вызов).

Бюджет привязан к account_id — сейчас один аккаунт, при подключении клиентов
структура не меняется.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

OPERATION = "monitoring_analyze"
DEFAULT_ACCOUNT = os.getenv("MONITOR_LLM_ACCOUNT", "boris_monitoring")


# ------------------------------------------------------------ настройки

def usd_rub() -> float:
    return float(os.getenv("MONITOR_USD_RUB", "100"))


def budget_usd(account_id: str = DEFAULT_ACCOUNT) -> float:
    """Месячный бюджет в долларах. -1 (или пусто) — без лимита (админ)."""
    v = os.getenv("MONITOR_BUDGET_USD", "1")
    try:
        return float(v)
    except ValueError:
        return 1.0


def budget_rub(account_id: str = DEFAULT_ACCOUNT) -> float:
    return budget_usd(account_id) * usd_rub()


def max_analysis_cost_rub() -> float:
    """Резерв под один худший анализ — сколько нужно оставить в остатке."""
    return float(os.getenv("MONITOR_MAX_ANALYSIS_COST_RUB", "1"))


def max_per_day() -> int:
    return int(os.getenv("MONITOR_MAX_ANALYSES_PER_DAY", "100"))


def max_per_month() -> int:
    return int(os.getenv("MONITOR_MAX_ANALYSES_PER_MONTH", "1000"))


def unlimited(account_id: str = DEFAULT_ACCOUNT) -> bool:
    return budget_usd(account_id) < 0


# ------------------------------------------------------------ чтение api_usage

def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def spent_this_month(cur, account_id: str = DEFAULT_ACCOUNT,
                     now: datetime | None = None) -> float:
    cur.execute(
        "SELECT COALESCE(SUM(cost_rub), 0) FROM api_usage "
        "WHERE account_id = %s AND operation = %s AND created_at >= %s",
        (account_id, OPERATION, _month_start(now)))
    return float(cur.fetchone()[0] or 0)


def analyses_this_month(cur, account_id: str = DEFAULT_ACCOUNT,
                        now: datetime | None = None) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM api_usage "
        "WHERE account_id = %s AND operation = %s AND created_at >= %s",
        (account_id, OPERATION, _month_start(now)))
    return int(cur.fetchone()[0] or 0)


def analyses_today(cur, account_id: str = DEFAULT_ACCOUNT,
                   now: datetime | None = None) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM api_usage "
        "WHERE account_id = %s AND operation = %s AND created_at >= %s",
        (account_id, OPERATION, _day_start(now)))
    return int(cur.fetchone()[0] or 0)


def avg_cost(cur, account_id: str = DEFAULT_ACCOUNT) -> float:
    """Средняя цена анализа — ТОЛЬКО для показа и прогноза, не для гейта."""
    cur.execute(
        "SELECT COALESCE(AVG(cost_rub), 0) FROM api_usage "
        "WHERE account_id = %s AND operation = %s AND cost_rub > 0",
        (account_id, OPERATION))
    return float(cur.fetchone()[0] or 0)


# ------------------------------------------------------------ гейт

def check_budget(cur, account_id: str = DEFAULT_ACCOUNT,
                 now: datetime | None = None) -> dict[str, Any]:
    """
    Возвращает решение ДО вызова LLM:
        {"allowed": bool, "reason": "ok|monthly_budget|daily_analysis_limit|monthly_analysis_limit",
         "spent": ₽, "budget": ₽, "remaining": ₽,
         "analyses_month": n, "analyses_day": n}
    reason != "ok" означает: модель НЕ звать, находку сохранить как awaiting_budget.
    """
    spent = spent_this_month(cur, account_id, now)
    b_rub = budget_rub(account_id)
    remaining = b_rub - spent
    a_month = analyses_this_month(cur, account_id, now)
    a_day = analyses_today(cur, account_id, now)

    base = {"spent": round(spent, 4), "budget": round(b_rub, 4),
            "remaining": round(remaining, 4),
            "analyses_month": a_month, "analyses_day": a_day}

    # безлимит для админа — только технические предохранители
    if not unlimited(account_id):
        # финансовый: остаток должен покрыть ХУДШИЙ вызов
        if remaining < max_analysis_cost_rub():
            return {**base, "allowed": False, "reason": "monthly_budget"}

    if a_day >= max_per_day():
        return {**base, "allowed": False, "reason": "daily_analysis_limit"}
    if a_month >= max_per_month():
        return {**base, "allowed": False, "reason": "monthly_analysis_limit"}

    return {**base, "allowed": True, "reason": "ok"}


# ------------------------------------------------------------ показ и прогноз

def forecast(cur, account_id: str = DEFAULT_ACCOUNT,
             now: datetime | None = None) -> dict[str, Any]:
    """Данные для интерфейса: бюджет, потрачено, остаток, прогноз, средняя цена."""
    import calendar
    now = now or datetime.now(timezone.utc)
    spent = spent_this_month(cur, account_id, now)
    b_rub = budget_rub(account_id)
    avg = avg_cost(cur, account_id)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day = now.day
    # линейный прогноз: потрачено / прошедшие дни × дни месяца
    projected = (spent / day * days_in_month) if day > 0 else spent

    percent_used = round(spent / b_rub * 100, 1) if b_rub > 0 else 0.0
    return {
        "account_id": account_id,
        "budget_usd": budget_usd(account_id),
        "usd_rub": usd_rub(),
        "budget_rub": round(b_rub, 2),
        "spent_rub": round(spent, 4),
        "remaining_rub": round(b_rub - spent, 4),
        "percent_used": percent_used,
        "projected_month_rub": round(projected, 2),
        "avg_analysis_rub": round(avg, 4),
        "analyses_month": analyses_this_month(cur, account_id, now),
        "analyses_today": analyses_today(cur, account_id, now),
        "max_per_day": max_per_day(),
        "max_per_month": max_per_month(),
        "unlimited": unlimited(account_id),
    }


# ------------------------------------------ уведомление один раз за период

def notify_key(account_id: str = DEFAULT_ACCOUNT,
               now: datetime | None = None) -> str:
    """
    Ключ периода для дедупликации уведомления «лимит исчерпан».
    Меняется раз в месяц — значит владелец получит не больше одного
    уведомления за расчётный период.
    """
    now = now or datetime.now(timezone.utc)
    return f"budget_alert:{account_id}:{now.year}-{now.month:02d}"


def already_notified(cur, key: str) -> bool:
    """Проверяет по monitor_runs-маркеру, слали ли уже уведомление в этом периоде."""
    cur.execute(
        "SELECT 1 FROM monitor_runs WHERE mode = %s AND status = 'budget_alert' LIMIT 1",
        (key,))
    return cur.fetchone() is not None


def mark_notified(cur, key: str) -> None:
    from . import store
    cur.execute(
        "INSERT INTO monitor_runs (id, platform, mode, status, started_at) "
        "VALUES (%s, %s, %s, 'budget_alert', %s)",
        (store._uuid(), "budget", key, store._now()))
