# -*- coding: utf-8 -*-
"""Клиентский отчёт для «Бытовки СПб»: незакрытые обязательства менеджера.
Только два их аккаунта. Сообщения покупателям НЕ отправляются и не готовятся."""
import json
import os

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_value import dialog_money

SRC = "/root/BORIS/baseline/react_final.json"
if not os.path.exists(SRC):
    SRC = "/tmp/react_final.json"
ACCOUNTS = {"prodazha_bytovok_25677": "Бытовки — продажа",
            "3411770_94346": "Бытовки — аренда"}
HOT_DAYS = 7

recs = json.load(open(SRC, encoding="utf-8"))["records"]
mgr = [x for x in recs
       if (x["verdict"] == "manager_action" or x.get("needs_manager_action"))
       and x["account_id"] in ACCOUNTS]
db = SessionLocal()
for x in mgr:
    x["money"] = dialog_money(db, x["account_id"], x["chat"])
    x["value"] = x["money"].get("display") or 0

hot = sorted([x for x in mgr if x["age"] <= HOT_DAYS], key=lambda z: -z["value"])
late = sorted([x for x in mgr if x["age"] > HOT_DAYS], key=lambda z: -z["age"])


def rub(n):
    return "{:,}".format(int(n)).replace(",", " ") + " \u20bd"


def money(x):
    m = x["money"]
    parts = []
    for k, label in (("price", "цена"), ("rent", "аренда"), ("deposit", "залог"),
                     ("delivery", "доставка")):
        if m.get(k):
            parts.append("%s %s" % (label, rub(m[k]["amount"])))
    return ", ".join(parts) if parts else "сумма не называлась"


def proof(x, n=3):
    ids = (x.get("evidence") or [])[:n]
    if not ids:
        return []
    rows = db.execute(text(
        "SELECT id, direction, left(coalesce(text,''), 150) FROM messenger_messages"
        " WHERE id = ANY(:i) ORDER BY id"), {"i": list(ids)}).fetchall()
    return ["[%s] %s: %s" % (r[0], "клиент" if str(r[1]).lower().startswith("in") else "мы",
                             " ".join((r[2] or "").split())) for r in rows]


def block(title, items, action_label):
    print("\n" + "=" * 78)
    print("%s — %d задач | обсуждалось %s" % (title, len(items), rub(sum(x["value"] for x in items))))
    for i, x in enumerate(items, 1):
        print("\n%d. %s | %s" % (i, ACCOUNTS[x["account_id"]], str(x["title"] or "-")[:44]))
        print("   Ждёт: %s дн (последнее сообщение %s)" % (x["age"], x["date"]))
        print("   Что произошло: %s" % str(x.get("summary"))[:200])
        print("   Обсуждалось: %s" % money(x))
        print("   %s" % action_label)
        for line in proof(x):
            print("      %s" % line[:150])


print("=" * 78)
print("БЫТОВКИ СПб — КЛИЕНТЫ, КОТОРЫЕ ЖДУТ ОТВЕТА")
print("Суммы ниже — то, что обсуждалось в незавершённых диалогах, а не выручка.")
block("🔴 ОТВЕТИТЬ СЕГОДНЯ", hot, "Что сделать: выполнить обещанное — прислать расчёт, фото или документы.")
block("🟡 ПРОСРОЧЕННЫЕ ОБЯЗАТЕЛЬСТВА", late,
      "Что сделать: извиниться за задержку и закрыть обещание либо честно снять вопрос.")

print("\n" + "=" * 78)
print("ИТОГО")
print("   срочных задач: %d | обсуждалось %s" % (len(hot), rub(sum(x["value"] for x in hot))))
print("   просроченных:  %d | обсуждалось %s" % (len(late), rub(sum(x["value"] for x in late))))
for acc, name in ACCOUNTS.items():
    n = len([x for x in mgr if x["account_id"] == acc])
    print("   %-20s %d задач" % (name, n))
print("   просрочено 14+ дней: %d | 30+ дней: %d"
      % (len([x for x in late if x["age"] >= 14]), len([x for x in late if x["age"] >= 30])))
print("\nСообщения покупателям не отправлялись и не готовились.")
