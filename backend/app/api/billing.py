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
from app.api.auth import require_owner, get_current_user
from app.models.storage import Storage
from sqlalchemy import text
from app.services.exception_observability import observe_suppressed

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


def add_ecs_package(account_id: str, days: int = 30):
    """Штатное включение пакета ЕЦС: записывает покупку и срок действия в billing storage."""
    b = _load_billing(account_id) or _default_billing("none")
    b = _ensure_period_fresh(b)
    until = datetime.utcnow() + timedelta(days=max(1, int(days or 30)))
    b["ecs_purchased"] = 1
    b["ecs_paid_until"] = until.isoformat()
    _save_billing(account_id, b)
    return {
        "status": "ok",
        "account_id": account_id,
        "ecs_purchased": b["ecs_purchased"],
        "ecs_paid_until": b["ecs_paid_until"],
    }


def _load_billing(account_id, db=None):
    """Load effective billing, reusing a caller session when supplied.

    Scheduler/guard paths frequently already own a short DB transaction. Opening
    a nested Session here can exhaust BORIS's deliberately small daemon pool and
    turn safe per-account work into fleet-wide QueuePool timeouts.
    """
    owned_db = db is None
    db = db or SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        data = json.loads(row.value) if row else None
        okey, n_acc = _billing_owner_key(account_id, db=db)
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
        if owned_db:
            db.close()



def _billing_owner_key(account_id, db=None):
    """Куда класть ДЕНЬГИ (тариф, период, оплата).
    manual (клиенты Кирилла) — на сам аккаунт, как раньше.
    auto — на владельца: у агентства один счёт на все аккаунты.
    Счётчики usage всегда остаются на аккаунте — лимиты у каждого свои.

    Reuse a caller session when supplied so billing lookups do not recursively
    exhaust the deliberately small scheduler DB pool.
    """
    from app.models.account import Account
    owned_db = db is None
    db = db or SessionLocal()
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
        if owned_db:
            db.close()


_MONEY_FIELDS = ("tier", "period_start", "unlimited", "paid_until")


def _save_billing(account_id, data, db=None, commit=True):
    owned_db = db is None
    db = db or SessionLocal()
    try:
        okey, _n = _billing_owner_key(account_id, db=db)
        if okey != account_id:
            money = {f: data[f] for f in _MONEY_FIELDS if f in data}
            orow = db.query(Storage).filter(Storage.account_id == okey, Storage.key == "billing").first()
            if orow:
                orow.value = json.dumps({**json.loads(orow.value), **money}, ensure_ascii=False)
            else:
                db.add(Storage(account_id=okey, key="billing", value=json.dumps(money, ensure_ascii=False)))
        data = {k: v for k, v in data.items() if k != "_accounts_count"}
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        if row:
            try:
                existing = json.loads(row.value) or {}
            except Exception:
                existing = {}
            existing.update(data)
            row.value = json.dumps(existing, ensure_ascii=False)
        else:
            db.add(Storage(account_id=account_id, key="billing", value=json.dumps(data, ensure_ascii=False)))
        if commit:
            db.commit()
        else:
            db.flush()
    finally:
        if owned_db:
            db.close()


def _default_billing(tier="none"):
    return {"tier": tier, "period_start": datetime.utcnow().isoformat(),
            "usage": {"listings": 0, "banners": 0, "ai_templates": 0, "images": 0},
            "unlimited": False}


def _naive_utc(dt):
    """Normalize parsed ISO datetimes for safe comparison with datetime.utcnow()."""
    if dt.tzinfo is None:
        return dt
    offset = dt.utcoffset() or timedelta(0)
    return dt.replace(tzinfo=None) - offset


def _paid_until(billing):
    """Authoritative paid expiry; legacy rows fall back to activation + 30 days."""
    if str((billing or {}).get("tier") or "none") not in ("tariff_1", "tariff_2"):
        return None
    raw = (billing or {}).get("paid_until")
    if raw:
        try:
            return _naive_utc(datetime.fromisoformat(str(raw)))
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=161)
    try:
        start = _naive_utc(datetime.fromisoformat(str((billing or {}).get("period_start"))))
        return start + timedelta(days=_PERIOD_DAYS)
    except Exception:
        return None


def _subscription_expired(billing):
    until = _paid_until(billing)
    return bool(until is not None and datetime.utcnow() >= until)


def _ensure_period_fresh(billing):
    try:
        start = _naive_utc(datetime.fromisoformat(billing["period_start"]))
    except Exception:
        start = datetime.utcnow()
        billing["period_start"] = start.isoformat()
    paid_tier = str(billing.get("tier") or "none") in ("tariff_1", "tariff_2")
    if datetime.utcnow() - start >= timedelta(days=_PERIOD_DAYS) and not paid_tier:
        billing["period_start"] = datetime.utcnow().isoformat()
        # допуслуги действуют только до конца оплаченного тарифа — сгорают вместе с ним
        if billing.get("extra"):
            billing["extra_burned"] = {"at": datetime.utcnow().isoformat(),
                                       "было": dict(billing["extra"])}
            billing["extra"] = {}
    # сброс счётчиков — по СВОЕЙ метке аккаунта: period_start общий у агентства,
    # по нему второй и последующие аккаунты никогда бы не обнулились
    try:
        us = _naive_utc(datetime.fromisoformat(billing.get("usage_start") or billing["period_start"]))
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
        # Persist ONLY the warning marker from a freshly row-locked value. Never
        # write the stale status snapshot back, because it may predate a concurrent
        # tariff consume on another backend replica.
        db = SessionLocal()
        try:
            row = db.query(Storage).filter(
                Storage.account_id == account_id, Storage.key == "billing"
            ).with_for_update().first()
            if row:
                try:
                    latest = json.loads(row.value) or {}
                except Exception:
                    latest = {}
                latest_sent = list(latest.get("warned") or [])
                if key not in latest_sent:
                    latest["warned"] = (latest_sent + [key])[-20:]
                    row.value = json.dumps(latest, ensure_ascii=False)
                    db.commit()
                else:
                    db.rollback()
            else:
                db.rollback()
        finally:
            db.close()
    except Exception as e:
        print("expiry warn skip:", e)


def has_unpaid_upgrade(account_id) -> int:
    """Сколько висит неоплаченной доплаты за подключение аккаунта. 0 — долга нет."""
    b = _load_billing(account_id) or {}
    try:
        return int(b.get("upgrade_due") or 0)
    except Exception:
        return 0


def _active_user_trial(account_id, db=None):
    """Read-only 4-day trial status for the owner of this account.

    Registration stores the trial on users, while billing limits live in
    storage:billing. Reuse a caller DB session when present so concurrent
    tariff consumes cannot deadlock the deliberately small DB pool.
    """
    owned_db = db is None
    db = db or SessionLocal()
    try:
        row = db.execute(text("""SELECT u.trial_started_at, u.subscription_expires_at
                                 FROM accounts a JOIN users u ON u.id=a.owner_user_id
                                 WHERE a.account_id=:a LIMIT 1"""),
                         {"a": account_id}).fetchone()
        if not row or not row[0] or not row[1]:
            return None
        started, expires = row[0], row[1]
        now = datetime.utcnow()
        started = _naive_utc(started)
        expires = _naive_utc(expires)
        if started <= now < expires:
            return {"started": started, "expires": expires}
    except Exception:
        return None
    finally:
        if owned_db:
            db.close()
    return None


def get_status(account_id):
    billing = _load_billing(account_id) or _default_billing("none")
    billing = _ensure_period_fresh(billing)
    tier = billing.get("tier", "none")
    trial = _active_user_trial(account_id) if tier == "none" else None
    if trial:
        # Runtime-only effective tier. Do not persist: the user record owns
        # trial expiry and remains the single clock for the four-day period.
        billing["period_start"] = trial["started"].isoformat()
        tier = "trial"
    # Status reads never write the whole billing JSON back. A stale dashboard
    # read racing an atomic consume must not overwrite newer usage counters.
    # Period freshness is persisted by the next real mutation/consume.
    limits = TARIFFS.get(tier, TARIFFS["none"])["limits"]
    usage = billing.get("usage", {})
    unlimited_flag = billing.get("unlimited", False)
    start = _naive_utc(datetime.fromisoformat(billing["period_start"]))
    period_days = 4 if tier == "trial" else _PERIOD_DAYS
    period_end = start + timedelta(days=period_days)
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
    if _subscription_expired(billing):
        until = _paid_until(billing)
        return {"upgrade_due": _due, "tier": tier,
                "tier_name": TARIFFS.get(tier, TARIFFS["none"])["name"],
                "price_rub": TARIFFS.get(tier, TARIFFS["none"]).get("price_rub", 0),
                "active": False, "blocked_reason": "subscription_expired",
                "paid_until": until.isoformat() if until else None,
                "message": "Срок оплаченного тарифа закончился. Продлите подписку, чтобы BORIS продолжил работу.",
                "usage": breakdown, "unlimited": unlimited_flag}
    if _due > 0:
        # аккаунт подключён, но доплата не внесена — работать не даём
        return {"upgrade_due": _due, "tier": tier,
                "tier_name": TARIFFS.get(tier, TARIFFS["none"])["name"],
                "active": False, "blocked_reason": "unpaid_upgrade",
                "message": f"Аккаунт подключён, но не оплачен. Доплата {_due} ₽.",
                "usage": {}}
    paid_until = _paid_until(billing)
    return {"upgrade_due": _due, "tier": tier, "tier_name": TARIFFS.get(tier, TARIFFS["none"])["name"],
            "price_rub": TARIFFS.get(tier, TARIFFS["none"]).get("price_rub", 0),
            "active": True, "period_start": billing["period_start"],
            "period_end": period_end.isoformat(),
            "paid_until": paid_until.isoformat() if paid_until else None,
            "days_left": max(0, (period_end - datetime.utcnow()).days), "usage": breakdown,
            "unlimited": unlimited_flag}


def _owner_unlimited(account_id, db=None) -> bool:
    """Only accounts explicitly marked is_own are unlimited; reuse caller DB."""
    from app.models.account import Account
    owned_db = db is None
    db = db or SessionLocal()
    try:
        row = db.query(Account.is_own).filter(Account.account_id == str(account_id)).first()
        return bool(row and row[0])
    except Exception:
        return False
    finally:
        if owned_db:
            db.close()


def _check_and_consume_unlocked(account_id, unit, amount=1, db=None):
    if _owner_unlimited(account_id, db=db):
        return {"allowed": True, "unit": unit, "unlimited": True, "used": 0, "limit": 0, "remaining": 999999}
    billing = _load_billing(account_id, db=db) or _default_billing("none")
    billing = _ensure_period_fresh(billing)
    # A verified new client really has a four-day trial in users.  Apply the
    # same effective tier to the consumption gate, otherwise the cabinet can
    # say "trial" while the product still behaves as "no tariff".
    if billing.get("tier", "none") == "none" and _active_user_trial(account_id, db=db):
        billing["tier"] = "trial"
    if _subscription_expired(billing):
        until = _paid_until(billing)
        return {"allowed": False, "unit": unit, "blocked_reason": "subscription_expired",
                "paid_until": until.isoformat() if until else None,
                "message": "Срок оплаченного тарифа закончился. Продлите подписку."}
    if billing.get("unlimited"):
        billing.setdefault("usage", {})
        billing["usage"][unit] = billing["usage"].get(unit, 0) + amount
        _save_billing(account_id, billing, db=db, commit=(db is None))
        return {"allowed": True, "used": billing["usage"][unit], "limit": None, "unlimited": True}
    tier = billing.get("tier", "none")
    limit = TARIFFS.get(tier, TARIFFS["none"])["limits"].get(unit, 0)
    used = billing.get("usage", {}).get(unit, 0)
    if used + amount > limit:
        extra = billing.get("extra", {}).get(unit, 0)
        if extra < amount:
            _ok = _owner_key(account_id, db=db)
            if _ok:
                ob = _load_billing(_ok, db=db) or {}
                oextra = (ob.get("extra") or {}).get(unit, 0)
                if oextra >= amount:
                    ob.setdefault("extra", {})
                    ob["extra"][unit] = oextra - amount
                    _save_billing(_ok, ob, db=db, commit=(db is None))
                    return {"allowed": True, "used": used, "limit": limit,
                            "from_shared": True, "shared_left": ob["extra"][unit]}
        if extra >= amount:
            billing.setdefault("extra", {})
            billing["extra"][unit] = extra - amount
            _save_billing(account_id, billing, db=db, commit=(db is None))
            return {"allowed": True, "used": used, "limit": limit,
                    "from_extra": True, "extra_left": billing["extra"][unit]}
        _save_billing(account_id, billing, db=db, commit=(db is None))
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
    _save_billing(account_id, billing, db=db, commit=(db is None))
    return {"allowed": True, "used": used + amount, "limit": limit}


def _ensure_billing_usage_intents_table(db):
    """Self-healing DDL for durable tariff-consume idempotency.

    Restore/new-host safety: a recovered production database may predate the
    migration file. CREATE IF NOT EXISTS keeps the paid path fail-safe without
    requiring a human migration step.
    """
    # BILLING_USAGE_IDEMPOTENCY_SELF_HEAL_V1
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))


def check_and_consume(account_id, unit, amount=1, idempotency_key=""):
    """Atomically consume a tariff unit across both backend replicas/workers.

    Storage billing is JSON, so a read-modify-write without a cross-process lock
    can lose concurrent increments. The lock scope includes the shared owner wallet
    when present, so two client accounts cannot spend the same shared package at once.
    Provider calls never happen here.
    """
    import hashlib
    unit = str(unit or "").strip()
    try:
        amount = int(amount)
    except Exception:
        amount = 0
    if unit not in UNIT_NAMES or amount <= 0:
        return {
            "allowed": False, "unit": unit, "blocked_reason": "invalid_usage_request",
            "message": "Некорректное списание лимита заблокировано.",
        }
    idem = str(idempotency_key or "").strip()[:255]
    shared_scope = _owner_key(account_id) or str(account_id)
    raw = int.from_bytes(
        hashlib.blake2b(("billing-consume|" + shared_scope + "|" + unit).encode("utf-8"), digest_size=8).digest(),
        "big", signed=False,
    )
    lock_key = raw if raw < (1 << 63) else raw - (1 << 64)
    lock_db = SessionLocal()
    try:
        # Use non-blocking PostgreSQL attempts instead of waiting inside one SQL
        # statement: production has a short lock_timeout, so a normal burst of
        # concurrent workers must queue here rather than surface LockNotAvailable.
        import time as _time
        acquired = False
        for _lock_attempt in range(120):
            acquired = bool(lock_db.execute(
                text("select pg_try_advisory_xact_lock(:k)"), {"k": lock_key}
            ).scalar())
            if acquired:
                break
            _time.sleep(0.05)
        if not acquired:
            lock_db.rollback()
            return {
                "allowed": False, "unit": unit, "blocked_reason": "billing_busy",
                "message": "Лимит сейчас обновляется другой операцией BORIS. Повторите действие — дополнительное списание не выполнялось.",
            }
        # BILLING_USAGE_IDEMPOTENCY_V1: the tariff increment and the durable
        # economic-intent record live in the same transaction. A retry after an
        # ambiguous HTTP/worker outcome returns the first result without spending
        # the tariff unit twice. Old callers remain supported when no key exists.
        if idem:
            _ensure_billing_usage_intents_table(lock_db)
            prior = lock_db.execute(text(
                "select amount,result_json from boris_billing_usage_intents"
                " where account_id=:a and unit=:u and idempotency_key=:k"
                " limit 1"), {"a": str(account_id), "u": unit, "k": idem}).fetchone()
            if prior:
                if int(prior[0] or 0) != amount:
                    lock_db.rollback()
                    return {"allowed": False, "unit": unit,
                            "blocked_reason": "billing_idempotency_conflict",
                            "message": "Повтор операции заблокирован: тот же ключ уже использован для другого количества."}
                cached = prior[1] if isinstance(prior[1], dict) else {}
                out = dict(cached or {})
                out.setdefault("allowed", True)
                out["idempotency_replay"] = True
                out["idempotency_key"] = idem
                lock_db.commit()
                return out
        result = _check_and_consume_unlocked(account_id, unit, amount, db=lock_db)
        if idem and result.get("allowed", False):
            import json as _json
            lock_db.execute(text(
                "insert into boris_billing_usage_intents"
                " (account_id,unit,idempotency_key,amount,result_json,created_at)"
                " values (:a,:u,:k,:n,cast(:r as jsonb),now())"),
                {"a": str(account_id), "u": unit, "k": idem, "n": amount,
                 "r": _json.dumps(result, ensure_ascii=False, default=str)})
            result = dict(result)
            result["idempotency_replay"] = False
            result["idempotency_key"] = idem
        lock_db.commit()
        return result
    except Exception:
        lock_db.rollback()
        raise
    finally:
        lock_db.close()


def _owner_key(account_id, db=None):
    """Кошелёк общих пакетов — на владельце аккаунта, а не на самом аккаунте."""
    from sqlalchemy import text as _t
    owned_db = db is None
    db = db or SessionLocal()
    try:
        r = db.execute(_t("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"),
                       {"a": account_id}).fetchone()
        return f"__owner_{r[0]}" if r and r[0] else None
    except Exception:
        return None
    finally:
        if owned_db:
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
    if tier in ("tariff_1", "tariff_2"):
        # Explicit expiry is part of the money state. Older rows without it are
        # interpreted as period_start + 30d, but every new payment writes it.
        fresh["paid_until"] = (datetime.utcnow() + timedelta(days=_PERIOD_DAYS)).isoformat()
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
    command_entitlement = None
    nonpayment_suspension_cleared = False
    if tier in ("tariff_1", "tariff_2"):
        # Paid BORIS subscription includes Command Center + voice/TTS. Keep
        # entitlement provisioning in the canonical tier activation path so
        # Robokassa, balance payment and owner activation behave identically.
        from app.services.command_limits import provision_paid_subscription
        command_entitlement = provision_paid_subscription(
            account_id, tier, days=_PERIOD_DAYS, reset_period=True,
            source=f"billing:{tier}",
        )

        # NONPAYMENT_AUTO_RESTORE_V1: an account suspended only because its
        # base paid period ended must recover automatically after real renewal.
        # Never clear an unrelated/manual actions kill-switch.
        _restore_db = SessionLocal()
        try:
            _row = _restore_db.execute(text("""
                SELECT id,blocked,reason
                  FROM reliability_kill_switches
                 WHERE account_id=:a AND module='actions'
                 ORDER BY id DESC LIMIT 1
                 FOR UPDATE
            """), {"a": str(account_id)}).mappings().first()
            _reason = str((_row or {}).get("reason") or "").lower()
            _is_nonpayment = (
                "основной оплаченный период заверш" in _reason
                or "nonpayment" in _reason
                or "subscription_expired" in _reason
            )
            if _row and bool(_row.get("blocked")) and _is_nonpayment:
                _restore_db.execute(text("""
                    UPDATE reliability_kill_switches
                       SET blocked=false,
                           reason='Оплата подтверждена: доступ к действиям BORIS восстановлен автоматически.',
                           updated_at=now()
                     WHERE id=:i
                """), {"i": int(_row["id"])})
                _restore_db.commit()
                nonpayment_suspension_cleared = True
            else:
                _restore_db.rollback()
        except Exception as _restore_exc:
            _restore_db.rollback()
            print("[billing] nonpayment auto-restore skipped:", type(_restore_exc).__name__)
        finally:
            _restore_db.close()

    return {"status": "ok", "tier": tier, "extra_kept": bool(fresh.get("extra")),
            "command_center": command_entitlement,
            "nonpayment_suspension_cleared": nonpayment_suspension_cleared}


# --- HTTP-эндпоинты ---
from fastapi import Depends, APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/status")
def billing_status(account_id: str, user=Depends(get_current_user)):
    from fastapi import HTTPException
    from app.services.command_policy import account_visible
    db = SessionLocal()
    try:
        if not account_visible(db, user, account_id):
            raise HTTPException(status_code=403, detail="forbidden_account")
    finally:
        db.close()
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
