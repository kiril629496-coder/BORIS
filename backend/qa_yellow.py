#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Полный цикл yellow-команды: parse -> preview -> confirm -> charge."""
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services import command_registry as R
from app.services.command_contract import CommandInput

PHRASE = "поставь мне завтра перезвонить Иванову"
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-38s %s" % ("PASS" if cond else "FAIL", name, str(got)[:70]))


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}
    acc = db.execute(text("select account_id from accounts where owner_user_id=2"
                          " order by account_id limit 1")).fetchone()[0]
    kind, oid = L.resolve_billing_owner(db, acc)
    if not L.has_feature(db, kind, oid, R.FEATURE_BASE):
        L.set_feature(db, kind, oid, R.FEATURE_BASE, True)

    max_before = db.execute(text(
        "select coalesce(max(id),0) from crm_tasks where account_id=:a"),
        {"a": acc}).fetchone()[0]
    used0 = L.pool_state(db, kind, oid)["used"]

    print("фраза: «%s»" % PHRASE)
    r = flow.start(db, user, CommandInput(source="text", text=PHRASE,
                                          account_id=acc))
    cid = r.get("command_id")
    print("  операция: %s | риск: %s | units: %s"
          % (r.get("operation"), r.get("risk_level"), r.get("units")))
    print("  предпросмотр: %s" % (r.get("human_summary") or "")[:90])
    ch = r.get("proposed_changes") or []
    used1 = L.pool_state(db, kind, oid)["used"]

    check("операция crm.task_create", r.get("operation") == "crm.task_create",
          r.get("operation"))
    check("ждёт подтверждения", r.get("status") == "awaiting_confirm",
          r.get("status"))
    check("предпросмотр не пустой", bool(ch), len(ch))
    check("дата нормализована в preview",
          "не распознана" not in (r.get("human_summary") or ""),
          r.get("human_summary"))
    check("до подтверждения units не списаны", used1 == used0,
          "%d -> %d" % (used0, used1))
    check("задача ещё НЕ создана",
          db.execute(text("select coalesce(max(id),0) from crm_tasks"
                          " where account_id=:a"), {"a": acc}).fetchone()[0]
          == max_before)

    c = flow.confirm(db, user, cid)
    used2 = L.pool_state(db, kind, oid)["used"]
    row = db.execute(text(
        "select id, title, due_date, due_time, task_type from crm_tasks"
        " where account_id=:a and id > :m order by id desc limit 1"),
        {"a": acc, "m": max_before}).fetchone()

    print("\n  подтверждение: %s | %s" % (c.get("status"),
                                          (c.get("answer") or "")[:70]))
    check("выполнено", c.get("status") == "done", c.get("status"))
    check("задача реально в базе", row is not None,
          ("id=%s %s %s %s" % (row[0], row[1], row[2], row[3])) if row else "нет")
    check("списан ровно 1 unit", used2 - used1 == 1, "%d -> %d" % (used1, used2))

    c2 = flow.confirm(db, user, cid)
    used3 = L.pool_state(db, kind, oid)["used"]
    check("повтор не выполняет заново", c2.get("error_code") in
          ("already_charged", "already_final"), c2.get("error_code"))
    check("повтор не списал ещё раз", used3 == used2, "%d -> %d" % (used2, used3))

    if row:
        db.execute(text("delete from crm_tasks where id=:i"), {"i": row[0]})
        db.commit()
        print("\n  уборка: тестовая задача id=%s удалена" % row[0])

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
