# -*- coding: utf-8 -*-
"""Мини-CRM: доступ не только владельцу, но и сотруднику по user_account_access.
Причина: _own проверяет только owner_user_id ⇒ сотрудницы получали 403."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/crm.py")

OLD = '''    row = db.execute(text("select 1 from accounts where account_id=:a and owner_user_id=:u"),
                     {"a": account_id, "u": uid}).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")'''

NEW = '''    row = db.execute(text("select 1 from accounts where account_id=:a and owner_user_id=:u"),
                     {"a": account_id, "u": uid}).fetchone()
    if not row:
        # Сотрудник аккаунтом не владеет — право даёт связь в user_account_access.
        # Владение проверено выше и имеет приоритет.
        row = db.execute(text(
            "select 1 from user_account_access"
            " where user_id=:u and account_id=:a and can_view = TRUE limit 1"),
            {"a": account_id, "u": uid}).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "user_account_access" in src:
    print("уже пропатчено"); sys.exit(0)
n = src.count(OLD)
print("  якорь встречается:", n)
if n != 1:
    print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(OLD, NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_crm_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
