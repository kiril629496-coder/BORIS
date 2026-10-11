#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск клиента и заметка НА РЕАЛЬНЫХ ДАННЫХ.

Аккаунт otdushi принадлежит владельцу BORIS (user 2) — клиентские данные
не трогаем. Исходная заметка восстанавливается в конце.
"""
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services import command_registry as R
from app.services.command_contract import CommandInput

ACC = "otdushi"
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-40s %s" % ("PASS" if cond else "FAIL", name, str(got)[:70]))


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}
    kind, oid = L.resolve_billing_owner(db, ACC)
    if not L.has_feature(db, kind, oid, R.FEATURE_BASE):
        L.set_feature(db, kind, oid, R.FEATURE_BASE, True)

    fill = db.execute(text(
        "select count(*),"
        " count(*) filter (where coalesce(contact_name,'') <> ''),"
        " count(*) filter (where coalesce(company,'') <> ''),"
        " count(*) filter (where coalesce(item_title,'') <> '')"
        " from messenger_leads where account_id=:a"), {"a": ACC}).fetchone()
    print("карточек %s | с именем %s | с компанией %s | с объявлением %s"
          % tuple(fill))

    lead = db.execute(text(
        "select id, coalesce(contact_name,''), coalesce(company,''),"
        " coalesce(item_title,''), avito_chat_id, coalesce(note,'')"
        " from messenger_leads where account_id=:a"
        "   and (coalesce(contact_name,'') <> '' or coalesce(company,'') <> ''"
        "        or coalesce(item_title,'') <> '')"
        " order by last_msg_at desc nulls last limit 1"), {"a": ACC}).fetchone()
    if not lead:
        print("нет ни одной заполненной карточки — тест невозможен")
        return
    lead_id = lead[0]
    name = (lead[1] or lead[2] or lead[3])[:40]
    chat_id, note0 = lead[4], lead[5]
    print("карточка #%s | %s | диалог %s" % (lead_id, name, chat_id))

    print("\n1. Поиск клиента по имени")
    r = flow.start(db, user, CommandInput(
        source="text", text="найди клиента %s" % name, account_id=ACC))
    print("   ответ: %s" % (r.get("answer") or "")[:100].replace("\n", " / "))
    check("операция crm.leads_search", r.get("operation") == "crm.leads_search",
          r.get("operation"))
    check("выполнено", r.get("status") == "done", r.get("status"))
    check("клиент найден в ответе", name.split()[0].lower() in
          (r.get("answer") or "").lower(), name)

    print("\n2. Заметка по имени клиента")
    r = flow.start(db, user, CommandInput(
        source="text", account_id=ACC,
        text="добавь заметку по клиенту %s: просил перезвонить после обеда" % name))
    cid = r.get("command_id")
    print("   предпросмотр: %s" % (r.get("human_summary") or "")[:90])
    check("операция crm.lead_note_add",
          r.get("operation") == "crm.lead_note_add", r.get("operation"))
    check("ждёт подтверждения", r.get("status") == "awaiting_confirm",
          r.get("status"))
    check("в предпросмотре виден клиент",
          name.split()[0].lower() in (r.get("human_summary") or "").lower(),
          r.get("human_summary"))
    note_mid = db.execute(text("select coalesce(note,'') from messenger_leads"
                               " where id=:i"), {"i": lead_id}).fetchone()[0]
    check("до подтверждения заметка НЕ записана", note_mid == note0)

    c = flow.confirm(db, user, cid)
    note_after = db.execute(text("select coalesce(note,'') from messenger_leads"
                                 " where id=:i"), {"i": lead_id}).fetchone()[0]
    print("   подтверждение: %s | %s" % (c.get("status"), (c.get("answer") or "")[:60]))
    check("выполнено", c.get("status") == "done", c.get("status"))
    check("заметка появилась в карточке",
          "перезвонить после обеда" in note_after.lower(), note_after[-60:])
    check("прежний текст заметки сохранён",
          note_after.startswith(note0) if note0 else True)

    print("\n3. Заметка из контекста экрана (без имени в фразе)")
    r = flow.start(db, user, CommandInput(
        source="text", account_id=ACC, entity_type="dialog", entity_id=chat_id,
        text="добавь заметку: клиент просил счёт на оплату"))
    check("операция crm.lead_note_add",
          r.get("operation") == "crm.lead_note_add", r.get("operation"))
    if r.get("status") == "awaiting_confirm":
        c = flow.confirm(db, user, r.get("command_id"))
        note2 = db.execute(text("select coalesce(note,'') from messenger_leads"
                                " where id=:i"), {"i": lead_id}).fetchone()[0]
        check("клиент определён по открытому диалогу",
              "счёт на оплату" in note2.lower(), note2[-60:])

    db.execute(text("update messenger_leads set note=:n where id=:i"),
               {"n": note0 or None, "i": lead_id})
    db.commit()
    restored = db.execute(text("select coalesce(note,'') from messenger_leads"
                               " where id=:i"), {"i": lead_id}).fetchone()[0]
    check("исходная заметка восстановлена", restored == note0, restored[:40])

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
