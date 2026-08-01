"""Привязка ИИ-сотрудников к аккаунтам клиента.

МОП и РОП обслуживают один аккаунт, несколько выбранных или все.
Если память общая — аккаунты попадают в одну группу memory_scopes,
и знание, полученное в одном кабинете, доступно во втором.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text, bindparam

from app.db.session import SessionLocal
from app.models.account import Account

try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])

PRODUCTS = {"mop": "ИИ-менеджер по продажам", "rop": "ИИ-руководитель отдела продаж"}
MODES = ("one", "selected", "all")


def _visible_accounts(db, user):
    q = db.query(Account)
    if getattr(user, "role", "") != "owner":
        q = q.filter((Account.owner_user_id == user.id) |
                     (Account.account_id == getattr(user, "account_id", None)))
    return [a for a in q.order_by(Account.created_at.asc()).all()]


def _binding(db, product):
    return db.execute(text(
        "SELECT account_id, group_key, memory_shared FROM ai_bindings"
        " WHERE product=:p ORDER BY account_id"), {"p": product}).all()


def _is_owner(user):
    return getattr(user, "role", "") == "owner"


def license_of(db, user_id, product):
    """Сколько ИИ оплачено клиенту и до какого числа."""
    from datetime import datetime, timezone
    r = db.execute(text(
        "SELECT qty, paid_until FROM ai_licenses WHERE user_id=:u AND product=:p"),
        {"u": user_id, "p": product}).first()
    if not r:
        return {"qty": 0, "paid_until": None, "active": False}
    until = r[1]
    active = True
    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        active = until >= datetime.now(timezone.utc)
    return {"qty": int(r[0] or 0), "paid_until": until,
            "active": active and int(r[0] or 0) > 0}


class LicenseBody(BaseModel):
    user_id: int
    product: str
    qty: int = 1
    days: int = 30
    comment: str = ""


@router.get("/licenses")
def licenses(user=Depends(get_current_user)):
    """Что оплачено этому клиенту. Владелец не ограничен."""
    db = SessionLocal()
    try:
        out = {}
        for pr in PRODUCTS:
            lic = license_of(db, getattr(user, "id", 0), pr)
            out[pr] = {
                "qty": lic["qty"],
                "active": lic["active"],
                "paid_until": lic["paid_until"].strftime("%d.%m.%Y") if lic["paid_until"] else "",
                "unlimited": _is_owner(user),
            }
        return {"status": "ok", "owner": _is_owner(user), "licenses": out}
    finally:
        db.close()


@router.post("/licenses")
def set_license(body: LicenseBody, user=Depends(get_current_user)):
    """Выдать клиенту оплаченные лицензии. Только владелец."""
    if not _is_owner(user):
        raise HTTPException(status_code=403, detail="Только владелец")
    if body.product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    from datetime import datetime, timedelta, timezone
    until = datetime.now(timezone.utc) + timedelta(days=max(body.days, 1))
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO ai_licenses (user_id, product, qty, paid_until, comment)"
            " VALUES (:u,:p,:q,:t,:c)"
            " ON CONFLICT (user_id, product) DO UPDATE SET qty=:q, paid_until=:t,"
            " comment=:c, updated_at=now()"),
            {"u": body.user_id, "p": body.product, "q": max(body.qty, 0),
             "t": until, "c": body.comment[:250]})
        db.commit()
        return {"status": "ok", "user_id": body.user_id, "product": body.product,
                "qty": body.qty, "paid_until": until.strftime("%d.%m.%Y"),
                "message": "Выдано %d шт. %s до %s" % (
                    body.qty, PRODUCTS[body.product], until.strftime("%d.%m.%Y"))}
    finally:
        db.close()


class BindBody(BaseModel):
    product: str
    mode: str = "one"
    account_ids: list = []
    memory_shared: bool = True
    group_key: str = ""


@router.get("/accounts")
def accounts(product: str = "mop", user=Depends(get_current_user)):
    """Список аккаунтов клиента и текущая привязка ИИ."""
    if product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    db = SessionLocal()
    try:
        accs = _visible_accounts(db, user)
        bound = {r[0]: r for r in _binding(db, product)}
        modes = {r[0]: r[1] for r in db.execute(text(
            "SELECT account_id, mode FROM memory_modes")).all()}
        out = []
        for a in accs:
            b = bound.get(a.account_id)
            out.append({
                "account_id": a.account_id,
                "name": a.name or a.account_id,
                "avito_user_id": a.avito_user_id or "",
                "bound": bool(b),
                "memory_shared": bool(b[2]) if b else False,
                "group_key": (b[1] if b else ""),
                "memory_mode": modes.get(a.account_id, "off"),
            })
        return {"status": "ok", "product": product,
                "product_title": PRODUCTS[product], "accounts": out,
                "bound_count": len(bound)}
    finally:
        db.close()


@router.post("/bind")
def bind(body: BindBody, user=Depends(get_current_user)):
    """Подключить ИИ к аккаунтам. Режимы: один / выбранные / все."""
    if body.product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail="Режим: one, selected или all")
    # Владелец не ограничен: он решает, кому и сколько активировать.
    # Клиент подключает строго в пределах оплаченного (проверка ниже).
    db = SessionLocal()
    try:
        visible = {a.account_id for a in _visible_accounts(db, user)}
        if body.mode == "all":
            chosen = sorted(visible)
        else:
            chosen = [a for a in (body.account_ids or []) if a in visible]
            if body.mode == "one":
                chosen = chosen[:1]
        if not chosen:
            raise HTTPException(status_code=400, detail="Не выбрано ни одного аккаунта")

        if not _is_owner(user):
            lic = license_of(db, getattr(user, "id", 0), body.product)
            if not lic["active"]:
                raise HTTPException(
                    status_code=402,
                    detail="%s не оплачен. Обратитесь к менеджеру BORIS."
                           % PRODUCTS[body.product])
            if len(chosen) > lic["qty"]:
                raise HTTPException(
                    status_code=402,
                    detail="Оплачено %d шт. %s, выбрано %d аккаунтов."
                           % (lic["qty"], PRODUCTS[body.product], len(chosen)))

        group = (body.group_key or ("%s:%s" % (body.product, chosen[0])))[:64]

        db.execute(text("DELETE FROM ai_bindings WHERE product=:p AND account_id IN :accs")
                   .bindparams(bindparam("accs", expanding=True)),
                   {"p": body.product, "accs": sorted(visible)})
        for a in chosen:
            db.execute(text(
                "INSERT INTO ai_bindings (product, account_id, group_key, memory_shared)"
                " VALUES (:p,:a,:g,:s)"),
                {"p": body.product, "a": a, "g": group, "s": bool(body.memory_shared)})

        if body.memory_shared and len(chosen) > 1:
            for a in chosen:
                db.execute(text(
                    "INSERT INTO memory_scopes (scope_key, account_id) VALUES (:k,:a)"
                    " ON CONFLICT (account_id) DO UPDATE SET scope_key=:k"),
                    {"k": group, "a": a})
        else:
            db.execute(text("DELETE FROM memory_scopes WHERE account_id IN :accs")
                       .bindparams(bindparam("accs", expanding=True)),
                       {"accs": chosen})
        db.commit()
        return {"status": "ok", "product": body.product, "accounts": chosen,
                "group_key": group, "memory_shared": bool(body.memory_shared),
                "message": "%s подключён к %d аккаунт(ам)%s" % (
                    PRODUCTS[body.product], len(chosen),
                    ", память общая" if body.memory_shared and len(chosen) > 1
                    else ", память раздельная")}
    finally:
        db.close()


@router.get("/summary")
def summary(product: str = "rop", user=Depends(get_current_user)):
    """Карточка администратора: с какими аккаунтами работает и что знает."""
    if product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    db = SessionLocal()
    try:
        rows = _binding(db, product)
        accs = [r[0] for r in rows]
        if not accs:
            return {"status": "ok", "product": product, "accounts": [],
                    "facts": 0, "confirmed": 0, "conflicts": 0, "usable": 0}
        stat = db.execute(text(
            "SELECT count(*) FILTER (WHERE status <> 'rejected'),"
            "       count(*) FILTER (WHERE status = 'confirmed'),"
            "       count(*) FILTER (WHERE status = 'conflict'),"
            "       count(*) FILTER (WHERE status = 'confirmed'"
            "                         OR (status = 'draft' AND confidence >= 80))"
            "  FROM client_facts WHERE account_id IN :accs")
            .bindparams(bindparam("accs", expanding=True)), {"accs": accs}).first()
        names = {a.account_id: (a.name or a.account_id)
                 for a in db.query(Account).filter(Account.account_id.in_(accs)).all()}
        return {"status": "ok", "product": product,
                "product_title": PRODUCTS[product],
                "accounts": [{"account_id": a, "name": names.get(a, a)} for a in accs],
                "memory_shared": bool(rows[0][2]) if rows else False,
                "facts": stat[0] or 0, "confirmed": stat[1] or 0,
                "conflicts": stat[2] or 0, "usable": stat[3] or 0}
    finally:
        db.close()
