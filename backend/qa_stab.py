#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Доказательство четырёх стабилизационных правок."""
import json
import sys

import requests
from sqlalchemy import text

sys.path.insert(0, ".")
from app.api.auth import create_access_token
from app.db.session import SessionLocal
from app.services import command_flow as flow
from app.services.command_contract import CommandInput

BASE = "https://boris-ai.pro"
ACC = "otdushi"
NO_OWNER = "qa_feed_test"
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-44s %s" % ("PASS" if cond else "FAIL", name, str(got)[:60]))


def main():
    db = SessionLocal()
    user = {"id": 2, "role": "client"}

    print("1. РЕЗУЛЬТАТ УСПЕШНОЙ КОМАНДЫ")
    r = flow.start(db, user, CommandInput(source="text", account_id=ACC,
                                          text="покажи статистику за неделю"))
    row = db.execute(text("select result, status from boris_commands"
                          " where command_id=cast(:c as uuid)"),
                     {"c": r.get("command_id")}).fetchone()
    saved = row[0] if row else None
    print("   сохранено: %s" % json.dumps(saved, ensure_ascii=False)[:110])
    check("команда выполнена", r.get("status") == "done", r.get("status"))
    check("result непустой", bool(saved), type(saved).__name__)
    check("result содержит поля исполнителя",
          isinstance(saved, dict) and "views" in saved, list(saved or {}))
    check("в result нет служебных ключей",
          isinstance(saved, dict) and "status" not in saved and
          "answer" not in saved, list(saved or {}))

    print("\n2. ЖУРНАЛ РАННЕГО ОТКАЗА")
    r2 = flow.start(db, user, CommandInput(source="text", account_id=NO_OWNER,
                                           text="покажи статистику"))
    ev = db.execute(text(
        "select event, payload_redacted from boris_command_events"
        " where command_id=cast(:c as uuid) order by ts desc limit 1"),
        {"c": r2.get("command_id")}).fetchone()
    print("   отказ: %s | событие: %s" % (r2.get("error_code"),
                                          ev[0] if ev else "НЕТ"))
    check("отказ до модели", r2.get("error_code") == "no_billing_owner",
          r2.get("error_code"))
    check("событие записано", ev is not None, ev[0] if ev else "нет")
    check("код ошибки в журнале",
          bool(ev) and (ev[1] or {}).get("error_code") == "no_billing_owner",
          (ev[1] if ev else None))
    check("секретов в payload нет",
          bool(ev) and "password" not in json.dumps(ev[1] or {}, ensure_ascii=False))

    print("\n3. PAGE_CONTEXT СО СТРАНИЦЫ")
    h = {"Authorization": "Bearer " + create_access_token(2),
         "Content-Type": "application/json"}
    resp = requests.post(BASE + "/api/command/parse", headers=h, timeout=60,
                         json={"source": "text", "text": "открой сообщения",
                               "account_id": ACC,
                               "page_context": {"route": "/command",
                                                "section": "command"}}).json()
    pc = db.execute(text("select page_context from boris_commands"
                         " where command_id=cast(:c as uuid)"),
                    {"c": resp.get("command_id")}).fetchone()
    saved_pc = pc[0] if pc else None
    print("   сохранено: %s" % json.dumps(saved_pc, ensure_ascii=False))
    check("page_context сохранён", bool(saved_pc), saved_pc)
    check("route на месте",
          isinstance(saved_pc, dict) and saved_pc.get("route") == "/command",
          saved_pc)
    check("только белый список полей",
          isinstance(saved_pc, dict) and set(saved_pc) <= {"route", "section"},
          list(saved_pc or {}))

    print("\n4. СВЕРКА ПУЛА В ГРАНИЦАХ ПЕРИОДА")
    rows = db.execute(text(
        "select billing_owner_id, used, period_start from product_limits"
        " where resource='commands' order by 1")).fetchall()
    for owner, used, start in rows:
        s = db.execute(text(
            "select coalesce(sum(units),0) from boris_commands"
            " where billing_owner_id=:o and units_charged_at is not null"
            "   and (cast(:p as date) is null"
            "        or units_charged_at >= cast(:p as date))"),
            {"o": owner, "p": start}).fetchone()[0]
        ok = int(s) == int(used)
        print("   владелец %s: период с %s, пул used=%s, списано=%s"
              % (owner, start, used, s))
        check("пул владельца %s сходится" % owner, ok, "%s против %s" % (used, s))

    print("\nИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
