# -*- coding: utf-8 -*-
"""Черновик помечается использованным СРАЗУ после отправки.

Раньше он оставался активным до ближайшей синхронизации (до десяти минут), и
всё это время менеджер видел в кабинете уже отправленный ответ — мог нажать
«Отправить» повторно. Фильтр «продавец ответил» срабатывал только потом."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

OLD = '''                   source="inbox_send")
    except Exception:
        pass
    try:
        sync_chats(req.account_id, limit=50)'''

NEW = '''                   source="inbox_send")
    except Exception:
        pass
    # Черновик закрываем сразу, не дожидаясь синхронизации: иначе до десяти минут
    # менеджер видит в кабинете уже отправленный ответ и может отправить его
    # второй раз. Фильтр «продавец ответил» сработал бы только после обхода.
    try:
        _ddb = SessionLocal()
        try:
            _ddb.execute(text(
                "UPDATE mop_drafts SET status = 'sent', sent_at = now(),"
                " reply_author = :w, updated_at = now()"
                " WHERE account_id = :a AND avito_chat_id = :c"
                "   AND status NOT IN ('sent', 'deleted', 'no_reply_required')"),
                {"a": req.account_id, "c": req.avito_chat_id,
                 "w": (getattr(user, "email", None) or "")[:120]})
            _ddb.commit()
        finally:
            _ddb.close()
    except Exception as e:
        print("[inbox_send] draft mark failed: " + repr(e)[:150], flush=True)
    try:
        sync_chats(req.account_id, limit=50)'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "draft mark failed" in src:
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
bak = path + ".bak_marksent_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
