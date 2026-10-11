#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Путь страницы /command через ПУБЛИЧНЫЙ домен — как его проходит браузер.

Те же ручки и тот же порядок: capabilities -> parse -> confirm/cancel ->
history. Созданная задача удаляется в конце.
"""
import sys

import requests
from sqlalchemy import text

sys.path.insert(0, ".")
from app.api.auth import create_access_token
from app.db.session import SessionLocal

BASE = "https://boris-ai.pro"
ACC = "otdushi"
OK, FAIL = [], []


def check(name, cond, got=""):
    (OK if cond else FAIL).append(name)
    print("  %s %-40s %s" % ("PASS" if cond else "FAIL", name, str(got)[:66]))


def main():
    h = {"Authorization": "Bearer " + create_access_token(2),
         "Content-Type": "application/json"}
    db = SessionLocal()
    max_task = db.execute(text(
        "select coalesce(max(id),0) from crm_tasks where account_id=:a"),
        {"a": ACC}).fetchone()[0]

    r = requests.get(BASE + "/api/command/capabilities",
                     params={"account_id": ACC}, headers=h, timeout=30)
    j = r.json() if r.status_code == 200 else {}
    pool = j.get("pool") or {}
    check("GET /capabilities", j.get("status") == "ok", r.status_code)
    check("флаг тарифа включён",
          (j.get("features") or {}).get("command_center_enabled") is True,
          j.get("features"))
    check("остаток пула виден", pool.get("purchased", 0) > 0,
          "%s из %s" % (pool.get("used"), pool.get("purchased")))
    check("список операций не пуст", len(j.get("operations") or []) >= 8,
          len(j.get("operations") or []))

    def parse(t):
        return requests.post(BASE + "/api/command/parse", headers=h, timeout=60,
                             json={"source": "text", "text": t,
                                   "account_id": ACC}).json()

    g = parse("открой сообщения")
    check("green выполняется сразу", g.get("status") == "done", g.get("status"))
    check("data.route для навигации",
          (g.get("data") or {}).get("route") == "/messages", g.get("data"))

    a = parse("покажи статистику за неделю")
    check("аналитика вернула числа",
          isinstance((a.get("data") or {}).get("views"), int), a.get("data"))

    s = parse("покажи последние обращения")
    check("поиск вернул список",
          isinstance((s.get("data") or {}).get("clients"), list),
          type((s.get("data") or {}).get("clients")).__name__)

    y = parse("поставь мне завтра перезвонить Иванову")
    check("yellow ждёт подтверждения", y.get("status") == "awaiting_confirm",
          y.get("status"))
    check("предпросмотр с изменениями",
          bool(y.get("proposed_changes")), y.get("human_summary"))

    c = requests.post(BASE + "/api/command/confirm", headers=h, timeout=60,
                      json={"command_id": y.get("command_id")}).json()
    check("подтверждение выполнено", c.get("status") == "done", c.get("status"))

    y2 = parse("поставь мне послезавтра написать Петрову")
    x = requests.post(BASE + "/api/command/cancel", headers=h, timeout=30,
                      json={"command_id": y2.get("command_id")}).json()
    check("отмена работает", x.get("status") == "cancelled", x.get("status"))

    hist = requests.get(BASE + "/api/command/history", params={"limit": 10},
                        headers=h, timeout=30).json()
    items = hist.get("commands") or []
    check("история отдаётся", len(items) >= 5, len(items))
    check("выполненная команда в истории",
          any(i.get("command_id") == c.get("command_id") for i in items))

    n = db.execute(text("delete from crm_tasks where account_id=:a and id > :m"),
                   {"a": ACC, "m": max_task})
    db.commit()
    print("\nуборка: удалено задач %s" % n.rowcount)
    print("ИТОГ: PASS %d, FAIL %d" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  провал:", f)
    db.close()


if __name__ == "__main__":
    main()
