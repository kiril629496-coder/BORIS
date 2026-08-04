# -*- coding: utf-8 -*-
"""Заполнение avito uid у аккаунтов, где он пуст. Без --apply только показывает.
Роль диалога без uid определить нельзя, поэтому такие аккаунты выпадают из работы."""
import sys

import httpx
from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_outgoing import _accounts_uid_column, own_avito_uid

APPLY = "--apply" in sys.argv
db = SessionLocal()
col = _accounts_uid_column(db)
print("колонка uid в accounts:", col)

print("\n=== ТЕКУЩЕЕ СОСТОЯНИЕ")
rows = db.execute(text(
    "SELECT account_id, %s FROM accounts ORDER BY account_id" % (col or "NULL"))).fetchall()
slots = {r[0]: r[1] for r in db.execute(text(
    "SELECT account_id, avito_user_id FROM account_slots WHERE avito_user_id IS NOT NULL"))}
missing = []
for acc, uid in rows:
    src = "accounts" if uid else ("account_slots" if slots.get(acc) else "НЕТ")
    eff = uid or slots.get(acc)
    print("   %-46s %-12s %s" % (acc, str(eff or "-"), src))
    if not eff:
        missing.append(acc)

print("\n=== БЕЗ uid: %d" % len(missing))
if not missing:
    print("   нечего заполнять")
    db.close()
    sys.exit(0)

from app.api.messenger import _get_user_id_and_token
for acc in missing:
    try:
        uid, tok = _get_user_id_and_token(acc)
    except Exception as e:
        print("   %-46s ключей нет: %s" % (acc, str(e)[:50]))
        continue
    real = None
    try:
        r = httpx.get("https://api.avito.ru/core/v1/accounts/self",
                      headers={"Authorization": "Bearer %s" % tok}, timeout=30)
        if r.status_code == 200:
            real = str((r.json() or {}).get("id") or "")
        else:
            print("   %-46s HTTP %s" % (acc, r.status_code))
    except Exception as e:
        print("   %-46s ошибка запроса: %s" % (acc, str(e)[:50]))
    print("   %-46s из ключей %-12s из Avito %s" % (acc, str(uid), str(real or "-")))
    value = real or (str(uid) if uid else None)
    if value and APPLY and col:
        db.execute(text("UPDATE accounts SET %s=:v WHERE account_id=:a" % col),
                   {"v": value, "a": acc})
        db.commit()
        print("   %-46s ЗАПИСАНО %s" % (acc, value))

if not APPLY:
    print("\nЭто был показ. Для записи запусти с --apply")
else:
    print("\n=== ПРОВЕРКА ПОСЛЕ ЗАПИСИ")
    for acc in missing:
        print("   %-46s %s" % (acc, own_avito_uid(db, acc)))
db.close()
