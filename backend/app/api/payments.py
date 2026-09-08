# SECURITY NOTE: customer-facing payment reads/links are tenant-scoped; only the
# signed Robokassa callback remains intentionally public.
"""
Напоминания об оплате клиентов — ТОЛЬКО для владельца (панель Директора).
Хранит по каждому аккаунту: дату оплаты, сумму, период (дней). Считает остаток и цвет.
Клиенты этого НЕ видят — данные отдаются только в director_overview.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.api.auth import get_current_user, require_owner
from pydantic import BaseModel
import json
from datetime import date, datetime, timedelta, timezone

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _require_account_view(account_id: str, user):
    from app.db.session import SessionLocal
    from app.services.command_policy import account_visible
    db = SessionLocal()
    try:
        if not account_visible(db, user, account_id):
            raise HTTPException(status_code=403, detail="forbidden_account")
    finally:
        db.close()


def _require_private_platform_owner(user=Depends(get_current_user)):
    from app.services.platform_roles import is_private_platform_owner
    if not is_private_platform_owner(user):
        raise HTTPException(status_code=403, detail="private_platform_owner_required")
    return user


# Пороги напоминаний (дней до конца оплаты)
WARN_YELLOW = 10   # 10..5 дней — жёлтый
WARN_RED = 4       # <=4 дня или просрочено — красный


def compute_status(account_id: str, db) -> dict:
    """Вернуть статус оплаты аккаунта: дата окончания, остаток дней, цвет."""
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "payment_status").first()
    if not row:
        return {"has_payment": False}
    p = json.loads(row.value)
    paid_at = p.get("paid_at")            # ISO дата оплаты
    period_days = p.get("period_days", 30)
    amount = p.get("amount_rub", 0)
    if not paid_at:
        return {"has_payment": False}
    paid_date = datetime.fromisoformat(paid_at).date()
    until = paid_date + timedelta(days=period_days)
    days_left = (until - date.today()).days
    if days_left <= WARN_RED:
        color = "red"
    elif days_left <= WARN_YELLOW:
        color = "yellow"
    else:
        color = "green"
    return {
        "has_payment": True,
        "paid_at": paid_at,
        "period_days": period_days,
        "amount_rub": amount,
        "paid_until": until.isoformat(),
        "days_left": days_left,
        "color": color,
        "overdue": days_left < 0,
    }


class SetPaymentBody(BaseModel):
    account_id: str
    amount_rub: float
    period_days: int = 30
    paid_at: str | None = None   # ISO; если не задано — сегодня


@router.get("/status")
def get_status(account_id: str, user=Depends(get_current_user)):
    _require_account_view(account_id, user)
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return {"status": "ok", "payment": compute_status(account_id, db)}
    finally:
        db.close()


@router.post("/set")
def set_payment(body: SetPaymentBody, _owner=Depends(require_owner)):
    """Отметить оплату клиента (только владелец вызывает из панели Директора)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.api.avito import _audit_log
    db = SessionLocal()
    try:
        paid = body.paid_at or date.today().isoformat()
        data = {"paid_at": paid, "period_days": body.period_days, "amount_rub": body.amount_rub}
        row = db.query(Storage).filter(Storage.account_id == body.account_id, Storage.key == "payment_status").first()
        if row:
            row.value = json.dumps(data, ensure_ascii=False)
        else:
            db.add(Storage(account_id=body.account_id, key="payment_status", value=json.dumps(data, ensure_ascii=False)))
        db.commit()
        _audit_log(body.account_id, "set_payment",
                   f"оплата {body.amount_rub}₽ на {body.period_days} дней от {paid}", "director")
        return {"status": "ok", "payment": compute_status(body.account_id, db)}
    finally:
        db.close()


# ============ РОБОКАССА: ссылка на оплату и приём уведомлений ============
import hashlib
import os
from urllib.parse import quote
from fastapi import Request

RK_LOGIN = os.getenv("ROBOKASSA_LOGIN", "")
RK_PASS1 = os.getenv("ROBOKASSA_PASS1", "")
RK_PASS2 = os.getenv("ROBOKASSA_PASS2", "")

# Что можно купить: код -> сумма и что начислить
PACKAGES = {
    "ban1":    {"sum": 250,   "unit": "banners", "qty": 1,   "title": "1 баннер"},
    "ban10":   {"sum": 2300, "sum_ag": 1800,  "unit": "banners", "qty": 10,  "title": "10 баннеров"},
    "ban30":   {"sum": 6300, "sum_ag": 5100,  "unit": "banners", "qty": 30,  "title": "30 баннеров"},
    "ban50":   {"sum": 10500, "sum_ag": 8000, "unit": "banners", "qty": 50,  "shared": True, "title": "50 баннеров"},
    "ban100":  {"sum": 20000, "sum_ag": 15000, "unit": "banners", "qty": 100, "shared": True, "title": "100 баннеров"},
    "prof_ext":{"sum": 800, "sum_ag": 700,   "unit": "images",  "qty": 2,   "title": "Расширенный профиль"},
    "prof_max":{"sum": 2400, "sum_ag": 2100,  "unit": "images",  "qty": 6,   "title": "Максимальный профиль"},
    "msg1700": {"sum": 10000, "sum_ag": 9000, "unit": "messages", "qty": 1700, "days": 30,
                "title": "ИИ Менеджер — базовый пакет", "desc": "1 700 сообщений покупателям. Срок 30 дней."},
    "msg2000": {"sum": 8000, "sum_ag": 7000,  "unit": "messages", "qty": 2000, "addon": True,
                "title": "+2 000 сообщений", "desc": "2 000 сообщений. До конца текущего периода."},
    "msg3000": {"sum": 11000, "sum_ag": 10000, "unit": "messages", "qty": 3000, "addon": True,
                "title": "+3 000 сообщений", "desc": "3 000 сообщений. До конца текущего периода."},
    "rop1500": {"sum": 20000, "sum_ag": 20000, "unit": "rop_pack", "minutes": 1500, "chats": 450, "rep_calls": 30, "rep_chats": 30, "days": 30,
                "title": "ИИ РОП — базовый пакет", "desc": "1 500 минут звонков · 450 разборов переписок · 30 отчётов по звонкам · 30 по перепискам. Срок 30 дней."},
    "rop_a300":  {"sum": 5000,  "sum_ag": 5000,  "unit": "rop_pack", "minutes": 300,  "chats": 90,  "rep_calls": 5,  "rep_chats": 5,  "addon": True,
                  "title": "+300 минут", "desc": "300 минут · 90 переписок · 5 + 5 отчётов. До конца текущего периода."},
    "rop_a500":  {"sum": 7500,  "sum_ag": 7500,  "unit": "rop_pack", "minutes": 500,  "chats": 150, "rep_calls": 10, "rep_chats": 10, "addon": True,
                  "title": "+500 минут", "desc": "500 минут · 150 переписок · 10 + 10 отчётов. До конца текущего периода."},
    "rop_a700":  {"sum": 10500, "sum_ag": 10500, "unit": "rop_pack", "minutes": 700,  "chats": 200, "rep_calls": 15, "rep_chats": 15, "addon": True,
                  "title": "+700 минут", "desc": "700 минут · 200 переписок · 15 + 15 отчётов. До конца текущего периода."},
    "rop_a1000": {"sum": 14000, "sum_ag": 14000, "unit": "rop_pack", "minutes": 1000, "chats": 300, "rep_calls": 20, "rep_chats": 20, "addon": True,
                  "title": "+1 000 минут", "desc": "1 000 минут · 300 переписок · 20 + 20 отчётов. До конца текущего периода."},
    "rop_a1500": {"sum": 21000, "sum_ag": 21000, "unit": "rop_pack", "minutes": 1500, "chats": 400, "rep_calls": 30, "rep_chats": 30, "addon": True,
                  "title": "+1 500 минут", "desc": "1 500 минут · 400 переписок · 30 + 30 отчётов. До конца текущего периода."},
    "rop_a2000": {"sum": 26000, "sum_ag": 26000, "unit": "rop_pack", "minutes": 2000, "chats": 550, "rep_calls": 40, "rep_chats": 40, "addon": True,
                  "title": "+2 000 минут", "desc": "2 000 минут · 550 переписок · 40 + 40 отчётов. До конца текущего периода."},
    "sub_auto":{"sum": 7000,  "tier": "tariff_1",    "days": 30, "title": "Автопилот 2.0"},
    "sub_max": {"sum": 14000, "tier": "tariff_2",     "days": 30, "title": "Автопилот MAX"},
    "post_tg":  {"sum": 9000,  "posting": "tg",   "days": 30, "posts_per_day": 3, "title": "Постинг Telegram"},
    "post_vk":  {"sum": 12000, "posting": "vk",   "days": 30, "posts_per_day": 3, "title": "Постинг ВКонтакте"},
    "post_both":{"sum": 18000, "posting": "both", "days": 30, "posts_per_day": 3, "title": "Постинг ВК + Telegram"},
}


_PHONE_PRICE_STORAGE_ACCOUNT = "__platform_pricing__"
_PHONE_PRICE_STORAGE_KEY = "phone_monthly"


def _stored_phone_monthly_setting():
    """Durable platform-owner override. Any stored row wins over environment fallback."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as _phone_price_text
    db = SessionLocal()
    try:
        raw = db.execute(_phone_price_text("""
            SELECT value FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC LIMIT 1
        """), {"a": _PHONE_PRICE_STORAGE_ACCOUNT, "k": _PHONE_PRICE_STORAGE_KEY}).scalar()
    except Exception:
        # Commercial pricing fails closed if the settings store is unavailable.
        return None
    finally:
        db.close()
    if raw is None:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {"enabled": False, "invalid": True}
    return data if isinstance(data, dict) else {"enabled": False, "invalid": True}


def _configured_phone_monthly_package():
    """Expose BORIS Phone only when a real positive commercial price is configured."""
    stored = _stored_phone_monthly_setting()
    if stored is not None:
        if not bool(stored.get("enabled")):
            return None
        raw = stored.get("price_rub")
    else:
        raw = str(os.getenv("BORIS_PHONE_MONTHLY_PRICE_RUB", "") or "").strip()
    try:
        price = int(raw)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return {
        "sum": price,
        "unit": "phone_subscription",
        "days": 30,
        "title": "BORIS Phone",
        "desc": "Телефония BORIS на 30 дней. Минуты оператора оплачиваются отдельно по тарифу связи.",
    }


def _package_catalog() -> dict:
    """Single live catalog for UI, Robokassa and wallet; no import-time Phone price cache."""
    out = dict(PACKAGES)
    phone = _configured_phone_monthly_package()
    if phone:
        out["phone_monthly"] = phone
    else:
        out.pop("phone_monthly", None)
    return out


def _phone_commercial_settings_status() -> dict:
    stored = _stored_phone_monthly_setting()
    package = _configured_phone_monthly_package()
    env_present = bool(str(os.getenv("BORIS_PHONE_MONTHLY_PRICE_RUB", "") or "").strip())
    source = "platform_storage" if stored is not None else ("environment" if env_present else "not_configured")
    return {
        "status": "ok",
        "enabled": bool(package),
        "price_rub": int(package["sum"]) if package else None,
        "source": source,
        "stored_override_present": stored is not None,
        "environment_fallback_present": env_present,
        "truth": "Phone is saleable only when one positive real commercial price is configured.",
    }


class PhoneCommercialSettingsBody(BaseModel):
    enabled: bool
    price_rub: int | None = None
    confirm: bool = False


@router.get("/phone-commercial-settings")
def phone_commercial_settings(_owner=Depends(_require_private_platform_owner)):
    """Private platform-owner view. Never invents a price."""
    _require_private_platform_owner(_owner)
    return _phone_commercial_settings_status()


@router.post("/phone-commercial-settings")
def set_phone_commercial_settings(
    body: PhoneCommercialSettingsBody,
    _owner=Depends(_require_private_platform_owner),
):
    """Persist the one global BORIS Phone monthly sale price without editing .env."""
    _require_private_platform_owner(_owner)
    if not body.confirm:
        raise HTTPException(status_code=409, detail="explicit_confirmation_required")
    if body.enabled:
        try:
            price = int(body.price_rub)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="positive_price_required")
        if price <= 0 or price > 10_000_000:
            raise HTTPException(status_code=400, detail="positive_price_required")
    else:
        price = None

    from app.db.session import SessionLocal
    from sqlalchemy import text as _phone_price_text
    payload = json.dumps({
        "enabled": bool(body.enabled),
        "price_rub": price,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)
    db = SessionLocal()
    try:
        db.execute(_phone_price_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": "platform|phone_monthly_price"
        })
        rows = db.execute(_phone_price_text("""
            SELECT id FROM storage
            WHERE account_id=:a AND key=:k
            ORDER BY id DESC
            FOR UPDATE
        """), {"a": _PHONE_PRICE_STORAGE_ACCOUNT, "k": _PHONE_PRICE_STORAGE_KEY}).fetchall()
        if rows:
            keep_id = int(rows[0][0])
            db.execute(_phone_price_text("UPDATE storage SET value=:v WHERE id=:i"), {
                "v": payload, "i": keep_id
            })
            if len(rows) > 1:
                db.execute(_phone_price_text("""
                    DELETE FROM storage
                    WHERE account_id=:a AND key=:k AND id<>:i
                """), {
                    "a": _PHONE_PRICE_STORAGE_ACCOUNT,
                    "k": _PHONE_PRICE_STORAGE_KEY,
                    "i": keep_id,
                })
        else:
            db.execute(_phone_price_text("""
                INSERT INTO storage(account_id,key,value)
                VALUES(:a,:k,:v)
            """), {
                "a": _PHONE_PRICE_STORAGE_ACCOUNT,
                "k": _PHONE_PRICE_STORAGE_KEY,
                "v": payload,
            })
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return _phone_commercial_settings_status()


# Single authoritative tariff catalog for cabinet/payment UI.  Product packs
# above remain the source for add-ons; tariff prices/limits come from billing.
def _tariff_products(account_id: str) -> dict:
    from app.api.billing import TARIFFS
    from app.pricing import period_total
    n = _n_accounts(account_id)
    out = {}
    for code in ("tariff_1", "tariff_2"):
        t = TARIFFS[code]
        total = period_total(n, code) if n > 1 else int(t["price_rub"])
        per_account = int(total / n) if n else total
        out[code] = {
            "sum": total,
            "title": t["name"],
            "desc": (f"{n} аккаунт(а/ов) · {per_account:,} ₽ за аккаунт · 30 дней".replace(",", " ")
                     if n > 1 else "Основной тариф BORIS · 30 дней"),
            "days": 30,
            "addon": False,
            "unit": "tariff",
            "accounts": n,
            "price_per_account": per_account,
            "limits": t["limits"],
        }
    return out


def _is_agency(account_id: str) -> bool:
    """Agency pricing applies to a real multi-account client, not a stale form field."""
    return _n_accounts(account_id) > 1


# Личные клиенты Кирилла — ведутся вручную, в расчёт по числу аккаунтов не входят
OWNER_MANUAL_USER_ID = 2


def _n_accounts(account_id: str) -> int:
    """Сколько аккаунтов у владельца. Для личных клиентов владельца — всегда 1,
    их биллинг ведётся вручную и агентские вилки к ним не применяются."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        own = db.execute(_t("SELECT owner_user_id FROM accounts WHERE account_id=:a"),
                         {"a": account_id}).fetchone()
        if not own or not own[0] or int(own[0]) == OWNER_MANUAL_USER_ID:
            return 1
        r = db.execute(_t("""SELECT COUNT(*) FROM accounts
                             WHERE owner_user_id=:o
                               AND account_id NOT LIKE 'qa_%'
                               AND account_id NOT LIKE 'test%'
                               AND COALESCE(billing_mode,'') <> 'test'"""),
                       {"o": own[0]}).fetchone()
        return int(r[0] or 1) if r else 1
    except Exception:
        return 1
    finally:
        db.close()


def _price(pack: dict, account_id: str) -> int:
    # подписка агентства считается по числу подключённых аккаунтов
    if pack.get("tier"):
        n = _n_accounts(account_id)
        if n > 1:
            from app.pricing import period_total
            return period_total(n, pack["tier"])
        return pack["sum"]
    if "sum_ag" in pack and _is_agency(account_id):
        return pack["sum_ag"]
    return pack["sum"]


def _rk_sign(parts: list) -> str:
    return hashlib.md5(":".join(str(x) for x in parts).encode()).hexdigest()


def _shp_tail(acc: str, pack: str) -> list:
    # Робокасса требует доп. параметры по алфавиту
    return [f"Shp_acc={acc}", f"Shp_pack={pack}"]


@router.get("/prices")
def prices(account_id: str, user=Depends(get_current_user)):
    _require_account_view(account_id, user)
    """Цены под конкретного клиента: частник или агентство — решает planned_accounts."""
    ag = _is_agency(account_id) or _n_accounts(account_id) > 1
    out = {}
    for code, pack in _package_catalog().items():
        out[code] = {
            "sum": _price(pack, account_id),
            "title": pack.get("title"),
            "desc": pack.get("desc"),
            "days": pack.get("days"),
            "addon": bool(pack.get("addon")),
            "unit": pack.get("unit"),
        }
    out.update(_tariff_products(account_id))
    return {"status": "ok", "сегмент": "агентство" if ag else "частник", "цены": out}


@router.get("/robokassa/link")
def robokassa_link(account_id: str, pack: str, user=Depends(get_current_user)):
    _require_account_view(account_id, user)
    """Ссылка на оплату с зашитым аккаунтом — начисление пойдёт автоматически."""
    if not RK_LOGIN or not RK_PASS1:
        return {"status": "error", "message": "Робокасса не настроена"}

    if pack in ("tariff_1", "tariff_2"):
        tp = _tariff_products(account_id).get(pack)
        if not tp:
            return {"status": "error", "message": f"Неизвестный тариф: {pack}"}
        p = tp
        out_sum = int(tp["sum"])
    elif pack == "acc_upgrade":
        # доплата за подключённый аккаунт: сумму считаем ТОЛЬКО на сервере
        import json as _j
        from datetime import datetime as _dt
        from app.db.session import SessionLocal as _S
        from app.models.account import Account as _Acc
        from app.models.storage import Storage as _St
        from app.pricing import upgrade_charge
        db = _S()
        try:
            acc = db.query(_Acc).filter(_Acc.account_id == account_id).first()
            if not acc or not acc.owner_user_id:
                return {"status": "error", "message": "Аккаунт не найден"}
            n = db.query(_Acc).filter(_Acc.owner_user_id == acc.owner_user_id,
                                      _Acc.billing_mode == "auto").count()
            row = db.query(_St).filter(_St.account_id == "user:%s" % acc.owner_user_id,
                                       _St.key == "billing").first()
            tier, days_left = "tariff_1", 30
            if row:
                try:
                    d = _j.loads(row.value)
                    tier = d.get("tier") if d.get("tier") in ("tariff_1", "tariff_2") else "tariff_1"
                    if d.get("period_start"):
                        passed = (_dt.utcnow() - _dt.fromisoformat(d["period_start"])).days
                        days_left = max(0, 30 - passed)
                except Exception:
                    pass
            arow = db.query(_St).filter(_St.account_id == account_id, _St.key == "billing").first()
            due = 0
            if arow:
                try:
                    due = int(_j.loads(arow.value).get("upgrade_due") or 0)
                except Exception:
                    due = 0
            # долг зафиксирован при подключении — берём его, иначе считаем
            amount = due or upgrade_charge(max(1, n - 1), 1, days_left, tariff=tier)
            if amount <= 0:
                return {"status": "error", "message": "Доплата не требуется"}
            p = {"sum": amount, "title": "Подключение аккаунта %s" % account_id}
        finally:
            db.close()
    else:
        catalog = _package_catalog()
        if pack not in catalog:
            return {"status": "error", "message": f"Неизвестный пакет: {pack}"}
        p = catalog[pack]

    final_sum = int(p["sum"]) if pack in ("tariff_1", "tariff_2", "acc_upgrade") else _price(p, account_id)
    out_sum = f'{final_sum}.00'
    sign = _rk_sign([RK_LOGIN, out_sum, 0, RK_PASS1] + _shp_tail(account_id, pack))
    url = ("https://auth.robokassa.ru/Merchant/Index.aspx"
           f"?MerchantLogin={RK_LOGIN}&OutSum={out_sum}&InvId=0"
           f"&Description={quote(p['title'])}"
           f"&SignatureValue={sign}&Shp_acc={quote(account_id)}&Shp_pack={pack}")
    return {"status": "ok", "url": url, "title": p["title"], "sum": p["sum"]}


@router.post("/robokassa/result")
async def robokassa_result(request: Request):
    """Уведомление от Робокассы: проверяем подпись и начисляем купленное."""
    form = dict(await request.form())
    if not form:
        form = dict(request.query_params)
    out_sum = form.get("OutSum", "")
    inv_id = form.get("InvId", "")
    got = (form.get("SignatureValue", "") or "").lower()
    acc = form.get("Shp_acc", "")
    pack = form.get("Shp_pack", "")

    expect = _rk_sign([out_sum, inv_id, RK_PASS2] + _shp_tail(acc, pack))
    if got != expect:
        return "bad sign"
    catalog = _package_catalog()
    if pack not in catalog and pack not in ("acc_upgrade", "tariff_1", "tariff_2"):
        return "bad pack"

    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.api.avito import _audit_log
    if pack in ("tariff_1", "tariff_2"):
        p = _tariff_products(acc).get(pack) or {"title": pack, "sum": float(out_sum or 0), "tier": pack}
        p = {**p, "tier": pack}
    else:
        p = catalog.get(pack) or {"title": "Подключение аккаунта", "sum": float(out_sum or 0), "is_account": True}
    db = SessionLocal()
    try:
        grant_package(acc, pack, p, db, amount=out_sum, source="robokassa", inv_id=inv_id)

        # комиссия менеджеру, который привёл клиента
        try:
            from sqlalchemy import text as _text
            import datetime as _dt
            # ищем менеджера и по прямой привязке, и через владельца аккаунта:
            # у агентства users.account_id хранит только ПЕРВЫЙ аккаунт
            ref = db.execute(_text("""SELECT u.referred_by FROM users u
                                      WHERE u.referred_by IS NOT NULL
                                        AND (u.account_id = :a
                                             OR u.id = (SELECT owner_user_id FROM accounts WHERE account_id = :a))
                                      LIMIT 1"""),
                             {"a": acc}).fetchone()
            if ref and ref[0]:
                from app.api.manager import _grade, UPSELL_RATE
                me = ref[0]
                # подключение аккаунта — это продажа тарифа, идёт в грейд, а не в апсейл
                kind = "tariff" if ("tier" in p or p.get("is_account")) else "upsell"
                period = _dt.datetime.now().strftime("%Y-%m")
                is_renewal = False
                if kind == "tariff":
                    # продление — если этот клиент уже покупал тариф раньше
                    was = db.execute(_text("""SELECT COUNT(*) FROM manager_commissions
                                              WHERE client_account_id=:a AND sale_kind IN ('tariff','renewal')"""),
                                     {"a": acc}).fetchone()
                    is_renewal = bool(was and int(was[0] or 0) > 0)

                if is_renewal:
                    # фикс за продление: 300 ₽ агентству, 100 ₽ частнику
                    n_acc = db.execute(_text("""SELECT COUNT(*) FROM accounts
                                                WHERE owner_user_id = (SELECT owner_user_id FROM accounts WHERE account_id=:a)"""),
                                       {"a": acc}).fetchone()
                    fixed = 300 if (n_acc and int(n_acc[0] or 0) > 1) else 100
                    kind, rate, commission = "renewal", 0, fixed
                elif kind == "tariff":
                    sold = db.execute(_text("""SELECT COALESCE(SUM(payment_amount),0) FROM manager_commissions
                                               WHERE manager_email=:me AND period=:p AND sale_kind='tariff'"""),
                                      {"me": me, "p": period}).fetchone()
                    rate = _grade(float(sold[0] or 0))[2]
                    commission = round(p["sum"] * rate / 100, 2)
                else:
                    rate = UPSELL_RATE
                    commission = round(p["sum"] * rate / 100, 2)
                db.execute(_text("""INSERT INTO manager_commissions
                    (manager_email, client_account_id, payment_amount, rate, commission,
                     sale_kind, product, period, comment, created_at)
                    VALUES (:me,:a,:amt,:r,:c,:k,:pr,:p,:cm,NOW())"""),
                    {"me": me, "a": acc, "amt": p["sum"], "r": rate,
                     "c": commission, "k": kind,
                     "pr": p["title"], "p": period, "cm": f"автоматически, счёт {inv_id}"})
        except Exception as _e:
            print("manager commission skip:", _e)

        hrow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "payments_history").first()
        hist = json.loads(hrow.value) if hrow else []
        hist.append({"at": datetime.utcnow().isoformat(), "pack": pack, "title": p["title"],
                     "amount_rub": p["sum"], "inv_id": inv_id})
        if hrow:
            hrow.value = json.dumps(hist, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="payments_history", value=json.dumps(hist, ensure_ascii=False)))
        db.commit()
        _audit_log(acc, "robokassa_paid", f"{p['title']} — {p['sum']}₽ (счёт {inv_id})", "system")
    finally:
        db.close()
    return f"OK{inv_id}"


@router.get("/history")
def payments_history(account_id: str, user=Depends(get_current_user)):
    _require_account_view(account_id, user)
    """Unified real payment history: current SQL ledger + legacy Storage rows."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from sqlalchemy import text as _ht
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "payments_history").first()
        legacy = json.loads(row.value) if row else []
        sql_rows = db.execute(_ht("""SELECT paid_at, created_at, pack, amount_rub, inv_id, comment, source
                                   FROM payments WHERE account_id=:a AND status='paid'
                                   ORDER BY COALESCE(paid_at, created_at) DESC"""), {"a": account_id}).fetchall()
        hist = [{"at": (r[0] or r[1]).isoformat() if (r[0] or r[1]) else None,
                 "pack": r[2] or "", "amount_rub": float(r[3] or 0),
                 "inv_id": r[4], "title": r[5] or "Оплата BORIS", "source": r[6]}
                for r in sql_rows]
        seen = {(str(h.get("inv_id") or ""), str(h.get("pack") or ""), float(h.get("amount_rub") or 0)) for h in hist}
        for h in legacy:
            key = (str(h.get("inv_id") or ""), str(h.get("pack") or ""), float(h.get("amount_rub") or 0))
            if key not in seen:
                hist.append(h); seen.add(key)
        hist.sort(key=lambda h: str(h.get("at") or ""), reverse=True)
        return {"status": "ok", "history": hist, "total_rub": sum(float(h.get("amount_rub", 0) or 0) for h in hist)}
    finally:
        db.close()



def _grant_phone_subscription_from_payment_ledger(acc, p, db, source, payment_ref):
    """Idempotently materialize Phone from an already-recorded real payment."""
    if not payment_ref:
        return {"status": "phone_payment_reference_required", "active": False}
    from sqlalchemy import text as _phone_text
    row = db.execute(_phone_text("""
        SELECT amount_rub, paid_at
        FROM payments
        WHERE account_id=:a AND source=:s AND inv_id=:i
        ORDER BY id DESC LIMIT 1
    """), {"a": acc, "s": source, "i": str(payment_ref)}).mappings().first()
    if not row:
        return {"status": "phone_payment_ledger_missing", "active": False}
    paid_at = row.get("paid_at") or datetime.utcnow()
    days = max(1, int(p.get("days", 30) or 30))
    paid_until = paid_at + timedelta(days=days)
    commercial_ref = f"payment:{source}:{payment_ref}"
    from app.services.telephony_core import provision_paid_phone_entitlement
    result = provision_paid_phone_entitlement(
        acc,
        paid_until,
        commercial_ref,
        actor_user_id=None,
        price_rub=int(round(float(row.get("amount_rub") or 0))),
        source=f"payment:{source}",
    )
    if bool(result.get("active")):
        try:
            import telephony_guardian_runner as _phone_guardian
            _phone_guardian.mcn_mailbox_autoonboard_once()
        except Exception as _guardian_e:
            print("[payments] phone guardian refresh skipped:", type(_guardian_e).__name__)
    return result


def grant_package(acc, pack, p, db, amount=None, source="robokassa", inv_id=None):
    """Начисление купленного пакета. Единая точка для Робокассы и для списания с баланса."""
    import json
    from datetime import date
    from app.models.storage import Storage
    try:
        _amt = float(amount if amount not in (None, "") else (p.get("sum") or 0))
    except Exception:
        _amt = 0.0
    payment_ref = str(inv_id) if inv_id not in (None, "", "0") else None
    ledger_inserted = True
    try:
        from sqlalchemy import text as _pt
        ledger_row = db.execute(_pt(
            "insert into payments (account_id, source, pack, amount_rub, inv_id, comment) "
            "values (:a, :s, :k, :m, :i, :c) on conflict do nothing returning id"),
            {"a": acc, "s": source, "k": pack, "m": _amt,
             "i": payment_ref,
             "c": p.get("title") or ""}).first()
        db.commit()
        ledger_inserted = ledger_row is not None
    except Exception as _e:
        try:
            db.rollback()
        except Exception:
            pass
        if payment_ref:
            raise
        print("[payments] platezh ne zapisan:", _e)
    if payment_ref and not ledger_inserted:
        recovery = None
        if p.get("unit") == "phone_subscription":
            recovery = _grant_phone_subscription_from_payment_ledger(
                acc, p, db, source, payment_ref
            )
        return {
            "status": "duplicate_payment",
            "account_id": acc,
            "pack": pack,
            "source": source,
            "grant_skipped": p.get("unit") != "phone_subscription",
            "phone_recovery": recovery,
        }
    if "tier" in p:
        from app.api.billing import set_tier
        set_tier(acc, p["tier"], keep_extra=True)
        data = {"paid_at": date.today().isoformat(), "period_days": p["days"], "amount_rub": p["sum"]}
        row = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "payment_status").first()
        if row:
            row.value = json.dumps(data, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="payment_status", value=json.dumps(data, ensure_ascii=False)))

        # Автоматическая выдача оплаченного слота для «Единого центра сообщений».
        # Без этого клиент после оплаты упирается в 402 /api/inbox/slots/connect
        # и зависит от ручного действия owner через /api/inbox/slots/grant.
        try:
            from app.models.account import Account as _Account
            from app.models.account_slot import AccountSlot as _AccountSlot
            from app import inbox_pricing as _inbox_pricing
            owner_id = db.query(_Account.owner_user_id).filter(_Account.account_id == acc).scalar()
            if owner_id:
                now = datetime.utcnow()
                active_slot = db.query(_AccountSlot).filter(
                    _AccountSlot.owner_user_id == owner_id,
                    _AccountSlot.product == _inbox_pricing.PRODUCT,
                    _AccountSlot.status.in_(("paid_empty", "connecting", "connected")),
                    _AccountSlot.paid_until.isnot(None),
                    _AccountSlot.paid_until > now,
                ).first()
                if active_slot is None:
                    existing = db.query(_AccountSlot).filter(
                        _AccountSlot.owner_user_id == owner_id,
                        _AccountSlot.product == _inbox_pricing.PRODUCT,
                    ).count()
                    start = now
                    until = now + timedelta(days=30)
                    price = _inbox_pricing.add_slots_price(existing, 1)
                    db.add(_AccountSlot(
                        owner_user_id=owner_id,
                        product=_inbox_pricing.PRODUCT,
                        slot_no=existing + 1,
                        status="paid_empty",
                        period_start=start,
                        paid_until=until,
                        price_rub=int(price),
                        payment_id=str(inv_id or "") or None,
                    ))
                    db.flush()
        except Exception as _slot_e:
            print("[payments] auto-slot grant skipped:", _slot_e)
    elif p.get("unit") == "phone_subscription":
        phone_result = _grant_phone_subscription_from_payment_ledger(
            acc, p, db, source, payment_ref
        )
        if not bool(phone_result.get("active")):
            raise RuntimeError(
                "phone_entitlement_not_activated:" + str(phone_result.get("status") or "unknown")
            )
    elif p.get("unit") == "messages":
        # пакет сообщений менеджера — начисляем купленный объём
        from app.api.messenger import add_manager_package
        add_manager_package(acc, p["qty"], days=int(p.get("days", 0) or 0))
    elif p.get("unit") == "rop_pack":
        # пакет РОП: минуты + переписки + два вида отчётов; days>0 — базовый (новый период), days=0 — докупка
        from app.api.calltracking import add_rop_package
        add_rop_package(acc,
                        minutes=int(p.get("minutes", 0) or 0),
                        chats=int(p.get("chats", 0) or 0),
                        rep_calls=int(p.get("rep_calls", 0) or 0),
                        rep_chats=int(p.get("rep_chats", 0) or 0),
                        days=int(p.get("days", 0) or 0))
    elif p.get("unit") == "rop_minutes":
        # пакет минут РОП — начисляем купленные минуты разбора
        from app.api.calltracking import add_rop_minutes, add_rop_reports
        add_rop_minutes(acc, p["qty"])
        add_rop_reports(acc, int(p.get("reports", 0) or 0))
    elif "posting" in p:
        # постинг-подписка: тариф и открытые площадки
        pdata = {
            "plan": pack,
            "platforms": p["posting"],
            "paid_at": date.today().isoformat(),
            "period_days": p["days"],
            "amount_rub": p["sum"],
        }
        prow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "posting_subscription").first()
        if prow:
            prow.value = json.dumps(pdata, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="posting_subscription", value=json.dumps(pdata, ensure_ascii=False)))
    elif p.get("is_account"):
        # доплата за подключённый аккаунт: пакетов не начисляем, помечаем оплаченным
        brow = db.query(Storage).filter(Storage.account_id == acc, Storage.key == "billing").first()
        bdata = json.loads(brow.value) if brow else {}
        bdata["paid_upgrade"] = True
        bdata["paid_upgrade_at"] = date.today().isoformat()
        bdata["upgrade_due"] = 0
        if brow:
            brow.value = json.dumps(bdata, ensure_ascii=False)
        else:
            db.add(Storage(account_id=acc, key="billing", value=json.dumps(bdata, ensure_ascii=False)))
    else:
        if p.get("shared"):
            from app.api.billing import add_shared
            add_shared(acc, p["unit"], p["qty"])
        else:
            from app.api.billing import add_extra
            add_extra(acc, p["unit"], p["qty"])

    # Единый сервис дополнительных продуктовых лимитов относится только к
    # пакетам, которые явно описаны в PRODUCT_LIMITS. Phone и другие независимые
    # продукты не должны проходить через reactivation-контур и создавать ложный
    # warning "неизвестный пакет" после успешной оплаты.
    try:
        import logging as _lg
        from app.product_limits import PRODUCT_LIMITS, grant_product_limits
        if pack in PRODUCT_LIMITS:
            _pl = grant_product_limits(db, acc, pack)
            if _pl.get("status") not in ("granted", "no_reactivation_limit"):
                _lg.getLogger(__name__).warning(
                    "реактивация: лимит не начислен (%s), пакет=%s аккаунт=%s",
                    _pl.get("status"), pack, acc)
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).error(
            "реактивация: сбой начисления лимита, пакет=%s аккаунт=%s: %s", pack, acc, _e)
