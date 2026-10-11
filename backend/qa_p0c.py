# -*- coding: utf-8 -*-
"""Dry-run P0-C по аккаунтам с bid_autopilot=true. Только чтение, денег не тратит."""
import json
from sqlalchemy import text
from app.db.session import SessionLocal
import app.api.cpx_advisor as C

db = SessionLocal()
rows = db.execute(text(
    "select account_id, (value::jsonb->>'bid_autopilot') b,"
    " (value::jsonb->>'daily_budget_limit_rub') lim"
    " from storage where key='kpi_settings' order by account_id")).fetchall()
db.close()

auto = [(a, l) for a, b, l in rows if str(b).lower() == "true"]
print("аккаунтов с bid_autopilot=true: %d" % len(auto))
print("=" * 96)
print("%-46s %-10s %-30s %s" % ("account_id", "бюджет", "reason_code", "баланс"))
print("-" * 96)
res = {}
for acc, lim in auto:
    v = C.check_raise_allowed(acc)
    b = v["balance"]
    bal_s = ("real=%.2f bonus=%s" % (b["value"], b.get("bonus"))
             if b["status"] == "KNOWN" else b["status"])
    code = v["reason_code"] or "ALLOWED"
    res[acc] = code
    print("%-46s %-10s %-30s %s" % (acc[:44], lim, code, bal_s))
    print("      %s" % v["detail"])

print("=" * 96)
exp = {
    "garik_plitka_mo_58647": "blocked_balance_no_uid",
    "pbi_artashes_plitka_mo_83993": "blocked_balance_no_uid",
    "dinara_spb_vodnaya_ekipirovka_18294": "blocked_balance_no_uid",
    "georgiy_mo_elektrosamokaty_88067": "blocked_balance_no_uid",
    "otdushi": "blocked_insufficient_balance",
}
ok = 0
for acc, want in exp.items():
    got = res.get(acc, "(не проверен)")
    good = got == want
    ok += good
    print("%s %-46s ждали %-30s получили %s" % (
        "OK  " if good else "FAIL", acc[:44], want, got))
print()
print("ИТОГО: %d/%d совпало" % (ok, len(exp)))
print("РАЗРЕШЕНО auto-raise:", [a for a, c in res.items() if c == "ALLOWED"] or "ни одного")
