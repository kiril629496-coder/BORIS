from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.account import Account
from app.models.user import User
from app.api.auth import get_current_user
from app.crypto_utils import encrypt_secret, decrypt_secret

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

Base.metadata.create_all(bind=engine, tables=[Account.__table__])

class CreateAccountRequest(BaseModel):
    account_id: str
    name: str
    avito_login: str = ""
    avito_password: str = ""
    comment: str = ""
    avito_client_id: str = ""
    avito_client_secret: str = ""
    company_website: str = ""
    client_goal: str = ""
    client_goal_text: str = ""
    company_niche: str = ""
    company_tone: str = ""
    company_description: str = ""
    company_advantages: str = ""

@router.post("/create")
def create_account(req: CreateAccountRequest):
    db = SessionLocal()
    try:
        existing = db.query(Account).filter(Account.account_id == req.account_id).first()
        if existing:
            return {"status": "error", "message": "Аккаунт с таким ID уже существует"}
        acc = Account(
            account_id=req.account_id, name=req.name,
            avito_login=req.avito_login or None, avito_password=req.avito_password or None,
            comment=req.comment or None,
            avito_client_id=encrypt_secret(req.avito_client_id) or None, avito_client_secret=encrypt_secret(req.avito_client_secret) or None,
            company_website=req.company_website or None, company_niche=req.company_niche or None,
            client_goal=req.client_goal or None, client_goal_text=req.client_goal_text or None,
            company_tone=req.company_tone or None, company_description=req.company_description or None,
            company_advantages=req.company_advantages or None
        )
        db.add(acc)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()

@router.get("/list")
def list_accounts(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        if db.query(Account).count() == 0:
            # первый запуск — создаём дефолтный аккаунт из текущих данных
            default = Account(account_id="otdushi", name="От Души — поздравления")
            db.add(default)
            db.commit()

        if user.role == "owner":
            accs = db.query(Account).order_by(Account.created_at.asc()).all()
        else:
            # агентство: все аккаунты владельца + свой (на случай старой привязки)
            # + выданные через user_account_access (сотрудники клиента)
            from sqlalchemy import text as _uaa_text
            _access_ids = [r[0] for r in db.execute(_uaa_text(
                "SELECT account_id FROM user_account_access"
                " WHERE user_id = :u AND can_view = TRUE"),
                {"u": user.id}).fetchall()]
            accs = db.query(Account).filter(
                (Account.owner_user_id == user.id) |
                (Account.account_id == user.account_id) |
                (Account.account_id.in_(_access_ids))
            ).order_by(Account.created_at.asc()).all()

        # Роль пользователя В КАЖДОМ аккаунте: от неё зависят подпись в шапке
        # и состав меню. Сотруднику незачем видеть кошелёк и реквизиты владельца.
        from sqlalchemy import text as _role_text
        _roles = {r[0]: r[1] for r in db.execute(_role_text(
            "SELECT account_id, role FROM user_account_access WHERE user_id = :u"),
            {"u": user.id}).fetchall()}

        def _role_in(a):
            if user.role == "owner" or a.owner_user_id == user.id:
                return "owner"
            return _roles.get(a.account_id) or "employee"

        return {"status": "ok", "accounts": [{
            "account_id": a.account_id, "name": a.name,
            "role": _role_in(a),
            "company_website": a.company_website or "",
            "client_goal": a.client_goal or "",
            "client_goal_text": a.client_goal_text or "",
        } for a in accs]}
    finally:
        db.close()


class SetAvitoKeysRequest(BaseModel):
    account_id: str
    avito_client_id: str
    avito_client_secret: str
    avito_user_id: str = ""

@router.post("/set_avito_keys")
def set_avito_keys(req: SetAvitoKeysRequest):
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == req.account_id).first()
        if not acc:
            return {"status": "error", "message": "Аккаунт не найден"}
        acc.avito_client_id = encrypt_secret(req.avito_client_id)
        acc.avito_client_secret = encrypt_secret(req.avito_client_secret)
        if req.avito_user_id:
            acc.avito_user_id = req.avito_user_id
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()

@router.get("/avito_keys_status")
def avito_keys_status(account_id: str):
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"status": "error", "message": "Аккаунт не найден"}
        has_keys = bool(acc.avito_client_id and acc.avito_client_secret)
        return {"status": "ok", "has_keys": has_keys, "avito_user_id": acc.avito_user_id or ""}
    finally:
        db.close()


class UpdateAccountRequest(BaseModel):
    telegram_chat_id: Optional[str] = None
    name: str = None
    avito_login: str = None
    avito_password: str = None
    comment: str = None
    avito_client_id: str = None
    avito_client_secret: str = None
    company_website: str = None
    client_goal: str = None
    client_goal_text: str = None
    company_niche: str = None
    company_tone: str = None
    company_description: str = None
    company_advantages: str = None


@router.get("/billing_preview")
def billing_preview(user: User = Depends(get_current_user)):
    """Сколько будет стоить подключение ещё одного аккаунта — показываем ДО нажатия."""
    import json as _json
    from datetime import datetime as _dt
    from app.models.account import Account as _Acc
    from app.models.storage import Storage as _St
    from app.pricing import price_per_account, period_total, upgrade_charge

    db = SessionLocal()
    try:
        n = db.query(_Acc).filter(_Acc.owner_user_id == user.id,
                                  _Acc.billing_mode == "auto").count()
        row = db.query(_St).filter(_St.account_id == "user:%s" % user.id,
                                   _St.key == "billing").first()
        tier, days_left = "tariff_1", 30
        if row:
            try:
                d = _json.loads(row.value)
                tier = d.get("tier") or "tariff_1"
                if d.get("period_start"):
                    passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                    days_left = max(0, 30 - passed)
            except Exception:
                pass
        if tier not in ("tariff_1", "tariff_2"):
            tier = "tariff_1"
        return {
            "status": "ok",
            "аккаунтов_сейчас": n,
            "тариф": tier,
            "цена_за_аккаунт_сейчас": price_per_account(max(1, n), tier),
            "дней_до_конца_периода": days_left,
            "доплата_за_ещё_один": upgrade_charge(max(1, n), 1, days_left, tariff=tier),
            "со_следующего_периода": period_total(n + 1, tier),
            "цена_за_аккаунт_станет": price_per_account(n + 1, tier),
        }
    finally:
        db.close()

@router.get("/agency_overview")
def agency_overview(user: User = Depends(get_current_user)):
    """Панель «Директор» для владельца агентства: все свои аккаунты, их лимиты и общий счёт."""
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    from app.models.account import Account as _Acc
    from app.models.storage import Storage as _St
    from app.api.billing import TARIFFS, _load_billing
    from app.pricing import price_per_account, period_total

    db = SessionLocal()
    try:
        accs = db.query(_Acc).filter(_Acc.owner_user_id == user.id).order_by(_Acc.created_at.asc()).all()
        auto = [a for a in accs if (a.billing_mode or "manual") == "auto"]
        n = max(1, len(auto))

        row = db.query(_St).filter(_St.account_id == "user:%s" % user.id, _St.key == "billing").first()
        tier, days_left = "none", None
        if row:
            try:
                d = _json.loads(row.value)
                tier = d.get("tier") or "none"
                if d.get("period_start"):
                    passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                    days_left = max(0, 30 - passed)
            except Exception:
                pass
        bill_tier = tier if tier in ("tariff_1", "tariff_2") else "tariff_1"

        items = []
        # Показываем ВСЕ аккаунты владельца, а не только с автосписанием:
        # режим у каждого виден в своей колонке. Раньше клиент с ручным
        # режимом видел счётчик «2 аккаунта» и пустую таблицу под ним.
        for a in accs:
            b = _load_billing(a.account_id) or {}
            u = b.get("usage") or {}
            lim = TARIFFS.get(b.get("tier") or tier, TARIFFS["none"])["limits"]
            items.append({
                "account_id": a.account_id,
                "название": a.name or a.account_id,
                "режим": a.billing_mode or "manual",
                "тариф": b.get("tier") or tier,
                "баннеры": "%s / %s" % (u.get("banners", 0), lim.get("banners", 0)),
                "объявления": "%s / %s" % (u.get("listings", 0), lim.get("listings", 0)),
                "лимит_баннеров_исчерпан": u.get("banners", 0) >= lim.get("banners", 0) if lim.get("banners") else False,
                "avito_подключён": bool(a.avito_client_id),
            })

        return {
            "status": "ok",
            "аккаунтов": len(accs),
            "из_них_платных": len(auto),
            "тариф": tier,
            "цена_за_аккаунт": price_per_account(n, bill_tier),
            "к_оплате_за_период": period_total(n, bill_tier),
            "дней_до_конца_периода": days_left,
            "аккаунты": items,
        }
    finally:
        db.close()

@router.get("/{account_id}")
def get_account(account_id: str):
    """Полные данные аккаунта, включая компанию — для формы редактирования."""
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"status": "error", "message": "Аккаунт не найден"}
        return {"status": "ok", "account": {
            "account_id": acc.account_id, "name": acc.name,
            "avito_login": acc.avito_login or "", "avito_password": acc.avito_password or "",
            "comment": acc.comment or "",
            "avito_client_id": decrypt_secret(acc.avito_client_id) or "", "avito_client_secret": decrypt_secret(acc.avito_client_secret) or "",
            "company_website": acc.company_website or "", "company_niche": acc.company_niche or "",
            "client_goal": acc.client_goal or "", "client_goal_text": acc.client_goal_text or "",
            "company_tone": acc.company_tone or "", "company_description": acc.company_description or "",
            "company_advantages": acc.company_advantages or "",
        }}
    finally:
        db.close()


@router.post("/{account_id}/update")
def update_account(account_id: str, req: UpdateAccountRequest):
    """Частичное обновление аккаунта — только те поля, что переданы (не-None)."""
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"status": "error", "message": "Аккаунт не найден"}
        for field in ("name", "avito_login", "avito_password", "comment",
                      "avito_client_id", "avito_client_secret",
                      "company_website", "company_niche", "company_tone", "client_goal", "client_goal_text",
                      "company_description", "company_advantages",
                      # чат для напоминаний мини-CRM: у каждого клиента свой
                      "telegram_chat_id"):
            new_value = getattr(req, field)
            if new_value is not None:
                if field in ("avito_client_id", "avito_client_secret") and new_value:
                    new_value = encrypt_secret(new_value)
                setattr(acc, field, new_value or None)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Агентский сегмент: подключение дополнительного Avito-аккаунта.
# Цена зависит от числа подключённых аккаунтов (app/pricing.py).
# Доплата — сразу, пропорционально остатку оплаченного периода, по ТЕКУЩЕЙ вилке.
# Скидка новой вилки включается со следующего периода.
# ---------------------------------------------------------------------------

_TRANSLIT = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i",
    "й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t",
    "у":"u","ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"",
    "э":"e","ю":"yu","я":"ya"," ":"_","-":"_",
}


def _slugify(text: str) -> str:
    """Кириллица → латиница, чтобы адрес аккаунта был читаемым, а не acc_12345."""
    out = []
    for ch in (text or "").lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and (ch.isalnum() or ch == "_"):
            out.append(ch)
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:24]


class AddAccountRequest(BaseModel):
    name: str = ""




@router.post("/add_account")
def add_account(req: AddAccountRequest, user: User = Depends(get_current_user)):
    """Подключить ещё один Avito-аккаунт к своей организации."""
    import re as _re, random as _random, string as _string, json as _json
    from datetime import datetime as _dt
    from app.models.account import Account as _Acc
    from app.models.storage import Storage as _St
    from app.pricing import price_per_account, period_total, upgrade_charge

    if getattr(user, "role", None) not in ("client", "owner"):
        return {"status": "error", "message": "Недостаточно прав"}

    db = SessionLocal()
    try:
        n = db.query(_Acc).filter(_Acc.owner_user_id == user.id,
                                  _Acc.billing_mode == "auto").count()
        base = _slugify(req.name) or _slugify(user.email.split("@")[0]) or "acc"
        slug = "%s_%s" % (base, "".join(_random.choices(_string.digits, k=5)))

        row = db.query(_St).filter(_St.account_id == "user:%s" % user.id,
                                   _St.key == "billing").first()
        tier, days_left = "tariff_1", 30
        if row:
            try:
                d = _json.loads(row.value)
                tier = d.get("tier") or "tariff_1"
                if d.get("period_start"):
                    passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                    days_left = max(0, 30 - passed)
            except Exception:
                pass
        if tier not in ("tariff_1", "tariff_2"):
            tier = "tariff_1"

        charge = upgrade_charge(max(1, n), 1, days_left, tariff=tier)

        acc = _Acc(account_id=slug, name=req.name or ("Аккаунт %d" % (n + 1)),
                   owner_user_id=user.id, billing_mode="auto")
        db.add(acc)
        db.commit()

        # фиксируем долг на самом аккаунте: иначе подключение двух подряд
        # без оплаты посчиталось бы как один
        if charge > 0:
            db.add(_St(account_id=slug, key="billing",
                       value=_json.dumps({"upgrade_due": charge}, ensure_ascii=False)))
            db.commit()

        return {
            "status": "ok",
            "account_id": slug,
            "аккаунтов_стало": n + 1,
            "доплата": charge,
            "оплачено": False,
            "со_следующего_периода": period_total(n + 1, tier),
            "цена_за_аккаунт": price_per_account(n + 1, tier),
            "сообщение": "Аккаунт подключён. Доплата %d ₽ за оставшиеся %d дней текущего периода." % (charge, days_left),
        }
    finally:
        db.close()


