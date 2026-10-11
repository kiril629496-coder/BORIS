#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Аудит контура Command Center. ТОЛЬКО ЧТЕНИЕ."""
import sys
from collections import Counter

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_executor as E
from app.services import command_policy as P
from app.services import command_registry as R
from app.services import command_flow  # noqa: F401 — регистрирует исполнителей

NOTES = []


def head(t):
    print("")
    print("=" * 68)
    print(t)
    print("=" * 68)


def one(db, sql, args=None):
    return db.execute(text(sql), args or {}).fetchone()


def many(db, sql, args=None):
    return db.execute(text(sql), args or {}).fetchall()


def main():
    db = SessionLocal()

    head("1. ОПЕРАЦИИ")
    print("  %-22s %-7s %-16s %5s %s" % ("операция", "риск", "право", "units", "исполнитель"))
    no_exec = []
    for op in sorted(R.REGISTRY.values(), key=lambda o: o.id):
        has = "есть" if E.is_implemented(op.id) else "НЕТ"
        prev = " +preview" if op.id in getattr(E, "PREVIEWERS", {}) else ""
        if not E.is_implemented(op.id) and op.intents:
            no_exec.append(op.id)
        print("  %-22s %-7s %-16s %5d %s%s"
              % (op.id, op.risk, op.permission or "-", op.units, has, prev))
    print("  всего операций: %d | с исполнителем: %d | предпросмотров: %d"
          % (len(R.REGISTRY), len(E.EXECUTORS), len(getattr(E, "PREVIEWERS", {}))))
    if no_exec:
        NOTES.append("операции без исполнителя: " + ", ".join(no_exec))

    head("2. ПРАВА")
    perms = Counter(op.permission or "-" for op in R.REGISTRY.values())
    for p, n in perms.most_common():
        print("  %-18s операций: %d" % (p, n))
    print("  белый список колонок: %s" % ", ".join(sorted(P.PERMISSION_COLUMNS)))
    acc = one(db, "select account_id from accounts where owner_user_id=2 limit 1")
    if acc:
        a = acc[0]
        print("  проверка на живых людях (аккаунт %s):" % a)
        for uid, label in ((2, "владелец BORIS"), (57, "сотрудница")):
            u = {"id": uid, "role": "client"}
            print("    %-16s owner=%-5s can_reply=%-5s can_view=%s"
                  % (label, P.has_permission(db, u, a, "owner"),
                     P.has_permission(db, u, a, "can_reply"),
                     P.has_permission(db, u, a, "can_view")))

    head("3. ПОДТВЕРЖДЕНИЕ")
    for st, n in many(db, "select status, count(*) from boris_commands"
                          " group by 1 order by 2 desc"):
        print("  %-18s %d" % (st, n))
    r = one(db, "select count(*) filter (where needs_confirmation),"
                " count(*) filter (where needs_confirmation and expires_at is null),"
                " count(*) filter (where risk_level='yellow' and status='done'"
                "                        and confirmed_at is null)"
                " from boris_commands")
    print("  требовали подтверждения: %d | из них без срока: %d" % (r[0], r[1]))
    print("  ⚠ yellow выполнены БЕЗ подтверждения: %d" % r[2])
    if r[2]:
        NOTES.append("yellow без confirmed_at: %d — проверить путь" % r[2])

    head("4. ЖУРНАЛ")
    for ev, n in many(db, "select event, count(*) from boris_command_events"
                          " group by 1 order by 2 desc"):
        print("  %-18s %d" % (ev, n))
    r = one(db, "select count(*) from boris_commands c where not exists"
                " (select 1 from boris_command_events e"
                "   where e.command_id = c.command_id)")
    print("  команд без единой записи в журнале: %d" % r[0])
    if r[0]:
        NOTES.append("команд без журнала: %d" % r[0])
    r = one(db, "select count(*) from boris_command_events"
                " where payload_redacted::text ilike '%%пароль%%'"
                "    or payload_redacted::text ilike '%%token%%'")
    print("  подозрительных payload в журнале: %d" % r[0])

    head("5. КОНТЕКСТ АККАУНТА")
    r = one(db, "select count(*), count(*) filter (where account_id is null),"
                " count(*) filter (where entity_type is not null),"
                " count(*) filter (where page_context is not null)"
                " from boris_commands")
    print("  команд всего %d | без account_id %d | с объектом экрана %d"
          " | с контекстом страницы %d" % tuple(r))
    if r[3] == 0:
        NOTES.append("page_context не передаётся фронтом ни разу")

    head("6. КОНТЕКСТ ВЛАДЕЛЬЦА")
    for k, n in many(db, "select billing_owner_kind, count(distinct billing_owner_id)"
                         " from boris_commands group by 1"):
        print("  вид владельца %-8s разных владельцев: %d" % (k, n))
    ownerless = many(db, "select account_id,coalesce(name,'') from accounts where owner_user_id is null order by account_id")
    real_ownerless = [(a,n) for a,n in ownerless if not str(a or '').lower().startswith(('qa_','test_','demo_','sandbox_'))]
    print("  аккаунтов без владельца (команды недоступны): %d | реальных: %d" % (len(ownerless), len(real_ownerless)))
    if real_ownerless:
        print("  ⚠ реальные без владельца: %s" % ", ".join(a for a,_ in real_ownerless))
        NOTES.append("реальные аккаунты без владельца: " + ", ".join(a for a,_ in real_ownerless))
    for row in many(db, "select billing_owner_id, resource, purchased, used,"
                        " paid_until from product_limits order by 1"):
        print("  пул: владелец %s %s %s/%s до %s"
              % (row[0], row[1], row[3], row[2], row[4]))

    head("7. ОШИБКИ")
    rows = many(db, "select coalesce(error_code,'—'), count(*) from boris_commands"
                    " where status in ('failed','expired') group by 1 order by 2 desc")
    if not rows:
        print("  неуспешных команд нет")
    for ec, n in rows:
        print("  %-30s %d" % (ec, n))

    head("8. ПОВТОРНОЕ ВЫПОЛНЕНИЕ")
    r = one(db, "select count(*) filter (where units_charged_at is not null),"
                " coalesce(sum(units) filter (where units_charged_at is not null), 0)"
                " from boris_commands")
    print("  списанных команд: %d | сумма units: %d" % (r[0], r[1]))
    for row in many(db, "select billing_owner_id, used, period_start"
                        " from product_limits where resource='commands' order by 1"):
        # Считаем ТОЛЬКО внутри текущего периода: при продлении пакета used
        # обнуляется, и сравнение со всей историей даёт ложное расхождение.
        s = one(db, "select coalesce(sum(units),0) from boris_commands"
                    " where billing_owner_id=:o and units_charged_at is not null"
                    "   and (cast(:p as date) is null"
                    "        or units_charged_at >= cast(:p as date))",
                {"o": row[0], "p": row[2]})
        mark = "сходится" if int(s[0]) == int(row[1]) else "РАСХОЖДЕНИЕ"
        print("  владелец %s: период с %s, пул used=%s, списано в периоде=%s — %s"
              % (row[0], row[2], row[1], s[0], mark))
        if mark != "сходится":
            NOTES.append("расхождение пула у владельца %s" % row[0])

    head("9. ОТОБРАЖЕНИЕ РЕЗУЛЬТАТА")
    r = one(db, "select count(*) filter (where status='done'),"
                " count(*) filter (where status='done' and result is null),"
                " count(*) filter (where human_summary is not null),"
                " count(*) filter (where clarify_question is not null),"
                " count(*) filter (where status='done' and created_at >= now()-interval '24 hours'),"
                " count(*) filter (where status='done' and created_at >= now()-interval '24 hours' and result is null)"
                " from boris_commands")
    print("  выполнено %d | из них result пуст: %d (legacy включён)" % (r[0], r[1]))
    print("  за 24ч выполнено: %d | result пуст: %d" % (r[4], r[5]))
    print("  с человеческим описанием: %d | с уточняющим вопросом: %d" % (r[2], r[3]))
    if r[5]:
        NOTES.append("у свежих done-команд пустой result: %d" % r[5])

    head("ИТОГ")
    if not NOTES:
        print("  замечаний нет")
    for n in NOTES:
        print("  • %s" % n)
    db.close()


if __name__ == "__main__":
    main()
