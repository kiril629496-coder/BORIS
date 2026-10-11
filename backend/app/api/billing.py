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


def add_ecs_package(account_id: str, days: int = 30, *, db=None, commit: bool = True):
    """Canonical ECS grant; preserve longer rights and caller transaction ownership."""
    if db is None and not commit:
        raise ValueError("caller_db_required_for_no_commit")
    owned_db = db is None
    db = db or SessionLocal()
    try:
        if not db.execute(text("SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"),
                          {"a":str(account_id)}).first():
            raise ValueError("account_not_found")
        b = _load_billing(account_id, db=db) or _default_billing("none")
        until = datetime.utcnow() + timedelta(days=max(1,int(days or 30)))
        if b.get("ecs_paid_until"):
            old_until = _naive_utc(datetime.fromisoformat(str(b["ecs_paid_until"]).replace("Z","+00:00")))
            until = max(until, old_until)
        change = {"ecs_purchased":max(1,int(b.get("ecs_purchased") or 0)),
                  "ecs_paid_until":until.isoformat()}
        _save_billing(account_id, change, db=db, commit=False)
        if commit:
            db.commit()
        return {"status":"ok","account_id":account_id,**change}
    except Exception:
        if owned_db:
            db.rollback()
        raise
    finally:
        if owned_db:
            db.close()


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
        # Product pilot metadata has a single canonical writer. General paid
        # usage/grants may contain an older loaded copy and must preserve it.
        if "owner_pilot" in data and set(data) != {"owner_pilot"}:
            data = {k: v for k, v in data.items() if k != "owner_pilot"}
        db.execute(text("SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"),
                   {"a": str(account_id)})
        okey, _n = _billing_owner_key(account_id, db=db)
        if okey != account_id and any(f in data for f in _MONEY_FIELDS):
            money = {f: data[f] for f in _MONEY_FIELDS if f in data}
            orow = db.query(Storage).filter(Storage.account_id == okey, Storage.key == "billing").populate_existing().with_for_update().first()
            if orow:
                orow.value = json.dumps({**json.loads(orow.value), **money}, ensure_ascii=False)
            else:
                db.add(Storage(account_id=okey, key="billing", value=json.dumps(money, ensure_ascii=False)))
        data = {k: v for k, v in data.items() if k != "_accounts_count"}
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").populate_existing().with_for_update().first()
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
    pilot = owner_pilot_state(billing)
    if pilot.get("configured") and (tier not in {"tariff_1","tariff_2"} or _subscription_expired(billing)):
        breakdown = {}
        for unit in UNIT_NAMES:
            used = int((pilot.get("usage") or {}).get(unit) or 0)
            limit = int((pilot.get("limits") or {}).get(unit) or 0)
            breakdown[unit] = {"used":used,"limit":limit,"extra":0,
                               "remaining":max(0,limit-used),"name":UNIT_NAMES[unit]}
        return {"tier":tier,"tier_name":"Пилот BORIS","payment_tier_name":TARIFFS.get(tier,TARIFFS["none"])["name"],
                "price_rub":0,"active":bool(pilot.get("active")),
                "blocked_reason":None if pilot.get("active") else "owner_pilot_"+str(pilot["state"]).lower(),
                "access_mode":"owner_pilot","period_start":pilot.get("starts_at"),
                "period_end":pilot.get("expires_at"),"access_until":pilot.get("expires_at"),
                "paid_until":billing.get("paid_until"),"usage":breakdown,"unlimited":False,"owner_pilot":pilot}
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
    pilot = owner_pilot_state(billing)
    base_expired = _subscription_expired(billing)
    # An explicitly configured owner pilot uses its own finite counters.
    # It never resurrects an expired unlimited/paid quota or changes paid fields.
    if pilot.get("configured") and (billing.get("tier","none") not in {"tariff_1","tariff_2"} or base_expired):
        return _consume_owner_pilot(account_id,billing,pilot,unit,amount,db=db)
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
        # Share the same account lock as explicit pilot and paid grants.
        lock_db.execute(text("SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"),
                        {"a": str(account_id)})
        # JSON billing is shared across units: serialize the account row too.
        # An explicit pilot always has a persisted billing row before consumption.
        lock_db.query(Storage).filter(
            Storage.account_id == str(account_id), Storage.key == "billing"
        ).populate_existing().with_for_update().all()
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
                if out.get("owner_pilot"):
                    current_pilot = owner_pilot_product_access(lock_db, account_id, "campaign")
                    if not current_pilot.get("allowed"):
                        lock_db.rollback()
                        return {"allowed": False, "unit": unit,
                                "blocked_reason": current_pilot.get("reason"),
                                "idempotency_replay": True}
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

# OWNER_PILOT_TRANSACTION_DEPENDENCY_PACKAGE:
# Operator-reviewed helper layer only. Product consume gates must also recognize
# the separate pilot quota, and module grant helpers must join the caller DB.






def extend_base_access(account_id: str, *, days: int = 0, paid_until: str | None = None,
                       source: str, note: str = "", actor_user_id: int | None = None,
                       db=None, commit: bool = True):
    """Extend explicit access with max-date preservation; never records a payment."""
    aid, src = str(account_id or "").strip(), str(source or "").strip()
    if not aid:
        raise ValueError("account_id_required")
    if not src:
        raise ValueError("extension_source_required")
    if db is None and not commit:
        raise ValueError("caller_db_required_for_no_commit")
    now = datetime.utcnow()
    if paid_until:
        try:
            requested = _naive_utc(datetime.fromisoformat(str(paid_until).replace("Z", "+00:00")))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_paid_until") from exc
    else:
        requested = now + timedelta(days=max(1, int(days or _PERIOD_DAYS)))
    if requested <= now:
        raise ValueError("future_paid_until_required")
    owned_db = db is None
    db = db or SessionLocal()
    try:
        owner_id = db.execute(text(
            "SELECT owner_user_id FROM accounts WHERE account_id=:a FOR UPDATE"
        ), {"a": aid}).scalar()
        if not owner_id:
            raise ValueError("account_not_found")
        # Read under the same canonical account lock as the writer, and refresh
        # any preloaded account/owner billing identities before calculating max.
        db.query(Storage).filter(
            Storage.account_id.in_((aid, "user:" + str(owner_id))), Storage.key == "billing"
        ).populate_existing().with_for_update().all()
        billing = _load_billing(aid, db=db) or _default_billing("none")
        previous = str(billing.get("paid_until") or "") or None
        current = None
        if previous:
            try:
                current = _naive_utc(datetime.fromisoformat(previous.replace("Z", "+00:00")))
            except (TypeError, ValueError, OverflowError):
                current = None
        until = max(requested, current) if current is not None else requested
        billing["paid_until"] = until.isoformat()
        billing["access_extension"] = {
            "source": src, "note": str(note or "")[:500], "actor_user_id": actor_user_id,
            "previous_paid_until": previous, "requested_paid_until": requested.isoformat(),
            "extended_at": now.isoformat(),
        }
        _save_billing(aid, billing, db=db, commit=False)
        db.execute(text(
            "UPDATE account_slots SET paid_until=:t,updated_at=now(),"
            " status=CASE WHEN status='readonly' THEN 'connected' ELSE status END "
            "WHERE account_id=:a AND product='inbox' AND (paid_until IS NULL OR paid_until<:t)"
        ), {"a": aid, "t": until})
        db.execute(text(
            "UPDATE users SET subscription_expires_at=:t WHERE id=:u "
            "AND (subscription_expires_at IS NULL OR subscription_expires_at<:t)"
        ), {"u": int(owner_id), "t": until})
        db.execute(text(
            "UPDATE product_limits SET paid_until=:d,updated_at=now() "
            "WHERE billing_owner_kind='user' AND billing_owner_id=:u "
            "AND resource='commands' AND (paid_until IS NULL OR paid_until<:d)"
        ), {"u": str(owner_id), "d": until.date()})
        if commit:
            db.commit()
        return {"status": "ok", "account_id": aid, "previous_paid_until": previous,
                "paid_until": until.isoformat(), "requested_paid_until": requested.isoformat(),
                "source": src, "payment_ledger_touched": False, "committed": bool(commit)}
    except Exception:
        if owned_db:
            db.rollback()
        raise
    finally:
        if owned_db:
            db.close()



# OWNER_PILOT_PRODUCT_CONTRACT_V2
# Product trials remain in owner_pilot. Paid periods, payments and provider
# mandates are independent authorities and are never synthesized here.
OWNER_PILOT_CONTRACT_VERSION = 2
OWNER_PILOT_SERVICES = ("mop", "rop", "ecs", "crm", "reactivation", "campaign", "marketing")
OWNER_PILOT_DEFAULT_LIMITS = {"listings": 100, "banners": 100, "ai_templates": 100, "images": 1000}
OWNER_PILOT_RESOURCES = {
    "listings": ("campaign", 1000), "banners": ("campaign", 500),
    "ai_templates": ("campaign", 500), "images": ("campaign", 5000),
    "mop_requests": ("mop", 100000), "mop_sends": ("mop", 100000),
    "reactivation_sends": ("reactivation", 100000),
    "rop_seconds": ("rop", 6000000), "rop_chats": ("rop", 100000),
    "rop_reports_calls": ("rop", 10000), "rop_reports_chats": ("rop", 10000),
}


def _pilot_dt(value):
    if isinstance(value, datetime):
        return _naive_utc(value)
    try:
        return _naive_utc(datetime.fromisoformat(str(value or "").replace("Z", "+00:00")))
    except (TypeError, ValueError, OverflowError):
        return None


def _pilot_count(value, *, cap=None):
    from decimal import Decimal, InvalidOperation
    if isinstance(value, bool):
        raise ValueError("pilot_count_must_be_integer")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid_pilot_count") from exc
    if (not number.is_finite() or number < 0 or number != number.to_integral_value()
            or (cap is not None and number > cap)):
        raise ValueError("invalid_pilot_count")
    return int(number)


def owner_pilot_state(billing, now=None):
    """Read-only lifecycle, selected products and separate finite reservations."""
    raw = (billing if isinstance(billing, dict) else {}).get("owner_pilot")
    if not isinstance(raw, dict):
        return {"configured": False, "active": False, "state": "NOT_STARTED",
                "services": [], "limits": {}, "usage": {}, "starts_at": None,
                "expires_at": None, "contract_version": None}
    now = _pilot_dt(now or datetime.utcnow())
    starts = _pilot_dt(raw.get("starts_at") or raw.get("started_at"))
    expires = _pilot_dt(raw.get("expires_at"))
    selected = raw.get("services")
    valid = (type(raw.get("contract_version")) is int
             and raw.get("contract_version") == OWNER_PILOT_CONTRACT_VERSION
             and isinstance(selected, list) and bool(selected)
             and all(isinstance(p, str) and p in OWNER_PILOT_SERVICES for p in selected)
             and len(selected) == len(set(selected))
             and bool(str(raw.get("pilot_id") or "").strip()))
    services = list(selected) if valid else []
    limits, usage = {}, {}
    for key, (_, cap) in OWNER_PILOT_RESOURCES.items():
        try:
            raw_limits, raw_usage = raw.get("limits") or {}, raw.get("usage") or {}
            if not isinstance(raw_limits, dict) or not isinstance(raw_usage, dict):
                raise ValueError("invalid_pilot_counters")
            limits[key] = _pilot_count(raw_limits.get(key, 0), cap=cap)
            usage[key] = _pilot_count(raw_usage.get(key, 0))
        except (TypeError, ValueError, OverflowError):
            valid = False
            limits[key] = usage[key] = 0
    # Reject grants in an unselected product instead of silently broadening them.
    if any(limits[k] and p not in services for k, (p, _) in OWNER_PILOT_RESOURCES.items()):
        valid = False
    explicit = str(raw.get("status") or "ACTIVE").upper()
    if raw.get("enabled") is False or explicit == "CANCELLED":
        state = "CANCELLED"
    elif explicit in {"PAUSED", "CONVERTED_TO_PAID"}:
        state = explicit
    elif not valid or not now or not starts or not expires or expires <= starts:
        state = "INVALID"
    elif explicit not in {"ACTIVE", "ENDING_SOON", "SCHEDULED", "EXPIRED"}:
        state = "INVALID"
    elif now >= expires:
        state = "EXPIRED"
    elif now < starts:
        state = "SCHEDULED"
    else:
        state = "ENDING_SOON" if expires - now <= timedelta(hours=24) else "ACTIVE"
    return {
        "configured": True, "active": state in {"ACTIVE", "ENDING_SOON"}, "state": state,
        "contract_version": raw.get("contract_version"), "pilot_id": raw.get("pilot_id"),
        "starts_at": starts.isoformat() if starts else None,
        "expires_at": expires.isoformat() if expires else None,
        "services": services, "limits": limits, "usage": usage,
        "source": str(raw.get("source") or "owner_pilot"),
        "actor_user_id": raw.get("actor_user_id"),
        "paid_conversion": raw.get("paid_conversion") or {},
        "money_authority": False,
    }


def owner_pilot_access(billing, product, *, resource=None, now=None):
    pilot = owner_pilot_state(billing, now=now)
    selected = str(product or "") in pilot.get("services", [])
    allowed = bool(pilot.get("active") and selected)
    reason = None if allowed else (
        "product_not_selected" if pilot.get("active") and not selected
        else "owner_pilot_" + str(pilot.get("state") or "invalid").lower()
    )
    used = limit = 0
    if resource is not None:
        spec = OWNER_PILOT_RESOURCES.get(str(resource))
        if not spec or spec[0] != product:
            allowed, reason = False, "invalid_pilot_resource"
        else:
            used = int(pilot["usage"].get(resource, 0))
            limit = int(pilot["limits"].get(resource, 0))
            if allowed and used >= limit:
                allowed, reason = False, "owner_pilot_limit_exhausted"
    return {
        "allowed": allowed, "state": pilot["state"], "configured": pilot["configured"],
        "product": product, "selected": selected, "resource": resource,
        "used": used, "limit": limit, "left": max(0, limit - used),
        "until": pilot.get("expires_at"), "pilot_id": pilot.get("pilot_id"),
        "source": "storage:billing.owner_pilot", "reason": reason,
        "money_authority": False,
    }


def owner_pilot_product_access(db, account_id, product, *, resource=None, now=None):
    return owner_pilot_access(_load_billing(str(account_id), db=db) or {}, product,
                              resource=resource, now=now)


def owner_pilot_paid_scope_readback(billing, *, now=None):
    """Read actual paid periods and quantities; never infer them from a pilot."""
    b = billing if isinstance(billing, dict) else {}
    now = _pilot_dt(now or datetime.utcnow())
    base_until = _pilot_dt(b.get("paid_until"))
    base = bool(base_until and base_until > now and b.get("tier") in {"tariff_1", "tariff_2"})
    def purchased(key):
        try:
            return _pilot_count(b.get(key, 0)) > 0
        except (TypeError, ValueError, OverflowError):
            return False
    def period(key):
        until = _pilot_dt(b.get(key))
        return bool(until and until > now), until.isoformat() if until else None
    mop_period, mop_until = period("manager_paid_until")
    rop_period, rop_until = period("rop_paid_until")
    ecs_period, ecs_until = period("ecs_paid_until")
    # MOP's current production send gate additionally needs an active base.
    mop = bool(base and mop_period and purchased("manager_msgs_purchased"))
    rop = bool(rop_period and purchased("rop_minutes_purchased"))
    ecs = bool(ecs_period and purchased("ecs_purchased"))
    values = {
        "campaign": (base, "paid_until", base_until.isoformat() if base_until else None),
        "marketing": (base, "paid_until", base_until.isoformat() if base_until else None),
        "mop": (mop, "manager_paid_until", mop_until),
        "rop": (rop, "rop_paid_until", rop_until),
        "ecs": (ecs, "ecs_paid_until", ecs_until),
        "crm": (bool(base or mop or ecs), "crm_existing_product_access", None),
        "reactivation": (bool(mop and purchased("reactivation_purchased")),
                         "manager_paid_until+reactivation_purchased", mop_until),
    }
    return {
        p: {"covered": ok, "source": "storage:billing", "period_field": field,
            "until": until, "reason": None if ok else "actual_paid_scope_not_active"}
        for p, (ok, field, until) in values.items()
    }


def owner_pilot_activation_contract(*, services):
    """Capabilities of the complete installed implementation, not a grant."""
    requested = list(services) if isinstance(services, (list, tuple)) else []
    valid = bool(requested) and all(p in OWNER_PILOT_SERVICES for p in requested)
    # The ordinary service bundle can be staged independently, but must remain
    # activation-closed until the separately protected route adapter is installed.
    try:
        from app.api.auth import owner_pilot_route_contract
        route = owner_pilot_route_contract()
        route_ready = (isinstance(route, dict)
                       and route.get("contract") == "boris.owner_pilot.route_access"
                       and type(route.get("version")) is int and route["version"] == 2
                       and route.get("product_scoped") is True
                       and route.get("tenant_checks_preserved") is True)
    except (ImportError, AttributeError, TypeError, ValueError):
        route_ready = False
    reactivation_ready = True
    blocked = [] if route_ready else ["app.api.auth.owner_pilot_route_contract"]
    if "reactivation" in requested:
        reactivation_ready = False
        try:
            from app.reactivation_core import owner_pilot_reactivation_core_contract
            from app.reactivation_runtime import owner_pilot_reactivation_runtime_contract
            core = owner_pilot_reactivation_core_contract()
            runtime = owner_pilot_reactivation_runtime_contract()
            reactivation_ready = all(
                isinstance(item, dict)
                and item.get("contract") == contract
                and type(item.get("version")) is int and item["version"] == 2
                and item.get("product_scoped") is True
                and item.get("finite_quota") is True
                for item, contract in (
                    (core, "boris.owner_pilot.reactivation_core"),
                    (runtime, "boris.owner_pilot.reactivation_runtime"))
            )
        except (ImportError, AttributeError, TypeError, ValueError):
            reactivation_ready = False
        if not reactivation_ready:
            blocked.append("app.reactivation_core+app.reactivation_runtime.owner_pilot_v2")
    notification_ready = False
    try:
        from app.services.client_pilot_lifecycle import owner_pilot_notification_contract
        notification = owner_pilot_notification_contract()
        notification_ready = (
            isinstance(notification, dict)
            and notification.get("contract") == "boris.owner_pilot.notifications"
            and type(notification.get("version")) is int and notification["version"] == 2
            and notification.get("existing_scheduler") is True
            and notification.get("durable_event_idempotency") is True
            and set(notification.get("events") or []) == {"ENDING_SOON", "EXPIRED", "CONVERTED_TO_PAID"}
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        notification_ready = False
    if not notification_ready:
        blocked.append("app.services.client_pilot_lifecycle.owner_pilot_notification_contract")
    valid = bool(valid and route_ready and reactivation_ready and notification_ready)
    return {
        "contract": "boris.owner_pilot.activation", "version": 1,
        "implementation_version": OWNER_PILOT_CONTRACT_VERSION, "ready": valid,
        "services": list(OWNER_PILOT_SERVICES) if valid else [],
        "checks": {key: valid for key in (
            "account_atomic_grants", "product_scoped_trial_access",
            "bounded_quota_consumption", "expiry_preserves_paid_rights",
            "paid_conversion_scope_readback")},
        "required_input": {"reactivation": "explicit reactivation_messages; no msg2000 default"},
        "money_authority": False,
        "route_adapter_ready": route_ready,
        "reactivation_adapter_ready": reactivation_ready,
        "notification_adapter_ready": notification_ready,
        "blocked_dependency": blocked[0] if blocked else None,
        "blocked_dependencies": blocked,
    }


def activate_owner_pilot(account_id: str, *, days: int = 7, services=None, limits=None,
                         mop_messages: int = 2000, reactivation_messages=None,
                         source: str = "client_pilot_v1", actor_user_id=None,
                         renew: bool = False, db=None, commit: bool = True):
    """Atomic account-scoped grant. Never writes purchased/paid_until fields."""
    import uuid
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id_required")
    if db is None and not commit:
        raise ValueError("caller_db_required_for_no_commit")
    selected = list(services) if isinstance(services, (list, tuple)) else []
    if (not selected or len(selected) != len(set(selected))
            or any(p not in OWNER_PILOT_SERVICES for p in selected)):
        raise ValueError("explicit_selected_products_required")
    period_days = _pilot_count(days, cap=30)
    if period_days < 1:
        raise ValueError("pilot_days_required")
    proposed = {key: 0 for key in OWNER_PILOT_RESOURCES}
    if "campaign" in selected:
        proposed.update(OWNER_PILOT_DEFAULT_LIMITS)
    if "mop" in selected:
        qty = _pilot_count(mop_messages, cap=100000)
        if not qty:
            raise ValueError("mop_messages_required")
        proposed.update(mop_requests=qty, mop_sends=qty)
    if "reactivation" in selected:
        if reactivation_messages is None:
            raise ValueError("reactivation_quota_required_no_default")
        qty = _pilot_count(reactivation_messages, cap=100000)
        if not qty:
            raise ValueError("positive_reactivation_quota_required")
        proposed["reactivation_sends"] = qty
    if "rop" in selected:
        # Same explicit optional pilot package as the existing owner endpoint.
        proposed.update(rop_seconds=1500 * 60, rop_chats=450,
                        rop_reports_calls=30, rop_reports_chats=30)
    if limits is not None:
        if not isinstance(limits, dict) or any(k not in OWNER_PILOT_RESOURCES for k in limits):
            raise ValueError("invalid_pilot_limits")
        for key, value in limits.items():
            product, cap = OWNER_PILOT_RESOURCES[key]
            number = _pilot_count(value, cap=cap)
            if number and product not in selected:
                raise ValueError("quota_for_unselected_product")
            proposed[key] = number
    if "reactivation" in selected and not proposed["reactivation_sends"]:
        raise ValueError("positive_reactivation_quota_required")
    if not owner_pilot_activation_contract(services=selected).get("ready"):
        raise RuntimeError("pilot_route_adapter_not_installed")
    owned_db = db is None
    db = db or SessionLocal()
    try:
        locked = db.execute(text(
            "SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"
        ), {"a": aid}).first()
        if not locked:
            raise ValueError("account_not_found")
        db.query(Storage).filter(
            Storage.account_id == aid, Storage.key == "billing"
        ).populate_existing().with_for_update().all()
        b = _load_billing(aid, db=db) or _default_billing("none")
        current = owner_pilot_state(b)
        if current.get("configured") and not renew:
            return {"status": "already_active" if current["active"] else "restart_required",
                    "account_id": aid, "pilot": current, "payment_ledger_touched": False}
        if "reactivation" in selected and "mop" not in selected:
            if not owner_pilot_paid_scope_readback(b)["mop"]["covered"]:
                raise ValueError("reactivation_requires_selected_or_active_paid_mop")
        now = datetime.utcnow()
        raw = {
            "contract_version": OWNER_PILOT_CONTRACT_VERSION, "pilot_id": str(uuid.uuid4()),
            "enabled": True, "status": "ACTIVE", "services": selected,
            "source": str(source or "client_pilot_v1")[:80],
            "actor_user_id": int(actor_user_id) if actor_user_id else None,
            "starts_at": now.isoformat(), "expires_at": (now + timedelta(days=period_days)).isoformat(),
            "limits": proposed, "usage": {key: 0 for key in OWNER_PILOT_RESOURCES},
        }
        _save_billing(aid, {"owner_pilot": raw}, db=db, commit=False)
        if commit:
            db.commit()
        return {"status": "activated", "account_id": aid, "pilot": owner_pilot_state({"owner_pilot": raw}),
                "payment_ledger_touched": False, "paid_fields_touched": False, "committed": bool(commit)}
    except Exception:
        if owned_db:
            db.rollback()
        raise
    finally:
        if owned_db:
            db.close()


def consume_owner_pilot_unit(account_id, resource, amount=1, *, idempotency_key,
                             db=None, commit=True):
    """Reserve a finite trial unit in the existing durable billing intent ledger.

    A reservation is not evidence of provider delivery. Ambiguous attempts keep
    their reservation; retrying the same business intent never consumes twice.
    This function creates no table and makes no provider/payment calls.
    """
    import hashlib
    spec = OWNER_PILOT_RESOURCES.get(str(resource))
    if not spec:
        return {"allowed": False, "reason": "invalid_pilot_resource"}
    try:
        qty = _pilot_count(amount, cap=spec[1])
    except (TypeError, ValueError, OverflowError):
        return {"allowed": False, "reason": "invalid_pilot_amount"}
    if not qty:
        return {"allowed": False, "reason": "positive_pilot_amount_required"}
    if db is None and not commit:
        raise ValueError("caller_db_required_for_no_commit")
    aid = str(account_id or "").strip()
    if not aid:
        return {"allowed": False, "reason": "account_id_required"}
    owned_db = db is None
    db = db or SessionLocal()
    try:
        if not db.execute(text(
            "SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"
        ), {"a": aid}).first():
            return {"allowed": False, "reason": "account_not_found"}
        db.query(Storage).filter(
            Storage.account_id == aid, Storage.key == "billing"
        ).populate_existing().with_for_update().all()
        b = _load_billing(aid, db=db) or {}
        pilot = owner_pilot_state(b)
        paid = owner_pilot_paid_scope_readback(b).get(spec[0], {}).get("covered", False)
        if paid and spec[0] == "mop":
            # Conversion proves a paid product period, not remaining requests.
            # Re-read the actual balance in this locked caller transaction: an
            # exhausted paid package must still charge the finite pilot fallback.
            from app.api.messenger import get_manager_balance
            balance = get_manager_balance(aid, db=db)
            paid = bool(balance.get("active") and not balance.get("owner_pilot"))
        if not pilot.get("configured") or paid:
            return {"allowed": True, "owner_pilot": False, "charged": False,
                    "reason": "existing_paid_path" if paid else "no_owner_pilot"}
        access = owner_pilot_access(b, spec[0])
        if not access["allowed"]:
            return {"allowed": False, "owner_pilot": True, "reason": access["reason"]}
        intent = str(idempotency_key or "").strip()
        if not intent:
            return {"allowed": False, "owner_pilot": True, "reason": "pilot_intent_required"}
        unit = "owner_pilot:" + str(resource)
        key = str(pilot["pilot_id"]) + ":" + hashlib.sha256(intent.encode("utf-8")).hexdigest()
        prior = db.execute(text(
            "SELECT amount,result_json FROM boris_billing_usage_intents"
            " WHERE account_id=:a AND unit=:u AND idempotency_key=:k LIMIT 1"
        ), {"a": aid, "u": unit, "k": key}).fetchone()
        if prior:
            if int(prior[0]) != qty:
                return {"allowed": False, "owner_pilot": True, "reason": "pilot_idempotency_conflict"}
            cached = prior[1] if isinstance(prior[1], dict) else json.loads(prior[1] or "{}")
            return {**cached, "idempotency_replay": True}
        limit, used = int(pilot["limits"][resource]), int(pilot["usage"][resource])
        if used + qty > limit:
            return {"allowed": False, "owner_pilot": True, "reason": "owner_pilot_limit_exhausted",
                    "used": used, "limit": limit, "left": max(0, limit-used)}
        raw = dict(b["owner_pilot"])
        usage = dict(raw.get("usage") or {})
        usage[resource] = used + qty
        raw["usage"] = usage
        _save_billing(aid, {"owner_pilot": raw}, db=db, commit=False)
        result = {"allowed": True, "owner_pilot": True, "charged": True,
                  "reservation": True, "resource": resource, "used": used+qty,
                  "limit": limit, "left": limit-used-qty, "pilot_id": pilot["pilot_id"],
                  "expires_at": pilot["expires_at"], "idempotency_replay": False}
        db.execute(text(
            "INSERT INTO boris_billing_usage_intents"
            " (account_id,unit,idempotency_key,amount,result_json,created_at)"
            " VALUES (:a,:u,:k,:n,cast(:r as jsonb),now())"
        ), {"a": aid, "u": unit, "k": key, "n": qty, "r": json.dumps(result)})
        if commit:
            db.commit()
        return result
    except Exception:
        if owned_db:
            db.rollback()
        raise
    finally:
        if owned_db:
            db.close()


def _consume_owner_pilot(account_id, billing, pilot, unit, amount, db=None):
    """Content units use the existing check_and_consume lock and receipt."""
    access = owner_pilot_access(billing, "campaign", resource=unit)
    if not access["allowed"]:
        return {"allowed": False, "unit": unit, "blocked_reason": access["reason"],
                "pilot_expires_at": access.get("until")}
    used, limit = access["used"], access["limit"]
    if used + amount > limit:
        return {"allowed": False, "unit": unit, "blocked_reason": "owner_pilot_limit_exhausted",
                "used": used, "limit": limit}
    raw = dict(billing["owner_pilot"])
    usage = dict(raw.get("usage") or {})
    usage[unit] = used + amount
    raw["usage"] = usage
    _save_billing(account_id, {"owner_pilot": raw}, db=db, commit=(db is None))
    return {"allowed": True, "owner_pilot": True, "unit": unit, "used": used+amount,
            "limit": limit, "remaining": limit-used-amount, "pilot_expires_at": access["until"]}


def reconcile_owner_pilot_paid_conversion(account_id, *, db=None, commit=True,
                                          payment_ref=None, payment_source=None):
    """Metadata-only paid conversion after actual grants, including retry recovery."""
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id_required")
    if db is None and not commit:
        raise ValueError("caller_db_required_for_no_commit")
    owned_db = db is None
    db = db or SessionLocal()
    try:
        if not db.execute(text(
            "SELECT account_id FROM accounts WHERE account_id=:a FOR UPDATE"
        ), {"a": aid}).first():
            raise ValueError("account_not_found")
        db.query(Storage).filter(
            Storage.account_id == aid, Storage.key == "billing"
        ).populate_existing().with_for_update().all()
        b = _load_billing(aid, db=db) or {}
        pilot = owner_pilot_state(b)
        if not pilot.get("configured"):
            result = _reconcile_legacy_client_pilot_paid_conversion(
                aid, billing=b, db=db, payment_ref=payment_ref, payment_source=payment_source)
            if commit:
                db.commit()
            return result
        # Ledger presence is only a payment trigger. It never substitutes for
        # the post-grant scope readback below.
        payment = db.execute(text(
            "SELECT id,pack,source,inv_id FROM payments WHERE account_id=:a"
            " AND amount_rub>0 AND status='paid' AND (:i IS NULL OR inv_id=:i)"
            " AND (:s IS NULL OR source=:s) ORDER BY id DESC LIMIT 1"
        ), {"a": aid, "i": str(payment_ref) if payment_ref is not None else None,
            "s": str(payment_source) if payment_source else None}).mappings().first()
        scopes = owner_pilot_paid_scope_readback(b)
        selected = list(pilot.get("services") or [])
        covered = [p for p in selected if scopes[p]["covered"]]
        missing = [p for p in selected if not scopes[p]["covered"]]
        converted = bool(payment and selected and not missing)
        record = {
            "checked_at": datetime.utcnow().isoformat(), "covered_services": covered,
            "missing_services": missing, "all_selected_covered": not missing and bool(selected),
            "payment_proven": bool(payment), "payment": dict(payment) if payment else None,
            "scope_evidence": {p: scopes[p] for p in selected}, "converted": converted,
        }
        raw = dict(b["owner_pilot"])
        raw["paid_conversion"] = record
        if converted:
            raw["status"] = "CONVERTED_TO_PAID"
        _save_billing(aid, {"owner_pilot": raw}, db=db, commit=False)
        if converted:
            row = db.query(Storage).filter(
                Storage.account_id == aid, Storage.key == "client_pilot_v1"
            ).order_by(Storage.id.desc()).first()
            if row:
                meta = json.loads(row.value or "{}")
                if meta.get("pilot_id") == pilot.get("pilot_id"):
                    meta.update(status="CONVERTED_TO_PAID", paid_conversion=record)
                    row.value = json.dumps(meta, ensure_ascii=False)
                    db.flush()
        notification_receipt = None
        if converted:
            from app.services.client_pilot_lifecycle import publish_owner_pilot_notification
            notification_receipt = publish_owner_pilot_notification(db, aid)
        if commit:
            db.commit()
        return {"notification_receipt": notification_receipt,
                "status": "converted" if converted else "partial_paid_scope" if payment else "payment_not_proven",
                "converted": converted, **record, "paid_fields_touched": False}
    except Exception:
        if owned_db:
            db.rollback()
        raise
    finally:
        if owned_db:
            db.close()



class ExtendBaseAccessRequest(BaseModel):
    account_id: str
    days: int = 0
    paid_until: str | None = None
    source: str
    note: str = ""


@router.post("/base-access/extend")
def billing_extend_base_access(req: ExtendBaseAccessRequest, user=Depends(require_owner)):
    try:
        return extend_base_access(
            req.account_id,
            days=req.days,
            paid_until=req.paid_until,
            source=req.source,
            note=req.note,
            actor_user_id=getattr(user, "id", None),
        )
    except ValueError as exc:
        from fastapi import HTTPException
        code = str(exc)
        status = 404 if code == "account_not_found" else 400
        raise HTTPException(status_code=status, detail=code)

def _reconcile_legacy_client_pilot_paid_conversion(
        account_id, *, billing, db, payment_ref=None, payment_source=None):
    """Reconcile stored legacy pilot metadata after a named canonical payment.

    Caller holds the account row lock. Historical payments, unnamed triggers and
    partial paid scopes cannot convert the pilot. This does not grant any right.
    """
    aid = str(account_id)
    row = db.query(Storage).filter(
        Storage.account_id == aid, Storage.key == "client_pilot_v1"
    ).order_by(Storage.id.desc()).populate_existing().with_for_update().first()
    if row is None:
        return {"status": "no_pilot", "converted": False, "paid_fields_touched": False}
    try:
        raw = json.loads(row.value or "{}")
    except (TypeError, ValueError):
        return {"status": "invalid_legacy_pilot", "converted": False, "paid_fields_touched": False}
    if not isinstance(raw, dict):
        return {"status": "invalid_legacy_pilot", "converted": False, "paid_fields_touched": False}
    starts = _pilot_dt(raw.get("starts_at")) or _pilot_dt(raw.get("started_at"))
    expires = _pilot_dt(raw.get("expires_at"))
    selected = raw.get("services")
    selected = list(dict.fromkeys(selected)) if isinstance(selected, list) and all(
        isinstance(p, str) for p in selected) else []
    valid = (starts is not None and expires is not None and expires > starts
             and starts <= datetime.utcnow() and bool(selected)
             and all(p in OWNER_PILOT_SERVICES for p in selected)
             and str(raw.get("status") or "").upper() not in {"CANCELLED", "PAUSED"})
    if not valid:
        return {"status": "invalid_legacy_pilot", "converted": False, "paid_fields_touched": False}
    ref = str(payment_ref).strip() if payment_ref is not None else ""
    source = str(payment_source or "").strip()
    payment = None
    if ref and source:
        payment = db.execute(text(
            "SELECT id,pack,source,inv_id,created_at FROM payments WHERE account_id=:a"
            " AND amount_rub>0 AND status='paid' AND inv_id=:i AND source=:s"
            " ORDER BY id DESC LIMIT 1"
        ), {"a": aid, "i": ref, "s": source}).mappings().first()
    payment_at = _pilot_dt(payment.get("created_at")) if payment else None
    payment_proven = bool(payment and payment_at and starts <= payment_at <= datetime.utcnow())
    scopes = owner_pilot_paid_scope_readback(billing)
    covered = [p for p in selected if scopes[p]["covered"]]
    missing = [p for p in selected if not scopes[p]["covered"]]
    converted = bool(payment_proven and selected and not missing)
    payment_evidence = ({key: value for key, value in dict(payment).items() if key != "created_at"}
                        if payment else None)
    if payment_evidence is not None:
        payment_evidence["created_at"] = payment_at.isoformat() if payment_at else None
    record = {
        "checked_at": datetime.utcnow().isoformat(), "covered_services": covered,
        "missing_services": missing, "all_selected_covered": bool(selected) and not missing,
        "payment_proven": payment_proven, "payment": payment_evidence,
        "payment_trigger": {"source": source or None, "ref": ref or None,
                            "pilot_starts_at": starts.isoformat()},
        "scope_evidence": {p: scopes[p] for p in selected}, "converted": converted,
    }
    # Preserve an already proven transition on unrelated failed retries; current
    # partial readback is returned honestly without rewriting its historical proof.
    previous = raw.get("paid_conversion")
    previous = previous if isinstance(previous, dict) else {}
    if str(raw.get("status") or "").upper() != "CONVERTED_TO_PAID" or not previous.get("converted") or converted:
        raw["paid_conversion"] = record
        if converted:
            raw["status"] = "CONVERTED_TO_PAID"
        row.value = json.dumps(raw, ensure_ascii=False)
        db.flush()
    notification_receipt = None
    if converted:
        from app.services.client_pilot_lifecycle import publish_legacy_client_pilot_notification
        notification_receipt = publish_legacy_client_pilot_notification(db, aid)
    return {
        "notification_receipt": notification_receipt,
        "status": "converted" if converted else "partial_paid_scope" if payment_proven else "payment_not_proven",
        "converted": converted, **record, "paid_fields_touched": False,
        "pilot_source": "client_pilot_v1",
    }
