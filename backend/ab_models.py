# -*- coding: utf-8 -*-
"""A/B сравнение GigaChat и OpenAI на НЕИЗМЕННОЙ выборке.
Базовые результаты (OpenAI) берутся из baseline, новый прогон идёт как есть —
через chat_with_fallback, то есть на GigaChat, если он жив.
Ничего не отправляет и baseline не перезаписывает."""
import json
import os
import sys

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_ai import analyze, OPERATION

SRC = "/root/BORIS/baseline/react_final.json"
OUT = "/root/BORIS/baseline/ab_gigachat.json"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 35

if not os.path.exists(SRC):
    raise SystemExit("Нет baseline %s" % SRC)
base = json.load(open(SRC, encoding="utf-8"))["records"]

# Выборка со всеми типами вердиктов, чтобы сравнивать не только одобренные.
buckets, pick = {}, []
for r in base:
    buckets.setdefault(r["verdict"], []).append(r)
for v, items in buckets.items():
    share = max(2, int(LIMIT * len(items) / max(1, len(base))))
    pick += items[:share]
pick = pick[:LIMIT]
print("выборка %d из %d | по вердиктам: %s"
      % (len(pick), len(base), {k: len([x for x in pick if x["verdict"] == k]) for k in buckets}))

db = SessionLocal()
before = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).fetchone()
FIELDS = ("eligible", "verdict", "reason", "goal", "purchase_confirmed",
          "needs_manager_action", "asked_not_to_contact")
same = {f: 0 for f in FIELDS}
diffs, rows, errors = [], [], 0

for i, r in enumerate(pick, 1):
    res = analyze(db, r["account_id"], r["chat"], r["title"])
    if not res["ok"]:
        errors += 1
        continue
    d = res["data"]
    new = {"verdict": res["verdict"], "reason": d.get("reason"),
           "eligible": bool(d.get("eligible")), "goal": d.get("recommended_message_goal"),
           "purchase_confirmed": bool(d.get("purchase_confirmed")),
           "needs_manager_action": bool(d.get("needs_manager_action")),
           "asked_not_to_contact": bool(d.get("asked_not_to_contact")),
           "conf": d.get("confidence")}
    old = {"verdict": r["verdict"], "reason": r.get("reason"),
           "eligible": bool(r.get("eligible")), "goal": r.get("goal"),
           "purchase_confirmed": bool(r.get("purchase_confirmed")),
           "needs_manager_action": bool(r.get("needs_manager_action")),
           "asked_not_to_contact": bool(r.get("asked_not_to_contact")),
           "conf": r.get("conf")}
    for f in FIELDS:
        same[f] += 1 if old[f] == new[f] else 0
    rows.append({"chat": r["chat"], "account_id": r["account_id"], "title": r["title"],
                 "openai": old, "gigachat": new, "summary_openai": r.get("summary"),
                 "summary_gigachat": d.get("summary")})
    if old["verdict"] != new["verdict"] or old["purchase_confirmed"] != new["purchase_confirmed"]:
        diffs.append(rows[-1])
    if i % 10 == 0:
        print("   ...%d из %d" % (i, len(pick)), flush=True)

after = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).fetchone()
n, rub = after[0] - before[0], float(after[1]) - float(before[1])
json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

total = len(rows)
print("\n=== СОВПАДЕНИЕ ПОЛЕЙ (из %d диалогов)" % total)
for f in FIELDS:
    print("   %-22s %d из %d (%d%%)" % (f, same[f], total,
                                        round(100.0 * same[f] / total) if total else 0))

print("\n=== ВЕРДИКТЫ")
for name, src in (("OpenAI (baseline)", "openai"), ("GigaChat (сейчас)", "gigachat")):
    cnt = {}
    for x in rows:
        cnt[x[src]["verdict"]] = cnt.get(x[src]["verdict"], 0) + 1
    print("   %-20s %s" % (name, cnt))

print("\n=== МАТРИЦА РАСХОЖДЕНИЙ (строки — OpenAI, столбцы — GigaChat)")
verdicts = sorted({x["openai"]["verdict"] for x in rows} | {x["gigachat"]["verdict"] for x in rows})
print("   %-18s %s" % ("", " ".join("%-14s" % v[:14] for v in verdicts)))
for a in verdicts:
    line = []
    for b in verdicts:
        line.append("%-14s" % len([x for x in rows
                                   if x["openai"]["verdict"] == a and x["gigachat"]["verdict"] == b]))
    print("   %-18s %s" % (a[:18], " ".join(line)))

print("\n=== ОПАСНЫЕ ПРИЗНАКИ: сколько раз каждая модель их подняла")
for f in ("purchase_confirmed", "needs_manager_action", "asked_not_to_contact"):
    print("   %-22s OpenAI %d | GigaChat %d | совпало %d"
          % (f, len([x for x in rows if x["openai"][f]]),
             len([x for x in rows if x["gigachat"][f]]),
             len([x for x in rows if x["openai"][f] and x["gigachat"][f]])))
ni_o = len([x for x in rows if x["openai"]["verdict"] == "not_interested_now"])
ni_g = len([x for x in rows if x["gigachat"]["verdict"] == "not_interested_now"])
print("   %-22s OpenAI %d | GigaChat %d" % ("not_interested_now", ni_o, ni_g))
print("\n=== ЛОЖНЫЕ РАЗРЕШЕНИЯ GIGACHAT (OpenAI не разрешил, GigaChat разрешил): %d"
      % len([x for x in rows if x["gigachat"]["verdict"] == "candidate"
             and x["openai"]["verdict"] != "candidate"]))
print("=== ЛОЖНЫЕ ОТКАЗЫ GIGACHAT (OpenAI разрешил, GigaChat нет): %d"
      % len([x for x in rows if x["openai"]["verdict"] == "candidate"
             and x["gigachat"]["verdict"] != "candidate"]))

print("\n=== РАСХОЖДЕНИЯ ПО ВЕРДИКТУ ИЛИ ПОКУПКЕ: %d" % len(diffs))
for x in diffs:
    print("\n   %s | %s" % (x["account_id"][:26], str(x["title"] or "-")[:40]))
    print("      OpenAI:   %-16s reason %-22s покупка %s"
          % (x["openai"]["verdict"], x["openai"]["reason"], x["openai"]["purchase_confirmed"]))
    print("      GigaChat: %-16s reason %-22s покупка %s"
          % (x["gigachat"]["verdict"], x["gigachat"]["reason"], x["gigachat"]["purchase_confirmed"]))
    print("      OpenAI:   %s" % str(x["summary_openai"])[:110])
    print("      GigaChat: %s" % str(x["summary_gigachat"])[:110])
    print("      чат %s" % x["chat"])

print("\n=== ОШИБОК РАЗБОРА: %d | расход %d вызовов на %.2f ₽" % (errors, n, rub))
print("=== Результат: %s | baseline НЕ изменён" % OUT)
