# -*- coding: utf-8 -*-
"""Кнопка «Повторить» при ошибке генерации (последний кусок п.1 ТЗ).

Своей генерации НЕ заводим — право на вызов модели должно выдаваться в одном
месте, иначе рискуем двойной оплатой. Вместо этого сдвигаем отметку времени
черновика назад: begin_incoming уже умеет перезапускать генерацию, если
черновик висит в analyzing дольше десяти минут, и ближайший проход поллера
сделает это сам."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

OLD = '@router.post("/take")'
NEW = '''@router.post("/retry_draft")
def inbox_retry_draft(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Просит Бориса подготовить ответ заново.

    Не генерирует сама: сдвигает updated_at назад, и ближайший проход поллера
    видит черновик как «зависший» и перезапускает генерацию штатным путём."""
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        res = db.execute(text(
            "UPDATE mop_drafts SET updated_at = now() - interval '11 minutes'"
            " WHERE account_id = :a AND avito_chat_id = :c"
            "   AND status IN ('analyzing', 'new')"
            "   AND coalesce(reply_text, '') = ''"),
            {"a": req.account_id, "c": req.avito_chat_id})
        db.commit()
        if res.rowcount == 0:
            return {"status": "error",
                    "message": "Нечего повторять: ответ уже готов либо диалог не в работе"}
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=req.account_id, action="Попросил подготовить ответ заново",
                       object_kind="диалог", object_name=req.avito_chat_id,
                       reason="сотрудник нажал «Повторить» после ошибки генерации",
                       actor=ACTOR_USER, user_id=getattr(user, "id", None),
                       source="inbox.retry_draft")
        except Exception:
            pass
        return {"status": "ok",
                "message": "Борис подготовит ответ в ближайшие минуты"}
    finally:
        db.close()


@router.post("/take")'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "inbox_retry_draft" in src:
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
bak = path + ".bak_retry_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
