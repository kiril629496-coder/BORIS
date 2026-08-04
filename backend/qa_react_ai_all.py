# -*- coding: utf-8 -*-
"""Сухой прогон AI-анализа по ВСЕМ кандидатам. В таблицы реактивации не пишет.
Побочный эффект — строки расхода в api_usage. Прерывание безопасно."""
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter
from app.reactivation_profile import apply_profile
from app.reactivation_ai import analyze, OPERATION

EXCLUDE = {"andrey_mebel_launzh_moskva_69737"}
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = без ограничения


def score(row, d):
    """Приоритет по ПРАВИЛАМ. Числа модели для ранжирования не используем:
    на обкатке purchase_probability была 0.50 у восьми кандидатов из десяти."""
    s = 0
    if d["reason"] == "price_requested" or "price_requested" in row["matched_reasons"]:
        s += 30
    if row["msg_count"] >= 6:
        s += 20
    elif row["msg_count"] >= 3:
        s += 10
    age = (datetime.now(timezone.utc) - row["last_activity"]).days
    if age <= 14:
        s += 25
    elif age <= 45:
        s += 12
    if d.get("phone_received"):
        s += 10
    if row.get("needs_review") or row.get("profile") == "foreign":
        s -= 20
    return max(0, min(100, s)), age


db = SessionLocal()
before = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).fetchone()
accounts = [r[0] for r in db.execute(text(
    "SELECT DISTINCT account_id FROM messenger_messages ORDER BY account_id"))]

import json as _json
verdicts, reasons, promah, best, done, errors = {}, {}, {}, [], 0, []
whys, examples, dump = {}, {}, []
for acc in accounts:
    if acc in EXCLUDE:
        continue
    rows = prefilter(db, acc)
    apply_profile(db, acc, rows)
    if LIMIT:
        rows = rows[:LIMIT]
    print("== %s: %d кандидатов" % (acc, len(rows)), flush=True)
    for i, r in enumerate(rows, 1):
        res = analyze(db, acc, r["avito_chat_id"], r["item_title"])
        done += 1
        if not res["ok"]:
            errors.append((acc, r["avito_chat_id"], res["error"]))
            continue
        d = res["data"]
        verdicts[res["verdict"]] = verdicts.get(res["verdict"], 0) + 1
        whys[res["why"]] = whys.get(res["why"], 0) + 1
        examples.setdefault(res["verdict"], []).append(
            (acc, r["item_title"] or "-", d["summary"] or "", res["why"]))
        dump.append({"account_id": acc, "chat": r["avito_chat_id"], "verdict": res["verdict"],
                     "why": res["why"], "reason": d["reason"], "conf": d["confidence"],
                     "phone": d.get("phone_received"), "bought": d.get("purchase_confirmed"),
                     "no_contact": d.get("asked_not_to_contact"),
                     "title": r["item_title"], "summary": d["summary"]})
        reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        if d.get("reason_raw"):
            promah[d["reason_raw"]] = promah.get(d["reason_raw"], 0) + 1
        if res["verdict"] == "candidate":
            sc, age = score(r, d)
            best.append((sc, age, acc, r["item_title"] or "-", d["summary"] or "",
                         r["avito_chat_id"]))
        if i % 10 == 0:
            print("   ...обработано %d из %d" % (i, len(rows)), flush=True)

after = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).fetchone()
n, rub = after[0] - before[0], float(after[1]) - float(before[1])

print("\n=== ВЕРДИКТЫ (всего %d)" % done)
for k, v in sorted(verdicts.items(), key=lambda x: -x[1]):
    print("   %-14s %d" % (k, v))
print("=== ПРИЧИНЫ")
for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
    print("   %-24s %d" % (k, v))
if promah:
    print("=== МОДЕЛЬ ПРОМАХНУЛАСЬ ПОЛЕМ reason (заменено на other)")
    for k, v in sorted(promah.items(), key=lambda x: -x[1]):
        print("   %-24s %d" % (k[:24], v))
if errors:
    print("=== ОШИБКИ РАЗБОРА: %d" % len(errors))
    for acc, chat, err in errors[:5]:
        print("   %s | %s | %s" % (acc[:24], chat[:22], err[:50]))

print("=== ПОЧЕМУ ИМЕННО ТАКОЙ ВЕРДИКТ")
for k, v in sorted(whys.items(), key=lambda x: -x[1]):
    print("   %-42s %d" % (k[:42], v))
for verdict in ("not_eligible", "needs_review", "do_not_contact"):
    ex = examples.get(verdict, [])[:5]
    if ex:
        print("=== ПРИМЕРЫ %s" % verdict)
        for acc, title, summ, why in ex:
            print("   %-26s | %-32s | %s" % (acc[:26], title[:32], why[:34]))
            print("        %s" % summ[:100])
print("\n=== ТОП-10 ПО ПРАВИЛАМ (кому писать в первую очередь)")
for sc, age, acc, title, summ, chat in sorted(best, reverse=True)[:10]:
    print("   %3d | %3d дн | %-28s | %s" % (sc, age, acc[:28], title[:36]))
    print("        %s" % summ[:100])

open("/tmp/react_all.json", "w", encoding="utf-8").write(
    _json.dumps(dump, ensure_ascii=False, indent=1))
print("\n=== РАСХОД: %d вызовов на %.2f ₽ (в среднем %.4f ₽)" % (n, rub, (rub / n if n else 0)))
print("=== Полные результаты: /tmp/react_all.json (%d записей)" % len(dump))
db.close()
