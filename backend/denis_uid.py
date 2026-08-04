# -*- coding: utf-8 -*-
"""Проверка и восстановление avito uid ТОЛЬКО для аккаунта Дениса.
Без --apply ничего не пишет. С --apply пишет лишь если пройдены ВСЕ проверки.
Идемпотентно: повторный запуск ничего не меняет."""
import sys

import httpx
from sqlalchemy import text
from app.db.session import SessionLocal
from app.reactivation_scan import prefilter
from app.reactivation_outgoing import dialog_role
from app.api.messenger import _get_user_id_and_token

ACC = "evz_denis_aleksandra_evakuatory_i_pricepy_22264"
APPLY = "--apply" in sys.argv
db = SessionLocal()
checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print("   %-4s %-52s %s" % ("OK" if ok else "СТОП", name, detail))


def mask(s, keep=4):
    s = str(s or "")
    return ("…" + s[-keep:]) if len(s) > keep else "…"


print("=== 1. ДАННЫЕ ДО ИЗМЕНЕНИЙ")
print("   account_id: %s" % ACC)
acc_uid = db.execute(text("SELECT avito_user_id FROM accounts WHERE account_id=:a"),
                     {"a": ACC}).scalar()
print("   accounts.avito_user_id:      %s" % (acc_uid or "пусто"))
slot_rows = db.execute(text(
    "SELECT id, avito_user_id FROM account_slots WHERE account_id=:a"), {"a": ACC}).fetchall()
print("   account_slots: %s" % (", ".join("id=%s uid=%s" % (r[0], r[1] or "пусто")
                                          for r in slot_rows) or "слотов нет"))

print("\n=== 2. ЗАПРОС В AVITO ЕГО ЖЕ КЛЮЧАМИ")
uid_keys, tok = _get_user_id_and_token(ACC)
print("   user_id из ключей аккаунта:  %s" % uid_keys)
print("   токен (последние 4 символа): %s" % mask(tok))
r = httpx.get("https://api.avito.ru/core/v1/accounts/self",
              headers={"Authorization": "Bearer %s" % tok}, timeout=30)
print("   HTTP: %s" % r.status_code)
data = r.json() if r.status_code == 200 else {}
real = str(data.get("id") or "")
print("   id из Avito:   %s" % (real or "-"))
print("   имя профиля:   %s" % (data.get("name") or "-"))
print("   почта профиля: %s" % mask(data.get("email"), 8))
print("   телефон:       %s" % mask(data.get("phone"), 4))

print("\n=== 3. ПРОВЕРКИ")
check("Avito вернул непустой числовой id", bool(real) and real.isdigit(), real or "-")
check("запрос выполнен ключами Дениса (id ключей = id Avito)",
      bool(real) and str(uid_keys) == real, "ключи %s / Avito %s" % (uid_keys, real or "-"))
others = [x[0] for x in db.execute(text(
    "SELECT account_id FROM accounts WHERE avito_user_id=:u AND account_id<>:a"),
    {"u": real, "a": ACC})] if real else []
others += [x[0] for x in db.execute(text(
    "SELECT account_id FROM account_slots WHERE avito_user_id=:u AND account_id<>:a"),
    {"u": real, "a": ACC})] if real else []
check("id не занят другим аккаунтом BORIS", not others, ", ".join(others) or "свободен")
conflict = [str(x) for x in ([acc_uid] + [r[1] for r in slot_rows])
            if x and real and str(x) != real]
check("нет противоречащих значений в accounts и account_slots",
      not conflict, ", ".join(conflict) or "противоречий нет")

rows = prefilter(db, ACC)
roles_now = {}
roles_after = {}
for row in rows:
    roles_now[dialog_role(db, ACC, row["avito_chat_id"])] = \
        roles_now.get(dialog_role(db, ACC, row["avito_chat_id"]), 0) + 1
    if real:
        rr = dialog_role(db, ACC, row["avito_chat_id"], own_uid=real)
        roles_after[rr] = roles_after.get(rr, 0) + 1
print("\n=== 4. ДИАЛОГИ ДЕНИСА: %d" % len(rows))
print("   роли сейчас:         %s" % roles_now)
print("   роли после подстановки: %s" % roles_after)
# Роль — ОТДЕЛЬНЫЙ вопрос от достоверности uid. Она не блокирует запись:
# в старых диалогах item_owner_id может быть пуст, и это не делает uid неверным.
print("   (справочно) роль не влияет на запись uid: %s" % roles_after)

bad = [n for n, ok, _ in checks if not ok]
print("\n=== ИТОГ: %d проверок идентичности, провалов %d" % (len(checks), len(bad)))
if bad:
    print("   СТОП, не выполнено: %s" % "; ".join(bad))
    db.close()
    sys.exit(2)

if not APPLY:
    print("   Все проверки пройдены. Это был показ, для записи запусти с --apply")
    db.close()
    sys.exit(0)

print("\n=== 5. ЗАПИСЬ")
if str(acc_uid or "") == real:
    print("   accounts: уже %s, не трогаю" % real)
else:
    db.execute(text("UPDATE accounts SET avito_user_id=:u WHERE account_id=:a"),
               {"u": real, "a": ACC})
    print("   accounts: было %s → стало %s" % (acc_uid or "пусто", real))
# Слоты не создаём: если их нет, значит аккаунт подключён без слота.
for sid, suid in slot_rows:
    if str(suid or "") == real:
        print("   слот %s: уже %s, не трогаю" % (sid, real))
    else:
        db.execute(text("UPDATE account_slots SET avito_user_id=:u WHERE id=:i"),
                   {"u": real, "i": sid})
        print("   слот %s: было %s → стало %s" % (sid, suid or "пусто", real))
db.commit()

print("\n=== 6. ПРОВЕРКА ПОСЛЕ ЗАПИСИ")
print("   accounts.avito_user_id: %s" % db.execute(text(
    "SELECT avito_user_id FROM accounts WHERE account_id=:a"), {"a": ACC}).scalar())
for r2 in db.execute(text("SELECT id, avito_user_id FROM account_slots WHERE account_id=:a"),
                     {"a": ACC}):
    print("   слот %s: %s" % (r2[0], r2[1]))
r3 = httpx.get("https://api.avito.ru/core/v1/accounts/self",
               headers={"Authorization": "Bearer %s" % tok}, timeout=30)
print("   повторный self: HTTP %s, id %s" % (r3.status_code,
                                             (r3.json() or {}).get("id") if r3.status_code == 200 else "-"))
final = {}
for row in prefilter(db, ACC):
    rr = dialog_role(db, ACC, row["avito_chat_id"])
    final[rr] = final.get(rr, 0) + 1
print("   роли диалогов Дениса: %s" % final)
db.close()
