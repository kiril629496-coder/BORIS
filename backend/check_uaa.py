# -*- coding: utf-8 -*-
"""Проверка доступа после патча. Только чтение, через боевой домен."""
import sys, os, json, urllib.request
sys.path.insert(0, "/root/BORIS/backend")
for line in open("/root/BORIS/backend/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from app.api.auth import create_access_token
from sqlalchemy import text
from app.db.session import SessionLocal

HOST = "https://boris-ai.pro"
BYT_A, BYT_B = "3411770_94346", "prodazha_bytovok_25677"
AND_A = "andrey_mebel_launzh_moskva_69737"


def token(db, uid):
    r = db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": uid}).fetchone()
    return create_access_token(uid), r[0]


def call(url, tok):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def show(label, code, expect, body=""):
    ok = "OK " if code == expect else "⛔ "
    extra = ""
    if body and code == 200 and "accounts" in body:
        try:
            extra = "  аккаунтов: %s" % len(json.loads(body).get("accounts", []))
        except Exception:
            extra = ""
    print("  %s%-52s %s (ждали %s)%s" % (ok, label, code, expect, extra))


db = SessionLocal()
t42, e42 = token(db, 42)
t47, e47 = token(db, 47)
t2, e2 = token(db, 2)

print("=== КЛИЕНТ БЫТОВОК (42, %s) ===" % e42)
c, b = call(HOST + "/api/accounts/list", t42); show("список аккаунтов", c, 200, b)
c, _ = call(HOST + "/api/storage/load?account_id=" + BYT_A, t42); show("свой: аренда", c, 200)
c, _ = call(HOST + "/api/storage/load?account_id=" + BYT_B, t42); show("свой: продажа", c, 200)
c, _ = call(HOST + "/api/storage/load?account_id=" + AND_A, t42); show("ЧУЖОЙ: Москва Андрея", c, 403)

print("=== КЛИЕНТ АНДРЕЙ (47, %s) ===" % e47)
c, b = call(HOST + "/api/accounts/list", t47); show("список аккаунтов", c, 200, b)
c, _ = call(HOST + "/api/storage/load?account_id=" + AND_A, t47); show("свой: Москва", c, 200)
c, _ = call(HOST + "/api/storage/load?account_id=" + BYT_A, t47); show("ЧУЖОЙ: аренда бытовок", c, 403)

print("=== ВЛАДЕЛЕЦ (2, %s) ===" % e2)
c, b = call(HOST + "/api/accounts/list", t2); show("список аккаунтов", c, 200, b)
c, _ = call(HOST + "/api/storage/load?account_id=" + BYT_A, t2); show("доступ владельца", c, 200)
db.close()
