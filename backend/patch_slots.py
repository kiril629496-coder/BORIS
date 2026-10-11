# -*- coding: utf-8 -*-
"""Счётчики инбокса у сотрудника были нулевыми: _owner_scope отбирает слоты
по владельцу, а сотрудник слотами не владеет. Добавляем запасной путь —
слоты аккаунтов, к которым у него есть доступ по user_account_access."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

OLD = '''        slots = _owner_scope(db, user).filter(
            AccountSlot.account_id.isnot(None)).order_by(AccountSlot.slot_no.asc()).all()
        acc_ids = [s.account_id for s in slots if s.account_id]'''

NEW = '''        slots = _owner_scope(db, user).filter(
            AccountSlot.account_id.isnot(None)).order_by(AccountSlot.slot_no.asc()).all()
        if not slots:
            # Сотрудник клиента слотами не владеет — они выданы владельцу.
            # Показываем слоты тех аккаунтов, к которым ему открыт доступ,
            # иначе шапка инбокса у него пустая при живых диалогах.
            _ids = [r[0] for r in db.execute(text(
                "SELECT account_id FROM user_account_access"
                " WHERE user_id = :u AND can_view = TRUE"),
                {"u": getattr(user, "id", 0)}).fetchall()]
            if _ids:
                slots = db.query(AccountSlot).filter(
                    AccountSlot.account_id.in_(_ids),
                    AccountSlot.account_id.isnot(None)).order_by(
                    AccountSlot.slot_no.asc()).all()
        acc_ids = [s.account_id for s in slots if s.account_id]'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "Сотрудник клиента слотами не владеет" in src:
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
bak = path + ".bak_slots_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
