import os, getpass
for _l in open('/root/BORIS/backend/.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1); os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
import requests
from sqlalchemy import text
from app.api.auth import create_access_token
from app.db.session import SessionLocal

EMAIL = "3411770@mail.ru"
NAME = "Бытовки СПБ"
ACC = "Аренда бытовок"

p1 = getpass.getpass("Временный пароль клиента: ")
p2 = getpass.getpass("Повторите пароль: ")
if p1 != p2:
    raise SystemExit("пароли не совпали, ничего не создано")
if len(p1) < 6:
    raise SystemExit("пароль короче 6 символов, ничего не создано")

r = requests.post("http://127.0.0.1:8000/api/admin/clients/create",
                  json={"email": EMAIL, "name": NAME,
                        "temporary_password": p1, "account_name": ACC},
                  headers={"Authorization": "Bearer " + create_access_token(2)},
                  timeout=30)
p1 = p2 = None
print("HTTP", r.status_code)
print(r.text[:300])
if r.status_code != 200:
    raise SystemExit("создание не выполнено")

db = SessionLocal()
row = db.execute(text(
    "select u.id, u.email, u.role, u.status, u.email_verified, u.email_verified_at, "
    "u.account_id, a.name, a.billing_mode, a.owner_user_id "
    "from users u left join accounts a on a.account_id = u.account_id "
    "where u.email = :e"), {"e": EMAIL}).fetchone()
print("user_id      ", row[0])
print("email        ", row[1])
print("role/status  ", row[2], "/", row[3])
print("verified     ", row[4], row[5])
print("account_id   ", row[6])
print("имя аккаунта ", row[7])
print("billing_mode ", row[8], "| owner_user_id", row[9])
print("слотов       ", db.execute(text(
    "select count(*) from account_slots where owner_user_id=:i"), {"i": row[0]}).scalar())
db.close()
