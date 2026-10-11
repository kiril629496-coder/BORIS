# -*- coding: utf-8 -*-
"""Отправка файлов покупателю ссылкой.

Avito принимает от внешних систем ТОЛЬКО текст: загрузка фото закрыта (403),
документы и видео площадка не принимает вовсе. Клиент уже сам обходит это,
отправляя ссылки на облако — делаем то же самое удобно из кабинета.

Файл ложится в уже смонтированную статику /images (подпапка attach), имя
случайное — покупатель не наш пользователь, войти он не может, поэтому защита
в неугадываемости адреса. Ссылка подставляется в поле ответа и уходит обычным
текстовым сообщением, то есть журнал и автор пишутся существующим путём."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

A_OLD = "from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks"
A_NEW = "from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, File, Form, UploadFile"

B_OLD = "class InboxTakeRequest(BaseModel):"
B_NEW = '''ATTACH_DIR = "/root/BORIS/backend/images/attach"
ATTACH_MAX_MB = 20


@router.post("/upload_file")
async def inbox_upload_file(account_id: str = Form(...), file: UploadFile = File(...),
                            user=Depends(get_current_user)):
    """Кладёт файл в статику и возвращает ссылку для вставки в сообщение."""
    import os as _os
    import re as _re
    import secrets as _secrets
    db = SessionLocal()
    try:
        if account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
    finally:
        db.close()
    data = await file.read()
    if not data:
        return {"status": "error", "message": "Файл пустой"}
    if len(data) > ATTACH_MAX_MB * 1024 * 1024:
        return {"status": "error",
                "message": "Файл больше %s МБ — отправьте ссылкой на облако" % ATTACH_MAX_MB}
    orig = _os.path.basename(file.filename or "file")
    ext = _os.path.splitext(orig)[1][:12]
    if not _re.match(r"^\\.[A-Za-z0-9]+$", ext or ""):
        ext = ""
    _os.makedirs(ATTACH_DIR, exist_ok=True)
    name = _secrets.token_hex(16) + ext
    with open(_os.path.join(ATTACH_DIR, name), "wb") as fh:
        fh.write(data)
    url = "https://boris-ai.pro/images/attach/" + name
    try:
        from app.services.action_log import log_action, ACTOR_USER
        log_action(account_id=account_id, action="Загрузил файл для отправки",
                   object_kind="файл", object_name=orig[:200], after_val=url,
                   reason="ссылка будет отправлена покупателю сообщением",
                   actor=ACTOR_USER, user_id=getattr(user, "id", None),
                   source="inbox.upload_file")
    except Exception:
        pass
    return {"status": "ok", "url": url, "name": orig, "size": len(data)}


class InboxTakeRequest(BaseModel):'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "inbox_upload_file" in src:
    print("уже пропатчено"); sys.exit(0)
for nm, old in (("импорты", A_OLD), ("ручка", B_OLD)):
    n = src.count(old)
    print("  %-10s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(A_OLD, A_NEW, 1).replace(B_OLD, B_NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_upload_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
