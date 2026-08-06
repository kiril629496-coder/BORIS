# -*- coding: utf-8 -*-
"""Индекс здоровья отдела продаж — общий расчёт для экрана, сводки и консоли.
Только существующие факты: долги менеджеров, просроченные обязательства,
клиенты, переставшие отвечать. Ни одного выдуманного коэффициента."""
from datetime import datetime, timezone

from sqlalchemy import text

DEBT = "seller_action_missing"
WINDOW = 60      # окно для оценки объёма работы
HOT_DAYS = 7
FLOOR = 25       # знаменатель не меньше: на маленькой базе один случай не роняет индекс
ACTIVE = ("candidate", "needs_review", "approved", "scheduled", "cooldown")


def _age(ts):
    if not ts:
        return 999
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).days


def _score(d, dialogs):
    n = max(FLOOR, dialogs or 0)
    per100 = lambda x: 100.0 * x / n
    # Невыполненные обещания — вина отдела, основной штраф.
    p_debt = min(25.0, per100(d["debt_hot"]) * 3)
    p_late = min(30.0, per100(d["debt_late"]) * 3 + per100(d["overdue30"]) * 2)
    # Потерянные клиенты — нормальный фон Avito и возможность вернуть, а не болезнь.
    p_lost = min(10.0, per100(d["lost"]) * 0.25)
    return max(0, round(100 - p_debt - p_late - p_lost)), {
        "свежие долги": round(p_debt), "просроченные": round(p_late),
        "потерянные клиенты": round(p_lost)}


def health(db, accounts):
    """Возвращает общий индекс, разбивку по аккаунтам и что сделать для улучшения."""
    if not accounts:
        return {"score": None, "light": "", "accounts": [], "todo": []}
    vol = {r[0]: r[1] for r in db.execute(text(
        "SELECT account_id, count(DISTINCT avito_chat_id) FROM messenger_messages"
        " WHERE account_id = ANY(:a)"
        "   AND to_timestamp(avito_created_at) > now() - make_interval(days => :d)"
        " GROUP BY 1"), {"a": accounts, "d": WINDOW})}
    per_acc = {}
    for a, reason, watch in db.execute(text(
            "SELECT account_id, primary_reason, watch_since FROM reactivation_candidates"
            " WHERE account_id = ANY(:a) AND status = ANY(:s)"),
            {"a": accounts, "s": list(ACTIVE)}):
        d = per_acc.setdefault(a, {"debt_hot": 0, "debt_late": 0, "overdue30": 0, "lost": 0})
        if reason == DEBT:
            if _age(watch) <= HOT_DAYS:
                d["debt_hot"] += 1
            else:
                d["debt_late"] += 1
                if _age(watch) >= 30:
                    d["overdue30"] += 1
        else:
            d["lost"] += 1

    tot = {"debt_hot": 0, "debt_late": 0, "overdue30": 0, "lost": 0}
    rows = []
    for a, d in per_acc.items():
        for k in tot:
            tot[k] += d[k]
        s, parts = _score(d, vol.get(a))
        rows.append({"account_id": a, "score": s, "parts": parts, "counts": d,
                     "dialogs": vol.get(a, 0)})
    rows.sort(key=lambda z: z["score"])
    score, parts = _score(tot, sum(vol.get(a, 0) for a in per_acc)) if per_acc else (100, {})

    todo = []
    if tot["debt_hot"]:
        todo.append("ответить %d клиентам из «Клиенты ждут ответа»" % tot["debt_hot"])
    if tot["debt_late"]:
        todo.append("закрыть %d просроченных обещаний" % tot["debt_late"])
    return {"score": score, "light": "green" if score >= 90 else ("yellow" if score >= 70 else "red"),
            "parts": parts, "counts": tot, "accounts": rows, "todo": todo}
