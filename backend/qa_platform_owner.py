# -*- coding: utf-8 -*-
"""Регресс-тесты права platform_owner. Ничего не меняет, только проверяет."""
import sys, os
sys.path.insert(0, "/root/BORIS/backend")
for _l in open("/root/BORIS/backend/.env", encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import command_policy as P
from app.services import command_registry as R

ACC = "prodazha_bytovok_25677"
ok = fail = 0


def check(name, got, want):
    global ok, fail
    good = got == want
    print("  %s %-58s получено=%s ожидалось=%s" % ("OK  " if good else "ПРОВАЛ", name, got, want))
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)


db = SessionLocal()
U = {}
for uid in (2, 7, 42, 56, 57):
    U[uid] = db.execute(text("select id, email, role from users where id=:i"), {"i": uid}).fetchone()

print("=== ПРАВО platform_owner ===")
check("1. системный владелец BORIS (id 2) — доступ", P.has_permission(db, U[2], ACC, "platform_owner"), True)
check("2. второй системный (id 7, qa_test) — доступ", P.has_permission(db, U[7], ACC, "platform_owner"), True)
check("3. клиентский owner Сергей (42) — ОТКАЗ", P.has_permission(db, U[42], ACC, "platform_owner"), False)
check("4. сотрудник Валерия (56) — ОТКАЗ", P.has_permission(db, U[56], ACC, "platform_owner"), False)
check("5. сотрудник Юлия (57) — ОТКАЗ", P.has_permission(db, U[57], ACC, "platform_owner"), False)

n_uaa = db.execute(text("select count(*) from user_account_access where user_id=42 and role='owner'"), {}).scalar()
n_acc = db.execute(text("select count(*) from accounts where owner_user_id=42"), {}).scalar()
check("6. role='owner' в связях (%s шт) не даёт доступ" % n_uaa, P.has_permission(db, U[42], ACC, "platform_owner"), False)
check("7. владение аккаунтами (%s шт) не даёт доступ" % n_acc, P.has_permission(db, U[42], ACC, "platform_owner"), False)

print("=== ИСПОЛНИТЕЛЬ НАПРЯМУЮ ===")
from app.services.command_ops import op_analytics_revenue
r = op_analytics_revenue(db, U[42], {"account_id": ACC})
check("8. прямой вызов клиентским owner — отказ до SQL", r.get("error_code"), "forbidden")
r2 = op_analytics_revenue(db, U[2], {"account_id": ACC})
check("8b. прямой вызов системным — работает", r2.get("status"), "ok")

print("=== РЕЕСТР И АЛИАСЫ ===")
perm = R.snapshot("analytics.revenue").get("permission")
check("9. analytics.revenue требует platform_owner", perm, "platform_owner")
al = {}
for _n in dir(R):
    _v = getattr(R, _n)
    if isinstance(_v, dict) and "manager" in _v:
        al = _v
        break
check("10. алиас money не ведёт на себестоимость", al.get("money"), None)
check("11. алиас revenue не ведёт на себестоимость", al.get("revenue"), None)

print("=== КЛИЕНТСКИЕ ПРАВА НЕ СЛОМАНЫ ===")
check("12. Сергей сохраняет своё право owner", P.has_permission(db, U[42], ACC, "owner"), True)
check("12b. Юлия сохраняет can_view", P.has_permission(db, U[57], ACC, "can_view"), True)

db.close()
print()
print("ИТОГО: OK %s | ПРОВАЛОВ %s" % (ok, fail))
sys.exit(1 if fail else 0)
