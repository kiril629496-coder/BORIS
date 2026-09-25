"""Canonical business-day clock for Russian marketplace money controls.

Avito daily budgets/spendings are operated on the Moscow calendar day.  Server
UTC date differs from Moscow between 00:00 and 02:59 MSK; using UTC there keeps
yesterday's budget brake alive for the first three hours of a new business day.
"""
from __future__ import annotations
from datetime import datetime, date
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")

def marketing_now() -> datetime:
    return datetime.now(MOSCOW)

def marketing_today() -> date:
    return marketing_now().date()

def marketing_today_iso() -> str:
    return marketing_today().isoformat()
