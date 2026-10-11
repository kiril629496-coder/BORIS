# -*- coding: utf-8 -*-
"""Действия сотрудника: (1) проверка доступа в ручках черновиков МОПа,
(2) реактивация видит аккаунты по таблице связей. Просмотр по умолчанию, --apply."""
import hashlib, os, sys, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend/app/api")
MSG = os.path.join(BASE, "messenger.py")
REA = os.path.join(BASE, "reactivation.py")
MARK_MSG = "BORIS_EMP_DRAFT_GUARD"
MARK_REA = "BORIS_EMP_REACT_SCOPE"

HELPER = '''
# ''' + MARK_MSG + ''': ручки черновиков не проверяли пользователя вовсе - по номеру
# черновика можно было отправить или отклонить чужой. Проверяем владение аккаунтом
# либо связь в user_account_access.
from fastapi import Depends as _Dep_dr
from app.api.auth import get_current_user as _cu_dr


def _emp_draft_account(draft_id):
    from app.db.session import SessionLocal as _S
    from app.models.storage import Storage as _St
    db = _S()
    try:
        row = db.query(_St).filter(_St.account_id == "_telegram_drafts",
                                   _St.key == "messenger_draft:%s" % draft_id).first()
        if not row:
            return None
        return (_json.loads(row.value) or {}).get("account_id")
    except Exception:
        return None
    finally:
        db.close()


def _emp_draft_allowed(draft_id, user, need_reply=False):
    acc = _emp_draft_account(draft_id)
    if not acc:
        return True
    from app.db.session import SessionLocal as _S
    from sqlalchemy import text as _t
    db = _S()
    try:
        uid = getattr(user, "id", 0)
        if getattr(user, "role", "") == "owner":
            return True
        if db.execute(_t("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u"),
                      {"a": acc, "u": uid}).fetchone():
            return True
        col = "can_reply" if need_reply else "can_view"
        return bool(db.execute(_t("SELECT 1 FROM user_account_access WHERE user_id=:u"
                                  " AND account_id=:a AND " + col + " = TRUE"),
                               {"u": uid, "a": acc}).fetchone())
    finally:
        db.close()


'''

A_APPROVE = ('@router.post("/draft/approve")\n'
             'def approve_draft(req: DraftActionRequest):\n')
N_APPROVE = (HELPER +
             '@router.post("/draft/approve")\n'
             'def approve_draft(req: DraftActionRequest, user=_Dep_dr(_cu_dr)):\n'
             '    if not _emp_draft_allowed(req.draft_id, user, need_reply=True):\n'
             '        return {"status": "error", "message": "Черновик недоступен"}\n')

A_DISCARD = ('@router.post("/draft/discard")\n'
             'def discard_draft(req: DraftActionRequest):\n')
N_DISCARD = ('@router.post("/draft/discard")\n'
             'def discard_draft(req: DraftActionRequest, user=_Dep_dr(_cu_dr)):\n'
             '    if not _emp_draft_allowed(req.draft_id, user):\n'
             '        return {"status": "error", "message": "Черновик недоступен"}\n')

A_REA = '"SELECT account_id FROM accounts WHERE owner_user_id = :u"), {"u": uid}).fetchall()'
N_REA = ('"SELECT account_id FROM accounts WHERE owner_user_id = :u"\n'
         '            # ' + MARK_REA + ': сотрудник аккаунтами не владеет, доступ у него\n'
         '            # через таблицу связей - без этого Спящие у него пустые\n'
         '            " UNION SELECT account_id FROM user_account_access"\n'
         '            " WHERE user_id = :u AND can_view = TRUE"), {"u": uid}).fetchall()')

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def patch(path, mark, pairs):
    src = open(path, encoding="utf-8").read()
    print("")
    print("file: %s  sha %s  строк %d" % (path, sha(src), src.count("\n")))
    if mark in src:
        print("  ALREADY — маркер %s уже в файле" % mark)
        return None
    out = src
    for old, new in pairs:
        n = out.count(old)
        if n != 1:
            print("  STOP — якорь найден %d раз(а): %s" % (n, old.strip().splitlines()[0][:60]))
            return None
        out = out.replace(old, new)
    try:
        compile(out, path, "exec")
    except SyntaxError as e:
        print("  STOP — не компилируется: %s" % e)
        return None
    print("  new sha %s  строк %d (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    return src, out

def main():
    do = "--apply" in sys.argv
    jobs = [(MSG, MARK_MSG, [(A_APPROVE, N_APPROVE), (A_DISCARD, N_DISCARD)]),
            (REA, MARK_REA, [(A_REA, N_REA)])]
    ready = []
    for path, mark, pairs in jobs:
        r = patch(path, mark, pairs)
        if r:
            ready.append((path, r[0], r[1]))
    if not do:
        print("")
        print("DRY RUN. Запись: venv/bin/python patch_emp_actions.py --apply")
        return
    for path, src, out in ready:
        bak = "%s.bak_empact_%d" % (path, int(time.time()))
        open(bak, "w", encoding="utf-8").write(src)
        open(path, "w", encoding="utf-8").write(out)
        print("APPLIED %s  (бэкап %s)" % (path, bak))

main()
