# -*- coding: utf-8 -*-
import json, sys
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "garik_plitka_mo_58647"
db = SessionLocal()
row = db.execute(text("select id, value from storage where account_id=:a and key='kpi_settings'"), {"a": ACC}).fetchone()
k = json.loads(row[1])
print("=== БЫЛО:", json.dumps(k, ensure_ascii=False))
old_budget = k.get("daily_budget_limit_rub")
k["daily_budget_limit_rub"] = 5000.0
need = round(k["daily_budget_limit_rub"] / k["target_leads_per_day"], 2)
print("=== СТАНЕТ:", json.dumps(k, ensure_ascii=False))
print("=== ПРОВЕРКА ДОСТИЖИМОСТИ")
print("   бюджет %s -> %s ₽/сутки" % (old_budget, k["daily_budget_limit_rub"]))
print("   цель %s лидов => нужна цена %s ₽, разрешено %s ₽ -> %s"
      % (k["target_leads_per_day"], need, k["max_cost_per_lead_rub"],
         "ДОСТИЖИМО" if need <= k["max_cost_per_lead_rub"] else "НЕДОСТИЖИМО"))
print("   при максимальной цене %s ₽ бюджета хватит на %.0f лидов"
      % (k["max_cost_per_lead_rub"], k["daily_budget_limit_rub"] / k["max_cost_per_lead_rub"]))
if APPLY:
    db.execute(text("update storage set value=:v where id=:i"),
               {"v": json.dumps(k, ensure_ascii=False), "i": row[0]})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
