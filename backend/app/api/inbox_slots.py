"""Слоты аккаунтов «Единого центра сообщений».

Оплачиваются слоты, а не подключённые аккаунты. Ключи Avito пишутся в
существующую таблицу accounts (avito_client_id/secret/avito_user_id),
дублирующего хранилища креденшлов не заводим.
"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import text, bindparam

from app.db.session import SessionLocal
from app.models.account import Account
from app.models.account_slot import AccountSlot
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

    for url in TARIFF_PROBES:
        st, body = _http("GET", url, headers=head)
        if st == 200:
            out["tariff_ok"] = True
            out["raw"] = "tariff %s %s: ok" % (url, st)
            break
        if st in (401, 403):
            out["tariff_ok"] = False
            out["message"] = ("Аккаунт подключён, но автозагрузка недоступна. "
                              "Обычно причина одна из двух: тариф Avito ниже "
                              "«Расширенного» либо автозагрузка не подключена "
                              "в кабинете Avito.")
            out["raw"] = "tariff %s %s: %s" % (url, st, str(body)[:200])
            break
        out["raw"] = "tariff %s %s: %s" % (url, st, str(body)[:200])
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
              "      AND (item_owner_id IS NULL OR item_owner_id = (SELECT avito_user_id FROM account_slots WHERE account_id = messenger_messages.account_id LIMIT 1))"
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
        "   AND lower(coalesce(m.direction,'')) LIKE 'in%' AND coalesce(m.msg_type,'') <> 'system'    AND (m.item_owner_id IS NULL OR m.item_owner_id = (SELECT avito_user_id FROM account_slots WHERE account_id = m.account_id LIMIT 1)) "
        "   AND ( s.last_read_at IS NULL "
        "         OR to_timestamp(coalesce(m.avito_created_at,0)) > s.last_read_at ) "
        " GROUP BY 1,2"
    ).bindparams(bindparam("ids", expanding=True))
    for acc, cid, n in db.execute(q, {"uid": user_id, "ids": ids}).all():
        out[(acc, cid)] = int(n or 0)
    return out


def _connected_account_ids(db, user):
    rows = _owner_scope(db, user).filter(AccountSlot.account_id.isnot(None)).all()
    out = []
    for s in rows:
        if _effective_status(s) in ("connected", "readonly") and s.account_id:
            out.append((s.account_id, s.account_name or s.account_id))
    return out


def _fmt_ts(sec):
    if not sec:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).astimezone().strftime("%H:%M")
    except Exception:
        return ""


@router.get("/accounts")
def inbox_accounts(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        slots = _owner_scope(db, user).filter(
            AccountSlot.account_id.isnot(None)).order_by(AccountSlot.slot_no.asc()).all()
        acc_ids = [s.account_id for s in slots if s.account_id]
        counts = _counts(db, acc_ids)
        unread = _unread_map(db, user.id, acc_ids)
        unread_by_acc = {}
        for (acc, _cid), n in unread.items():
            unread_by_acc[acc] = unread_by_acc.get(acc, 0) + n
        accounts = []
        total_unread = 0
        total_unanswered = 0
        for s in slots:
            st = _effective_status(s)
            u = unread_by_acc.get(s.account_id, 0)
            total_unread += u
            total_unanswered += counts.get(s.account_id or "", {}).get("unanswered", 0)
            accounts.append({
                "account_id": s.account_id or "",
                "name": s.account_name or s.account_id or "",
                "phone": s.display_phone or "",
                "status": st,
                "unread": u,
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
        _own = {r[0]: (r[1] or "") for r in db.execute(text(
            "SELECT account_id, avito_user_id FROM account_slots WHERE account_id IS NOT NULL")).all()}
        for acc, cid, item_id, item_title, direction, txt, created, iowner, iurl in rows:
            _is_purchase = bool(iowner) and bool(_own.get(acc)) and str(iowner) != str(_own.get(acc))
            dialogs.append({
                "account_id": acc,
                "account_name": name_by_acc.get(acc, acc),
                "avito_chat_id": cid,
                "client_name": "Покупатель",
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
            dialogs = [d for d in dialogs if not d.get("is_purchase")]
        elif kind == "purchases":
            dialogs = [d for d in dialogs if d.get("is_purchase")]
        if _q_search:
            _fields = ("last_text", "item_title", "avito_chat_id", "client_name", "account_name")
            dialogs = [d for d in dialogs
                       if any(_q_search in str(d.get(f) or "").lower() for f in _fields)]
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
            "SELECT direction, text, avito_created_at, item_title, msg_type, content_type, media_ref, item_url "
            "  FROM messenger_messages WHERE account_id = :acc AND avito_chat_id = :cid "
            " ORDER BY avito_created_at ASC, id ASC"
        )
        rows = db.execute(q, {"acc": account_id, "cid": avito_chat_id}).all()
        messages = [{
            "direction": "out" if str(d or "").lower().startswith("out") else "in",
            "text": t or "",
            "at": _fmt_ts(c),
            "msg_type": mt or ("seller" if str(d or "").lower().startswith("out") else "user"),
            "content_type": ct or "text",
            "media_ref": mr,
            "voice_url": (f"/api/inbox/voice?account_id={account_id}&voice_id={mr}" if (ct == "voice" and mr) else None),
        } for (d, t, c, _title, mt, ct, mr, _iu) in rows]
        item_title = next((r[3] for r in rows if r[3]), "")
        dialog = {
            "account_id": account_id,
            "account_name": conn.get(account_id, account_id),
            "avito_chat_id": avito_chat_id,
            "client_name": "Покупатель",
            "item_title": item_title or "",
            "item_url": next((r[7] for r in rows if r[7]), ""),
            "chat_url": "https://www.avito.ru/profile/messenger/channel/" + str(avito_chat_id),
            "phone": "",
        }
        return {"status": "ok", "dialog": dialog, "messages": messages}
    finally:
        db.close()


class InboxSendRequest(BaseModel):
    account_id: str
    avito_chat_id: str
    text: str


@router.post("/send")
def inbox_send(req: InboxSendRequest, user=Depends(get_current_user)):
    if not (req.text or "").strip():
        return {"status": "error", "message": "Пустое сообщение"}
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
        if req.account_id not in conn:
            return {"status": "error", "message": "Нельзя отправить через чужой или неподключённый аккаунт"}
        slot = _owner_scope(db, user).filter(AccountSlot.account_id == req.account_id).first()
        if slot and _effective_status(slot) == "readonly":
            return {"status": "error", "message": "Подписка на аккаунт неактивна — отправка недоступна"}
    finally:
        db.close()
    from app.api.messenger import send_message, sync_chats
    res = send_message(req.account_id, req.avito_chat_id, req.text.strip())
    if res.get("status") != "ok":
        return res
    try:
        sync_chats(req.account_id, limit=50)
    except Exception as e:
        print("[inbox_send] sync after send failed: " + repr(e)[:150], flush=True)
    return {"status": "ok"}


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
