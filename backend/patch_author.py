# -*- coding: utf-8 -*-
"""Кто из сотрудников ответил клиенту. Синхронизация с Avito автора не знает —
для Avito все ответы идут от аккаунта. Поэтому пишем в момент отправки,
отдельной таблицей, не трогая горячий путь приёма сообщений."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

OLD = '''    try:
        sync_chats(req.account_id, limit=50)
    except Exception as e:
        print("[inbox_send] sync after send failed: " + repr(e)[:150], flush=True)
    return {"status": "ok"}'''

NEW = '''    # Кто ответил. Пишем ДО синхронизации и в своей таблице: Avito автора
    # не возвращает, а перезапись сообщений синхронизацией эту отметку не тронет.
    # Ошибка записи не должна мешать отправке — сообщение клиенту уже ушло.
    try:
        _adb = SessionLocal()
        try:
            _adb.execute(text(
                "INSERT INTO outgoing_authors (account_id, avito_chat_id, user_id, text, sent_at)"
                " VALUES (:a, :c, :u, :t, now())"),
                {"a": req.account_id, "c": req.avito_chat_id,
                 "u": getattr(user, "id", None), "t": req.text.strip()[:2000]})
            _adb.commit()
        finally:
            _adb.close()
    except Exception as e:
        print("[inbox_send] author log failed: " + repr(e)[:150], flush=True)
    try:
        sync_chats(req.account_id, limit=50)
    except Exception as e:
        print("[inbox_send] sync after send failed: " + repr(e)[:150], flush=True)
    return {"status": "ok"}'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "outgoing_authors" in src:
    print("уже пропатчено"); sys.exit(0)
n = src.count(OLD)
print("  якорь встречается:", n)
if n != 1:
    print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(OLD, NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_author_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
