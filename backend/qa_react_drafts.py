# -*- coding: utf-8 -*-
"""Контрольные черновики: 5 price_requested + 5 estimate_sent + 5 no_reply.
Плюс задачи менеджеру по seller_action_missing — БЕЗ обращения к модели.
Ничего не отправляется и не записывается в таблицы реактивации."""
import json
import os

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_draft import generate, OPERATION

SRC = "/tmp/react_final.json"
OUT = "/tmp/react_drafts.json"
PER_REASON = 5

if not os.path.exists(SRC):
    raise SystemExit("Нет %s — сначала прогони qa_react_final.py" % SRC)
data = json.load(open(SRC, encoding="utf-8"))
recs = data["records"]
ok = [x for x in recs if x["verdict"] == "candidate"]
mgr = [x for x in recs if x["verdict"] == "manager_action" or x.get("needs_manager_action")]

pick = []
for reason in ("price_requested", "estimate_sent", "no_reply"):
    sel = sorted([x for x in ok if x.get("reason") == reason],
                 key=lambda z: (-(z.get("score") or 0), z["age"]))[:PER_REASON]
    pick += sel
    print("== %s: беру %d" % (reason, len(sel)))

db = SessionLocal()
before = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).fetchone()
out, clean = [], 0
for i, x in enumerate(pick, 1):
    res = generate(db, x["account_id"], x["chat"], x["title"],
                   reason=x.get("reason"), goal=x.get("goal"), summary=x.get("summary"))
    print("\n" + "=" * 76)
    print("%d. %s | %s | %s дн | причина %s | цель %s"
          % (i, x["account_id"][:26], str(x["title"] or "-")[:34], x["age"],
             x.get("reason"), x.get("goal")))
    print("   ИСТОРИЯ: %s" % str(x.get("summary"))[:150])
    if not res["ok"]:
        print("   ОШИБКА: %s | %s" % (res["error"], str(res.get("raw"))[:100]))
        continue
    d = res["draft"]
    clean += 1 if d["ok_to_show"] else 0
    print("   ТЕКСТ: %s" % d.get("message"))
    print("   ФАКТЫ ИЗ ПЕРЕПИСКИ:")
    for f in d.get("facts_used") or []:
        print("      [%s] %s" % (f.get("message_id"), str(f.get("fact"))[:90]))
    if d.get("missing"):
        print("   НЕ ХВАТАЕТ: %s" % "; ".join(str(m) for m in d["missing"])[:150])
    if d.get("must_not_invent"):
        print("   НЕЛЬЗЯ ПРИДУМЫВАТЬ: %s" % "; ".join(str(m) for m in d["must_not_invent"])[:150])
    print("   confidence %s | проверка: %s"
          % (d.get("confidence"), ", ".join(d["problems"]) if d["problems"] else "чисто"))
    if d.get("invented_ids"):
        print("   ⚠ выдуманные id сообщений: %s" % d["invented_ids"])
    out.append({"candidate": x, "draft": d})

after = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).fetchone()
n, rub = after[0] - before[0], float(after[1]) - float(before[1])
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

print("\n" + "=" * 76)
print("ЗАДАЧИ МЕНЕДЖЕРУ («Клиенты ждут ответа»): %d — сообщения НЕ генерируются" % len(mgr))
for i, x in enumerate(sorted(mgr, key=lambda z: z["age"]), 1):
    print("\n   %d. %s | %s | ждёт %s дн"
          % (i, x["account_id"][:26], str(x["title"] or "-")[:36], x["age"]))
    print("      ЧТО ОБЕЩАЛИ И НЕ СДЕЛАЛИ: %s" % str(x.get("summary"))[:150])
    print("      ДЕЙСТВИЕ: %s | доказательства %s" % (x.get("goal"), x.get("evidence")))
    print("      чат %s" % x["chat"])

print("\n=== ЧЕРНОВИКОВ %d, из них без замечаний %d | расход %d вызовов на %.2f ₽"
      % (len(out), clean, n, rub))
print("=== Черновики сохранены в %s. НИЧЕГО НЕ ОТПРАВЛЕНО." % OUT)
db.close()
