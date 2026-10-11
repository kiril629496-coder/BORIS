# -*- coding: utf-8 -*-
import json, sys
from sqlalchemy import text
from app.db.session import SessionLocal

APPLY = "--apply" in sys.argv
ACC = "evgeniy_peregorodki_12447"
KPI = {
    "target_leads_per_day": 3,
    "daily_budget_limit_rub": 2200.0,
    "max_cost_per_lead_rub": 2400.0,
    "lead_temperature": "тёплый",
    "bid_autopilot": False,
}

db = SessionLocal()
cur = db.execute(text("select id, value from storage where account_id=:a and key='kpi_settings'"), {"a": ACC}).fetchone()
print("=== kpi_settings:", "ОБНОВИТ" if cur else "СОЗДАСТ")
print("   ", json.dumps(KPI, ensure_ascii=False))
ap = db.execute(text("select id, value from storage where account_id=:a and key='autopilot_settings'"), {"a": ACC}).fetchone()
print("=== autopilot сейчас:", ap[1] if ap else "нет", "-> станет always_ask")
if APPLY:
    v = json.dumps(KPI, ensure_ascii=False)
    if cur:
        db.execute(text("update storage set value=:v where id=:i"), {"v": v, "i": cur[0]})
    else:
        db.execute(text("insert into storage (account_id, key, value) values (:a,'kpi_settings',:v)"), {"a": ACC, "v": v})
    av = json.dumps({"mode": "always_ask"}, ensure_ascii=False)
    if ap:
        db.execute(text("update storage set value=:v where id=:i"), {"v": av, "i": ap[0]})
    else:
        db.execute(text("insert into storage (account_id, key, value) values (:a,'autopilot_settings',:v)"), {"a": ACC, "v": av})
    db.commit(); print("=== ЗАПИСАНО")
else:
    print("=== ПРОСМОТР (dry-run), для записи добавьте --apply")
db.close()
