#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Правка карточки клиента живой фразой. Исходные значения возвращаются."""
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services import command_limits as L
from app.services.command_contract import CommandInput

ACC = "otdushi"
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-40s %s" % ("PASS" if cond else "FAIL", name, str(got)[:70]))


def card(db, lead_id):
    r = db.execute(text("select contact_name, cost_rub from messenger_leads"
                        " where id=:i"), {"i": lead_id}).fetchone()
    return (r[0], r[1]) if r else (None, None)


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}
    kind, oid = L.resolve_billing_owner(db, ACC)

    lead = db.execute(text(
        "select id, coalesce(item_title,'') from messenger_leads"
        " where account_id=:a and coalesce(item_title,'') <> ''"
        " order by last_msg_at desc nulls last limit 1"), {"a": ACC}).fetchone()
    lead_id, title = lead[0], lead[1]
    name0, cost0 = card(db, lead_id)
    print("карточка #%s «%s» | было: имя=%r сумма=%r"
          % (lead_id, title, name0, cost0))

    phrase = ("у клиента %s контактное лицо Мария, бюджет 15 тысяч рублей"
              % title[:30])
    used0 = L.pool_state(db, kind, oid)["used"]
    r = flow.start(db, user, CommandInput(source="text", account_id=ACC,
                                          text=phrase))
    print("фраза: «%s»" % phrase)
    print("  предпросмотр: %s" % (r.get("human_summary") or "")[:100])
    ch = r.get("proposed_changes") or []
    check("операция crm.lead_update", r.get("operation") == "crm.lead_update",
          r.get("operation"))
    check("ждёт подтверждения", r.get("status") == "awaiting_confirm",
          r.get("status"))
    check("в предпросмотре есть было/стало", bool(ch) and "after" in (ch[0] or {}),
          ch[:1])
    check("до подтверждения карточка НЕ изменена", card(db, lead_id) == (name0, cost0),
          card(db, lead_id))

    c = flow.confirm(db, user, r.get("command_id"))
    name1, cost1 = card(db, lead_id)
    used1 = L.pool_state(db, kind, oid)["used"]
    print("  подтверждение: %s | %s" % (c.get("status"), (c.get("answer") or "")[:70]))
    check("выполнено", c.get("status") == "done", c.get("status"))
    check("имя записано", (name1 or "").strip() == "Мария", name1)
    check("сумма нормализована в число", cost1 in (15000, 15000.0), cost1)
    check("списан 1 unit", used1 - used0 == 1, "%d -> %d" % (used0, used1))

    db.execute(text("update messenger_leads set contact_name=:n, cost_rub=:c"
                    " where id=:i"), {"n": name0, "c": cost0, "i": lead_id})
    db.commit()
    check("исходные значения возвращены", card(db, lead_id) == (name0, cost0),
          card(db, lead_id))

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
