from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.api.auth import create_access_token
from app.db.session import SessionLocal
from app.models.user import User
from app.models.account import Account
import app.api.messenger as _M

EMAIL = "qa_reuse_probe@borisqa.ru"
PWD = "QaReuse2026!"
URL = "/api/admin/clients/create"
A1, A2 = "qa_reuse_penza", "qa_reuse_moskva"
ALIEN = "qa_reuse_alien"
FAKE = {A1: "111000111", A2: "222000222", ALIEN: "333000333"}

c = TestClient(app)
OW = {"Authorization": "Bearer " + create_access_token(2)}
res = []
def ok(n, cond, note=""):
    res.append((("OK   " if cond else "FAIL ") + n + ("  " + note if note else "")))

_orig = _M._get_user_id_and_token
_M._get_user_id_and_token = lambda a: (FAKE.get(a, "999999999"), "faketoken")

def snap(db):
    return (db.execute(text("select count(*) from tg_routes")).scalar(),
            db.execute(text("select count(*) from mop_modes")).scalar())

def owners(db):
    return {r[0]: r[1] for r in db.execute(text(
        "select account_id, owner_user_id from accounts where account_id in (:a,:b)"),
        {"a": A1, "b": A2})}

db = SessionLocal()
before = snap(db)
try:
    for aid, own, nm in ((A1, 2, "QA Пенза"), (A2, 2, "QA Москва"), (ALIEN, 7, "QA Чужой")):
        db.execute(text("insert into accounts (account_id, name, owner_user_id, billing_mode,"
                        " created_at) values (:a,:n,:o,'manual', now())"),
                   {"a": aid, "n": nm, "o": own})
    db.commit()
    has_src = True
    try:
        db.execute(text("insert into client_sources (owner_user_id, account_id)"
                        " values (2, :a)"), {"a": A1})
        db.commit()
    except Exception:
        db.rollback(); has_src = False
    db.close()

    B = {"email": EMAIL, "name": "QA Reuse", "temporary_password": PWD}

    r = c.post(URL, json=dict(B, account_name="", reuse_account_ids=[A1, A1]), headers=OW)
    ok("11 повтор в списке -> 422", r.status_code == 422, str(r.status_code))

    r = c.post(URL, json=dict(B, account_name="", reuse_account_ids=[A1, "нет_такого"]), headers=OW)
    ok("9  несуществующий -> 409", r.status_code == 409, str(r.status_code))

    r = c.post(URL, json=dict(B, account_name="", reuse_account_ids=[A1, ALIEN]), headers=OW)
    ok("10 чужой аккаунт -> 409", r.status_code == 409, str(r.status_code))

    db = SessionLocal()
    ok("13 после 409 пользователя нет",
       db.query(User).filter(User.email == EMAIL).count() == 0)
    ok("13 после 409 владельцы прежние", owners(db) == {A1: 2, A2: 2})
    db.close()

    r = c.post(URL, json=dict(B, account_name="", reuse_account_ids=[A1, A2]), headers=OW)
    j = r.json() if r.status_code == 200 else {}
    ok("2  режим 2 создал", r.status_code == 200 and j.get("created") is True, r.text[:90])
    uid = j.get("user_id")

    db = SessionLocal()
    u = db.query(User).filter(User.email == EMAIL).first()
    accs = db.query(Account).filter(Account.owner_user_id == (uid or -1)).all()
    ok("2  нового Account нет, ровно 2", len(accs) == 2, str([a.account_id for a in accs]))
    ok("3  основной = первый (Пенза)", u is not None and u.account_id == A1)
    ok("4  оба owner_user_id сменились", owners(db) == {A1: uid, A2: uid})
    slots = list(db.execute(text(
        "select slot_no, status, account_id, cast(avito_user_id as text)"
        " from account_slots where owner_user_id=:o order by slot_no"), {"o": uid}))
    ok("5  два слота connected", len(slots) == 2 and all(s[1] == "connected" for s in slots))
    ok("5  верные avito_user_id",
       [s[3] for s in slots] == [FAKE[A1], FAKE[A2]], str(slots))
    if has_src:
        n = db.execute(text("select count(*) from client_sources where account_id=:a"
                            " and owner_user_id=:o"), {"a": A1, "o": uid}).scalar()
        ok("6  client_sources обновлены", n == 1)
    else:
        res.append("SKIP 6  client_sources — фикстуру вставить не удалось")
    bm = [r0[0] for r0 in db.execute(text("select billing_mode from accounts"
                                          " where account_id in (:a,:b)"), {"a": A1, "b": A2})]
    ok("7  billing_mode manual", bm == ["manual", "manual"], str(bm))
    ok("8  триал не начислен",
       getattr(u, "trial_started_at", None) is None
       and getattr(u, "subscription_expires_at", None) is None)
    ok("14 tg_routes и mop_modes неизменны", snap(db) == before, str(snap(db)))
    db.close()

    r = c.post(URL, json=dict(B, account_name="", reuse_account_ids=[A1, A2]), headers=OW)
    ok("12 повтор email -> created=false",
       r.status_code == 200 and r.json().get("created") is False, str(r.status_code))
    db = SessionLocal()
    ok("12 вторичной передачи нет", owners(db) == {A1: uid, A2: uid})
    db.close()

    r = c.post(URL, json={"email": "qa_reuse_mode1@borisqa.ru", "name": "QA Mode1",
                          "temporary_password": PWD, "account_name": "QA режим 1"},
               headers=OW)
    j1 = r.json() if r.status_code == 200 else {}
    ok("1  режим 1 работает как раньше",
       r.status_code == 200 and j1.get("created") is True and j1.get("account_id"),
       r.text[:90])
    db = SessionLocal()
    u1 = db.query(User).filter(User.email == "qa_reuse_mode1@borisqa.ru").first()
    n1 = db.query(Account).filter(Account.owner_user_id == (u1.id if u1 else -1)).count()
    ok("1  режим 1 создал ровно один Account", n1 == 1, str(n1))
    db.close()
finally:
    _M._get_user_id_and_token = _orig
    db = SessionLocal()
    try:
        for e in (EMAIL, "qa_reuse_mode1@borisqa.ru"):
            uu = db.query(User).filter(User.email == e).first()
            if uu:
                db.execute(text("delete from account_slots where owner_user_id=:o"), {"o": uu.id})
                db.execute(text("delete from accounts where owner_user_id=:o and"
                                " account_id not in (:a,:b)"), {"o": uu.id, "a": A1, "b": A2})
        db.execute(text("delete from users where email in (:e1,:e2)"),
                   {"e1": EMAIL, "e2": "qa_reuse_mode1@borisqa.ru"})
        db.execute(text("delete from client_sources where account_id in (:a,:b,:c)"),
                   {"a": A1, "b": A2, "c": ALIEN})
        db.execute(text("delete from accounts where account_id in (:a,:b,:c)"),
                   {"a": A1, "b": A2, "c": ALIEN})
        db.commit()
        print("очистка выполнена")
    except Exception as ex:
        db.rollback(); print("ОШИБКА ОЧИСТКИ:", str(ex)[:150])
    finally:
        db.close()

print()
for line in res:
    print(" ", line)
print()
print("ИТОГ: OK %d | FAIL %d | SKIP %d" %
      (sum(1 for x in res if x.startswith("OK")),
       sum(1 for x in res if x.startswith("FAIL")),
       sum(1 for x in res if x.startswith("SKIP"))))
