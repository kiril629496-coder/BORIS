import getpass, sys
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.api.auth import create_access_token
from app.db.session import SessionLocal
from app.models.user import User
from app.models.account import Account
import app.api.messenger as _M

OWNER_ID = 2
PENZA = "andrey_launzh_mebel_akkaunt_penza_82435"
MOSCOW = "andrey_mebel_launzh_moskva_69737"
URL = "/api/admin/clients/create"
c = TestClient(app)
OW = {"Authorization": "Bearer " + create_access_token(OWNER_ID)}

def q(sql, p=None):
    db = SessionLocal()
    try:
        return list(db.execute(text(sql), p or {}))
    finally:
        db.close()

def state(tag):
    print("\n--- %s ---" % tag)
    for r in q("select account_id, owner_user_id, billing_mode from accounts"
               " where account_id in (:a,:b)", {"a": PENZA, "b": MOSCOW}):
        print("   accounts:", tuple(r))
    print("   tg_routes:",
          q("select count(*) from tg_routes")[0][0],
          "| mop_modes:", [tuple(x) for x in q("select account_id, contour from mop_modes")])

# ---------------- РЕПЕТИЦИЯ ----------------
def rehearsal():
    A, B = "qa_dry_penza", "qa_dry_moskva"
    E = "qa_dry_reuse@borisqa.ru"
    orig = _M._get_user_id_and_token
    _M._get_user_id_and_token = lambda a: ("111000111" if a == A else "222000222", "tok")
    db = SessionLocal()
    try:
        for aid, nm in ((A, "QA сухой прогон Пенза"), (B, "QA сухой прогон Москва")):
            db.execute(text("insert into accounts (account_id, name, owner_user_id,"
                            " billing_mode, created_at)"
                            " values (:a,:n,:o,'manual', now())"),
                       {"a": aid, "n": nm, "o": OWNER_ID})
        db.commit()
    finally:
        db.close()
    try:
        r = c.post(URL, json={"email": E, "name": "QA сухой", "temporary_password": "QaDry2026!",
                              "account_name": "", "reuse_account_ids": [A, B]}, headers=OW)
        j = r.json() if r.status_code == 200 else {}
        uid = j.get("user_id")
        okc = r.status_code == 200 and j.get("created") is True
        print("  создание:", "OK" if okc else "FAIL " + r.text[:120])
        if uid:
            n_acc = q("select count(*) from accounts where owner_user_id=:o", {"o": uid})[0][0]
            sl = q("select slot_no, status, account_id, cast(avito_user_id as text)"
                   " from account_slots where owner_user_id=:o order by slot_no", {"o": uid})
            u = q("select account_id from users where id=:i", {"i": uid})[0][0]
            print("  аккаунтов у нового:", n_acc, "(ждём 2, третьего нет)")
            print("  слоты:", [tuple(x) for x in sl])
            print("  users.account_id:", u, "(ждём %s)" % A)
            return okc and n_acc == 2 and len(sl) == 2 and u == A
        return False
    finally:
        _M._get_user_id_and_token = orig
        db = SessionLocal()
        try:
            uu = db.query(User).filter(User.email == E).first()
            if uu:
                db.execute(text("delete from account_slots where owner_user_id=:o"), {"o": uu.id})
            db.execute(text("delete from users where email=:e"), {"e": E})
            db.execute(text("delete from client_sources where account_id in (:a,:b)"),
                       {"a": A, "b": B})
            db.execute(text("delete from accounts where account_id in (:a,:b)"), {"a": A, "b": B})
            db.commit()
            print("  репетиционные данные удалены")
        except Exception as ex:
            db.rollback(); print("  ОШИБКА ОЧИСТКИ:", str(ex)[:150])
        finally:
            db.close()

print("=" * 62)
print("РЕПЕТИЦИЯ РЕЖИМА 2 (на временных аккаунтах)")
print("=" * 62)
if not rehearsal():
    print("\nСТОП: репетиция не прошла. Реальный вызов не выполняется.")
    sys.exit(1)
print("\nРЕПЕТИЦИЯ ПРОЙДЕНА")

state("СОСТОЯНИЕ ДО")

print("\n" + "=" * 62)
print("РЕАЛЬНОЕ СОЗДАНИЕ")
print("=" * 62)
email = input("Email Андрея: ").strip()
if "@" not in email:
    sys.exit("СТОП: не похоже на email")
pwd = getpass.getpass("Временный пароль: ")
pwd2 = getpass.getpass("Повторите: ")
if pwd != pwd2:
    sys.exit("СТОП: пароли не совпали")
if len(pwd) < 6:
    sys.exit("СТОП: минимум 6 символов")
print("пароль принят: %d символов, %d байт (лимит bcrypt 72)"
      % (len(pwd), len(pwd.encode())))
print("\nБудет выполнено:")
print("  создать пользователя", email)
print("  передать ему", PENZA, "(основной) и", MOSCOW)
print("  создать два слота connected с avito_user_id 421099186 / 365558304")
if input('\nНапишите ДА для выполнения: ').strip().upper() != "ДА":
    sys.exit("отменено, ничего не изменено")

r = c.post(URL, json={"email": email, "name": "Андрей — Лаунж Мебель",
                      "temporary_password": pwd, "account_name": "",
                      "reuse_account_ids": [PENZA, MOSCOW]}, headers=OW)
print("\nHTTP", r.status_code)
print(r.text[:400] if pwd not in r.text else "(в ответе обнаружен пароль — не печатаю)")
if r.status_code != 200:
    sys.exit("СТОП: изменений нет")
uid = r.json().get("user_id")

print("\n" + "=" * 62)
print("ПРОВЕРКА ПОСЛЕ")
print("=" * 62)
print("  user_id:", uid)
for r0 in q("select id, email, role, status, email_verified,"
            " account_id, trial_started_at, subscription_expires_at"
            " from users where id=:i", {"i": uid}):
    print("  users:", tuple(r0))
state("АККАУНТЫ И КОНТУР")
print("  слоты:", [tuple(x) for x in q(
    "select slot_no, status, account_id, cast(avito_user_id as text), account_name"
    " from account_slots where owner_user_id=:o order by slot_no", {"o": uid})])
print("  аккаунтов у Андрея:", q("select count(*) from accounts where owner_user_id=:o",
                                 {"o": uid})[0][0], "(ждём ровно 2)")
print("  осталось у владельца:", q("select count(*) from accounts where owner_user_id=2")[0][0])
print("  client_sources Андрея:", q("select count(*) from client_sources"
                                    " where owner_user_id=:o", {"o": uid})[0][0])
print("  баннеры:", [tuple(x) for x in q(
    "select account_id, count(*) from banners where account_id in (:a,:b)"
    " group by 1", {"a": PENZA, "b": MOSCOW})])
print("  задачи:", [tuple(x) for x in q(
    "select account_id, count(*) from tasks where account_id in (:a,:b)"
    " group by 1", {"a": PENZA, "b": MOSCOW})])
print("\nГОТОВО. Вход: https://boris-ai.pro/login")
