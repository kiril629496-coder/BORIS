# -*- coding: utf-8 -*-
"""Доступ сотрудника к диалогам Единого окна.
Правка одна: _connected_account_ids при пустом владении слотами добирает
аккаунты из user_account_access (can_view). Просмотр по умолчанию, запись --apply.
"""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_INBOX_PATH", "/root/BORIS/backend/app/api/inbox_slots.py")
MARK = "BORIS_EMP_INBOX_FALLBACK"

ANCHOR = (
    "def _connected_account_ids(db, user):\n"
    "    rows = _owner_scope(db, user).filter(AccountSlot.account_id.isnot(None)).all()\n"
    "    out = []\n"
)

NEW = (
    "def _connected_account_ids(db, user):\n"
    "    rows = _owner_scope(db, user).filter(AccountSlot.account_id.isnot(None)).all()\n"
    "    if not rows:\n"
    "        # " + MARK + ": сотрудник клиента слотами не владеет - они выданы\n"
    "        # владельцу. Берём слоты тех аккаунтов, к которым ему явно открыт\n"
    "        # доступ в user_account_access, иначе окно сообщений у него пустое.\n"
    "        from sqlalchemy import text as _txt_emp\n"
    "        _uid = getattr(user, \"id\", 0)\n"
    "        _ids = [r[0] for r in db.execute(_txt_emp(\n"
    "            \"SELECT account_id FROM user_account_access\"\n"
    "            \" WHERE user_id = :u AND can_view = TRUE\"),\n"
    "            {\"u\": _uid}).fetchall() if r[0]]\n"
    "        if _ids:\n"
    "            rows = db.query(AccountSlot).filter(\n"
    "                AccountSlot.account_id.in_(_ids),\n"
    "                AccountSlot.account_id.isnot(None)).all()\n"
    "    out = []\n"
)

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def main():
    do = "--apply" in sys.argv
    src = open(PATH, encoding="utf-8").read()
    print("file   : %s" % PATH)
    print("sha    : %s   строк: %d" % (sha(src), src.count("\n")))
    if MARK in src:
        print("ALREADY: маркер %s уже в файле, правка применена ранее. Выходим." % MARK)
        return 0
    n = src.count(ANCHOR)
    if n != 1:
        print("STOP   : якорь найден %d раз(а), ожидалась ровно 1. Файл не тронут." % n)
        return 2
    out = src.replace(ANCHOR, NEW)
    try:
        compile(out, PATH, "exec")
    except SyntaxError as e:
        print("STOP   : результат не компилируется: %s" % e)
        return 3
    print("new sha: %s   строк: %d  (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    if not do:
        print("--- предпросмотр вставки ---")
        print(NEW)
        print("DRY RUN. Для записи: python patch_inbox_emp.py --apply")
        return 0
    bak = "%s.bak_emp_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup : %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
