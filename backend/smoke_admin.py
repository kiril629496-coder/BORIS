import os
for _l in open('/root/BORIS/backend/.env'):
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k, _v = _l.split('=', 1); os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.api.auth import create_access_token
from app.db.session import SessionLocal
from app.api.admin_clients import ProvisionBody

c = TestClient(app)
OW = {"Authorization": "Bearer " + create_access_token(2)}
CL = {"Authorization": "Bearer " + create_access_token(31)}
EMAIL = "qa_smoke_probe@borisqa.ru"
res = []
def ok(n, cond, extra=""):
    res.append(("OK   " if cond else "FAIL ") + n + ("  " + extra if extra else ""))
try:
    fields = getattr(ProvisionBody, "model_fields", None) or ProvisionBody.__fields__
    req = [k for k, f in fields.items()
           if getattr(f, "is_required", lambda: getattr(f, "required", False))()]
    print("provision обязательные поля:", req)
except Exception as e:
    print("не удалось прочитать модель provision:", type(e).__name__)

db = SessionLocal()
before = db.execute(text("select count(*) from account_slots where owner_user_id=31")).scalar()
db.close()

r = c.get("/api/admin/clients/search", params={"q": "qa_multi"}, headers=OW)
ok("search под owner", r.status_code == 200, str(r.status_code))
r = c.get("/api/admin/clients/search", params={"q": "qa"}, headers=CL)
ok("search под клиентом 403", r.status_code == 403, str(r.status_code))
r = c.get("/api/admin/clients/card", params={"user_id": 31}, headers=OW)
ok("card под owner", r.status_code == 200, str(r.status_code))
r = c.post("/api/admin/clients/provision", json={"user_id": 31, "dry_run": True}, headers=OW)
ok("provision dry_run", r.status_code == 200, str(r.status_code) + " " + r.text[:120])

accid = None
try:
    r = c.post("/api/admin/clients/create", headers=OW, json={
        "email": EMAIL, "name": "QA Smoke", "temporary_password": "QaSmoke2026!",
        "account_name": "Смоук проба"})
    j = r.json() if r.status_code == 200 else {}
    accid = j.get("account_id")
    ok("create 200 created=true", r.status_code == 200 and j.get("created") is True, str(r.status_code))
    r2 = c.post("/api/admin/clients/create", headers=OW, json={
        "email": EMAIL, "name": "QA Smoke", "temporary_password": "QaSmoke2026!",
        "account_name": "Смоук проба"})
    ok("повтор created=false", r2.status_code == 200 and r2.json().get("created") is False, str(r2.status_code))
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

db = SessionLocal()
after = db.execute(text("select count(*) from account_slots where owner_user_id=31")).scalar()
db.close()
ok("provision ничего не записал", before == after, "слотов было %s, стало %s" % (before, after))
print("\n".join(res))
print("--- очистка выполнена ---")
