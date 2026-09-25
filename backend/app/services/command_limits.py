# -*- coding: utf-8 -*-
"""Billing owner, тарифные флаги и пул команд.

Ключ владельца намеренно нейтральный — пара (billing_owner_kind,
billing_owner_id). Сегодня kind='user', завтра 'company' или 'agency':
меняется ОДНА функция resolve_billing_owner, схема не трогается.

Правило списания (решение 07.08):
  нет успешного результата — нет списания command units.
Защита сразу от двух вещей: повторного списания и параллельного
перерасхода пула. Порядок операций в charge_units_once менять нельзя.
"""
import datetime
from sqlalchemy import text

RESOURCE_COMMANDS = "commands"
RESOURCE_BRIEFINGS = "briefings"

KIND_USER = "user"


def resolve_billing_owner(db, account_id):
    """(kind, id) или None. Единственное место, где владелец вычисляется."""
    if not account_id:
        return None

    # Legacy social/posting buckets are stored as u<user_id> rather than as
    # accounts.account_id. They still bill the same user-level product pool.
    # Accept only an exact synthetic bucket whose user really exists; all
    # ordinary account ids continue through the canonical accounts mapping.
    raw = str(account_id).strip()
    if raw.startswith("u") and raw[1:].isdigit():
        uid = int(raw[1:])
        exists = db.execute(text("select 1 from users where id=:u"), {"u": uid}).fetchone()
        if exists:
            return (KIND_USER, uid)

    row = db.execute(
        text("select owner_user_id from accounts where account_id=:a"),
        {"a": account_id}).fetchone()
    if not row or row[0] is None:
        return None
    return (KIND_USER, int(row[0]))


def has_feature(db, kind, oid, flag):
    """Нет записи — флаг выключен. Тариф включает флаги явной записью."""
    if not kind or oid is None or not flag:
        return False
    row = db.execute(text(
        "select enabled from product_features"
        " where billing_owner_kind=:k and billing_owner_id=:o and flag=:f"),
        {"k": kind, "o": oid, "f": flag}).fetchone()
    return bool(row and row[0])


def features_of(db, kind, oid):
    rows = db.execute(text(
        "select flag, enabled from product_features"
        " where billing_owner_kind=:k and billing_owner_id=:o"),
        {"k": kind, "o": oid}).fetchall()
    return {r[0]: bool(r[1]) for r in rows}


def set_feature(db, kind, oid, flag, enabled=True, source="tariff"):
    """Выдача флага при оплате тарифа. Идемпотентно."""
    db.execute(text(
        "insert into product_features"
        " (billing_owner_kind, billing_owner_id, flag, enabled, source, updated_at)"
        " values (:k,:o,:f,:e,:s,:ts)"
        " on conflict (billing_owner_kind, billing_owner_id, flag)"
        " do update set enabled=:e, source=:s, updated_at=:ts"),
        {"k": kind, "o": oid, "f": flag, "e": bool(enabled),
         "s": source, "ts": datetime.datetime.utcnow()})
    db.commit()


def pool_state(db, kind, oid, resource=RESOURCE_COMMANDS):
    row = db.execute(text(
        "select purchased, used, period_start, paid_until from product_limits"
        " where billing_owner_kind=:k and billing_owner_id=:o and resource=:r"),
        {"k": kind, "o": oid, "r": resource}).fetchone()
    if not row:
        return {"exists": False, "purchased": 0, "used": 0, "left": 0,
                "active": False, "expired": False,
                "period_start": None, "paid_until": None}
    purchased, used, start, until = int(row[0] or 0), int(row[1] or 0), row[2], row[3]
    expired = bool(until and until < datetime.date.today())
    return {"exists": True, "purchased": purchased, "used": used,
            "left": max(purchased - used, 0),
            "active": purchased > 0 and not expired, "expired": expired,
            "period_start": start, "paid_until": until}


def has_units(db, kind, oid, units=1, resource=RESOURCE_COMMANDS):
    """Ранний отказ ДО AI-вызова. Не резервирует: гонку закрывает charge."""
    st = pool_state(db, kind, oid, resource)
    if not st["exists"] or st["expired"]:
        return False
    return st["left"] >= max(int(units or 0), 0)


def charge_units_once(db, command_id, units, kind, oid,
                      duration_ms=None, result_json=None,
                      resource=RESOURCE_COMMANDS):
    """Вызывается ТОЛЬКО после успешного выполнения.

    Порядок важен: пул обновляется ПЕРВЫМ, потому что только так его
    инкремент можно откатить, если окажется, что команда уже списана.

    Возвращает: charged | already_charged | pool_exhausted_after_success
    """
    units = max(int(units or 0), 0)
    now = datetime.datetime.utcnow()

    if units == 0:
        db.execute(text(
            "update boris_commands set status='done', executed_at=:ts,"
            " units_charged_at=:ts, duration_ms=:ms, result=cast(:res as jsonb)"
            " where command_id=cast(:c as uuid) and units_charged_at is null"),
            {"ts": now, "ms": duration_ms, "res": result_json, "c": command_id})
        db.commit()
        return "charged"

    try:
        pool = db.execute(text(
            "update product_limits set used = used + :u, updated_at=:ts"
            " where billing_owner_kind=:k and billing_owner_id=:o"
            "   and resource=:r and purchased - used >= :u"
            "   and (paid_until is null or paid_until >= current_date)"
            " returning used, purchased"),
            {"u": units, "ts": now, "k": kind, "o": oid, "r": resource}).fetchone()

        if pool is None:
            db.rollback()
            db.execute(text(
                "update boris_commands set status='done', executed_at=:ts,"
                " duration_ms=:ms, result=cast(:res as jsonb),"
                " error_code='pool_exhausted_after_success'"
                " where command_id=cast(:c as uuid) and units_charged_at is null"),
                {"ts": now, "ms": duration_ms, "res": result_json, "c": command_id})
            db.commit()
            return "pool_exhausted_after_success"

        cmd = db.execute(text(
            "update boris_commands set status='done', executed_at=:ts,"
            " units_charged_at=:ts, duration_ms=:ms, result=cast(:res as jsonb)"
            " where command_id=cast(:c as uuid) and units_charged_at is null"
            "   and status in ('parsed','confirmed','executing')"
            " returning units"),
            {"ts": now, "ms": duration_ms, "res": result_json,
             "c": command_id}).fetchone()

        if cmd is None:
            db.rollback()
            return "already_charged"

        db.commit()
        return "charged"
    except Exception:
        db.rollback()
        raise


PAID_SUBSCRIPTION_COMMANDS = 1500
PAID_SUBSCRIPTION_FEATURES = (
    "command_center_enabled",
    "command_center_voice_enabled",
    "command_center_tts_enabled",
)


def provision_paid_subscription(account_id, tier, days=30, reset_period=True, source=None):
    """Provision Command Center entitlements from the canonical paid BORIS tariff.

    The entitlement is owner-scoped because one owner may have several accounts.
    Voice/TTS are included in the paid subscription; product_limits is the expiry
    clock, so an old feature flag alone can never authorize paid voice usage.
    """
    if str(tier or "") not in {"tariff_1", "tariff_2"}:
        return {"status": "not_paid_tier", "tier": tier}
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        owner = resolve_billing_owner(db, account_id)
        if not owner:
            return {"status": "no_billing_owner", "account_id": account_id}
        kind, oid = owner
        now = datetime.datetime.utcnow()
        until = (now + datetime.timedelta(days=max(1, int(days or 30)))).date()
        start = now.date()
        src = str(source or f"billing:{tier}")[:80]
        for flag in PAID_SUBSCRIPTION_FEATURES:
            db.execute(text(
                "insert into product_features"
                " (billing_owner_kind,billing_owner_id,flag,enabled,source,updated_at)"
                " values (:k,:o,:f,true,:s,:ts)"
                " on conflict (billing_owner_kind,billing_owner_id,flag)"
                " do update set enabled=true, source=:s, updated_at=:ts"),
                {"k": kind, "o": oid, "f": flag, "s": src, "ts": now})
        if reset_period:
            db.execute(text(
                "insert into product_limits"
                " (billing_owner_kind,billing_owner_id,resource,purchased,used,period_start,paid_until,updated_at)"
                " values (:k,:o,:r,:p,0,:ps,:pu,:ts)"
                " on conflict (billing_owner_kind,billing_owner_id,resource)"
                " do update set purchased=:p, used=0, period_start=:ps, paid_until=:pu, updated_at=:ts"),
                {"k": kind, "o": oid, "r": RESOURCE_COMMANDS, "p": PAID_SUBSCRIPTION_COMMANDS,
                 "ps": start, "pu": until, "ts": now})
        else:
            db.execute(text(
                "insert into product_limits"
                " (billing_owner_kind,billing_owner_id,resource,purchased,used,period_start,paid_until,updated_at)"
                " values (:k,:o,:r,:p,0,:ps,:pu,:ts)"
                " on conflict (billing_owner_kind,billing_owner_id,resource) do update set"
                " purchased=greatest(product_limits.purchased,:p),"
                " paid_until=greatest(product_limits.paid_until,:pu), updated_at=:ts"),
                {"k": kind, "o": oid, "r": RESOURCE_COMMANDS, "p": PAID_SUBSCRIPTION_COMMANDS,
                 "ps": start, "pu": until, "ts": now})
        db.commit()
        return {"status": "provisioned", "billing_owner_kind": kind,
                "billing_owner_id": oid, "features": list(PAID_SUBSCRIPTION_FEATURES),
                "commands": PAID_SUBSCRIPTION_COMMANDS, "paid_until": until.isoformat()}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
