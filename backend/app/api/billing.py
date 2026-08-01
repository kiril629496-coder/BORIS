"""
Биллинг и лимиты BORIS.
Хранит на каждый account_id (Storage, key="billing") тариф, дату начала текущего
30-дневного периода и счётчики использования. Лимиты за скользящий 30-дневный
период от даты активации (не по календарному месяцу). При исчерпании действие
останавливается, клиенту предлагается перейти на след. тариф или подождать.
"""
import json
from datetime import datetime, timedelta
from app.db.session import SessionLocal
from app.api.auth import require_owner
from app.models.storage import Storage

TARIFFS = {
    "none": {"name": "Без тарифа", "price_rub": 0,
             "limits": {"listings": 0, "banners": 0, "ai_templates": 0, "images": 0}},
    "trial": {"name": "Пробный период (4 дня)", "price_rub": 0,
              "limits": {"listings": 50, "banners": 3, "ai_templates": 5, "images": 20}},
    "tariff_1": {"name": "Тариф 1", "price_rub": 7000,
                 "limits": {"listings": 1000, "banners": 15, "ai_templates": 30, "images": 100}},
    "tariff_2": {"name": "Тариф 2", "price_rub": 14000,
                 "limits": {"listings": 3000, "banners": 40, "ai_templates": 50, "images": 100}},
}
UNIT_NAMES = {"listings": "объявлений", "banners": "баннеров",
              "ai_templates": "шаблонов ИИ", "images": "картинок"}
NEXT_TARIFF = {"trial": "tariff_1", "tariff_1": "tariff_2", "tariff_2": None}
_PERIOD_DAYS = 30


def _load_billing(account_id):
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        data = json.loads(row.value) if row else None
        okey, n_acc = _billing_owner_key(account_id)
        if okey != account_id:
            orow = db.query(Storage).filter(Storage.account_id == okey, Storage.key == "billing").first()
            if orow:
                money = json.loads(orow.value)
                data = data or _default_billing("none")
                for f in _MONEY_FIELDS:
                    if f in money:
                        data[f] = money[f]
            if data is not None:
                data["_accounts_count"] = n_acc
        return data
    finally:
        db.close()



def _billing_owner_key(account_id):
    """Куда класть ДЕНЬГИ (тариф, период, оплата).
    manual (клиенты Кирилла) — на сам аккаунт, как раньше.
    auto — на владельца: у агентства один счёт на все аккаунты.
    Счётчики usage всегда остаются на аккаунте — лимиты у каждого свои."""
    from app.models.account import Account
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return account_id, 1
        mode = getattr(acc, "billing_mode", "manual") or "manual"
        if mode != "auto" or not acc.owner_user_id:
            return account_id, 1
        n = db.query(Account).filter(Account.owner_user_id == acc.owner_user_id,
                                     Account.billing_mode == "auto").count()
        return "user:%s" % acc.owner_user_id, max(1, n)
    finally:
        db.close()


_MONEY_FIELDS = ("tier", "period_start", "unlimited", "paid_until")


def _save_billing(account_id, data):
    db = SessionLocal()
    try:
        okey, _n = _billing_owner_key(account_id)
        if okey != account_id:
            money = {f: data[f] for f in _MONEY_FIELDS if f in data}
            orow = db.query(Storage).filter(Storage.account_id == okey, Storage.key == "billing").first()
            if orow:
                orow.value = json.dumps({**json.loads(orow.value), **money}, ensure_ascii=False)
            else:
                db.add(Storage(account_id=okey, key="billing", value=json.dumps(money, ensure_ascii=False)))
        data = {k: v for k, v in data.items() if k != "_accounts_count"}
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        value_json = json.dumps(data, ensure_ascii=False)
        if row:
            row.value = value_json
        else:
            db.add(Storage(account_id=account_id, key="billing", value=value_json))
        db.commit()
    finally:
        db.close()


def _default_billing(tier="none"):
    return {"tier": tier, "period_start": datetime.utcnow().isoformat(),
            "usage": {"listings": 0, "banners": 0, "ai_templates": 0, "images": 0},
            "unlimited": False}


def _ensure_period_fresh(billing):
    try:
        start = datetime.fromisoformat(billing["period_start"])
    except Exception:
        start = datetime.utcnow()
        billing["period_start"] = start.isoformat()
    if datetime.utcnow() - start >= timedelta(days=_PERIOD_DAYS):
        billing["period_start"] = datetime.utcnow().isoformat()
        # допуслуги действуют только до конца оплаченного тарифа — сгорают вместе с ним
        if billing.get("extra"):
            billing["extra_burned"] = {"at": datetime.utcnow().isoformat(),
                                       "было": dict(billing["extra"])}
            billing["extra"] = {}
    # сброс счётчиков — по СВОЕЙ метке аккаунта: period_start общий у агентства,
    # по нему второй и последующие аккаунты никогда бы не обнулились
    try:
        us = datetime.fromisoformat(billing.get("usage_start") or billing["period_start"])
    except Exception:
        us = datetime.utcnow()
    if datetime.utcnow() - us >= timedelta(days=_PERIOD_DAYS) or not billing.get("usage_start"):
        billing["usage_start"] = datetime.utcnow().isoformat()
        if datetime.utcnow() - us >= timedelta(days=_PERIOD_DAYS):
            billing["usage"] = {"listings": 0, "banners": 0, "ai_templates": 0, "images": 0}
    return billing


WARN_DAYS = [10, 7, 5, 3, 1]


def _check_expiry_warnings(account_id, billing, days_left):
    """Предупреждаем за 10, 7, 5, 3 и 1 день. Каждый порог — один раз."""
    if days_left is None or days_left < 0:
        return
    hit = next((d for d in WARN_DAYS if days_left == d), None)
    if hit is None:
        return
    sent = billing.get("warned") or []
    key = f"{billing.get('period_start', '')[:10]}:{hit}"
    if key in sent:
        return
    extra = billing.get("extra") or {}
    total_extra = sum(int(v or 0) for v in extra.values())
    txt = f"⏳ Тариф заканчивается через {hit} дн."
    if total_extra:
        parts = []
        if extra.get("banners"): parts.append(f"{extra['banners']} баннеров")
        if extra.get("messages"): parts.append(f"{extra['messages']} сообщений")
        if extra.get("images"): parts.append(f"{extra['images']} картинок профиля")
        if parts:
            txt += " Вместе с ним сгорит докупленное: " + ", ".join(parts) + ". Продлите тариф, чтобы не потерять."
    else:
        txt += " Продлите, чтобы Борис продолжил работу."
    try:
        from app.api.avito import _push_notification
        _push_notification(account_id, txt)
        billing["warned"] = (sent + [key])[-20:]
        _save_billing(account_id, billing)
    except Exception as e:
        print("expiry warn skip:", e)


def has_unpaid_upgrade(account_id) -> int:
    """Сколько висит неоплаченной доплаты за подключение аккаунта. 0 — долга нет."""
    b = _load_billing(account_id) or {}
    try:
        return int(b.get("upgrade_due") or 0)
    except Exception:
        return 0


def get_status(account_id):
    billing = _load_billing(account_id) or _default_billing("none")
    billing = _ensure_period_fresh(billing)
    _save_billing(account_id, billing)
    tier = billing.get("tier", "none")
    limits = TARIFFS.get(tier, TARIFFS["none"])["limits"]
    usage = billing.get("usage", {})
    unlimited_flag = billing.get("unlimited", False)
    start = datetime.fromisoformat(billing["period_start"])
    period_end = start + timedelta(days=_PERIOD_DAYS)
    breakdown = {}
    for unit, limit in limits.items():
        used = usage.get(unit, 0)
        extra = billing.get("extra", {}).get(unit, 0)
        breakdown[unit] = {"used": used, "limit": limit, "extra": extra,
                           "remaining": max(0, limit - used) + extra, "name": UNIT_NAMES.get(unit, unit)}
    import math as _math
    _left_sec = (period_end - datetime.utcnow()).total_seconds()
    _check_expiry_warnings(account_id, billing, max(0, int(_math.ceil(_left_sec / 86400.0))))
    _due = has_unpaid_upgrade(account_id)
    if _due > 0:
        # аккаунт подключён, но доплата не внесена — работать не даём
        return {"upgrade_due": _due, "tier": tier,
                "tier_name": TARIFFS.get(tier, TARIFFS["none"])["name"],
                "active": False, "blocked_reason": "unpaid_upgrade",
                "message": f"Аккаунт подключён, но не оплачен. Доплата {_due} ₽.",
                "usage": {}}
    return {"upgrade_due": _due, "tier": tier, "tier_name": TARIFFS.get(tier, TARIFFS["none"])["name"],
            "period_start": billing["period_start"], "period_end": period_end.isoformat(),
            "days_left": max(0, (period_end - datetime.utcnow()).days), "usage": breakdown,
            "unlimited": unlimited_flag}


def _owner_unlimited(account_id) -> bool:
    """Аккаунты владельца сервиса — без лимитов."""
    try:
        from app.api.calltracking import _is_owner_account
        return _is_owner_account(str(account_id))
    except Exception:
        return False


def check_and_consume(account_id, unit, amount=1):
    if _owner_unlimited(account_id):
        return {"allowed": True, "unit": unit, "unlimited": True, "used": 0, "limit": 0, "remaining": 999999}
    billing = _load_billing(account_id) or _default_billing("none")
    billing = _ensure_period_fresh(billing)
    if billing.get("unlimited"):
        billing.setdefault("usage", {})
        billing["usage"][unit] = billing["usage"].get(unit, 0) + amount
        _save_billing(account_id, billing)
        return {"allowed": True, "used": billing["usage"][unit], "limit": None, "unlimited": True}
    tier = billing.get("tier", "none")
    limit = TARIFFS.get(tier, TARIFFS["none"])["limits"].get(unit, 0)
    used = billing.get("usage", {}).get(unit, 0)
    if used + amount > limit:
        extra = billing.get("extra", {}).get(unit, 0)
        if extra < amount:
            _ok = _owner_key(account_id)
            if _ok:
                ob = _load_billing(_ok) or {}
                oextra = (ob.get("extra") or {}).get(unit, 0)
                if oextra >= amount:
                    ob.setdefault("extra", {})
                    ob["extra"][unit] = oextra - amount
                    _save_billing(_ok, ob)
                    return {"allowed": True, "used": used, "limit": limit,
                            "from_shared": True, "shared_left": ob["extra"][unit]}
        if extra >= amount:
            billing.setdefault("extra", {})
            billing["extra"][unit] = extra - amount
            _save_billing(account_id, billing)
            return {"allowed": True, "used": used, "limit": limit,
                    "from_extra": True, "extra_left": billing["extra"][unit]}
        _save_billing(account_id, billing)
        unit_name = UNIT_NAMES.get(unit, unit)
        next_tier = NEXT_TARIFF.get(tier)
        start = datetime.fromisoformat(billing["period_start"])
        days_left = max(0, ((start + timedelta(days=_PERIOD_DAYS)) - datetime.utcnow()).days)
        options = []
        if next_tier:
            nt = TARIFFS[next_tier]
            options.append({"action": "upgrade", "to_tier": next_tier,
                            "label": f"Перейти на {nt['name']} ({nt['price_rub']}\u20bd/мес) — до {nt['limits'].get(unit, 0)} {unit_name}"})
        options.append({"action": "wait", "label": f"Подождать до обновления лимита (осталось {days_left} дн.)"})
        return {"allowed": False, "unit": unit,
                "message": f"Достигнут лимит по {unit_name} на вашем тарифе ({used} из {limit}). Выберите: перейти на следующий тариф или дождаться обновления лимита.",
                "options": options}
    billing.setdefault("usage", {})
    billing["usage"][unit] = used + amount
    _save_billing(account_id, billing)
    return {"allowed": True, "used": used + amount, "limit": limit}


def _owner_key(account_id):
    """Кошелёк общих пакетов — на владельце аккаунта, а не на самом аккаунте."""
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        r = db.execute(_t("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),
                       {"a": account_id}).fetchone()
        return f"__owner_{r[0]}" if r and r[0] else None
    except Exception:
        return None
    finally:
        db.close()


def add_shared(account_id, unit, amount):
    """Пакеты 50 и 100 — общие на все аккаунты владельца."""
    key = _owner_key(account_id) or account_id
    b = _load_billing(key) or _default_billing("none")
    b.setdefault("extra", {})
    b["extra"][unit] = b["extra"].get(unit, 0) + int(amount)
    _save_billing(key, b)
    return {"status": "ok", "unit": unit, "shared": b["extra"][unit]}


def add_extra(account_id, unit, amount):
    """Начислить докупленный запас — не сгорает при смене месяца."""
    billing = _load_billing(account_id) or _default_billing("none")
    billing.setdefault("extra", {})
    billing["extra"][unit] = billing["extra"].get(unit, 0) + int(amount)
    _save_billing(account_id, billing)
    return {"status": "ok", "unit": unit, "extra": billing["extra"][unit]}


def set_tier(account_id, tier, keep_extra=False):
    if tier not in TARIFFS:
        return {"status": "error", "message": f"Неизвестный тариф: {tier}"}
    old = _load_billing(account_id) or {}
    fresh = _default_billing(tier)
    # продление ТОГО ЖЕ тарифа без перерыва — докупленное переносим;
    # смена тарифа или возврат после паузы — сгорает по правилу прайса
    if keep_extra and old.get("tier") == tier and old.get("extra"):
        try:
            start = datetime.fromisoformat(old.get("period_start"))
            not_expired = datetime.utcnow() - start < timedelta(days=_PERIOD_DAYS)
        except Exception:
            not_expired = False
        if not_expired:
            fresh["extra"] = old["extra"]
    _save_billing(account_id, fresh)
    return {"status": "ok", "tier": tier, "extra_kept": bool(fresh.get("extra"))}


# --- HTTP-эндпоинты ---
from fastapi import Depends, APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/status")
def billing_status(account_id: str):
    return {"status": "ok", "billing": get_status(account_id)}


@router.get("/tariffs")
def billing_tariffs():
    # публичный список тарифов для страницы цен
    return {"status": "ok", "tariffs": {
        k: {"name": v["name"], "price_rub": v["price_rub"], "limits": v["limits"]}
        for k, v in TARIFFS.items() if k in ("tariff_1", "tariff_2")
    }}


class SetTierRequest(BaseModel):
    account_id: str
    tier: str


@router.post("/set_tier")
def billing_set_tier(req: SetTierRequest, user=Depends(require_owner)):
    # ВНИМАНИЕ: должен вызываться только после реальной оплаты (Робокасса webhook)
    # или вручную владельцем. Защита ролью/подписью платежа — отдельная задача.
    return set_tier(req.account_id, req.tier)


class SetUnlimitedRequest(BaseModel):
    account_id: str
    unlimited: bool


@router.post("/set_unlimited")
def billing_set_unlimited(req: SetUnlimitedRequest, user=Depends(require_owner)):
    billing = _load_billing(req.account_id) or _default_billing("none")
    billing["unlimited"] = req.unlimited
    _save_billing(req.account_id, billing)
    return {"status": "ok", "account_id": req.account_id, "unlimited": req.unlimited}
