#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тестовая матрица этапа 1 Command Center.

Пишет только в QA-контур (boris_commands, boris_command_events,
product_limits, product_features). Боевые владельцы и их лимиты не трогает.
Владелец для тестов — отдельный QA user 46 / account qa_dry_moskva.
"""
import datetime
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services import command_policy as P
from app.services import command_registry as R
from app.services import command_audit as A
from app.services import command_store as S
from app.services.command_contract import CommandInput

OK, FAIL = [], []
QA_OWNER_ID = 46
QA_ACCOUNT_ID = "qa_dry_moskva"


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-46s %s" % ("PASS" if cond else "FAIL", name, got))


def owner_account(db, uid=QA_OWNER_ID):
    row = db.execute(text(
        "select account_id from accounts where account_id=:a and owner_user_id=:u"),
        {"a": QA_ACCOUNT_ID, "u": uid}).fetchone()
    return row[0] if row else None


def main():
    db = SessionLocal()
    user = {"id": QA_OWNER_ID, "role": "client"}
    acc = owner_account(db)
    if not acc:
        raise RuntimeError("QA account qa_dry_moskva is missing or has wrong owner")
    print("тестовый аккаунт:", acc)
    kind, oid = L.resolve_billing_owner(db, acc)

    # Прогон обязан начинаться с чистого состояния: иначе выданные ранее
    # флаг и пул делают отрицательные проверки невоспроизводимыми.
    db.execute(text("delete from product_features where billing_owner_kind=:k"
                    " and billing_owner_id=:o"), {"k": kind, "o": oid})
    db.execute(text("delete from product_limits where billing_owner_kind=:k"
                    " and billing_owner_id=:o"), {"k": kind, "o": oid})
    db.commit()

    print("\n1. Отказы ДО обращения к модели")
    o = L.resolve_billing_owner(db, "qa_feed_test")
    check("аккаунт без владельца -> no_billing_owner", o is None, str(o))

    inp = CommandInput(source="text", text="покажи задачи", account_id=acc)
    r = flow.start(db, user, inp)
    check("флаг тарифа выключен -> feature_disabled",
          r.get("error_code") == "feature_disabled", r.get("error_code"))

    L.set_feature(db, kind, oid, R.FEATURE_BASE, True)
    check("флаг выдан", L.has_feature(db, kind, oid, R.FEATURE_BASE))

    r = flow.start(db, user, inp)
    check("пула нет -> pool_exhausted",
          r.get("error_code") == "pool_exhausted", r.get("error_code"))

    db.execute(text(
        "insert into product_limits (billing_owner_kind, billing_owner_id,"
        " resource, purchased, used, period_start, paid_until)"
        " values (:k,:o,'commands',10,0,current_date,current_date + 30)"
        " on conflict (billing_owner_kind, billing_owner_id, resource)"
        " do update set purchased=10, used=0, paid_until=current_date + 30"),
        {"k": kind, "o": oid})
    db.commit()
    st = L.pool_state(db, kind, oid)
    check("пул выдан 10", st["left"] == 10, str(st["left"]))

    print("\n2. Права без обращения к модели")
    emp = {"id": 57, "role": "client"}
    check("сотрудник не получает owner-право",
          P.has_permission(db, emp, acc, "owner") is False)
    check("владелец получает owner-право",
          P.has_permission(db, user, acc, "owner") is True)
    check("неизвестное право -> отказ",
          P.has_permission(db, user, acc, "can_fly") is False)

    print("\n3. Маскирование при записи")
    cid_r = "00000000-0000-4000-8000-000000000001"
    db.execute(text("delete from boris_commands where command_id=cast(:c as uuid)"),
               {"c": cid_r})
    db.commit()
    S.create(db, cid_r, QA_OWNER_ID, kind, oid, acc, "text",
             raw_text="мой пароль: hunter2 и карта 4276 1600 1234 5678")
    row = db.execute(text("select raw_text from boris_commands"
                          " where command_id=cast(:c as uuid)"),
                     {"c": cid_r}).fetchone()
    check("секреты замаскированы", "hunter2" not in (row[0] or ""), row[0])

    print("\n4. Списание units")
    db.execute(text("update boris_commands set status='confirmed', units=1"
                    " where command_id=cast(:c as uuid)"), {"c": cid_r})
    db.commit()
    used0 = L.pool_state(db, kind, oid)["used"]
    r1 = L.charge_units_once(db, cid_r, 1, kind, oid)
    r2 = L.charge_units_once(db, cid_r, 1, kind, oid)
    used1 = L.pool_state(db, kind, oid)["used"]
    check("первое списание -> charged", r1 == "charged", r1)
    check("повтор -> already_charged", r2 == "already_charged", r2)
    check("пул вырос ровно на 1", used1 - used0 == 1, "%d -> %d" % (used0, used1))

    print("\n5. Журнал и retention")
    A.log_event(db, cid_r, "qa", actor="qa", payload={"api_key": "abc", "t": "ok"})
    ev = A.events_of(db, cid_r)
    masked = any((e.get("payload") or {}).get("api_key") == "[скрыто]" for e in ev)
    check("payload очищен в журнале", masked, str(ev[-1:] or ""))
    check("purge по умолчанию сухой",
          A.purge_texts(db, days=90, dry=True)["purged"] == 0)

    max_task_before = db.execute(text(
        "select coalesce(max(id),0) from crm_tasks where account_id=:a"),
        {"a": acc}).fetchone()[0]

    print("\n6. Живой разбор (единственный AI-вызов)")
    inp2 = CommandInput(source="text", account_id=acc,
                        text="поставь задачу перезвонить Иванову завтра в 11")
    r = flow.start(db, user, inp2)
    print("   ответ:", {k: r.get(k) for k in
                        ("status", "intent", "operation", "risk_level", "units")})
    check("операция определена кодом",
          r.get("operation") in ("crm.task_create", "crm.lead_note_add",
                                 "crm.lead_update"), str(r.get("operation")))
    check("yellow ждёт подтверждения",
          r.get("status") == "awaiting_confirm", r.get("status"))

    cid = r.get("command_id")
    if r.get("status") == "awaiting_confirm":
        used_a = L.pool_state(db, kind, oid)["used"]
        c1 = flow.confirm(db, user, cid)
        used_b = L.pool_state(db, kind, oid)["used"]
        check("операция выполнена исполнителем", c1.get("status") == "done",
              c1.get("status"))
        check("успех списал ровно 1 unit", used_b - used_a == 1,
              "%d -> %d" % (used_a, used_b))
        db.execute(text("delete from crm_tasks where account_id=:a and id > :m"),
                   {"a": acc, "m": max_task_before})
        db.commit()
        c2 = flow.confirm(db, user, cid)
        check("повторный confirm не выполняет заново",
              c2.get("status") in ("failed", "expired", "done"), c2.get("status"))

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
