#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка боевых операций этапа 2 ЖИВЫМИ фразами.

KPI этапа: пользователь не выбирает модуль руками. Поэтому фразы здесь
человеческие («открой сообщения»), а не искусственные («navigation»).
"""
import datetime
import os
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services import command_registry as R
from app.services.command_contract import CommandInput

PHRASES = [
    ("открой сообщения", "ui.navigate", 0),
    ("перейди в соцсети", "ui.navigate", 0),
    ("привет, ты тут?", "chat.smalltalk", 0),
    ("найди клиента Иванова", "crm.leads_search", 1),
    ("покажи последние обращения", "crm.leads_search", 1),
]

OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("     %s %-34s %s" % ("PASS" if cond else "FAIL", name, str(got)[:70]))


def ensure(db, kind, oid):
    if not L.has_feature(db, kind, oid, R.FEATURE_BASE):
        L.set_feature(db, kind, oid, R.FEATURE_BASE, True)
    st = L.pool_state(db, kind, oid)
    if not st["exists"] or st["left"] < 5:
        db.execute(text(
            "insert into product_limits (billing_owner_kind, billing_owner_id,"
            " resource, purchased, used, period_start, paid_until)"
            " values (:k,:o,'commands',100,0,current_date,current_date + 30)"
            " on conflict (billing_owner_kind, billing_owner_id, resource)"
            " do update set purchased=100, used=0, paid_until=current_date + 30"),
            {"k": kind, "o": oid})
        db.commit()


def cost_of(db, cid):
    row = db.execute(text(
        "select coalesce(sum(cost_rub),0), count(*) from api_usage"
        " where command_id = cast(:c as uuid)"), {"c": cid}).fetchone()
    return float(row[0] or 0), int(row[1] or 0)


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}
    row = db.execute(text("select account_id from accounts where owner_user_id=2"
                          " order by account_id limit 1")).fetchone()
    acc = row[0]
    if not str(acc).lower().startswith(('qa_','test_','demo_','sandbox_')) and os.getenv('BORIS_QA_ALLOW_PRODUCTION') != '1':
        raise RuntimeError('QA_ISOLATION_V1: production account blocked; use dedicated QA data')
    kind, oid = L.resolve_billing_owner(db, acc)
    ensure(db, kind, oid)
    print("аккаунт: %s | владелец пула: %s %s" % (acc, kind, oid))

    total_rub, total_units = 0.0, 0
    for phrase, expect_op, expect_units in PHRASES:
        used_before = L.pool_state(db, kind, oid)["used"]
        r = flow.start(db, user, CommandInput(source="text", text=phrase,
                                              account_id=acc))
        used_after = L.pool_state(db, kind, oid)["used"]
        cid = r.get("command_id")
        rub, calls = cost_of(db, cid)
        op = R.get(r.get("operation") or "")
        spent = used_after - used_before
        total_rub += rub
        total_units += spent

        print("\n  «%s»" % phrase)
        print("     операция: %-18s риск: %-7s права: %s"
              % (r.get("operation"), r.get("risk_level") or (op.risk if op else "-"),
                 (op.permission if op else "-")))
        print("     статус: %-16s ответ: %s"
              % (r.get("status"), (r.get("answer") or r.get("message") or "")[:60]))
        print("     расход: %.4f ₽ (%d вызов) | units списано: %d"
              % (rub, calls, spent))

        check("операция та, что ожидали", r.get("operation") == expect_op,
              r.get("operation"))
        check("выполнено сразу (green)", r.get("status") == "done", r.get("status"))
        check("units списаны как в реестре", spent == expect_units,
              "%d, ждали %d" % (spent, expect_units))
        check("расход записан с command_id", calls >= 1, calls)

    print("\nИТОГО по прогону: %.4f ₽, units списано %d" % (total_rub, total_units))
    print("ИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
