# -*- coding: utf-8 -*-
"""Отправка из Единого окна: гейт неоплаченной подписки и право can_reply
для сотрудника клиента. Просмотр по умолчанию, запись --apply."""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_INBOX_PATH", "/root/BORIS/backend/app/api/inbox_slots.py")
MARK = "BORIS_EMP_SEND_GUARD"

ANCHOR = (
    "        slot = _owner_scope(db, user).filter(AccountSlot.account_id == req.account_id).first()\n"
    "        if slot and _effective_status(slot) == \"readonly\":\n"
)

NEW = (
    "        # " + MARK + ": у сотрудника своих слотов нет, поэтому _owner_scope\n"
    "        # возвращал None и проверка readonly не выполнялась вовсе. Слот ищем\n"
    "        # среди уже разрешённых conn, а право отвечать проверяем явно.\n"
    "        _own = _owner_scope(db, user).filter(AccountSlot.account_id == req.account_id).first()\n"
    "        if _own is None:\n"
    "            from sqlalchemy import text as _txt_snd\n"
    "            _row = db.execute(_txt_snd(\n"
    "                \"SELECT can_reply FROM user_account_access\"\n"
    "                \" WHERE user_id = :u AND account_id = :a LIMIT 1\"),\n"
    "                {\"u\": getattr(user, \"id\", 0), \"a\": req.account_id}).fetchone()\n"
    "            if _row is not None and not _row[0]:\n"
    "                return {\"status\": \"error\", \"message\": \"Нет права отвечать в этом аккаунте\"}\n"
    "        slot = _own or db.query(AccountSlot).filter(\n"
    "            AccountSlot.account_id == req.account_id).first()\n"
    "        if slot and _effective_status(slot) == \"readonly\":\n"
)

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def main():
    do = "--apply" in sys.argv
    src = open(PATH, encoding="utf-8").read()
    print("file   : %s" % PATH)
    print("sha    : %s   строк: %d" % (sha(src), src.count("\n")))
    if MARK in src:
        print("ALREADY: маркер %s уже в файле. Выходим." % MARK)
        return 0
    n = src.count(ANCHOR)
    if n != 1:
        print("STOP   : якорь найден %d раз(а), ожидалась 1. Файл не тронут." % n)
        return 2
    out = src.replace(ANCHOR, NEW)
    try:
        compile(out, PATH, "exec")
    except SyntaxError as e:
        print("STOP   : результат не компилируется: %s" % e)
        return 3
    print("new sha: %s   строк: %d  (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    if not do:
        print("--- предпросмотр ---")
        print(NEW)
        print("DRY RUN. Запись: venv/bin/python patch_send_emp.py --apply")
        return 0
    bak = "%s.bak_send_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup : %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
