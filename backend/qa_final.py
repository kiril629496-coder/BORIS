#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Финальный прогон этапа 2: все восемь интентов ЖИВЫМИ фразами.

Всё созданное убирается в конце: задачи удаляются, заметка и карточка
возвращаются к исходному виду, тестовый факт удаляется.
"""
import os
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services import command_registry as R
from app.services.command_contract import CommandInput

OK, FAIL, ROWS = [], [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    if not cond:
        print("     FAIL %-34s %s" % (name, str(got)[:60]))


def pick(db):
    row = db.execute(text(
        "select l.account_id from messenger_leads l"
        " join accounts a on a.account_id = l.account_id"
        " where a.owner_user_id = 2 and coalesce(l.item_title,'') <> ''"
        " group by 1 order by count(*) desc limit 1")).fetchone()
    return row[0]


def run(db, user, acc, kind, oid, phrase, expect_op, expect_units,
        entity=None, confirm=True):
    used0 = L.pool_state(db, kind, oid)["used"]
    kw = {}
    if entity:
        kw = {"entity_type": entity[0], "entity_id": entity[1]}
    r = flow.start(db, user, CommandInput(source="text", text=phrase,
                                          account_id=acc, **kw))
    op = r.get("operation")
    preview = r.get("human_summary") or ""
    if r.get("status") == "awaiting_confirm" and confirm:
        r2 = flow.confirm(db, user, r.get("command_id"))
    else:
        r2 = r
    used1 = L.pool_state(db, kind, oid)["used"]
    rub = float(db.execute(text(
        "select coalesce(sum(cost_rub),0) from api_usage"
        " where command_id = cast(:c as uuid)"),
        {"c": r.get("command_id")}).fetchone()[0] or 0)
    meta = R.get(op or "")
    ROWS.append({"phrase": phrase, "op": op,
                 "risk": meta.risk if meta else "-",
                 "perm": (meta.permission or "-") if meta else "-",
                 "units_reg": meta.units if meta else 0,
                 "units_spent": used1 - used0, "rub": rub,
                 "status": r2.get("status"),
                 "answer": (r2.get("answer") or r2.get("message") or "")[:70],
                 "preview": preview[:70]})
    check("%s -> %s" % (phrase[:28], expect_op), op == expect_op, op)
    check("%s выполнено" % phrase[:20], r2.get("status") == "done", r2.get("status"))
    check("%s units" % phrase[:20], used1 - used0 == expect_units,
          "%d/%d" % (used1 - used0, expect_units))
    return r2


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}
    acc = pick(db)
    if not str(acc).lower().startswith(('qa_','test_','demo_','sandbox_')) and os.getenv('BORIS_QA_ALLOW_PRODUCTION') != '1':
        raise RuntimeError('QA_ISOLATION_V1: production account blocked; use dedicated QA data')
    kind, oid = L.resolve_billing_owner(db, acc)
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

    lead = db.execute(text(
        "select id, coalesce(item_title,''), avito_chat_id, coalesce(note,''),"
        " contact_name from messenger_leads where account_id=:a"
        "   and coalesce(item_title,'') <> ''"
        " order by last_msg_at desc nulls last limit 1"), {"a": acc}).fetchone()
    lead_id, title, chat_id, note0, name0 = lead
    max_task = db.execute(text(
        "select coalesce(max(id),0) from crm_tasks where account_id=:a"),
        {"a": acc}).fetchone()[0]
    print("аккаунт %s | карточка #%s «%s»" % (acc, lead_id, title[:40]))

    run(db, user, acc, kind, oid, "открой сообщения", "ui.navigate", 0)
    run(db, user, acc, kind, oid, "найди клиента %s" % title[:25],
        "crm.leads_search", 1)
    run(db, user, acc, kind, oid, "поставь мне завтра перезвонить Иванову",
        "crm.task_create", 1)
    run(db, user, acc, kind, oid,
        "напомни послезавтра в 15:00 отправить КП Петрову", "crm.task_create", 1)
    run(db, user, acc, kind, oid, "добавь заметку: клиент просил счёт на оплату",
        "crm.lead_note_add", 1, entity=("dialog", chat_id))
    run(db, user, acc, kind, oid,
        "у клиента %s контактное лицо Мария" % title[:25], "crm.lead_update", 1)
    run(db, user, acc, kind, oid, "покажи статистику за неделю",
        "analytics.week", 1)
    run(db, user, acc, kind, oid,
        "запиши что доставка бесплатная до 50 километров",
        "knowledge.fact_add", 1)

    print("\n%-42s %-20s %-7s %-16s %5s %8s" %
          ("фраза", "операция", "риск", "право", "units", "₽"))
    for r in ROWS:
        print("%-42s %-20s %-7s %-16s %5s %8.4f" %
              (r["phrase"][:41], r["op"], r["risk"], r["perm"],
               "%d/%d" % (r["units_spent"], r["units_reg"]), r["rub"]))
        print("      → %s" % r["answer"])

    db.execute(text("delete from crm_tasks where account_id=:a and id > :m"),
               {"a": acc, "m": max_task})
    db.execute(text("update messenger_leads set note=:n, contact_name=:c"
                    " where id=:i"), {"n": note0 or None, "c": name0, "i": lead_id})
    db.execute(text("delete from client_facts where account_id=:a"
                    "   and source_ref='command_center'"), {"a": acc})
    db.commit()
    print("\nуборка: тестовые задачи, заметка, карточка и факт возвращены")

    print("ИТОГО: %.4f ₽, units %d" %
          (sum(r["rub"] for r in ROWS), sum(r["units_spent"] for r in ROWS)))
    print("ИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    db.close()


if __name__ == "__main__":
    main()
