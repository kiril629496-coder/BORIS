#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс после унификации платформенного владельца.

Роли берутся из БД. require_owner-ручки проверяются через публичный домен
живыми токенами — отсутствие UI-элемента защитой не считается.
"""
import sys

import requests
from sqlalchemy import text

sys.path.insert(0, ".")
from app.api.auth import create_access_token
from app.db.session import SessionLocal
from app.services import command_policy as P
from app.services import command_registry as R
from app.services import guard_reasons as G
from app.services.platform_roles import is_platform_owner

BASE = "https://boris-ai.pro"
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-46s %s" % ("PASS" if cond else "FAIL", name, str(got)[:48]))


def main():
    db = SessionLocal()
    people = []
    for uid, label, acc in ((2, "платформенный владелец", "otdushi"),
                            (42, "клиент-владелец", "3411770_94346"),
                            (57, "сотрудница", "3411770_94346")):
        role = db.execute(text("select role from users where id=:i"),
                          {"i": uid}).fetchone()[0]
        people.append((uid, label, acc, role))

    print("1. ЕДИНАЯ ФУНКЦИЯ ПЛАТФОРМЕННОГО ВЛАДЕЛЬЦА")
    for uid, label, acc, role in people:
        u = {"id": uid, "role": role}
        got = is_platform_owner(u)
        check("%-24s роль %s -> %s" % (label, role, got), got == (role == "owner"))

    print("\n2. ВНУТРЕННЯЯ ЭКОНОМИКА ЧЕРЕЗ COMMAND CENTER")
    rev = R.get("analytics.revenue")
    for uid, label, acc, role in people:
        u = {"id": uid, "role": role}
        got = P.has_permission(db, u, acc, rev.permission)
        check("%-24s -> %s" % (label, got), got == (role == "owner"))

    print("\n3. КЛИЕНТСКИЕ ПРАВА НЕ ЗАТРОНУТЫ")
    for uid, label, acc, role in people:
        u = {"id": uid, "role": role}
        check("%s: knowledge owner" % label,
              P.has_permission(db, u, acc, "owner") == (uid in (2, 42)))
        check("%s: analytics can_view" % label,
              P.has_permission(db, u, acc, "can_view") is True)

    print("\n4. require_owner-РУЧКИ ЧЕРЕЗ ПУБЛИЧНЫЙ ДОМЕН")
    for uid, label, _, role in people:
        h = {"Authorization": "Bearer " + create_access_token(uid)}
        r = requests.get(BASE + "/api/avito/director_stats", params={"days": 7},
                         headers=h, timeout=30)
        want = 200 if role == "owner" else 403
        check("director/stats: %-22s %s" % (label, r.status_code),
              r.status_code == want, "ожидали %s" % want)

    print("\n5. GUARD REASONS")
    for code in ("reserve_spent", "usage_not_ready", "budget_exhausted", "stopped"):
        msg = G.user_reason(code)
        check("известный %-18s -> %s" % (code, msg), G.is_known(code) and msg != G.FALLBACK)
    for code in ("совершенно_новый_код", None, "", "DROP TABLE"):
        msg = G.user_reason(code)
        check("неизвестный %-14s -> fallback" % str(code)[:14], msg == G.FALLBACK, msg)

    dto = G.safe_dto({"status": "blocked", "reason": "reserve_spent",
                      "reserved_kopeks": 500, "committed_kopeks": 120,
                      "run_id": "r-1", "request_id": "q-1", "calls_used": 3},
                     keep=("calls_used",))
    print("   DTO наружу: %s" % dto)
    check("внутренних полей в DTO нет",
          not ({"reserved_kopeks", "committed_kopeks", "run_id",
                "request_id"} & set(dto)), list(dto))
    check("технический код наружу не ушёл", "reserve_spent" not in str(dto), dto)
    check("разрешённое поле сохранено", dto.get("calls_used") == 3, dto)

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
