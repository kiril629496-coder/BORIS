# -*- coding: utf-8 -*-
"""Подтверждение, исправление и отклонение фактов не проверяли доступ к аккаунту:
account_id приходит телом, а общий гейт тело не читает. Просмотр по умолчанию, --apply."""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_MEM", "/root/BORIS/backend/app/api/client_memory.py")
MARK = "BORIS_MEM_ACCOUNT_GUARD"

HELPER = '''
# ''' + MARK + ''': account_id у этих ручек приходит В ТЕЛЕ запроса, а
# check_account_access читает только путь, query и форму - поэтому проверки
# доступа не было вовсе. Владение аккаунтом либо связь в user_account_access.
def _mem_allowed(account_id, user):
    from sqlalchemy import text as _t
    if not account_id:
        return False
    uid = getattr(user, "id", 0)
    if getattr(user, "role", "") == "owner":
        return True
    db = SessionLocal()
    try:
        if db.execute(_t("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u"),
                      {"a": account_id, "u": uid}).fetchone():
            return True
        return bool(db.execute(_t(
            "SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a"
            " AND can_view = TRUE"), {"u": uid, "a": account_id}).fetchone())
    finally:
        db.close()


'''

GUARD = ('    if not _mem_allowed(body.account_id, user):\n'
         '        return {"status": "error", "message": "Аккаунт недоступен"}\n')

JOBS = [
    ('@router.post("/confirm")\ndef confirm(body: FactBody, user=Depends(get_current_user)):\n',
     HELPER + '@router.post("/confirm")\ndef confirm(body: FactBody, user=Depends(get_current_user)):\n' + GUARD),
    ('@router.post("/edit")\ndef edit(body: EditBody, user=Depends(get_current_user)):\n',
     '@router.post("/edit")\ndef edit(body: EditBody, user=Depends(get_current_user)):\n' + GUARD),
    ('@router.post("/reject")\ndef reject(body: FactBody, user=Depends(get_current_user)):\n',
     '@router.post("/reject")\ndef reject(body: FactBody, user=Depends(get_current_user)):\n' + GUARD),
]

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def main():
    do = "--apply" in sys.argv
    src = open(PATH, encoding="utf-8").read()
    print("file: %s  sha %s  строк %d" % (PATH, sha(src), src.count("\n")))
    if MARK in src:
        print("ALREADY — правка уже применена")
        return 0
    out = src
    for old, new in JOBS:
        n = out.count(old)
        if n != 1:
            print("STOP — якорь найден %d раз(а): %s" % (n, old.splitlines()[0]))
            return 2
        out = out.replace(old, new)
    try:
        compile(out, PATH, "exec")
    except SyntaxError as e:
        print("STOP — не компилируется: %s" % e)
        return 3
    print("new sha %s  строк %d (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    if not do:
        print("DRY RUN. Запись: venv/bin/python patch_mem_guard.py --apply")
        return 0
    bak = "%s.bak_memguard_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
