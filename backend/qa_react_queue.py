# -*- coding: utf-8 -*-
"""Три очереди с денежной величиной. Показываем ТОЛЬКО confidence=high,
иначе «цена не определена». Отдельно печатаем отброшенные суммы с цитатами."""
import json
import os

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_value import dialog_money

SRC = "/tmp/react_final.json"
HOT_DAYS = 7
if not os.path.exists(SRC):
    raise SystemExit("Нет %s" % SRC)
recs = json.load(open(SRC, encoding="utf-8"))["records"]
db = SessionLocal()

for x in recs:
    m = dialog_money(db, x["account_id"], x["chat"])
    x["money"] = m
    x["value"] = m.get("display") or 0

mgr = [x for x in recs if x["verdict"] == "manager_action" or x.get("needs_manager_action")]
cand = [x for x in recs if x["verdict"] == "candidate"]
hot = [x for x in mgr if x["age"] <= HOT_DAYS]
late = [x for x in mgr if x["age"] > HOT_DAYS]


def rub(n):
    return "{:,}".format(int(n)).replace(",", " ")


def money_line(x):
    m = x.get("money") or {}
    parts = []
    for key, label in (("price", "цена"), ("rent", "аренда"), ("deposit", "предоплата или залог"),
                       ("delivery", "доставка"), ("installation", "монтаж")):
        a = m.get(key)
        if a and not (key == "rent" and m.get("price")
                      and m["price"]["amount"] == a["amount"]):
            parts.append("%s %s ₽ [%s]" % (label, rub(a["amount"]), a["message_id"]))
    return " | ".join(parts) if parts else "цена не определена"


def show(x, i):
    print("\n   %d. %s | %s | ждёт %s дн" % (i, x["account_id"][:26],
                                             str(x["title"] or "-")[:36], x["age"]))
    print("      ДЕНЬГИ: %s" % money_line(x))
    print("      %s" % str(x.get("summary"))[:140])
    print("      чат %s" % x["chat"])


def total(items):
    return sum(x["value"] for x in items)


print("\n" + "=" * 76)
print("🔴 ОТВЕТИТЬ СЕГОДНЯ (долг продавца, не старше %d дней): %d | подтверждённых сумм %s ₽"
      % (HOT_DAYS, len(hot), rub(total(hot))))
for i, x in enumerate(sorted(hot, key=lambda z: -z["value"]), 1):
    show(x, i)

print("\n" + "=" * 76)
print("🟡 ПРОСРОЧЕННЫЕ ОБЯЗАТЕЛЬСТВА: %d | 14+ дней: %d | 30+ дней: %d | подтверждённых сумм %s ₽"
      % (len(late), len([x for x in late if x["age"] >= 14]),
         len([x for x in late if x["age"] >= 30]), rub(total(late))))
for i, x in enumerate(sorted(late, key=lambda z: -z["value"]), 1):
    show(x, i)

print("\n" + "=" * 76)
print("🔵 РЕАКТИВАЦИЯ: %d кандидатов | подтверждённых сумм %s ₽ | топ-10"
      % (len(cand), rub(total(cand))))
for i, x in enumerate(sorted(cand, key=lambda z: -z["value"])[:10], 1):
    show(x, i)
print("\n   Без определённой цены: %d" % len([x for x in cand if not x["value"]]))

print("\n" + "=" * 76)
print("ОТБРОШЕНО КАК НЕ ЦЕНА (проверка глазами)")
seen, shown = set(), 0
for x in recs:
    for a in (x.get("money") or {}).get("rejected", []):
        key = (a["source"], a["amount"])
        if key in seen or a["amount"] < 1000:
            continue
        seen.add(key)
        shown += 1
        if shown <= 25:
            print("   %-13s %12s ₽ | «%s»" % (a["source"], rub(a["amount"]), a["quote"][:70]))
print("   всего различных отброшенных: %d" % len(seen))
print("\nНичего не отправлено, черновики не создавались.")
