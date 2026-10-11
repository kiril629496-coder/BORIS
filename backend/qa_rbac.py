#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс ролей после разделения внутренней экономики и клиентской."""
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_policy as P
from app.services import command_registry as R

OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-46s %s" % ("PASS" if cond else "FAIL", name, str(got)[:52]))


def main():
    db = SessionLocal()
    people = []
    for uid, label, acc in ((2, "платформенный владелец", "otdushi"),
                            (42, "клиент-владелец", "3411770_94346"),
                            (57, "сотрудница", "3411770_94346")):
        row = db.execute(text("select role, email from users where id=:i"),
                         {"i": uid}).fetchone()
        people.append((uid, label, acc, row[0] if row else "?", row[1] if row else "?"))
        print("  %-24s id=%s роль в БД=%s" % (label, uid, row[0] if row else "?"))

    rev = R.get("analytics.revenue")
    print("\nПРАВО НА ВНУТРЕННЮЮ ЭКОНОМИКУ: %s -> %s" % (rev.id, rev.permission))
    check("analytics.revenue требует platform_owner",
          rev.permission == R.PLATFORM_OWNER, rev.permission)

    print("\n1. ВНУТРЕННЯЯ ЭКОНОМИКА (analytics.revenue)")
    for uid, label, acc, role, _ in people:
        u = {"id": uid, "role": role}
        got = P.has_permission(db, u, acc, rev.permission)
        want = (role == "owner")
        check("%-24s -> %s" % (label, got), got == want, "ожидали %s" % want)

    print("\n2. КЛИЕНТСКАЯ АНАЛИТИКА (daily / week / accounts)")
    for op_id in ("analytics.daily", "analytics.week", "analytics.accounts"):
        op = R.get(op_id)
        for uid, label, acc, role, _ in people:
            u = {"id": uid, "role": role}
            got = P.has_permission(db, u, acc, op.permission)
            check("%s: %s" % (op_id, label), got is True, got)

    print("\n3. КЛИЕНТСКИЕ ОПЕРАЦИИ С ПРАВОМ owner (knowledge.fact_add)")
    ka = R.get("knowledge.fact_add")
    check("право осталось owner", ka.permission == "owner", ka.permission)
    for uid, label, acc, role, _ in people:
        u = {"id": uid, "role": role}
        got = P.has_permission(db, u, acc, ka.permission)
        want = uid in (2, 42)
        check("%-24s -> %s" % (label, got), got == want, "ожидали %s" % want)

    print("\n4. АЛИАСЫ INTENT RESOLVER")
    for scope in ("revenue", "money", "manager", "accounts", ""):
        op = R.resolve_operation("analytics_query", {"scope": scope})
        perm = (R.get(op).permission if R.get(op) else "-")
        print("     scope=%-10s -> %-20s право %s" % (scope or "нет", op, perm))
        if scope in ("revenue", "money"):
            check("алиас %s ведёт в закрытую операцию" % scope,
                  op == "analytics.revenue" and perm == R.PLATFORM_OWNER, perm)

    print("\n5. ПОЛЯ ОТВЕТА КЛИЕНТСКОЙ АНАЛИТИКИ")
    import inspect
    from app.services import command_ops as O
    banned = ("cost_rub", "cost_usd", "margin", "markup", "internal_price",
              "prompt_tokens", "completion_tokens", "rate_source",
              "pricing_version")
    for fn in (O.op_analytics_daily, O.op_analytics_week, O.op_analytics_accounts):
        src = inspect.getsource(fn)
        bad = [b for b in banned if b in src]
        check("%s без внутренней экономики" % fn.__name__, not bad, bad)

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
