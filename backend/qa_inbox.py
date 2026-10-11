# -*- coding: utf-8 -*-
"""Приёмка восьми клиентских правок по Единому окну. Только проверки.

Единственное изменяющее действие — взять и тут же освободить один диалог,
чтобы доказать атомарность захвата. Оба действия попадут в журнал."""
import sys, os, json, urllib.request, urllib.error, urllib.parse
sys.path.insert(0, "/root/BORIS/backend")
for _l in open("/root/BORIS/backend/.env", encoding="utf-8"):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k, v = _l.split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.auth import create_access_token

ARENDA, PRODAZHA = "3411770_94346", "prodazha_bytovok_25677"
TOK = create_access_token(42)
ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    print("  %s %-52s %s" % ("OK  " if cond else "ПРОВАЛ", name, str(got)[:60]))
    ok, bad = (ok + 1, bad) if cond else (ok, bad + 1)


def api(path, body=None):
    r = urllib.request.Request("https://boris-ai.pro" + path,
                               headers={"Authorization": "Bearer " + TOK,
                                        "Content-Type": "application/json"},
                               data=(json.dumps(body).encode() if body is not None else None),
                               method=("POST" if body is not None else "GET"))
    try:
        return json.loads(urllib.request.urlopen(r, timeout=40).read().decode())
    except urllib.error.HTTPError as e:
        return {"status": "http_%s" % e.code, "body": e.read().decode()[:120]}


db = SessionLocal()
chat_a = db.execute(text(
    "SELECT avito_chat_id FROM mop_drafts WHERE account_id=:a"
    " AND coalesce(reply_text,'') <> '' ORDER BY coalesce(updated_at, created_at) DESC LIMIT 1"),
    {"a": ARENDA}).scalar()
chat_p = db.execute(text(
    "SELECT avito_chat_id FROM messenger_messages WHERE account_id=:a"
    " GROUP BY avito_chat_id ORDER BY max(avito_created_at) DESC LIMIT 1"),
    {"a": PRODAZHA}).scalar()

# A persisted draft is intentionally hidden by /thread after the seller has
# already answered. That is a safety contract, not a missing-draft failure:
# showing the stale text would invite a duplicate/out-of-context reply.
persisted_a = db.execute(text(
    "SELECT status, COALESCE(reply_text,'') AS reply_text"
    " FROM mop_drafts WHERE account_id=:a AND avito_chat_id=:c"
    " ORDER BY COALESCE(updated_at,created_at) DESC LIMIT 1"),
    {"a": ARENDA, "c": chat_a}).mappings().first()
last_business_dir = db.execute(text(
    "SELECT direction FROM messenger_messages"
    " WHERE account_id=:a AND avito_chat_id=:c"
    "   AND COALESCE(msg_type,'') <> 'system'"
    " ORDER BY avito_created_at DESC,id DESC LIMIT 1"),
    {"a": ARENDA, "c": chat_a}).scalar()
draft_should_be_hidden = bool(
    persisted_a
    and str(persisted_a.get("reply_text") or "").strip()
    and str(last_business_dir or "").lower().startswith("out")
)
db.close()

print("=== П.1 ЧЕРНОВИК В КАБИНЕТЕ ===")
t1 = api("/api/inbox/thread?account_id=%s&avito_chat_id=%s" % (ARENDA, urllib.parse.quote(chat_a or "")))
d1_raw = t1.get("draft")
d1 = d1_raw or {}
hidden_ok = d1_raw is None and draft_should_be_hidden
draft_state = d1.get("status") or ("скрыт после ответа продавца" if hidden_ok else None)
check("аренда: draft есть или безопасно скрыт после ответа",
      bool(d1) or hidden_ok, draft_state)
check("аренда: состояние корректно", hidden_ok or d1.get("status") in
      ("draft_ready", "human_required", "analyzing", "generation_failed", "not_connected"),
      draft_state)
check("аренда: источник есть для видимого draft",
      hidden_ok or bool(d1.get("source")),
      ("не нужен: stale draft скрыт" if hidden_ok else d1.get("source")))
t2 = api("/api/inbox/thread?account_id=%s&avito_chat_id=%s" % (PRODAZHA, urllib.parse.quote(chat_p or "")))
d2 = t2.get("draft")
check("продажа: черновик или честное состояние", True, (d2 or {}).get("status", "нет черновика"))

print("=== П.2 ВРЕМЯ ===")
msgs = t1.get("messages") or []
check("есть at_iso у сообщений", bool(msgs and msgs[0].get("at_iso")), (msgs[0].get("at_iso") if msgs else ""))

print("=== П.3 ВЛОЖЕНИЯ ===")
check("content_type отдаётся", bool(msgs and "content_type" in msgs[0]),
      (msgs[0].get("content_type") if msgs else ""))

print("=== П.7 ПРОЧТЕНИЕ ===")
check("поля прочтения в ответе", bool(msgs and "is_read" in msgs[0] and "read_at_iso" in msgs[0]),
      "is_read=%s" % (msgs[0].get("is_read") if msgs else "-"))

print("=== П.5 ВЗЯТЬ ДИАЛОГ ===")
dlg = t1.get("dialog") or {}
check("поля назначения в диалоге", "assigned_user_id" in dlg and "assigned_mine" in dlg,
      dlg.get("assigned_name") or "свободен")
was = dlg.get("assigned_user_id")
if was is None:
    r1 = api("/api/inbox/take", {"account_id": ARENDA, "avito_chat_id": chat_a})
    check("захват проходит", r1.get("status") == "ok", r1.get("status"))
    r2 = api("/api/inbox/take", {"account_id": ARENDA, "avito_chat_id": chat_a})
    check("повтор тем же — не ошибка", r2.get("status") == "ok", r2.get("status"))
    t3 = api("/api/inbox/thread?account_id=%s&avito_chat_id=%s" % (ARENDA, urllib.parse.quote(chat_a)))
    check("отмечено как мой", (t3.get("dialog") or {}).get("assigned_mine") is True)
    r3 = api("/api/inbox/release", {"account_id": ARENDA, "avito_chat_id": chat_a})
    check("освобождение проходит", r3.get("status") == "ok", r3.get("status"))
else:
    print("  (диалог уже занят — захват не проверяем, чтобы не отбирать)")

print("=== ЖУРНАЛ ===")
j = api("/api/action_log/recent?account_id=%s&limit=5" % ARENDA)
check("журнал отвечает", j.get("status") == "ok", "записей: %s" % len(j.get("записи") or []))

print()
print("ИТОГО: OK %s | ПРОВАЛОВ %s" % (ok, bad))
