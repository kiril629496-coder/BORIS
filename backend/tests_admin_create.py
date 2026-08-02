import os
for _l in open('/root/BORIS/backend/.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1); os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.api.auth import create_access_token, verify_password
from app.db.session import SessionLocal
from app.models.user import User
from app.models.account import Account

EMAIL = "qa_bytovki_probe@borisqa.ru"; PWD = "QaProbe2026!"
URL = "/api/admin/clients/create"
BODY = {"email": EMAIL, "name": "QA Пробный", "temporary_password": PWD,
        "account_name": "Бытовки — аренда (проба)"}
c = TestClient(app)
OW = {"Authorization": "Bearer " + create_access_token(2)}
CL = {"Authorization": "Bearer " + create_access_token(31)}
res = []
def ok(n, cond): res.append(("OK   " if cond else "FAIL ") + n)
uid = None; accid = None
try:
    r = c.post(URL, json=BODY, headers=CL); ok("2  не-owner 403", r.status_code == 403)
    r = c.post(URL, json=dict(BODY, temporary_password="123"), headers=OW); ok("24 короткий пароль", r.status_code in (400, 422))
    r = c.post(URL, json=dict(BODY, email="не-почта"), headers=OW); ok("24 невалидный email", r.status_code == 422)
    r = c.post(URL, json=dict(BODY, name="  "), headers=OW); ok("25 пустое имя", r.status_code in (400, 422))
    r = c.post(URL, json=dict(BODY, account_name=" "), headers=OW); ok("25 пустое имя аккаунта", r.status_code in (400, 422))
    r = c.post(URL, json=BODY, headers=OW); j = r.json()
    ok("1  owner создаёт", r.status_code == 200 and j.get("created") is True)
    uid = j.get("user_id"); accid = j.get("account_id")
    ok("14 пароля нет в ответе", PWD not in r.text)
    db = SessionLocal()
    us = db.query(User).filter(User.email == EMAIL).all(); ok("3  ровно один User", len(us) == 1)
    u = us[0]
    accs = db.query(Account).filter(Account.owner_user_id == u.id).all(); ok("4  ровно один Account", len(accs) == 1)
    a = accs[0]
    ok("5  users.account_id корректен", u.account_id == a.account_id == accid)
    ok("6  owner_user_id корректен", a.owner_user_id == u.id)
    ok("7  billing_mode manual", (a.billing_mode or "") == "manual")
    ok("8  status active", (getattr(u, "status", "") or "") == "active")
    ok("9  email_verified True", bool(getattr(u, "email_verified", False)))
    ok("10 email_verified_at заполнен", getattr(u, "email_verified_at", None) is not None)
    ok("11 subscription_expires_at NULL", getattr(u, "subscription_expires_at", None) is None)
    ok("12 trial_started_at NULL", getattr(u, "trial_started_at", None) is None)
    ok("13 пароль проходит verify_password", verify_password(PWD, u.password_hash))
    n = db.execute(text("select count(*) from account_slots where owner_user_id=:i"), {"i": u.id}).scalar()
    ok("16 слоты не созданы", n == 0)
    try:
        b = db.execute(text("select count(*) from storage where account_id=:a and key='billing'"), {"a": accid}).scalar()
        ok("17/18 МОП и РОП не начислены", b == 0)
        aud = db.execute(text("select value from storage where account_id=:a and key='audit_log'"), {"a": accid}).scalar()
        ok("15 пароля и хеша нет в audit", aud is None or (PWD not in str(aud) and u.password_hash not in str(aud)))
        ok("   audit содержит формулировку", aud is not None and "без публичного email-подтверждения" in str(aud))
    except Exception as e:
        ok("проверка storage: " + type(e).__name__, False)
    db.close()
    r2 = c.post(URL, json=dict(BODY, temporary_password="СовсемДругойПароль"), headers=OW); j2 = r2.json()
    ok("19 повтор created=false", r2.status_code == 200 and j2.get("created") is False)
    db = SessionLocal()
    ok("20 повтор без дублей", db.query(User).filter(User.email == EMAIL).count() == 1)
    u2 = db.query(User).filter(User.email == EMAIL).first()
    ok("21 повтор не сменил пароль", verify_password(PWD, u2.password_hash))
    db.close()
    r3 = c.post(URL, json=dict(BODY, email="ostapenko-kirill-86@yandex.ru"), headers=OW)
    ok("22 email владельца 409", r3.status_code == 409)
    r4 = c.post(URL, json=dict(BODY, email="qa_client@borisqa.ru"), headers=OW)
    ok("23 битая связка 409", r4.status_code == 409)
finally:
    db = SessionLocal()
    try:
        if accid:
            db.execute(text("delete from storage where account_id=:a"), {"a": accid})
            db.execute(text("delete from accounts where account_id=:a"), {"a": accid})
        db.execute(text("delete from users where email=:e"), {"e": EMAIL})
        db.commit()
    finally:
        db.close()
    print("\n".join(res))
    print("--- очистка: тестовые записи удалены ---")
