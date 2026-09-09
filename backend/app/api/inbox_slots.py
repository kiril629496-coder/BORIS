"""Слоты аккаунтов «Единого центра сообщений».

Оплачиваются слоты, а не подключённые аккаунты. Ключи Avito пишутся в
существующую таблицу accounts (avito_client_id/secret/avito_user_id),
дублирующего хранилища креденшлов не заводим.
"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import text, bindparam

from app.db.session import SessionLocal
from app.models.account import Account
from app.models.account_slot import AccountSlot
from app.models.storage import Storage
import json
from app import inbox_pricing

try:
    from app.api.auth import get_current_user
except ImportError:  # на случай другой раскладки
    from app.auth import get_current_user

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

TOKEN_URL = "https://api.avito.ru/token/"
SELF_URL = "https://api.avito.ru/core/v1/accounts/self"
# read-only пробы тарифа: какая ответит — та и рабочая, результат пишем в слот
TARIFF_PROBES = (
    "https://api.avito.ru/autoload/v2/profile",
    "https://api.avito.ru/autoload/v1/profile",
)


# ---------------------------------------------------------------- http
def _http(method, url, headers=None, data=None, timeout=20):
    """(status, text). Работает и через requests, и через stdlib."""
    try:
        import requests
        try:
            r = requests.request(method, url, headers=headers or {},
                                 data=data, timeout=timeout)
            return r.status_code, r.text
        except Exception as e:
            return 0, str(e)
    except ImportError:
        import urllib.request, urllib.parse, urllib.error
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers or {},
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, str(e)


def verify_avito_keys(client_id, client_secret):
    """Три реальные проверки. Ничего не выдумываем: что Avito ответил,
    то и записываем в слот."""
    out = {"ok": False, "token": False, "avito_user_id": "", "name": "",
           "phone": "", "tariff_ok": None, "message": "", "raw": ""}

    st, body = _http("POST", TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": (client_id or "").strip(),
        "client_secret": (client_secret or "").strip()})
    m = re.search(r'"access_token"\s*:\s*"([^"]+)"', body or "")
    if st != 200 or not m:
        d = re.search(r'"error_description"\s*:\s*"([^"]+)"', body or "")
        e = re.search(r'"error"\s*:\s*"([^"]+)"', body or "")
        desc = d.group(1) if d else (e.group(1) if e else "")
        out["message"] = ("Avito не принял Client ID и Secret" +
                          (": " + desc if desc
                           else ". Проверьте, что ключи скопированы целиком и без пробелов."))
        out["raw"] = "token %s: %s" % (st, str(body)[:300])
        return out
    token = m.group(1)
    out["token"] = True
    head = {"Authorization": "Bearer " + token}

    st, body = _http("GET", SELF_URL, headers=head)
    if st != 200:
        out["message"] = ("Ключи приняты, но аккаунт не опознан "
                          "(ответ Avito %s). Обычно это значит, что у ключей "
                          "нет доступа к аккаунту." % st)
        out["raw"] = "self %s: %s" % (st, str(body)[:300])
        return out
    def _pick(field):
        mm = re.search(r'"%s"\s*:\s*"?([^",}]+)' % field, body or "")
        return (mm.group(1).strip() if mm else "")
    out["avito_user_id"] = _pick("id")
    out["name"] = _pick("name")
    out["phone"] = _pick("phone")
    out["ok"] = True

    probe_results = []
    for url in TARIFF_PROBES:
        st, body = _http("GET", url, headers=head)
        probe_results.append((url, st, str(body or "")[:300]))
        if st == 200:
            out["tariff_ok"] = True
            out["raw"] = "tariff %s %s: ok" % (url, st)
            break

    if out["tariff_ok"] is not True:
        # Важно: 401/403 у /autoload/*/profile не доказывает отсутствие
        # автозагрузки. Avito может запрещать сам API-метод профиля даже при
        # включённой автозагрузке в кабинете. Поэтому generic 401/403 =
        # «не удалось проверить автоматически», а не ложный tariff_error.
        joined = " | ".join("tariff %s %s: %s" % x for x in probe_results)
        out["raw"] = joined[:900]
        explicit_denial = any(
            st in (401, 403) and any(marker in body.lower() for marker in (
                "тариф", "автозагруз", "autoload access denied", "subscription required"
            ))
            for _, st, body in probe_results
        )
        if explicit_denial:
            out["tariff_ok"] = False
            out["message"] = "Avito явно сообщил, что доступ к автозагрузке для аккаунта отсутствует."
        else:
            out["tariff_ok"] = None
            out["message"] = ("API подключён. Статус автозагрузки не удалось подтвердить через API Avito; "
                              "это не означает, что автозагрузка выключена.")
    if not out["message"]:
        out["message"] = "Аккаунт подключён."
    return out


# ---------------------------------------------------------------- utils
def _now():
    return datetime.now(timezone.utc)


def _expired(dt):
    """Сравнение, устойчивое к naive-датам от драйвера."""
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < _now()


def _slugify(value, fallback):
    try:
        from app.api.accounts import _slugify as project_slugify
        s = project_slugify(value)
        if s:
            return s
    except Exception:
        pass
    table = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh",
             "з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o",
             "п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"c",
             "ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e",
             "ю":"yu","я":"ya"}
    s = "".join(table.get(ch, ch) for ch in (value or "").lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or fallback


def _effective_status(slot):
    if slot.status in ("released",):
        return slot.status
    if _expired(slot.paid_until):
        return "readonly"
    return slot.status


def _counts(db, account_ids):
    """Диалоги и неотвеченные по каждому аккаунту. Чистый SQL, без ИИ."""
    res = {}
    if not account_ids:
        return res
    ids = [a for a in account_ids if a]
    if not ids:
        return res
    q1 = text("SELECT account_id, count(DISTINCT avito_chat_id) "
              "  FROM messenger_messages WHERE account_id IN :ids GROUP BY 1"
              ).bindparams(bindparam("ids", expanding=True))
    rows = db.execute(q1, {"ids": ids}).all()
    for acc, n in rows:
        res.setdefault(acc, {})["dialogs"] = int(n or 0)
    q2 = text("SELECT account_id, count(*) FROM ("
              "  SELECT DISTINCT ON (account_id, avito_chat_id) account_id, direction"
              "    FROM messenger_messages WHERE account_id IN :ids"
              "      AND (item_owner_id IS NULL OR item_owner_id = COALESCE((SELECT avito_user_id FROM account_slots WHERE account_id = messenger_messages.account_id LIMIT 1),(SELECT avito_user_id FROM accounts WHERE account_id = messenger_messages.account_id LIMIT 1)))"
              "   ORDER BY account_id, avito_chat_id, avito_created_at DESC) t"
              " WHERE lower(coalesce(direction,'')) LIKE 'in%' GROUP BY 1"
              ).bindparams(bindparam("ids", expanding=True))
    rows = db.execute(q2, {"ids": ids}).all()
    for acc, n in rows:
        res.setdefault(acc, {})["unanswered"] = int(n or 0)
    return res


def _slot_dict(slot, counts):
    c = counts.get(slot.account_id or "", {})
    return {
        "id": slot.id,
        "slot_no": slot.slot_no,
        "status": _effective_status(slot),
        "raw_status": slot.status,
        "account_id": slot.account_id or "",
        "account_name": slot.account_name or "",
        "avito_user_id": slot.avito_user_id or "",
        "display_phone": slot.display_phone or "",
        "tariff_ok": slot.tariff_ok,
        "last_check_at": slot.last_check_at.isoformat() if slot.last_check_at else "",
        "last_check_result": slot.last_check_result or "",
        "paid_until": slot.paid_until.isoformat() if slot.paid_until else "",
        "price_rub": slot.price_rub or 0,
        "dialogs": c.get("dialogs", 0),
        "unanswered": c.get("unanswered", 0),
    }


def _owner_scope(db, user):
    q = db.query(AccountSlot).filter(AccountSlot.product == inbox_pricing.PRODUCT)
    if getattr(user, "role", "") == "owner":
        return q
    return q.filter(AccountSlot.owner_user_id == user.id)


# ---------------------------------------------------------------- schemas
class GrantRequest(BaseModel):
    owner_user_id: int
    count: int = 1
    days: int = 30
    payment_id: str = ""


class ConnectRequest(BaseModel):
    slot_id: int
    avito_client_id: str
    avito_client_secret: str
    display_phone: str = ""
    account_name: str = ""


class PhoneRequest(BaseModel):
    slot_id: int
    display_phone: str


class CheckRequest(BaseModel):
    slot_id: int = 0


# ---------------------------------------------------------------- endpoints
@router.get("/pricing")
def pricing(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        owned = _owner_scope(db, user).filter(
            AccountSlot.status != "released").count()
        return {"status": "ok",
                "table": inbox_pricing.price_table(),
                "owned": owned,
                "total_now": inbox_pricing.slots_total(owned),
                "add_one": inbox_pricing.add_slots_price(owned, 1)}
    finally:
        db.close()


@router.get("/slots")
def slots(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        rows = _owner_scope(db, user).order_by(AccountSlot.slot_no.asc()).all()
        counts = _counts(db, [r.account_id for r in rows if r.account_id])
        data = [_slot_dict(r, counts) for r in rows]
        return {"status": "ok", "slots": data,
                "connected": sum(1 for d in data if d["status"] == "connected"),
                "total": len(data)}
    finally:
        db.close()


@router.post("/slots/grant")
def grant(req: GrantRequest, user=Depends(get_current_user)):
    """Создать оплаченные слоты. Вызывается после подтверждения платежа."""
    if getattr(user, "role", "") != "owner":
        raise HTTPException(status_code=403, detail="Только владелец")
    if req.count < 1 or req.count > 50:
        raise HTTPException(status_code=400, detail="count вне диапазона 1..50")
    db = SessionLocal()
    try:
        existing = db.query(AccountSlot).filter(
            AccountSlot.owner_user_id == req.owner_user_id,
            AccountSlot.product == inbox_pricing.PRODUCT).count()
        start, until = _now(), _now() + timedelta(days=max(req.days, 1))
        price = inbox_pricing.add_slots_price(existing, req.count)
        created = []
        for i in range(req.count):
            slot = AccountSlot(
                owner_user_id=req.owner_user_id,
                product=inbox_pricing.PRODUCT,
                slot_no=existing + i + 1,
                status="paid_empty",
                period_start=start, paid_until=until,
                price_rub=int(price / req.count) if req.count else price,
                payment_id=req.payment_id or None)
            db.add(slot)
            created.append(slot)
        db.commit()
        return {"status": "ok", "created": len(created),
                "slots_total": existing + req.count,
                "charged": price,
                "monthly_after": inbox_pricing.slots_total(existing + req.count)}
    finally:
        db.close()


def refresh_avito_slot_capability(account_id: str):
    """AUTOLOAD_CAPABILITY_REFRESH_V1: read-only self-heal of stale Autoload evidence."""
    from app.api.avito import _get_avito_credentials
    db = SessionLocal()
    try:
        slot = db.query(AccountSlot).filter(AccountSlot.account_id == account_id).first()
        try:
            cid, csec, _ = _get_avito_credentials(account_id)
        except Exception as exc:
            return {"status":"error","reason":str(exc)[:300]}
        if not cid:
            return {"status":"error","reason":"Avito keys missing"}
        res = verify_avito_keys(cid, csec)
        if slot:
            slot.tariff_ok = res.get("tariff_ok")
            slot.last_check_result = (str(res.get("message") or "") + " | " + str(res.get("raw") or ""))[:1000]
            slot.last_check_at = _now(); slot.updated_at = _now()
            if _expired(slot.paid_until): slot.status = "readonly"
            elif res.get("ok"): slot.status = "connected"
            else: slot.status = "error"
        # LEGACY_AUTOLOAD_CAPABILITY_SELFHEAL_V1: direct/legacy accounts may have
        # no AccountSlot. Persist the same provider proof in canonical account-scoped
        # storage so readiness can self-heal without owner confirmation.
        cap = {
            "connected": bool(res.get("ok")),
            "autoload_ready": res.get("tariff_ok") is True,
            "http_status": 200 if res.get("tariff_ok") is True else None,
            "message": res.get("message"),
            "checked_at": _now().isoformat(),
        }
        cap_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "avito_autoload_capability_v1").first()
        if cap_row: cap_row.value = json.dumps(cap, ensure_ascii=False)
        else: db.add(Storage(account_id=account_id, key="avito_autoload_capability_v1", value=json.dumps(cap, ensure_ascii=False)))
        db.commit()
        return {"status":"ok" if res.get("ok") else "error","account_id":account_id,
                "autoload_access":res.get("tariff_ok"),"slot_status":slot.status if slot else "legacy_direct",
                "provider_message":res.get("message")}
    finally:
        db.close()


def _initial_sync(slot_id: int, account_id: str):
    """Real post-connect lifecycle: zero-active bootstrap + fresh Avito stats."""
    from app.services.initial_portfolio import post_connect_sync
    result = post_connect_sync(account_id) or {}
    db = SessionLocal()
    try:
        slot = db.query(AccountSlot).filter(AccountSlot.id == slot_id).first()
        if slot:
            slot.sync_status = "ok" if result.get("status") == "ok" else "error"
            slot.last_sync_error = None if result.get("status") == "ok" else str(result.get("reason") or "initial sync failed")[:500]
            slot.updated_at = _now()
            db.commit()
    finally:
        db.close()


@router.post("/slots/connect")
def connect(req: ConnectRequest, background: BackgroundTasks, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        slot = _owner_scope(db, user).filter(AccountSlot.id == req.slot_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="Слот не найден")
        if _effective_status(slot) == "readonly":
            raise HTTPException(status_code=402,
                                detail="Слот не оплачен — подключение недоступно")

        slot.status = "connecting"
        slot.updated_at = _now()
        db.commit()

        res = verify_avito_keys(req.avito_client_id, req.avito_client_secret)
        slot.last_check_at = _now()
        slot.last_check_result = (res["message"] + " | " + res["raw"])[:1000]

        if not res["ok"]:
            slot.status = "error"
            slot.updated_at = _now()
            db.commit()
            return {"status": "error", "message": res["message"],
                    "slot": _slot_dict(slot, {})}

        name = (req.account_name or res["name"] or
                "Аккаунт %d" % slot.slot_no).strip()

        acc = None
        if slot.account_id:
            acc = db.query(Account).filter(
                Account.account_id == slot.account_id).first()
        if acc is None and res["avito_user_id"]:
            acc = db.query(Account).filter(
                Account.avito_user_id == res["avito_user_id"],
                Account.owner_user_id == slot.owner_user_id).first()
        if acc is None:
            base = _slugify(name, "inbox_%d" % slot.owner_user_id)
            slug, n = base, 1
            while db.query(Account).filter(Account.account_id == slug).first():
                n += 1
                slug = "%s_%d" % (base, n)
            acc = Account(account_id=slug, name=name,
                          owner_user_id=slot.owner_user_id,
                          billing_mode="manual")
            db.add(acc)
            db.flush()

        acc.name = name
        from app.crypto_utils import encrypt_secret
        acc.avito_client_id = encrypt_secret(req.avito_client_id.strip())
        acc.avito_client_secret = encrypt_secret(req.avito_client_secret.strip())
        if res["avito_user_id"]:
            acc.avito_user_id = res["avito_user_id"]

        if res["avito_user_id"]:
            _dup = db.query(AccountSlot).filter(
                AccountSlot.owner_user_id == slot.owner_user_id,
                AccountSlot.avito_user_id == res["avito_user_id"],
                AccountSlot.id != slot.id,
                AccountSlot.account_id.isnot(None),
                AccountSlot.status.in_(("connected", "readonly", "connecting")),
            ).first()
            if _dup:
                slot.status = "error"; slot.last_check_result = "duplicate avito_user_id"
                slot.updated_at = _now(); db.commit()
                return {"status": "error",
                        "message": "Этот аккаунт Avito уже подключён в другом слоте"}
        slot.account_id = acc.account_id
        slot.account_name = name
        slot.avito_user_id = res["avito_user_id"] or slot.avito_user_id
        slot.display_phone = (req.display_phone or res["phone"]
                              or slot.display_phone or "")
        slot.tariff_ok = res["tariff_ok"]
        slot.status = "connected"
        slot.sync_status = "pending"; slot.last_sync_error = None
        slot.updated_at = _now()
        _acc_id = acc.account_id; _slot_id = slot.id
        db.commit()

        background.add_task(_initial_sync, _slot_id, _acc_id)

        counts = _counts(db, [_acc_id])
        return {"status": "ok", "message": res["message"],
                "slot": _slot_dict(slot, counts)}
    finally:
        db.close()


@router.post("/slots/phone")
def set_phone(req: PhoneRequest, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        slot = _owner_scope(db, user).filter(AccountSlot.id == req.slot_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="Слот не найден")
        slot.display_phone = (req.display_phone or "").strip()
        slot.updated_at = _now()
        db.commit()
        return {"status": "ok", "slot": _slot_dict(slot, {})}
    finally:
        db.close()


@router.post("/slots/check")
def check(req: CheckRequest, user=Depends(get_current_user)):
    """Перепроверка слотов. Эта же функция — точка входа для суточной
    автопроверки: дергать её по расписанию для всех владельцев."""
    db = SessionLocal()
    try:
        q = _owner_scope(db, user).filter(AccountSlot.account_id.isnot(None))
        if req.slot_id:
            q = q.filter(AccountSlot.id == req.slot_id)
        checked = []
        for slot in q.all():
            from app.api.avito import _get_avito_credentials
            cid = csec = ""
            try:
                cid, csec, _u = _get_avito_credentials(slot.account_id)
            except Exception as ex:
                slot.last_check_result = "Не удалось прочитать ключи: %s" % ex
            if not cid:
                slot.status = "error"
                slot.last_check_result = slot.last_check_result or "Ключи Avito не заданы"
            else:
                res = verify_avito_keys(cid, csec)
                slot.tariff_ok = res["tariff_ok"]
                slot.last_check_result = (res["message"] + " | " + res["raw"])[:1000]
                slot.status = "connected" if res["ok"] else "error"
            if _expired(slot.paid_until):
                slot.status = "readonly"
            slot.last_check_at = _now()
            slot.updated_at = _now()
            checked.append(slot)
        db.commit()
        counts = _counts(db, [s.account_id for s in checked if s.account_id])
        return {"status": "ok", "checked": len(checked),
                "slots": [_slot_dict(s, counts) for s in checked]}
    finally:
        db.close()


@router.post("/slots/release")
def release(req: CheckRequest, user=Depends(get_current_user)):
    """Освободить слот. История переписки не удаляется."""
    db = SessionLocal()
    try:
        slot = _owner_scope(db, user).filter(AccountSlot.id == req.slot_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="Слот не найден")
        slot.account_id = None
        slot.status = "paid_empty"
        slot.last_check_result = "Слот освобождён, переписка сохранена"
        slot.updated_at = _now()
        db.commit()
        return {"status": "ok", "slot": _slot_dict(slot, {})}
    finally:
        db.close()
# ЭТАП 2 — слой чтения/отправки переписки. Дописать в конец inbox_slots.py.
# Используются существующие: router, get_current_user, SessionLocal,
# _owner_scope, _counts, _effective_status, text, bindparam, AccountSlot.


def _unread_map(db, user_id, account_ids):
    out = {}
    ids = [a for a in (account_ids or []) if a]
    if not ids:
        return out
    q = text(
        "SELECT m.account_id, m.avito_chat_id, count(*) "
        "  FROM messenger_messages m "
        "  LEFT JOIN messenger_dialog_state s "
        "    ON s.user_id = :uid AND s.account_id = m.account_id "
        "   AND s.avito_chat_id = m.avito_chat_id "
        " WHERE m.account_id IN :ids "
        "   AND lower(coalesce(m.direction,'')) LIKE 'in%' AND coalesce(m.msg_type,'') <> 'system'    AND (m.item_owner_id IS NULL OR m.item_owner_id = COALESCE((SELECT avito_user_id FROM account_slots WHERE account_id = m.account_id LIMIT 1),(SELECT avito_user_id FROM accounts WHERE account_id = m.account_id LIMIT 1))) "
        "   AND ( s.last_read_at IS NULL "
        "         OR to_timestamp(coalesce(m.avito_created_at,0)) > s.last_read_at ) "
        " GROUP BY 1,2"
    ).bindparams(bindparam("ids", expanding=True))
    for acc, cid, n in db.execute(q, {"uid": user_id, "ids": ids}).all():
        out[(acc, cid)] = int(n or 0)
    return out


def _connected_account_ids(db, user):
    """Accounts visible in the unified inbox.

    Active transport comes only from account_slots. Historical account scopes are
    kept readable when they already own messenger history; this lets an Avito slot
    move to the canonical business account without re-keying old conversations.
    """
    rows = _owner_scope(db, user).filter(AccountSlot.account_id.isnot(None)).all()
    access_ids = []
    if not rows:
        # BORIS_EMP_INBOX_FALLBACK: employee sees explicitly granted account scopes.
        _uid = getattr(user, "id", 0)
        access_ids = [r[0] for r in db.execute(text(
            "SELECT account_id FROM user_account_access"
            " WHERE user_id = :u AND can_view = TRUE"),
            {"u": _uid}).fetchall() if r[0]]
        if access_ids:
            rows = db.query(AccountSlot).filter(
                AccountSlot.account_id.in_(access_ids),
                AccountSlot.account_id.isnot(None)).all()

    out = []
    seen = set()
    for s in rows:
        if _effective_status(s) in ("connected", "readonly") and s.account_id:
            name = (s.account_name or s.account_id).strip()
            if not name or name == "\\":
                name = s.account_id
            out.append((s.account_id, name))
            seen.add(s.account_id)

    # Historical scopes stay read-only in the inbox. Never copy/move their
    # messages: provenance remains the original account_id.
    uid = getattr(user, "id", 0)
    if getattr(user, "role", "") == "owner":
        hist = db.execute(text(
            "SELECT a.account_id,a.name FROM accounts a"
            " WHERE EXISTS (SELECT 1 FROM messenger_messages m WHERE m.account_id=a.account_id)"
            " ORDER BY a.id")).all()
    else:
        hist = db.execute(text(
            "SELECT a.account_id,a.name FROM accounts a"
            " WHERE (a.owner_user_id=:u OR a.account_id IN ("
            "   SELECT account_id FROM user_account_access WHERE user_id=:u AND can_view=TRUE))"
            " AND EXISTS (SELECT 1 FROM messenger_messages m WHERE m.account_id=a.account_id)"
            " ORDER BY a.id"), {"u": uid}).all()
    for account_id, raw_name in hist:
        if not account_id or account_id in seen:
            continue
        name = str(raw_name or "").strip()
        if not name or name == "\\":
            name = account_id
        out.append((account_id, name))
        seen.add(account_id)
    return out


def _fmt_ts(sec):
    if not sec:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).astimezone().strftime("%H:%M")
    except Exception:
        return ""


def _iso_ts(sec):
    """Полное время в ISO-8601 с зоной. Локальное время показывает БРАУЗЕР —
    сервер живёт в UTC, и _fmt_ts из-за этого давал клиенту минус три часа."""
    if not sec:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).isoformat()
    except Exception:
        return ""


@router.get("/accounts")
def inbox_accounts(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        slots = _owner_scope(db, user).filter(
            AccountSlot.account_id.isnot(None)).order_by(AccountSlot.slot_no.asc()).all()
        if not slots:
            # Employee fallback: only explicitly granted active slots.
            _ids = [r[0] for r in db.execute(text(
                "SELECT account_id FROM user_account_access"
                " WHERE user_id = :u AND can_view = TRUE"),
                {"u": getattr(user, "id", 0)}).fetchall() if r[0]]
            if _ids:
                slots = db.query(AccountSlot).filter(
                    AccountSlot.account_id.in_(_ids),
                    AccountSlot.account_id.isnot(None)).order_by(
                    AccountSlot.slot_no.asc()).all()

        visible = _connected_account_ids(db, user)
        name_by_acc = dict(visible)
        acc_ids = [a for a, _ in visible]
        slot_by_acc = {s.account_id: s for s in slots if s.account_id}
        counts = _counts(db, acc_ids)
        unread = _unread_map(db, getattr(user, "id", 0), acc_ids)
        unread_by_acc = {}
        for (acc, _cid), n in unread.items():
            unread_by_acc[acc] = unread_by_acc.get(acc, 0) + n

        import json as _json
        accounts = []
        total_unread = 0
        total_unanswered = 0
        for account_id in acc_ids:
            s = slot_by_acc.get(account_id)
            u = unread_by_acc.get(account_id, 0)
            total_unread += u
            total_unanswered += counts.get(account_id, {}).get("unanswered", 0)

            # MOP activation is account-scoped and requires all existing gates:
            # binding + paid runtime package + owner switch. No second engine/state.
            bound = bool(db.execute(text(
                "SELECT 1 FROM ai_bindings WHERE product='mop' AND account_id=:a LIMIT 1"
            ), {"a": account_id}).first())
            try:
                from app.api.messenger import get_manager_balance
                balance = get_manager_balance(account_id) or {}
                package_active = bool(balance.get("active"))
            except Exception:
                package_active = False
            raw_cfg = db.execute(text(
                "SELECT value FROM storage WHERE account_id=:a"
                " AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"
            ), {"a": account_id}).scalar()
            try:
                cfg = _json.loads(raw_cfg) if raw_cfg else {}
            except Exception:
                cfg = {}
            owner_enabled = bool((cfg if isinstance(cfg, dict) else {}).get(
                "mop_enabled", (cfg if isinstance(cfg, dict) else {}).get("enabled", True)))
            mop_enabled = bool(bound and package_active and owner_enabled)
            try:
                mop_contour = db.execute(text(
                    "SELECT contour FROM mop_modes WHERE account_id=:a"
                ), {"a": account_id}).scalar() or "legacy"
            except Exception:
                mop_contour = "legacy"

            attention = int(counts.get(account_id, {}).get("unanswered", 0) or 0)
            try:
                attention += int(db.execute(text(
                    "SELECT count(*) FROM mop_drafts WHERE account_id=:a"
                    " AND status IN ('human_required','send_failed','draft_ready')"
                ), {"a": account_id}).scalar() or 0)
            except Exception:
                pass

            if s is not None:
                status = _effective_status(s)
                sync_status = s.sync_status or ""
                last_sync = s.last_sync_at.isoformat() if s.last_sync_at else None
                last_sync_error = s.last_sync_error or ""
                phone = s.display_phone or ""
                name = s.account_name or name_by_acc.get(account_id) or account_id
            else:
                # Historical scope: readable, never autosynced because it owns no slot.
                status = "history"
                sync_status = "history"
                last_sync = None
                last_sync_error = ""
                phone = ""
                name = name_by_acc.get(account_id) or account_id
            name = str(name or account_id).strip()
            if not name or name == "\\":
                name = account_id

            accounts.append({
                "account_id": account_id,
                "name": name,
                "platform": "Avito",
                "phone": phone,
                "status": status,
                "sync_status": sync_status,
                "last_sync": last_sync,
                "last_sync_error": last_sync_error,
                "mop_enabled": mop_enabled,
                "mop_mode": mop_contour,
                "unread": u,
                "attention": attention,
                "history_only": s is None,
            })

        answered_today = 0
        if acc_ids:
            q = text(
                "SELECT count(*) FROM messenger_messages "
                " WHERE account_id IN :ids AND lower(coalesce(direction,'')) LIKE 'out%' AND coalesce(msg_type,'') <> 'system' "
                "   AND (to_timestamp(coalesce(avito_created_at,0)) AT TIME ZONE 'Europe/Moscow') "
                "       >= date_trunc('day', now() AT TIME ZONE 'Europe/Moscow')"
            ).bindparams(bindparam("ids", expanding=True))
            answered_today = int(db.execute(q, {"ids": acc_ids}).scalar() or 0)
        return {
            "status": "ok",
            "totals": {
                "accounts": len(accounts),
                "new_msgs": total_unread,
                "unanswered": total_unanswered,
                "answered_today": answered_today,
            },
            "accounts": accounts,
        }
    finally:
        db.close()


@router.get("/attention")
def inbox_attention(account_id: str = "all", user=Depends(get_current_user)):
    """Read-only manager-workspace projection over existing actionable states.

    No new queue/storage: unread comes from messenger state, MOP states from
    mop_drafts, overdue work from canonical BORIS CRM tasks, reactivation from
    the existing reactivation tables, and handoff ownership from messenger_leads.
    """
    db = SessionLocal()
    try:
        conn = _connected_account_ids(db, user)
        name_by_acc = {a: n for a, n in conn}
        if account_id and account_id != "all":
            if account_id not in name_by_acc:
                return {"status": "error", "message": "Аккаунт не найден или не подключён"}
            ids = [account_id]
        else:
            ids = list(name_by_acc.keys())
        if not ids:
            return {"status": "ok", "items": [], "counts": {}}

        # MOP_ATTENTION_ENTITLEMENT_FILTER_V1:
        # Historical MOP cards stay in DB for audit, but an unpaid/inactive MOP
        # account must not keep generating operator work in the owner's attention
        # queue. Unread inbox/CRM/reactivation evidence remains visible; only
        # actionable MOP states are suppressed until entitlement becomes active.
        _mop_entitled = {}
        try:
            from app.api.messenger import get_manager_balance as _attention_mop_balance
            for _aid in ids:
                try:
                    _mop_entitled[str(_aid)] = bool((_attention_mop_balance(str(_aid)) or {}).get("active"))
                except Exception:
                    _mop_entitled[str(_aid)] = False
        except Exception:
            _mop_entitled = {str(_aid): False for _aid in ids}

        items = {}
        def add(acc, chat, kind, label, priority=50, detail=""):
            if not acc or not chat:
                return
            key = (str(acc), str(chat))
            row = items.setdefault(key, {
                "account_id": str(acc),
                "account_name": name_by_acc.get(str(acc), str(acc)),
                "avito_chat_id": str(chat),
                "client_name": "Покупатель",
                "assigned_user_id": None,
                "assigned_name": "",
                "assigned_mine": False,
                "reasons": [],
                "priority": 0,
                "why_first": "",
            })
            if not any(x.get("kind") == kind for x in row["reasons"]):
                row["reasons"].append({"kind": kind, "label": label, "detail": detail})
            row["priority"] = max(int(row["priority"] or 0), int(priority))

        unread = _unread_map(db, getattr(user, "id", 0), ids)
        for (acc, chat), count in unread.items():
            if count:
                add(acc, chat, "unread", "Клиент ждёт ответа", 90, f"Новых сообщений: {count}")

        q_drafts = text(
            "SELECT DISTINCT ON (account_id,avito_chat_id) account_id,avito_chat_id,status,"
            " coalesce(send_error,''),coalesce(ai_summary,'') FROM mop_drafts WHERE account_id IN :ids"
            " AND account_id NOT LIKE '__qa_%' AND avito_chat_id NOT LIKE 'qa_%'"
            " AND status NOT IN ('sent','deleted','no_reply_required')"
            " ORDER BY account_id,avito_chat_id,coalesce(updated_at,created_at) DESC,id DESC"
        ).bindparams(bindparam("ids", expanding=True))
        try:
            for acc, chat, st, err, ai_summary in db.execute(q_drafts, {"ids": ids}).all():
                if not _mop_entitled.get(str(acc), False):
                    continue
                if st == "human_required":
                    handoff_reason = ""
                    try:
                        _hs = json.loads(ai_summary or "{}")
                        _hr = (_hs or {}).get("handoff_reason") or (_hs or {}).get("next_action") or ""
                        if isinstance(_hr, dict):
                            _hr = _hr.get("reason") or _hr.get("message") or ""
                        handoff_reason = str(_hr or "").strip()
                    except Exception:
                        handoff_reason = ""
                    _reason_labels = {
                        "messenger_channel_unconfirmed": "Нужно связаться с клиентом по подтверждённому каналу",
                        "client_requested_human": "Клиент попросил подключить человека",
                        "price_missing": "Нужно подтвердить актуальную цену",
                        "availability_requires_human": "Нужно подтвердить актуальное наличие",
                        "individual_delivery_price": "Нужен индивидуальный расчёт доставки/вывоза",
                        "previous_reply_needs_correction": "Нужно исправить предыдущий ответ МОПа и продолжить диалог",
                        "stale_unanswered_client_inquiry": "Просроченный вопрос клиента — нужен ответ менеджера",
                        "business_partnership_request": "Партнёрское предложение — нужно решение менеджера",
                    }
                    handoff_reason = _reason_labels.get(handoff_reason, handoff_reason)
                    details = [x for x in (handoff_reason, str(err or "").strip()) if x]
                    add(acc, chat, "human_required", "Нужен человек", 100,
                        " · ".join(details) or "МОП передал диалог сотруднику")
                elif st == "send_failed":
                    add(acc, chat, "send_failed", "Не удалось отправить", 100, err or "Проверьте отправку")
                elif st in ("draft_ready", "ready", "editing", "in_progress"):
                    add(acc, chat, "draft_ready", "Черновик готов", 70, "Можно проверить и отправить")
        except Exception:
            db.rollback()

        # Legacy send_failed/pending drafts are still authoritative for legacy accounts.
        try:
            import json as _json_att
            for _key, _value in db.execute(text(
                "SELECT key,value FROM storage WHERE account_id='_telegram_drafts'"
                " AND key LIKE 'messenger_draft:%'"
            )).fetchall():
                try:
                    d = _json_att.loads(_value)
                except Exception:
                    continue
                acc, chat, st = d.get("account_id"), d.get("avito_chat_id"), d.get("status")
                if acc not in ids or not chat:
                    continue
                if not _mop_entitled.get(str(acc), False):
                    continue
                if st == "send_failed":
                    add(acc, chat, "send_failed", "Не удалось отправить", 100, d.get("send_error") or "Проверьте отправку")
                elif st == "pending":
                    add(acc, chat, "draft_ready", "Черновик готов", 70, "Можно проверить и отправить")
        except Exception:
            db.rollback()

        # Existing qualification facts from messenger_leads / persisted MOP analysis.
        try:
            import json as _json_qual
            q_qual = text("""
                SELECT l.account_id,l.avito_chat_id,l.has_phone,
                       COALESCE(
                         (SELECT d.ai_summary FROM mop_drafts d
                           WHERE d.account_id=l.account_id AND d.avito_chat_id=l.avito_chat_id
                             AND NULLIF(TRIM(d.ai_summary),'') IS NOT NULL
                           ORDER BY d.id DESC LIMIT 1),
                         (SELECT s.value FROM storage s
                           WHERE s.account_id=l.account_id
                             AND s.key=('mop_qualification:' || l.avito_chat_id)
                           ORDER BY s.id DESC LIMIT 1)
                       ) ai_summary
                  FROM messenger_leads l WHERE l.account_id IN :ids
            """).bindparams(bindparam("ids", expanding=True))
            for acc, chat, has_phone, raw_summary in db.execute(q_qual, {"ids": ids}).all():
                if has_phone:
                    add(acc, chat, "phone_received", "Получен телефон", 80, "Контакт клиента уже сохранён")
                if raw_summary:
                    try: qa = _json_qual.loads(raw_summary)
                    except Exception: qa = {}
                    nxt = str(qa.get("next_action") or "").strip()
                    target = str(qa.get("target_action") or "").strip()
                    if nxt or target:
                        add(acc, chat, "next_action", "Нужно следующее действие", 75, nxt or target)
        except Exception:
            db.rollback()

        q_tasks = text(
            "SELECT d.avito_account_id,d.avito_chat_id,t.title,t.due_at FROM boris_crm_tasks t"
            " JOIN boris_crm_deals d ON d.id=t.deal_id"
            " WHERE d.avito_account_id IN :ids AND t.status='open'"
            " AND t.due_at IS NOT NULL AND t.due_at <= now() + interval '30 minutes'"
        ).bindparams(bindparam("ids", expanding=True))
        try:
            for acc, chat, title, due in db.execute(q_tasks, {"ids": ids}).all():
                overdue = bool(due and due < datetime.now(timezone.utc))
                add(acc, chat, "overdue_task" if overdue else "reminder_due", "Просрочена задача" if overdue else "Напоминание", 95 if overdue else 85, (title or "Есть действие") + (f" · срок {due}" if due else ""))
        except Exception:
            db.rollback()

        q_react = text(
            "SELECT account_id,avito_chat_id,primary_reason FROM reactivation_candidates"
            " WHERE account_id IN :ids AND status IN ('candidate','needs_review','approved','scheduled','cooldown')"
        ).bindparams(bindparam("ids", expanding=True))
        try:
            for acc, chat, reason in db.execute(q_react, {"ids": ids}).all():
                add(acc, chat, "reactivation", "Доступна реактивация", 40, reason or "Есть действующий сценарий реактивации")
        except Exception:
            db.rollback()

        if items:
            q_context = text("""
                SELECT DISTINCT ON (account_id,avito_chat_id) account_id,avito_chat_id,
                       text,item_title,direction,avito_created_at
                  FROM messenger_messages WHERE account_id IN :ids
                 ORDER BY account_id,avito_chat_id,avito_created_at DESC,id DESC
            """).bindparams(bindparam("ids", expanding=True))
            now_ts = datetime.now(timezone.utc).timestamp()
            for acc, chat, msg_text, item_title, direction, created in db.execute(q_context,{"ids":ids}).all():
                row=items.get((str(acc),str(chat)))
                if not row: continue
                row["last_text"] = str(msg_text or "")[:240]
                row["item_title"] = str(item_title or "")[:160]
                row["last_direction"] = str(direction or "")
                row["last_at"] = _fmt_ts(created)
                try:
                    sec=max(0,int(now_ts-float(created or 0))); row["wait_seconds"]=sec
                    row["wait_label"] = (f"{sec//86400} д." if sec>=86400 else f"{sec//3600} ч." if sec>=3600 else f"{max(1,sec//60)} мин.")
                except Exception: row["wait_seconds"]=0; row["wait_label"]=""

        if items:
            q_leads = text(
                "SELECT l.account_id,l.avito_chat_id,l.contact_name,l.assigned_user_id,u.email"
                " FROM messenger_leads l LEFT JOIN users u ON u.id=l.assigned_user_id"
                " WHERE l.account_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            for acc, chat, cname, auid, email in db.execute(q_leads, {"ids": ids}).all():
                row = items.get((str(acc), str(chat)))
                if not row:
                    continue
                row["client_name"] = cname or "Покупатель"
                row["assigned_user_id"] = auid
                row["assigned_name"] = ((email or "").split("@")[0] if email else "")
                row["assigned_mine"] = bool(auid and auid == getattr(user, "id", None))

        # Product priority is a projection over existing Attention reasons, not a second queue engine.
        # Urgency first, then sales value: human/send failure > overdue > phone > unread > follow-up > reactivation.
        # Strict business tiers; combinations improve order only inside the same urgency tier.
        for row in items.values():
            kinds = {r.get("kind") for r in row["reasons"]}
            tier = 800 if ("human_required" in kinds or "send_failed" in kinds) else 700 if "overdue_task" in kinds else 600 if ("phone_received" in kinds and "unread" in kinds) else 500 if "phone_received" in kinds else 400 if "unread" in kinds else 300 if "reminder_due" in kinds else 200 if "next_action" in kinds else 150 if "draft_ready" in kinds else 100
            wait_bonus = min(int(row.get("wait_seconds") or 0) // 1800, 80)
            row["priority"] = tier + wait_bonus + min(len(kinds), 9)
        def why_first(row):
            kinds = {r.get("kind") for r in row["reasons"]}
            details = [str(r.get("detail") or "") for r in row["reasons"]]
            wait = (" · ждёт " + row.get("wait_label")) if row.get("wait_label") and ("unread" in kinds or "human_required" in kinds or "send_failed" in kinds) else ""
            if "human_required" in kinds: return "МОП запросил человека — требуется решение менеджера" + wait
            if "send_failed" in kinds: return "Ответ не отправлен — клиент может остаться без ответа" + wait
            if "overdue_task" in kinds: return next((d for d in details if "срок" in d), "Просрочена задача по клиенту")
            if "phone_received" in kinds and "unread" in kinds: return "Есть телефон + клиент ждёт ответа" + wait
            if "phone_received" in kinds and "next_action" in kinds: return "Есть телефон + требуется следующее действие"
            if "phone_received" in kinds: return "Получен телефон — контакт готов к работе менеджера"
            if "unread" in kinds: return "Клиент ждёт ответа" + wait
            if "reminder_due" in kinds: return next((d for d in details if d), "Наступает срок напоминания")
            if "next_action" in kinds: return next((d for d in details if d), "BORIS определил следующее действие")
            if "draft_ready" in kinds: return "Черновик МОП готов к проверке"
            if "reactivation" in kinds: return next((d for d in details if d), "Клиента пора вернуть в диалог")
            return "Требует внимания менеджера"
        for row in items.values(): row["why_first"] = why_first(row)
        rows = sorted(items.values(), key=lambda x: (-x["priority"], x["client_name"].lower()))
        counts = {}
        for row in rows:
            for reason in row["reasons"]:
                counts[reason["kind"]] = counts.get(reason["kind"], 0) + 1
        today_tasks = today_completed = today_reactivation = 0
        try:
            q = text("SELECT count(*) FILTER (WHERE t.created_at >= date_trunc('day',now())) AS created, count(*) FILTER (WHERE t.completed_at >= date_trunc('day',now())) AS completed FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id WHERE d.avito_account_id IN :ids").bindparams(bindparam("ids", expanding=True))
            today_tasks, today_completed = [int(x or 0) for x in db.execute(q,{"ids":ids}).one()]
        except Exception: db.rollback()
        try:
            q = text("SELECT count(*) FROM reactivation_candidates WHERE account_id IN :ids AND updated_at >= date_trunc('day',now())").bindparams(bindparam("ids", expanding=True))
            today_reactivation = int(db.execute(q,{"ids":ids}).scalar() or 0)
        except Exception: db.rollback()
        today_target_actions = 0
        next_reminder = None
        try:
            q = text("SELECT count(*) FROM storage WHERE account_id IN :ids AND key LIKE 'mop_qualification:%' AND value ILIKE '%TARGET_ACTION%' AND CASE WHEN left(ltrim(value),1)='{' THEN COALESCE((value::jsonb->>'updated_at')::timestamptz,(value::jsonb->>'created_at')::timestamptz,(value::jsonb->>'ts')::timestamptz,(value::jsonb->>'timestamp')::timestamptz)::date=current_date ELSE false END").bindparams(bindparam("ids", expanding=True))
            today_target_actions = int(db.execute(q,{"ids":ids}).scalar() or 0)
        except Exception: db.rollback()
        try:
            q = text("SELECT min(t.due_at) FROM boris_crm_tasks t JOIN boris_crm_deals d ON d.id=t.deal_id WHERE d.avito_account_id IN :ids AND t.status='open' AND t.due_at > now()+interval '30 minutes'").bindparams(bindparam("ids", expanding=True))
            nr=db.execute(q,{"ids":ids}).scalar(); next_reminder = nr.isoformat() if nr else None
        except Exception: db.rollback()
        summary = {
            "attention": len(rows), "remaining": len(rows),
            "processed_today": today_completed,
            "overdue": counts.get("overdue_task", 0),
            "phone": counts.get("phone_received", 0),
            "waiting": counts.get("unread", 0),
            "reactivation": counts.get("reactivation", 0),
            "tasks_created_today": today_tasks,
            "reactivation_today": today_reactivation,
            "target_actions_today": today_target_actions,
            "next_reminder_at": next_reminder,
        }
        return {"status": "ok", "items": rows, "counts": counts, "summary": summary, "total": len(rows)}
    finally:
        db.close()


@router.get("/dialogs")
def inbox_dialogs(account_id: str = "all", q: str = "", kind: str = "sales", user=Depends(get_current_user)):
    _q_search = (q or "").strip().lower()   # перехват ДО того как q затрётся SQL-объектом
    db = SessionLocal()
    try:
        conn = _connected_account_ids(db, user)
        name_by_acc = {a: n for a, n in conn}
        if account_id and account_id != "all":
            if account_id not in name_by_acc:
                return {"status": "error", "message": "Аккаунт не найден или не подключён"}
            ids = [account_id]
        else:
            ids = list(name_by_acc.keys())
        if not ids:
            return {"status": "ok", "dialogs": []}
        q = text(
            "SELECT DISTINCT ON (account_id, avito_chat_id) "
            "       account_id, avito_chat_id, item_id, item_title, direction, text, avito_created_at, item_owner_id, item_url "
            "  FROM messenger_messages WHERE account_id IN :ids "
            " ORDER BY account_id, avito_chat_id, avito_created_at DESC"
        ).bindparams(bindparam("ids", expanding=True))
        rows = db.execute(q, {"ids": ids}).all()
        unread = _unread_map(db, user.id, ids)
        dialogs = []

        # KPI_LEAD_PURPOSE_INBOX_V1: expose the same account-scoped lead-quality
        # truth used by kpi_check. Consumers must not need to re-invent their own
        # applicant detection. Only positively classified accidental job seekers
        # are marked non-KPI; recruitment accounts/listings remain countable.
        _job_seeker_chats = set()
        try:
            from app.services.kpi_lead_quality import job_seekers_today
            for _aid in ids:
                _quality = job_seekers_today(db, _aid)
                if _quality.get("status") != "ok":
                    continue
                for _item in (_quality.get("items") or []):
                    _cid = str(_item.get("chat_id") or "")
                    if _cid:
                        _job_seeker_chats.add((_aid, _cid))
        except Exception:
            db.rollback()
            _job_seeker_chats = set()

        _client_names_q = text(
            "SELECT account_id, avito_chat_id, contact_name FROM messenger_leads "
            "WHERE account_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        _client_names = {
            (a, c): (n or "")
            for a, c, n in db.execute(_client_names_q, {"ids": ids}).all()
        }
        _own = {r[0]: (r[1] or "") for r in db.execute(text(
            "SELECT account_id, avito_user_id FROM accounts WHERE account_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).all()}
        for _a, _u in db.execute(text(
            "SELECT account_id, avito_user_id FROM account_slots WHERE account_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).all():
            if _u:
                _own[_a] = _u
        for acc, cid, item_id, item_title, direction, txt, created, iowner, iurl in rows:
            _is_purchase = bool(iowner) and bool(_own.get(acc)) and str(iowner) != str(_own.get(acc))
            _is_job_seeker = (acc, str(cid)) in _job_seeker_chats
            dialogs.append({
                "account_id": acc,
                "account_name": name_by_acc.get(acc, acc),
                "avito_chat_id": cid,
                "lead_purpose": "job_seeker" if _is_job_seeker else "business_or_unknown",
                "kpi_counted": not _is_job_seeker,
                "client_name": _client_names.get((acc, cid)) or "Покупатель",
                "item_title": item_title or "",
                "item_url": iurl or "",
                "chat_url": "https://www.avito.ru/profile/messenger/channel/" + str(cid),
                "last_text": txt or "",
                "last_at": _fmt_ts(created),
                "unread": unread.get((acc, cid), 0),
                "answered": str(direction or "").lower().startswith("out"),
                "is_purchase": _is_purchase,
                "_sort": int(created or 0),
            })
        dialogs.sort(key=lambda d: d.pop("_sort"), reverse=True)
        if kind == "sales":
            # BORIS_R11_4:
            # Основное окно продаж показывает все реальные
            # диалоги подключённого аккаунта.
            #
            # Purchase-фильтр остаётся отдельным режимом.
            dialogs = dialogs
        elif kind == "purchases":
            dialogs = [d for d in dialogs if d.get("is_purchase")]
        if _q_search:
            # Ищем не только по последнему сообщению, но и по ВСЕЙ переписке:
            # менеджер помнит фразу из середины диалога, а не последнюю реплику.
            _deep = set()
            try:
                _dq = text(
                    "SELECT DISTINCT account_id, avito_chat_id FROM messenger_messages "
                    " WHERE account_id IN :ids AND lower(text) LIKE :pat"
                ).bindparams(bindparam("ids", expanding=True))
                for _a, _c in db.execute(_dq, {"ids": ids, "pat": "%" + _q_search + "%"}).all():
                    _deep.add((_a, _c))
            except Exception:
                _deep = set()
            _fields = ("last_text", "item_title", "avito_chat_id", "client_name", "account_name")
            dialogs = [d for d in dialogs
                       if any(_q_search in str(d.get(f) or "").lower() for f in _fields)
                       or (d.get("account_id"), d.get("avito_chat_id")) in _deep]
        return {"status": "ok", "dialogs": dialogs}
    finally:
        db.close()



@router.get("/voice")
def inbox_voice(account_id: str, voice_id: str, user=Depends(get_current_user)):
    """Стрим голосового. URL Avito и токен клиенту НЕ отдаём — качаем сами и
    возвращаем аудио. Доступ — только владельцу подключённого слота."""
    from fastapi.responses import Response
    import httpx as _hx
    from app.api.messenger import _get_user_id_and_token
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
    finally:
        db.close()
    if account_id not in conn:
        return Response(content="Нет доступа к аккаунту", status_code=403,
                        media_type="text/plain; charset=utf-8")
    try:
        uid, tok = _get_user_id_and_token(account_id)
        r = _hx.get(f"https://api.avito.ru/messenger/v1/accounts/{uid}/getVoiceFiles?voice_ids={voice_id}",
                    headers={"Authorization": f"Bearer {tok}"}, timeout=25)
        if r.status_code == 401:
            return Response(content="Сессия Avito истекла", status_code=502,
                            media_type="text/plain; charset=utf-8")
        if r.status_code != 200:
            return Response(content=f"Avito вернул {r.status_code}", status_code=502,
                            media_type="text/plain; charset=utf-8")
        url = (r.json().get("voices_urls") or {}).get(voice_id)
        if not url:
            return Response(content="Голосовое не найдено", status_code=404,
                            media_type="text/plain; charset=utf-8")
        audio = _hx.get(url, timeout=60, follow_redirects=True)
        if audio.status_code != 200:
            return Response(content="Не удалось загрузить аудио", status_code=502,
                            media_type="text/plain; charset=utf-8")
        ctype = audio.headers.get("content-type") or "audio/mpeg"
        if ctype.startswith("video/"): ctype = "audio/" + ctype.split("/",1)[1]
        return Response(content=audio.content, media_type=ctype,
                        headers={"Cache-Control": "private, max-age=300"})
    except Exception:
        return Response(content="Ошибка получения голосового", status_code=502,
                        media_type="text/plain; charset=utf-8")


@router.get("/thread")
def inbox_thread(account_id: str, avito_chat_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
        if account_id not in conn:
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        q = text(
            "SELECT direction, text, avito_created_at, item_title, msg_type, content_type, media_ref, item_url, is_read, read_at "
            "  FROM messenger_messages WHERE account_id = :acc AND avito_chat_id = :cid "
            " ORDER BY avito_created_at ASC, id ASC"
        )
        rows = db.execute(q, {"acc": account_id, "cid": avito_chat_id}).all()
        messages = [{
            "direction": "out" if str(d or "").lower().startswith("out") else "in",
            "text": t or "",
            "at": _fmt_ts(c),
            "at_iso": _iso_ts(c),
            "msg_type": mt or ("seller" if str(d or "").lower().startswith("out") else "user"),
            "content_type": ct or "text",
            "media_ref": mr,
            "voice_url": (f"/api/inbox/voice?account_id={account_id}&voice_id={mr}" if (ct == "voice" and mr) else None),
            "is_read": bool(_ir) if _ir is not None else None,
            "read_at_iso": _iso_ts(_ra),
        } for (d, t, c, _title, mt, ct, mr, _iu, _ir, _ra) in rows]

        # Показываем владельцу происхождение исходящего ответа. Avito не возвращает
        # автора, поэтому используем только доказанный provenance BORIS: отправки из
        # Единого окна и реально отправленные черновики МОП. Всё остальное помечаем
        # как отправленное напрямую в Avito, не приписывая его ИИ.
        try:
            _human_rows = db.execute(text(
                "SELECT COALESCE(o.text,''), COALESCE(u.email,''), o.sent_at"
                " FROM outgoing_authors o LEFT JOIN users u ON u.id=o.user_id"
                " WHERE o.account_id=:a AND o.avito_chat_id=:c ORDER BY o.sent_at"),
                {"a": account_id, "c": avito_chat_id}).fetchall()
            _mop_rows = db.execute(text(
                "SELECT COALESCE(reply_text,''), COALESCE(reply_author,''), sent_at"
                " FROM mop_drafts WHERE account_id=:a AND avito_chat_id=:c"
                " AND status='sent' AND COALESCE(reply_text,'')<>'' ORDER BY sent_at"),
                {"a": account_id, "c": avito_chat_id}).fetchall()
            def _norm_out_text(v):
                return " ".join(str(v or "").split()).strip()
            _human = {_norm_out_text(r[0]): (r[1] or "") for r in _human_rows if _norm_out_text(r[0])}
            _mop = {_norm_out_text(r[0]): (r[1] or "") for r in _mop_rows if _norm_out_text(r[0])}
            for _message in messages:
                if _message.get("direction") != "out":
                    continue
                _key = _norm_out_text(_message.get("text"))
                if _key and _key in _mop:
                    _message["author_kind"] = "mop"
                    _message["author_label"] = "ИИ-менеджер BORIS"
                elif _key and _key in _human:
                    _message["author_kind"] = "boris_user"
                    _email = _human.get(_key) or ""
                    _message["author_label"] = ("Сотрудник BORIS · " + _email.split("@")[0]) if _email else "Сотрудник BORIS"
                else:
                    _message["author_kind"] = "avito_external"
                    _message["author_label"] = "Отправлено напрямую в Avito"
        except Exception as _ae:
            print("[inbox_thread] author provenance failed: " + repr(_ae)[:150], flush=True)
        item_title = next((r[3] for r in rows if r[3]), "")
        # П.5: кто ведёт диалог. Живёт в messenger_leads — там по записи на диалог.
        _asg = db.execute(text(
            "SELECT l.assigned_user_id, l.assigned_at, u.email, l.contact_name"
            "  FROM messenger_leads l LEFT JOIN users u ON u.id = l.assigned_user_id"
            " WHERE l.account_id = :a AND l.avito_chat_id = :c"),
            {"a": account_id, "c": avito_chat_id}).fetchone()
        dialog = {
            "account_id": account_id,
            "account_name": conn.get(account_id, account_id),
            "avito_chat_id": avito_chat_id,
            "client_name": (_asg[3] if _asg and _asg[3] else "Покупатель"),
            "item_title": item_title or "",
            "item_url": next((r[7] for r in rows if r[7]), ""),
            "chat_url": "https://www.avito.ru/profile/messenger/channel/" + str(avito_chat_id),
            "phone": "",
            "assigned_user_id": (_asg[0] if _asg else None),
            "assigned_name": ((_asg[2] or "").split("@")[0] if _asg and _asg[2] else ""),
            "assigned_mine": bool(_asg and _asg[0] == getattr(user, "id", None)),
        }
        # П.1: состояние AI-МОПа для кабинета. Инвариант — у входящего не может
        # быть состояния «BORIS молчит и непонятно почему». Только чтение.
        draft = None
        try:
            _d = db.execute(text(
                "SELECT d.status, COALESCE(d.reply_text, ''),"
                "       COALESCE(d.updated_at, d.created_at),"
                "       (SELECT string_agg(e.event, ',' ORDER BY e.at)"
                "          FROM mop_draft_events e WHERE e.draft_id = d.id)"
                "  FROM mop_drafts d"
                " WHERE d.account_id = :acc AND d.avito_chat_id = :cid"
                "   AND d.status NOT IN ('sent', 'deleted', 'no_reply_required')"
                " ORDER BY COALESCE(d.updated_at, d.created_at) DESC LIMIT 1"),
                {"acc": account_id, "cid": avito_chat_id}).fetchone()
            if _d:
                _st, _txt, _upd, _ev = _d[0], _d[1], _d[2], (_d[3] or "")
                _events = [x for x in _ev.split(",") if x]
                _last_ok = max([i for i, e in enumerate(_events)
                                if e == "ai_generated"], default=-1)
                _last_err = max([i for i, e in enumerate(_events)
                                 if e in ("generation_failed", "ai_failed")], default=-1)
                if _txt.strip():
                    draft = {"status": "draft_ready", "text": _txt, "reason": "",
                             "can_retry": False}
                elif _last_err > _last_ok:
                    draft = {"status": "generation_failed", "text": "",
                             "reason": "BORIS не смог подготовить ответ",
                             "can_retry": True}
                elif _st == "analyzing":
                    draft = {"status": "analyzing", "text": "",
                             "reason": "BORIS готовит ответ", "can_retry": False}
                elif "card_no_ai" in _events:
                    draft = {"status": "human_required", "text": "",
                             "reason": "AI-менеджер не был активен в момент обращения",
                             "can_retry": True}
                else:
                    draft = {"status": "human_required", "text": "",
                             "reason": "BORIS не подготовил ответ — нужен сотрудник",
                             "can_retry": True}
                draft["updated_at"] = _fmt_ts(_upd) if _upd else ""
            else:
                # Второй источник — legacy-хранилище старого контура. Без него
                # аккаунт на legacy получал ложное «не подключён», хотя черновики
                # у него есть и кабинет их показывает через pending_drafts.
                import json as _lj
                for _lk, _lv in db.execute(text(
                        "SELECT key, value FROM storage"
                        " WHERE account_id = '_telegram_drafts'"
                        "   AND key LIKE 'messenger_draft:%'")).fetchall():
                    try:
                        _ld = _lj.loads(_lv)
                    except Exception:
                        continue
                    _legacy_status = _ld.get("status")
                    if (_ld.get("account_id") == account_id
                            and _ld.get("avito_chat_id") == avito_chat_id
                            and _legacy_status in ("pending", "send_failed")):
                        draft = {"status": ("draft_ready" if _legacy_status == "pending" else "send_failed"),
                                 "text": _ld.get("text") or "",
                                 "reason": (_ld.get("send_error") or ""),
                                 "can_retry": (_legacy_status == "send_failed"), "updated_at": "",
                                 "source": "legacy",
                                 "draft_id": str(_lk).split(":", 1)[1]}
                        break
                if draft is None:
                    _c = db.execute(text(
                        "SELECT contour FROM mop_modes WHERE account_id = :a"),
                        {"a": account_id}).scalar()
                    if (_c or "legacy") != "new":
                        draft = {"status": "not_connected", "text": "",
                                 "reason": "AI-менеджер не подключён к этому аккаунту",
                                 "can_retry": False, "updated_at": ""}
        except Exception as _de:
            print("[inbox_thread] draft lookup failed: " + repr(_de)[:150], flush=True)
        # Продавец уже ответил после последнего входящего — черновик неактуален.
        # У legacy-записей нет дат, поэтому свежесть определяем только так.
        if draft and draft.get("status") in ("draft_ready", "human_required"):
            for _m in reversed(messages):
                if _m.get("msg_type") == "system":
                    continue
                if _m.get("direction") == "out":
                    draft = None
                break
        if draft is not None and "source" not in draft:
            draft["source"] = "mop"
        return {"status": "ok", "dialog": dialog, "messages": messages, "draft": draft}
    finally:
        db.close()


ATTACH_DIR = "/root/BORIS/backend/images/attach"
ATTACH_MAX_MB = 20


@router.post("/upload_file")
async def inbox_upload_file(account_id: str, file: UploadFile = File(...),
                            user=Depends(get_current_user)):
    # account_id ИМЕННО в адресе запроса, а не в форме: общая проверка доступа
    # на роутере читает его из query и иначе отдаёт 403 ещё до входа в функцию.
    """Кладёт файл в статику и возвращает ссылку для вставки в сообщение."""
    import os as _os
    import re as _re
    import secrets as _secrets
    db = SessionLocal()
    try:
        if account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
    finally:
        db.close()
    data = await file.read()
    if not data:
        return {"status": "error", "message": "Файл пустой"}
    if len(data) > ATTACH_MAX_MB * 1024 * 1024:
        return {"status": "error",
                "message": "Файл больше %s МБ — отправьте ссылкой на облако" % ATTACH_MAX_MB}
    orig = _os.path.basename(file.filename or "file")
    ext = _os.path.splitext(orig)[1][:12]
    if not _re.match(r"^\.[A-Za-z0-9]+$", ext or ""):
        ext = ""
    _os.makedirs(ATTACH_DIR, exist_ok=True)
    name = _secrets.token_hex(16) + ext
    with open(_os.path.join(ATTACH_DIR, name), "wb") as fh:
        fh.write(data)
    url = "https://boris-ai.pro/images/attach/" + name
    try:
        from app.services.action_log import log_action, ACTOR_USER
        log_action(account_id=account_id, action="Загрузил файл для отправки",
                   object_kind="файл", object_name=orig[:200], after_val=url,
                   reason="ссылка будет отправлена покупателю сообщением",
                   actor=ACTOR_USER, user_id=getattr(user, "id", None),
                   source="inbox.upload_file")
    except Exception:
        pass
    return {"status": "ok", "url": url, "name": orig, "size": len(data)}


class InboxTakeRequest(BaseModel):
    account_id: str
    avito_chat_id: str


@router.post("/retry_draft")
def inbox_retry_draft(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Просит Бориса подготовить ответ заново.

    Не генерирует сама: сдвигает updated_at назад, и ближайший проход поллера
    видит черновик как «зависший» и перезапускает генерацию штатным путём."""
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        res = db.execute(text(
            "UPDATE mop_drafts SET updated_at = now() - interval '11 minutes'"
            " WHERE account_id = :a AND avito_chat_id = :c"
            "   AND status IN ('analyzing', 'new')"
            "   AND coalesce(reply_text, '') = ''"),
            {"a": req.account_id, "c": req.avito_chat_id})
        db.commit()
        if res.rowcount == 0:
            return {"status": "error",
                    "message": "Нечего повторять: ответ уже готов либо диалог не в работе"}
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=req.account_id, action="Попросил подготовить ответ заново",
                       object_kind="диалог", object_name=req.avito_chat_id,
                       reason="сотрудник нажал «Повторить» после ошибки генерации",
                       actor=ACTOR_USER, user_id=getattr(user, "id", None),
                       source="inbox.retry_draft")
        except Exception:
            pass
        return {"status": "ok",
                "message": "Борис подготовит ответ в ближайшие минуты"}
    finally:
        db.close()


@router.post("/reply_suggestions")
def inbox_reply_suggestions(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Generate selectable MOP reply variants on explicit manager request.

    Reuses the existing production MOP generator; no parallel engine and no send side effect.
    The already persisted draft is reused as the first option so it costs no extra call.
    """
    db = SessionLocal()
    try:
        visible_accounts = dict(_connected_account_ids(db, user))
        if req.account_id not in visible_accounts:
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}

        # Historical inbox scopes keep their original message provenance after an
        # Avito slot is moved to the canonical business account.  Suggestions must
        # nevertheless use the active MOP package + company context of that
        # canonical account, otherwise an old scope (for example inbox_60) can
        # incorrectly report an exhausted package and generate with stale context.
        mop_account_id = req.account_id
        src = db.execute(text(
            "SELECT avito_user_id, owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"
        ), {"a": req.account_id}).mappings().first()
        if src and src.get("avito_user_id"):
            candidate = db.execute(text(
                "SELECT a.account_id FROM accounts a"
                " JOIN account_slots s ON s.account_id=a.account_id"
                " JOIN ai_bindings b ON b.account_id=a.account_id AND b.product='mop'"
                " WHERE a.avito_user_id=:avito_user_id"
                "   AND a.owner_user_id=:owner_user_id"
                "   AND s.status IN ('connected','readonly')"
                " ORDER BY s.updated_at DESC NULLS LAST, s.id DESC LIMIT 1"
            ), {"avito_user_id": str(src.get("avito_user_id")),
                "owner_user_id": src.get("owner_user_id")}).scalar()
            if candidate and str(candidate) in visible_accounts:
                mop_account_id = str(candidate)

        current = db.execute(text(
            "SELECT COALESCE(reply_text,'') FROM mop_drafts"
            " WHERE account_id IN (:source_account,:mop_account) AND avito_chat_id=:c"
            " AND status NOT IN ('sent','deleted','no_reply_required')"
            " ORDER BY COALESCE(updated_at,created_at) DESC,id DESC LIMIT 1"),
            {"source_account": req.account_id, "mop_account": mop_account_id,
             "c": req.avito_chat_id}).scalar() or ""
    finally:
        db.close()
    from app.api.messenger import generate_ai_draft_reply, get_manager_balance
    try:
        balance = get_manager_balance(mop_account_id) or {}
        if not balance.get("active"):
            return {"status": "error", "message": "Пакет ответов МОП закончился — дополнительные варианты не генерируются"}
    except Exception:
        return {"status": "error", "message": "Не удалось проверить пакет ответов МОП"}
    suggestions = []
    seen = set()
    def add(label, value):
        text_value = str(value or "").strip()
        if not text_value or text_value.startswith("[Ошибка") or text_value in seen:
            return
        seen.add(text_value)
        suggestions.append({"label": label, "text": text_value})
    add("Основной", current)
    variants = [("Короткий", "shorter"), ("Продающий", "sales")]
    if not current.strip():
        variants.insert(0, ("Основной", None))
    for label, style in variants:
        try:
            generated = generate_ai_draft_reply(mop_account_id, {"id": req.avito_chat_id}, style_hint=style)
            if isinstance(generated, dict):
                generated = generated.get("text") or generated.get("reply") or ""
            add(label, generated)
        except Exception as exc:
            print("MOP_SUGGESTION_ERROR %s/%s %s: %s" % (mop_account_id, req.avito_chat_id, label, str(exc)[:160]), flush=True)
    if not suggestions:
        return {"status": "error", "message": "МОП не смог подготовить варианты ответа"}
    return {"status": "ok", "suggestions": suggestions, "mop_account_id": mop_account_id}


@router.post("/take")
def inbox_take(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Взять диалог в работу. Захват атомарный: один условный UPDATE, поэтому
    два сотрудника не могут забрать один диалог одновременно. Повторное
    нажатие тем же человеком — не ошибка."""
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        uid = getattr(user, "id", None)
        res = db.execute(text(
            "UPDATE messenger_leads SET assigned_user_id = :u, assigned_at = now()"
            " WHERE account_id = :a AND avito_chat_id = :c"
            "   AND (assigned_user_id IS NULL OR assigned_user_id = :u)"),
            {"u": uid, "a": req.account_id, "c": req.avito_chat_id})
        db.commit()
        if res.rowcount == 0:
            who = db.execute(text(
                "SELECT u.email FROM messenger_leads l"
                "  LEFT JOIN users u ON u.id = l.assigned_user_id"
                " WHERE l.account_id = :a AND l.avito_chat_id = :c"),
                {"a": req.account_id, "c": req.avito_chat_id}).fetchone()
            if who is None:
                return {"status": "error", "message": "Диалог ещё не заведён в работу"}
            return {"status": "busy",
                    "assigned_name": (who[0] or "").split("@")[0] if who[0] else "другой сотрудник",
                    "message": "Диалог уже ведёт другой сотрудник"}
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=req.account_id, action="Взял диалог в работу",
                       object_kind="диалог", object_name=req.avito_chat_id,
                       reason="сотрудник принял диалог в кабинете",
                       actor=ACTOR_USER, user_id=uid, source="inbox.take")
        except Exception:
            pass
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/release")
def inbox_release(req: InboxTakeRequest, user=Depends(get_current_user)):
    """Освободить диалог. Может тот, кто взял, и владелец платформы."""
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        uid = getattr(user, "id", None)
        cond = "" if getattr(user, "role", "") == "owner" else " AND assigned_user_id = :u"
        res = db.execute(text(
            "UPDATE messenger_leads SET assigned_user_id = NULL, assigned_at = NULL"
            " WHERE account_id = :a AND avito_chat_id = :c" + cond),
            {"u": uid, "a": req.account_id, "c": req.avito_chat_id})
        db.commit()
        if res.rowcount == 0:
            return {"status": "error", "message": "Диалог ведёт другой сотрудник"}
        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=req.account_id, action="Освободил диалог",
                       object_kind="диалог", object_name=req.avito_chat_id,
                       reason="работа над диалогом завершена",
                       actor=ACTOR_USER, user_id=uid, source="inbox.release")
        except Exception:
            pass
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/mop-handoff")
def inbox_mop_handoff(req: InboxTakeRequest, action: str, user=Depends(get_current_user)):
    """Use the existing MOP draft state machine to hand a dialog to a human or back to MOP."""
    action = str(action or "").strip().lower()
    if action not in {"human", "mop"}:
        return {"status":"error","message":"action must be human or mop"}
    db = SessionLocal()
    try:
        if req.account_id not in dict(_connected_account_ids(db, user)):
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        draft = db.execute(text("""SELECT id,status FROM mop_drafts
            WHERE account_id=:a AND avito_chat_id=:c
            ORDER BY COALESCE(updated_at,created_at) DESC,id DESC LIMIT 1"""),
            {"a":req.account_id,"c":req.avito_chat_id}).mappings().first()
        if not draft:
            return {"status":"error","message":"Для диалога пока нет MOP draft"}
        from app import mop_core as _mc
        actor = str(getattr(user,"id","") or "manager")
        if action == "human":
            row, current = _mc.set_status(db, int(draft["id"]), "human_required",
                ("draft_ready","in_progress","editing","custom_waiting","waiting_confirm","deleted","send_failed","new"),
                "handed_to_human", channel="web", actor_type="user", actor_id=actor)
            return {"status":"ok" if row else "noop","mop_status":"human_required" if row else current}
        row, current = _mc.set_status(db, int(draft["id"]), "draft_ready", ("human_required",),
            "returned_to_ai", channel="web", actor_type="user", actor_id=actor)
        return {"status":"ok" if row else "noop","mop_status":"draft_ready" if row else current}
    finally:
        db.close()


class InboxSendRequest(BaseModel):
    account_id: str
    avito_chat_id: str
    text: str


def _set_legacy_delivery_state(account_id: str, avito_chat_id: str,
                               status: str, error: str = ""):
    """Persist delivery result for legacy MOP drafts without touching external send."""
    import json as _json
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id,value FROM storage"
            " WHERE account_id='_telegram_drafts'"
            "   AND key LIKE 'messenger_draft:%'"
        )).fetchall()
        changed = 0
        for row_id, raw in rows:
            try:
                data = _json.loads(raw)
            except Exception:
                continue
            if (data.get("account_id") != account_id
                    or data.get("avito_chat_id") != avito_chat_id
                    or data.get("status") not in ("pending", "send_failed")):
                continue
            data["status"] = status
            if error:
                data["send_error"] = str(error)[:500]
            else:
                data.pop("send_error", None)
            db.execute(text("UPDATE storage SET value=:v WHERE id=:i"),
                       {"v": _json.dumps(data, ensure_ascii=False), "i": row_id})
            changed += 1
        if changed:
            db.commit()
        return changed
    finally:
        db.close()


def _background_targeted_sync(account_id: str, avito_chat_id: str):
    """Reconcile the accepted outgoing after the HTTP response is already returned.

    Avito acceptance is the send boundary. A read-after-write GET is useful for
    local projection, but must never keep the operator's send button blocked.
    """
    try:
        from app.api.messenger import sync_chat_messages
        targeted = sync_chat_messages(account_id, avito_chat_id) or {}
        if targeted.get("status") != "ok":
            print("[inbox_send] background targeted sync deferred %s/%s: %s" %
                  (account_id, avito_chat_id, targeted.get("status")), flush=True)
    except Exception as e:
        print("[inbox_send] background targeted sync failed: " + repr(e)[:150], flush=True)


@router.post("/send")
def inbox_send(req: InboxSendRequest, background: BackgroundTasks, user=Depends(get_current_user)):
    if not (req.text or "").strip():
        return {"status": "error", "message": "Пустое сообщение"}
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
        if req.account_id not in conn:
            return {"status": "error", "message": "Нельзя отправить через чужой или неподключённый аккаунт"}
        # BORIS_EMP_SEND_GUARD: у сотрудника своих слотов нет, поэтому _owner_scope
        # возвращал None и проверка readonly не выполнялась вовсе. Слот ищем
        # среди уже разрешённых conn, а право отвечать проверяем явно.
        _own = _owner_scope(db, user).filter(AccountSlot.account_id == req.account_id).first()
        if _own is None:
            from sqlalchemy import text as _txt_snd
            _row = db.execute(_txt_snd(
                "SELECT can_reply FROM user_account_access"
                " WHERE user_id = :u AND account_id = :a LIMIT 1"),
                {"u": getattr(user, "id", 0), "a": req.account_id}).fetchone()
            if _row is not None and not _row[0]:
                return {"status": "error", "message": "Нет права отвечать в этом аккаунте"}
        slot = _own or db.query(AccountSlot).filter(
            AccountSlot.account_id == req.account_id).first()
        if slot and _effective_status(slot) == "readonly":
            return {"status": "error", "message": "Подписка на аккаунт неактивна — отправка недоступна"}
    finally:
        db.close()
    from app.api.messenger import send_message, _store_messages_locally
    res = send_message(req.account_id, req.avito_chat_id, req.text.strip())
    if res.get("status") != "ok":
        _set_legacy_delivery_state(
            req.account_id, req.avito_chat_id, "send_failed",
            res.get("message") or res.get("status") or "send failed")
        return res
    _set_legacy_delivery_state(req.account_id, req.avito_chat_id, "sent")
    # Кто ответил. Пишем ДО синхронизации и в своей таблице: Avito автора
    # не возвращает, а перезапись сообщений синхронизацией эту отметку не тронет.
    # Ошибка записи не должна мешать отправке — сообщение клиенту уже ушло.
    try:
        _adb = SessionLocal()
        try:
            _adb.execute(text(
                "INSERT INTO outgoing_authors (account_id, avito_chat_id, user_id, text, sent_at)"
                " VALUES (:a, :c, :u, :t, now())"),
                {"a": req.account_id, "c": req.avito_chat_id,
                 "u": getattr(user, "id", None), "t": req.text.strip()[:2000]})
            _adb.commit()
        finally:
            _adb.close()
    except Exception as e:
        print("[inbox_send] author log failed: " + repr(e)[:150], flush=True)
    # Журнал действий: нулевое правило Конституции — ответ покупателю виден клиенту
    try:
        from app.services.action_log import log_action, ACTOR_USER
        log_action(account_id=req.account_id, action="Ответил покупателю",
                   object_kind="диалог", object_name=req.avito_chat_id,
                   after_val=req.text.strip()[:300],
                   reason="ответ отправлен из Единого окна",
                   actor=ACTOR_USER, user_id=getattr(user, "id", None),
                   source="inbox_send")
    except Exception:
        pass
    # Черновик закрываем сразу, не дожидаясь синхронизации: иначе до десяти минут
    # менеджер видит в кабинете уже отправленный ответ и может отправить его
    # второй раз. Фильтр «продавец ответил» сработал бы только после обхода.
    try:
        _ddb = SessionLocal()
        try:
            _ddb.execute(text(
                "UPDATE mop_drafts SET status = 'sent', sent_at = now(),"
                " reply_author = :w, updated_at = now()"
                " WHERE account_id = :a AND avito_chat_id = :c"
                "   AND status NOT IN ('sent', 'deleted', 'no_reply_required')"),
                {"a": req.account_id, "c": req.avito_chat_id,
                 "w": (getattr(user, "email", None) or "")[:120]})
            _ddb.commit()
        finally:
            _ddb.close()
    except Exception as e:
        print("[inbox_send] draft mark failed: " + repr(e)[:150], flush=True)
    # Fast projection: if Avito returned the created message, persist that exact
    # provider id immediately. Never invent a synthetic id (which would later
    # duplicate the real message). Then perform one targeted authoritative GET
    # for the open chat; account-wide sync remains background work only.
    immediate = res.get("avito_message") if isinstance(res, dict) else None
    if isinstance(immediate, dict) and immediate.get("id"):
        try:
            _store_messages_locally(req.account_id, req.avito_chat_id, "", [immediate])
        except Exception as e:
            print("[inbox_send] immediate projection failed: " + repr(e)[:150], flush=True)
    # Do not block the operator on Avito read-after-write propagation. The UI
    # already appends an optimistic outgoing row; reconcile the authoritative
    # thread after the response has been returned.
    background.add_task(_background_targeted_sync, req.account_id, req.avito_chat_id)
    return {"status": "ok", "projection": "background"}


class InboxReadRequest(BaseModel):
    account_id: str
    avito_chat_id: str


@router.post("/mark_read")
def inbox_mark_read(req: InboxReadRequest, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
        if req.account_id not in conn:
            return {"status": "error", "message": "Аккаунт не найден или не подключён"}
        upd = db.execute(text(
            "UPDATE messenger_dialog_state SET last_read_at = now(), updated_at = now() "
            " WHERE user_id = :uid AND account_id = :acc AND avito_chat_id = :cid"
        ), {"uid": user.id, "acc": req.account_id, "cid": req.avito_chat_id})
        if upd.rowcount == 0:
            db.execute(text(
                "INSERT INTO messenger_dialog_state (user_id, account_id, avito_chat_id, last_read_at, updated_at) "
                "VALUES (:uid, :acc, :cid, now(), now())"
            ), {"uid": user.id, "acc": req.account_id, "cid": req.avito_chat_id})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()
