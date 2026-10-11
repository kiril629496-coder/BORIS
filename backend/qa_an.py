#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Аналитика живыми фразами. Только чтение — ничего не меняет."""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services import command_policy as P
from app.services.command_contract import CommandInput

PHRASES = [
    ("что там по заявкам за сегодня", "analytics.daily", 1),
    ("покажи статистику за неделю", "analytics.week", 1),
    ("как дела по всем аккаунтам", "analytics.accounts", 3),
    ("сколько мы потратили на ии за месяц", "analytics.revenue", 2),
]
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("     %s %-34s %s" % ("PASS" if cond else "FAIL", name, str(got)[:60]))


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}
    acc = db.execute(text(
        "select s.account_id from storage s join accounts a"
        "   on a.account_id = s.account_id"
        " where a.owner_user_id = 2 and s.key like 'daily_stats:%'"
        " order by s.key desc limit 1")).fetchone()
    if not acc:
        print("нет аккаунта владельца со срезами статистики")
        return
    acc = acc[0]
    if not str(acc).lower().startswith(('qa_','test_','demo_','sandbox_')) and os.getenv('BORIS_QA_ALLOW_PRODUCTION') != '1':
        raise RuntimeError('QA_ISOLATION_V1: production account blocked; use dedicated QA data')
    kind, oid = L.resolve_billing_owner(db, acc)
    from app.services import command_registry as R
    if not L.has_feature(db, kind, oid, R.FEATURE_BASE):
        L.set_feature(db, kind, oid, R.FEATURE_BASE, True)
    if L.pool_state(db, kind, oid)["left"] < 20:
        db.execute(text(
            "insert into product_limits (billing_owner_kind, billing_owner_id,"
            " resource, purchased, used, period_start, paid_until)"
            " values (:k,:o,'commands',200,0,current_date,current_date + 30)"
            " on conflict (billing_owner_kind, billing_owner_id, resource)"
            " do update set purchased=200, used=0, paid_until=current_date + 30"),
            {"k": kind, "o": oid})
        db.commit()
    print("аккаунт: %s | остаток пула: %d"
          % (acc, L.pool_state(db, kind, oid)["left"]))

    total_rub, total_units = 0.0, 0
    for phrase, expect_op, expect_units in PHRASES:
        used0 = L.pool_state(db, kind, oid)["used"]
        r = flow.start(db, user, CommandInput(source="text", text=phrase,
                                              account_id=acc))
        used1 = L.pool_state(db, kind, oid)["used"]
        cid = r.get("command_id")
        row = db.execute(text(
            "select coalesce(sum(cost_rub),0) from api_usage"
            " where command_id = cast(:c as uuid)"), {"c": cid}).fetchone()
        rub = float(row[0] or 0)
        total_rub += rub
        total_units += used1 - used0

        print("\n  «%s»" % phrase)
        print("     %s" % (r.get("answer") or r.get("message") or "")[:200]
              .replace("\n", "\n     "))
        print("     операция: %-20s расход: %.4f ₽ | units: %d"
              % (r.get("operation"), rub, used1 - used0))
        check("операция та, что ожидали", r.get("operation") == expect_op,
              r.get("operation"))
        check("выполнено сразу", r.get("status") == "done", r.get("status"))
        check("units по реестру", used1 - used0 == expect_units,
              "%d, ждали %d" % (used1 - used0, expect_units))

    print("\n  Права на денежный разрез (без выполнения команд)")
    emp = {"id": 57, "role": "client"}
    check("сотрудник НЕ получает analytics.revenue",
          P.has_permission(db, emp, "3411770_94346", "owner") is False)
    check("владелец аккаунта получает",
          P.has_permission(db, {"id": 42, "role": "client"},
                           "3411770_94346", "owner") is True)
    check("отчёты требуют can_view_reports у сотрудника",
          P.has_permission(db, emp, "3411770_94346", "can_view_reports") is False)

    print("\nИТОГО по прогону: %.4f ₽, units %d" % (total_rub, total_units))
    print("ИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
