# -*- coding: utf-8 -*-
"""Клиенты с ключами Avito, но вне автоматического контура. Только чтение."""
import json
from sqlalchemy import text
from app.db.session import SessionLocal

db = SessionLocal()
accs = db.execute(text(
    "select account_id, name, owner_user_id, (avito_client_id is not null) as keys, "
    "(avito_user_id is not null) as uid, billing_mode from accounts order by account_id")).fetchall()
kpi = {r[0] for r in db.execute(text("select account_id from storage where key='kpi_settings'"))}
stats = {r[0] for r in db.execute(text(
    "select distinct account_id from storage where key like 'daily_stats:2026-08-1%'"))}
tier = {r[0]: (json.loads(r[1]).get("tier") if r[1] else None)
        for r in db.execute(text("select account_id, value from storage where key='billing'"))}

print("%-46s %-6s %-6s %-6s %-8s %s" % ("аккаунт", "ключи", "uid", "KPI", "тариф", "проблема"))
bad = 0
for a, name, owner, keys, uid, bm in accs:
    if not keys:
        continue
    probs = []
    if a not in kpi:      probs.append("нет KPI")
    if owner is None:     probs.append("нет владельца")
    if not uid:           probs.append("нет avito_user_id")
    if (tier.get(a) or "none") == "none": probs.append("тариф none")
    if a not in stats:    probs.append("нет свежей статистики")
    if probs:
        bad += 1
        print("%-46s %-6s %-6s %-6s %-8s %s" % (a[:46], "да", "да" if uid else "НЕТ",
              "да" if a in kpi else "НЕТ", tier.get(a) or "none", ", ".join(probs)))
print("=== ВНЕ КОНТУРА: %d из %d аккаунтов с ключами" % (bad, sum(1 for x in accs if x[3])))
db.close()
