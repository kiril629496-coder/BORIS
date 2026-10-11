# -*- coding: utf-8 -*-
"""Смоук по инструментам BORIS: что отвечает, что нет. Только чтение.
Токен выпускается напрямую для владельца, пароль вводить не нужно."""
import json
import sys

import httpx
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.auth import create_access_token

BASE = "http://127.0.0.1:8000"
USER = int(sys.argv[1]) if len(sys.argv) > 1 else 2
tok = create_access_token(USER)
H = {"Authorization": "Bearer %s" % tok}
c = httpx.Client(timeout=60)

db = SessionLocal()
acc = db.execute(text(
    "SELECT account_id FROM accounts WHERE owner_user_id=:u ORDER BY account_id LIMIT 1"),
    {"u": USER}).scalar()
db.close()
print("пользователь %s | первый аккаунт: %s\n" % (USER, acc))

print("=== КАРТА API (сколько маршрутов по разделам)")
spec = c.get(BASE + "/openapi.json").json()
groups = {}
for p in spec.get("paths", {}):
    key = "/".join(p.split("/")[:3])
    groups[key] = groups.get(key, 0) + 1
for k, v in sorted(groups.items(), key=lambda z: -z[1])[:18]:
    print("   %-34s %d" % (k, v))

CHECKS = [
    ("кто я", "GET", "/api/auth/me", None),
    ("список аккаунтов", "GET", "/api/accounts/list", None),
    ("слоты инбокса", "GET", "/api/inbox/slots", None),
    ("аккаунты инбокса", "GET", "/api/inbox/accounts", None),
    ("диалоги инбокса", "GET", "/api/inbox/dialogs", {"account_id": acc}),
    ("очереди реактивации", "GET", "/api/reactivation/queues", None),
    ("кандидаты: долги", "GET", "/api/reactivation/candidates", {"queue": "hot"}),
    ("кандидаты: реактивация", "GET", "/api/reactivation/candidates", {"queue": "reactivation"}),
    ("воронка лидов МОП", "GET", "/api/messenger/leads/stats", {"account_id": acc}),
    ("баланс пакета МОП", "GET", "/api/messenger/manager/balance", {"account_id": acc}),
    ("лимиты РОП", "GET", "/api/calltracking/limits", {"account_id": acc}),
    ("статус биллинга", "GET", "/api/billing/status", {"account_id": acc}),
]

print("\n=== ПРОВЕРКА РУЧЕК")
for name, method, path, params in CHECKS:
    try:
        r = c.request(method, BASE + path, headers=H, params=params or {})
        body = ""
        if r.status_code == 200:
            try:
                d = r.json()
                if isinstance(d, dict):
                    body = ", ".join(list(d.keys())[:6])
                elif isinstance(d, list):
                    body = "список из %d" % len(d)
            except Exception:
                body = r.text[:50]
        else:
            body = r.text[:90]
        print("   %-24s %s %-3s %s" % (name, path[:36].ljust(36), r.status_code, body[:70]))
    except Exception as e:
        print("   %-24s %s ОШИБКА %s" % (name, path[:36].ljust(36), str(e)[:60]))

print("\n=== ДАННЫЕ РЕАКТИВАЦИИ ПОДРОБНО")
r = c.get(BASE + "/api/reactivation/queues", headers=H)
if r.status_code == 200:
    for k, v in r.json().items():
        if isinstance(v, dict):
            print("   %-14s %s: %s задач, суммы %s ₽, просрочено 14+ %s, 30+ %s"
                  % (k, v.get("title"), v.get("count"), v.get("amount"),
                     v.get("overdue_14"), v.get("overdue_30")))
        else:
            print("   %-14s %s" % (k, v))
else:
    print("   очереди недоступны: %s %s" % (r.status_code, r.text[:120]))
