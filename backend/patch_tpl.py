# -*- coding: utf-8 -*-
"""Шаблоны быстрых ответов: (1) сохранение не требует account_id - ручки к аккаунту
не привязаны, ключ у них владелец; (2) общие шаблоны видит вся команда клиента,
а не каждый свои. Просмотр по умолчанию, --apply."""
import hashlib, os, sys, time

BASE = os.environ.get("BORIS_BASE", "/root/BORIS/backend/app/api")
AUTH = os.path.join(BASE, "auth.py")
TPL = os.path.join(BASE, "inbox_templates.py")
MARK_A = "BORIS_TPL_GATE"
MARK_T = "BORIS_TPL_TEAM"

A_AUTH = ('    if request.url.path in ("/api/inbox/slots", "/api/inbox/accounts"):\n'
          '        return user\n')
N_AUTH = (A_AUTH +
          '    # ' + MARK_A + ': шаблоны быстрых ответов к аккаунту НЕ привязаны -\n'
          '    # ключ у них владелец, account_id в запросе нет и сравнивать нечего.\n'
          '    # Без этого сохранение шаблона падало с 403 у всех, включая владельца.\n'
          '    if request.url.path.startswith("/api/inbox/templates"):\n'
          '        return user\n')

HELPER = '''
# ''' + MARK_T + ''': владелец шаблонов - хозяин аккаунта, а не текущий человек.
# Иначе у Сергея, Валерии и Юлии три разные базы быстрых ответов вместо одной.
def _own_id(db, user):
    from sqlalchemy import text as _t
    uid = getattr(user, "id", 0)
    row = db.execute(_t("SELECT 1 FROM accounts WHERE owner_user_id = :u LIMIT 1"),
                     {"u": uid}).fetchone()
    if row:
        return uid
    row = db.execute(_t(
        "SELECT a.owner_user_id FROM user_account_access x"
        " JOIN accounts a ON a.account_id = x.account_id"
        " WHERE x.user_id = :u AND a.owner_user_id IS NOT NULL LIMIT 1"),
        {"u": uid}).fetchone()
    return row[0] if row else uid


'''

JOBS_TPL = [
    ('@router.get("/templates/meta")\n', HELPER + '@router.get("/templates/meta")\n'),
    ('            {"own": user.id, "uid": user.id}).all()\n',
     '            {"own": _own_id(db, user), "uid": user.id}).all()\n'),
    ('            if own != user.id:\n', '            if own != _own_id(db, user):\n'),
    ('            {"own": user.id, "uid": user.id if scope == "personal" else None,',
     '            {"own": _own_id(db, user), "uid": user.id if scope == "personal" else None,'),
    ('                       {"i": req.id, "own": user.id})\n',
     '                       {"i": req.id, "own": _own_id(db, user)})\n'),
    ('                   {"p": bool(req.pinned), "i": req.id, "own": user.id})\n',
     '                   {"p": bool(req.pinned), "i": req.id, "own": _own_id(db, user)})\n'),
    ('                         {"i": req.id, "own": user.id}).first()\n',
     '                         {"i": req.id, "own": _own_id(db, user)}).first()\n'),
]

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
            print("  STOP — якорь найден %d раз(а): %s" % (n, old.strip()[:70]))
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
    ready = []
    for path, mark, pairs in [(AUTH, MARK_A, [(A_AUTH, N_AUTH)]), (TPL, MARK_T, JOBS_TPL)]:
        r = patch(path, mark, pairs)
        if r:
            ready.append((path, r[0], r[1]))
    if not do:
        print("")
        print("DRY RUN. Запись: venv/bin/python patch_tpl.py --apply")
        return
    for path, src, out in ready:
        bak = "%s.bak_tpl_%d" % (path, int(time.time()))
        open(bak, "w", encoding="utf-8").write(src)
        open(path, "w", encoding="utf-8").write(out)
        print("APPLIED %s (бэкап %s)" % (path, bak))

main()
