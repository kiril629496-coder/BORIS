# -*- coding: utf-8 -*-
"""П.5: «Взять диалог» — чтобы два сотрудника не отвечали одному покупателю.

Новый механизм НЕ заводим: в messenger_leads уже по одной записи на диалог и
есть UNIQUE (account_id, avito_chat_id). Добавлены две колонки, захват идёт
ОДНИМ условным UPDATE — гонка исключена на уровне базы."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

A_OLD = '        item_title = next((r[3] for r in rows if r[3]), "")'
A_NEW = '''        item_title = next((r[3] for r in rows if r[3]), "")
        # П.5: кто ведёт диалог. Живёт в messenger_leads — там по записи на диалог.
        _asg = db.execute(text(
            "SELECT l.assigned_user_id, l.assigned_at, u.email"
            "  FROM messenger_leads l LEFT JOIN users u ON u.id = l.assigned_user_id"
            " WHERE l.account_id = :a AND l.avito_chat_id = :c"),
            {"a": account_id, "c": avito_chat_id}).fetchone()'''

B_OLD = '''            "phone": "",
        }'''
B_NEW = '''            "phone": "",
            "assigned_user_id": (_asg[0] if _asg else None),
            "assigned_name": ((_asg[2] or "").split("@")[0] if _asg and _asg[2] else ""),
            "assigned_mine": bool(_asg and _asg[0] == getattr(user, "id", None)),
        }'''

C_OLD = '''class InboxSendRequest(BaseModel):'''
C_NEW = '''class InboxTakeRequest(BaseModel):
    account_id: str
    avito_chat_id: str


@router.post("/take")
def inbox_take(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Взять диалог в работу. Захват атомарный: один условный UPDATE, поэтому
    два сотрудника не могут забрать один диалог одновременно. Повторное
    нажатие тем же человеком — не ошибка."""
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        uid = getattr(user, "id", None)
        res = db.execute(text(
            "UPDATE messenger_leads SET assigned_user_id = :u, assigned_at = now()"
            " WHERE account_id = :a AND avito_chat_id = :c"
            "   AND (assigned_user_id IS NULL OR assigned_user_id = :u)"),
            {"u": uid, "a": req.account_id, "c": req.avito_chat_id})
        db.commit()
        if res.rowcount == 0:
            who = db.execute(text(
                "SELECT u.email FROM messenger_leads l"
                "  LEFT JOIN users u ON u.id = l.assigned_user_id"
                " WHERE l.account_id = :a AND l.avito_chat_id = :c"),
                {"a": req.account_id, "c": req.avito_chat_id}).fetchone()
            if who is None:
                return {"status": "error", "message": "Диалог ещё не заведён в работу"}
            return {"status": "busy",
                    "assigned_name": (who[0] or "").split("@")[0] if who[0] else "другой сотрудник",
                    "message": "Диалог уже ведёт другой сотрудник"}
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=req.account_id, action="Взял диалог в работу",
                       object_kind="диалог", object_name=req.avito_chat_id,
                       reason="сотрудник принял диалог в кабинете",
                       actor=ACTOR_USER, user_id=uid, source="inbox.take")
        except Exception:
            pass
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/release")
def inbox_release(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Освободить диалог. Может тот, кто взял, и владелец платформы."""
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        uid = getattr(user, "id", None)
        cond = "" if getattr(user, "role", "") == "owner" else " AND assigned_user_id = :u"
        res = db.execute(text(
            "UPDATE messenger_leads SET assigned_user_id = NULL, assigned_at = NULL"
            " WHERE account_id = :a AND avito_chat_id = :c" + cond),
            {"u": uid, "a": req.account_id, "c": req.avito_chat_id})
        db.commit()
        if res.rowcount == 0:
            return {"status": "error", "message": "Диалог ведёт другой сотрудник"}
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=req.account_id, action="Освободил диалог",
                       object_kind="диалог", object_name=req.avito_chat_id,
                       reason="работа над диалогом завершена",
                       actor=ACTOR_USER, user_id=uid, source="inbox.release")
        except Exception:
            pass
        return {"status": "ok"}
    finally:
        db.close()


class InboxSendRequest(BaseModel):'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "def inbox_take" in src:
    print("уже пропатчено"); sys.exit(0)
jobs = [("выборка ответственного", A_OLD, A_NEW), ("поля диалога", B_OLD, B_NEW), ("ручки", C_OLD, C_NEW)]
for nm, old, _ in jobs:
    n = src.count(old)
    print("  %-22s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src
for _, old, new in jobs:
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_assign_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
