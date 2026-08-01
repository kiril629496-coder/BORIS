"""Администрирование → Клиенты.

Экран для владельца: найти клиента, назначить тариф и срок, выдать слоты,
записать оплату и комментарий. Своей бизнес-логики НЕ содержит — только
вызывает существующие механизмы:
  billing.set_tier            — тариф
  payments.set_payment        — фиксация оплаты
  inbox_slots.grant           — выдача слотов по стандартной сетке цен
  avito._audit_log            — журнал
Все ручки доступны только владельцу (require_owner).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.auth import require_owner

router = APIRouter(prefix="/api/admin/clients")


def _card(db, uid: int) -> dict:
    """Полная карточка клиента: пользователь, его аккаунты, слоты, биллинг."""
    u = db.execute(text(
        "select id, email, role, status, account_id, subscription_expires_at, "
        "created_at, planned_accounts from users where id = :i"), {"i": uid}).fetchone()
    if not u:
        return {}
    accs = db.execute(text(
        "select account_id, name, billing_mode, created_at "
        "from accounts where owner_user_id = :i order by created_at"),
        {"i": uid}).fetchall()
    slots = db.execute(text(
        "select id, slot_no, status, account_id, account_name, price_rub, "
        "period_start, paid_until, payment_id "
        "from account_slots where owner_user_id = :i order by slot_no"),
        {"i": uid}).fetchall()
    ids = [a[0] for a in accs]
    billing = {}
    if ids:
        rows = db.execute(text(
            "select account_id, key, value from storage "
            "where account_id = any(:ids) and key in ('billing', 'payment_status')"),
            {"ids": ids}).fetchall()
        for acc_id, key, val in rows:
            billing.setdefault(acc_id, {})[key] = val
    return {
        "user": {"id": u[0], "email": u[1], "role": u[2], "status": u[3],
                 "account_id": u[4],
                 "subscription_expires_at": str(u[5]) if u[5] else None,
                 "created_at": str(u[6]) if u[6] else None,
                 "planned_accounts": u[7]},
        "accounts": [{"account_id": a[0], "name": a[1], "billing_mode": a[2],
                      "created_at": str(a[3]) if a[3] else None} for a in accs],
        "slots": [{"id": s[0], "slot_no": s[1], "status": s[2], "account_id": s[3],
                   "account_name": s[4], "price_rub": s[5],
                   "period_start": str(s[6]) if s[6] else None,
                   "paid_until": str(s[7]) if s[7] else None,
                   "payment_id": s[8]} for s in slots],
        "billing": billing,
    }


@router.get("/search")
def search(q: str = "", user=Depends(require_owner)):
    """Поиск клиента по email, названию аккаунта или account_id."""
    db = SessionLocal()
    try:
        needle = "%" + (q or "").strip().lower() + "%"
        rows = db.execute(text(
            "select distinct u.id, u.email, u.role, u.status "
            "from users u left join accounts a on a.owner_user_id = u.id "
            "where lower(u.email) like :n or lower(coalesce(a.name,'')) like :n "
            "or lower(coalesce(a.account_id,'')) like :n "
            "or lower(coalesce(u.account_id,'')) like :n "
            "order by u.id desc limit 20"), {"n": needle}).fetchall()
        return {"status": "ok", "found": len(rows),
                "clients": [{"id": r[0], "email": r[1], "role": r[2], "status": r[3]}
                            for r in rows]}
    finally:
        db.close()


@router.get("/card")
def card(user_id: int, user=Depends(require_owner)):
    """Карточка одного клиента."""
    db = SessionLocal()
    try:
        data = _card(db, user_id)
        if not data:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return {"status": "ok", **data}
    finally:
        db.close()


class ProvisionBody(BaseModel):
    user_id: int
    tier: str = ""                      # tariff_1 | tariff_2 | none | "" = не менять
    tier_account_id: str = ""           # на какой аккаунт; пусто = основной
    slots: int = 0                      # сколько выдать; 0 = не выдавать
    slot_days: int = 30
    amount_rub: float = 0               # 0 = оплату не фиксировать
    period_days: int = 30
    paid_at: Optional[str] = None
    payment_id: str = ""
    comment: str = ""
    dry_run: bool = True                # по умолчанию НИЧЕГО не меняет


@router.post("/provision")
def provision(body: ProvisionBody, user=Depends(require_owner)):
    """Оформить клиента одним действием. dry_run=True показывает план без записи."""
    db = SessionLocal()
    try:
        before = _card(db, body.user_id)
        if not before:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        acc_id = body.tier_account_id or before["user"]["account_id"]
        if not acc_id and before["accounts"]:
            acc_id = before["accounts"][0]["account_id"]
    finally:
        db.close()

    plan = []
    if body.tier:
        plan.append("тариф %s на аккаунт %s" % (body.tier, acc_id))
    if body.slots:
        plan.append("выдать слотов: %d на %d дней" % (body.slots, body.slot_days))
    if body.amount_rub:
        plan.append("зафиксировать оплату %.0f ₽ на %d дней"
                    % (body.amount_rub, body.period_days))
    if body.comment:
        plan.append("комментарий в журнал: %s" % body.comment[:120])
    if not plan:
        return {"status": "error", "message": "Нечего применять"}

    if body.dry_run:
        return {"status": "ok", "dry_run": True, "plan": plan, "before": before}

    done, errors = [], []

    if body.tier:
        try:
            from app.api.billing import set_tier
            set_tier(acc_id, body.tier)
            done.append("тариф %s → %s" % (body.tier, acc_id))
        except Exception as e:
            errors.append("тариф: %s" % str(e)[:150])

    if body.slots:
        try:
            from app.api.inbox_slots import grant, GrantRequest
            res = grant(GrantRequest(owner_user_id=body.user_id, count=body.slots,
                                     days=body.slot_days,
                                     payment_id=body.payment_id), user=user)
            done.append("слоты: создано %s, всего %s, начислено %s ₽"
                        % (res.get("created"), res.get("slots_total"),
                           res.get("charged")))
        except Exception as e:
            errors.append("слоты: %s" % str(e)[:150])

    if body.amount_rub and acc_id:
        try:
            from app.api.payments import set_payment, SetPaymentBody
            set_payment(SetPaymentBody(account_id=acc_id,
                                       amount_rub=body.amount_rub,
                                       period_days=body.period_days,
                                       paid_at=body.paid_at))
            done.append("оплата %.0f ₽ на %d дней" % (body.amount_rub, body.period_days))
        except Exception as e:
            errors.append("оплата: %s" % str(e)[:150])

    if body.comment and acc_id:
        try:
            from app.api.avito import _audit_log
            _audit_log(acc_id, "admin_provision", body.comment, "director")
            done.append("комментарий записан в журнал")
        except Exception as e:
            errors.append("журнал: %s" % str(e)[:150])

    db = SessionLocal()
    try:
        after = _card(db, body.user_id)
    finally:
        db.close()

    return {"status": "ok" if not errors else "partial",
            "done": done, "errors": errors, "after": after}
