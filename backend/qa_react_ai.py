# -*- coding: utf-8 -*-
"""Обкатка AI-анализа на 10 кандидатах (по 2 с аккаунта). В базу реактивации
НЕ пишет. Единственный побочный эффект — строки расхода в api_usage."""
from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter
from app.reactivation_profile import apply_profile
from app.reactivation_ai import analyze, OPERATION

ACCS = ["andrey_launzh_mebel_akkaunt_penza_82435", "3411770_94346",
        "prodazha_bytovok_25677", "evz_denis_aleksandra_evakuatory_i_pricepy_22264", "otdushi"]
PER_ACC = 2

db = SessionLocal()
before = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                         " WHERE operation=:o"), {"o": OPERATION}).fetchone()
stats = {}
for acc in ACCS:
    rows = prefilter(db, acc)
    apply_profile(db, acc, rows)
    pick = [r for r in rows if r["primary_reason"] == "price_requested"][:PER_ACC]
    pick += [r for r in rows if r not in pick][:max(0, PER_ACC - len(pick))]
    print("\n=== %s" % acc)
    for r in pick:
        res = analyze(db, acc, r["avito_chat_id"], r["item_title"])
        if not res["ok"]:
            print("   ОШИБКА: %s | %s" % (res["error"], (res.get("raw") or "")[:80]))
            stats["error"] = stats.get("error", 0) + 1
            continue
        d = res["data"]
        stats[res["verdict"]] = stats.get(res["verdict"], 0) + 1
        print("   %-13s conf %.2f | %-5s | вероятность %.2f | профиль %s"
              % (res["verdict"], d["confidence"], d["lead_temperature"],
                 d["purchase_probability"], r.get("profile", "-")))
        print("      объявление: %s" % (r["item_title"] or "-")[:60])
        print("      резюме:     %s" % (d["summary"] or "")[:90])
        print("      причина %s | цель %s | доказательства %s%s"
              % (d["reason"], d["recommended_message_goal"], d["evidence_message_ids"],
                 (" ВЫДУМАННЫЕ ID: %s" % d["invented_ids"]) if d["invented_ids"] else ""))

after = db.execute(text("SELECT count(*), coalesce(sum(cost_rub),0) FROM api_usage"
                        " WHERE operation=:o"), {"o": OPERATION}).fetchone()
n, rub = after[0] - before[0], float(after[1]) - float(before[1])
print("\n=== ВЕРДИКТЫ: %s" % ", ".join("%s %d" % (k, v) for k, v in sorted(stats.items())))
print("=== РАСХОД: %d вызовов на %.2f ₽ (в среднем %.3f ₽), прогноз на 139 кандидатов: %.0f ₽"
      % (n, rub, (rub / n if n else 0), (rub / n * 139 if n else 0)))
for r in db.execute(text("SELECT provider, model, count(*), round(sum(cost_rub),3)"
                         " FROM api_usage WHERE operation=:o GROUP BY 1,2"), {"o": OPERATION}):
    print("   ", " | ".join(str(x) for x in r))
db.close()
