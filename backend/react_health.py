# -*- coding: utf-8 -*-
"""Индекс здоровья отдела продаж. Первая версия — только на существующих фактах:
долги менеджеров, просроченные обязательства, потерянные клиенты.
Никаких выдуманных коэффициентов: каждое слагаемое считается из базы и объясняется.
Показатели нормируются на объём работы, иначе у крупного клиента индекс всегда красный."""
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal

DEBT = "seller_action_missing"
WINDOW = 60          # окно, за которое считаем объём работы
HOT_DAYS = 7
db = SessionLocal()

BASE = text(
    "SELECT c.account_id, c.primary_reason, c.status, c.watch_since"
    " FROM reactivation_candidates c"
    " WHERE c.status IN ('candidate','needs_review','approved','scheduled','cooldown')")
VOLUME = text(
    "SELECT account_id, count(DISTINCT avito_chat_id) FROM messenger_messages"
    " WHERE to_timestamp(avito_created_at) > now() - make_interval(days => :d)"
    " GROUP BY 1")


def age(ts):
    if not ts:
        return 999
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).days


vol = {r[0]: r[1] for r in db.execute(VOLUME, {"d": WINDOW})}
acc = {}
for a, reason, status, watch in db.execute(BASE).fetchall():
    d = acc.setdefault(a, {"debt_hot": 0, "debt_late": 0, "overdue30": 0, "lost": 0})
    if reason == DEBT:
        if age(watch) <= HOT_DAYS:
            d["debt_hot"] += 1
        else:
            d["debt_late"] += 1
            if age(watch) >= 30:
                d["overdue30"] += 1
    else:
        d["lost"] += 1


def health(d, dialogs):
    """100 минус три штрафа. Каждый нормирован на 100 диалогов и ограничен потолком,
    чтобы один показатель не обнулял индекс целиком."""
    # Знаменатель не меньше 25: на маленькой базе один случай не должен ронять индекс.
    floor = 25
    n = max(floor, dialogs)
    per100 = lambda x: 100.0 * x / n
    # Невыполненные обещания — это вина отдела, штраф основной.
    p_debt = min(25.0, per100(d["debt_hot"]) * 3)
    p_late = min(30.0, per100(d["debt_late"]) * 3 + per100(d["overdue30"]) * 2)
    # Потерянные клиенты — не болезнь, а нормальный фон Avito и возможность вернуть.
    # Штраф символический, иначе индекс у всех вечно красный.
    p_lost = min(10.0, per100(d["lost"]) * 0.25)
    score = max(0, round(100 - p_debt - p_late - p_lost))
    return score, {"свежие долги": round(p_debt), "просроченные": round(p_late),
                   "потерянные клиенты": round(p_lost)}


def light(s):
    return "🟢" if s >= 90 else ("🟡" if s >= 70 else "🔴")


print("=" * 72)
print("ИНДЕКС ЗДОРОВЬЯ ОТДЕЛА ПРОДАЖ")
print("Считается из трёх фактов: свежие долги, просроченные обязательства,")
print("клиенты, переставшие отвечать. Всё нормировано на объём переписок за %d дней." % WINDOW)

tot = {"debt_hot": 0, "debt_late": 0, "overdue30": 0, "lost": 0}
rows = []
for a, d in sorted(acc.items()):
    for k in tot:
        tot[k] += d[k]
    s, parts = health(d, vol.get(a, 0))
    rows.append((s, a, d, parts, vol.get(a, 0)))

for s, a, d, parts, n in sorted(rows):
    print("\n%s %d/100  %s   (диалогов за %d дней: %d)" % (light(s), s, a[:44], WINDOW, n))
    print("   долги: свежих %d, просроченных %d (из них 30+ дней: %d) | потерянных клиентов %d"
          % (d["debt_hot"], d["debt_late"], d["overdue30"], d["lost"]))
    print("   снижение: %s" % ", ".join("%s −%d" % (k, v) for k, v in parts.items() if v))
    todo = []
    if d["debt_hot"]:
        todo.append("ответить %d клиентам из «Ответить сегодня»" % d["debt_hot"])
    if d["debt_late"]:
        todo.append("закрыть %d просроченных обещаний" % d["debt_late"])
    if todo:
        print("   чтобы поднять: %s" % "; ".join(todo))

s, parts = health(tot, sum(vol.get(a, 0) for a in acc))
print("\n" + "=" * 72)
print("ПО ВСЕМ АККАУНТАМ: %s %d/100" % (light(s), s))
print("   свежих долгов %d · просроченных %d · из них 30+ дней %d · потерянных клиентов %d"
      % (tot["debt_hot"], tot["debt_late"], tot["overdue30"], tot["lost"]))
db.close()
