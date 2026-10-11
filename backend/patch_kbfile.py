# -*- coding: utf-8 -*-
"""Файл клиента -> знания БЕЗ отдельной кнопки «Обучить».

Загрузил прайс или описание услуг — текст извлечён, нарезан и сохранён теми же
фактами, что и ручной ввод. Повторная загрузка того же файла обновляет факты,
а не плодит дубли: за это отвечает fact_hash в _save_manual_fact."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/client_memory.py")

A_OLD = "from fastapi import APIRouter, Depends, HTTPException"
A_NEW = "from fastapi import APIRouter, Depends, HTTPException, File, UploadFile"

B_OLD = '@router.post("/confirm")'
B_NEW = '''KB_FILE_MAX_MB = 15


@router.post("/upload_file")
async def upload_knowledge_file(account_id: str, file: UploadFile = File(...),
                                user=Depends(get_current_user)):
    """Принимает PDF / Word / Excel / текст и сам кладёт содержимое в знания.

    account_id ИМЕННО в адресе запроса: общая проверка доступа читает его из
    query и иначе отказывает ещё до входа в функцию."""
    from app.services.file_knowledge import extract_text, to_facts
    if not _mem_allowed(account_id, user):
        return {"status": "error", "message": "Аккаунт недоступен"}
    data = await file.read()
    if not data:
        return {"status": "error", "message": "Файл пустой"}
    if len(data) > KB_FILE_MAX_MB * 1024 * 1024:
        return {"status": "error",
                "message": "Файл больше %s МБ" % KB_FILE_MAX_MB}
    try:
        raw, kind = extract_text(file.filename or "", data)
    except Exception as e:
        return {"status": "error",
                "message": "Не удалось прочитать файл: %s" % str(e)[:90]}
    if kind == "unsupported":
        return {"status": "error",
                "message": "Поддерживаются PDF, Word, Excel и текстовые файлы"}
    if not (raw or "").strip():
        return {"status": "error",
                "message": "В файле не нашлось текста. Если это скан или фотография, "
                           "текст из неё пока не распознаётся"}
    facts = to_facts(raw, file.filename or "файл")
    if not facts:
        return {"status": "error", "message": "Текста слишком мало для знаний"}
    who = getattr(user, "email", None) or "клиент"
    db = SessionLocal()
    saved = 0
    try:
        for f in facts:
            try:
                _save_manual_fact(db, account_id, who, f["category"], f["name"],
                                  f["value"], snippet=f["snippet"])
                saved += 1
            except Exception as fe:
                print("[kb_file] факт не сохранён: %s" % str(fe)[:120], flush=True)
        db.commit()
    finally:
        db.close()
    try:
        from app.services.action_log import log_action, ACTOR_USER
        log_action(account_id=account_id, action="Изучил файл клиента",
                   object_kind="знания", object_name=(file.filename or "файл")[:200],
                   after_val="добавлено фактов: %s" % saved,
                   reason="файл загружен в информацию о компании",
                   actor=ACTOR_USER, user_id=getattr(user, "id", None),
                   source="memory.upload_file")
    except Exception:
        pass
    return {"status": "ok", "facts": saved, "file": file.filename,
            "message": "Борис прочитал файл и запомнил %s фрагментов" % saved}


@router.post("/confirm")'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "upload_knowledge_file" in src:
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
bak = path + ".bak_kbfile_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
