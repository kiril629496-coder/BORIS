# -*- coding: utf-8 -*-
"""Сотрудники клиента: заведение, список, выдача и отзыв доступа.

Роутер регистрируется БЕЗ _protected (как manager_router): check_account_access
требует одиночный account_id в запросе, а здесь работа со списком аккаунтов.
Права проверяются внутри каждой ручки через _owned_accounts().
"""
import datetime
import secrets
import string
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.user import User
from app.models.account import Account
from app.api.auth import get_current_user, hash_password

try:  # нормализатор почты живёт в auth.py; имя может отличаться
    from app.api.auth import normalize_email as _norm_email
except ImportError:
    def _norm_email(value: str) -> str:
        return (value or "").strip().lower()

router = APIRouter(prefix="/api/team", tags=["team"])

EMPLOYEE_ROLE = "employee"
_ALPHABET = string.ascii_lowercase + string.ascii_uppercase + string.digits


def _gen_password(n: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _owned_accounts(db, user) -> List[str]:
    """account_id, которыми вызывающий вправе распоряжаться.
    Владелец SaaS — все. Клиент — свои по владению плюс где can_manage_team."""
    if getattr(user, "role", "") == "owner":
        return [a.account_id for a in db.query(Account).all()]
    own = [a.account_id for a in db.query(Account).filter(
        Account.owner_user_id == user.id).all()]
    rows = db.execute(text(
        "SELECT account_id FROM user_account_access"
        " WHERE user_id = :u AND can_manage_team = TRUE"), {"u": user.id}).fetchall()
    for r in rows:
        if r[0] not in own:
            own.append(r[0])
    return own


class CreateEmployeeBody(BaseModel):
    email: str
    name: str = ""
    account_ids: List[str]
    can_reply: bool = True
    can_manage_ads: bool = False
    can_view_reports: bool = False
    password: Optional[str] = None


@router.post("/create")
def create_employee(body: CreateEmployeeBody, user: User = Depends(get_current_user)):
    email = (body.email or "").strip()
    if "@" not in email or len(email) < 5:
        raise HTTPException(status_code=400, detail="Некорректный email")
    if not body.account_ids:
        raise HTTPException(status_code=400, detail="Укажите хотя бы один аккаунт")

    db = SessionLocal()
    try:
        allowed = _owned_accounts(db, user)
        foreign = [a for a in body.account_ids if a not in allowed]
        if foreign:
            raise HTTPException(status_code=403,
                                detail="Нет прав на аккаунты: " + ", ".join(foreign))
        norm = _norm_email(email)
        exists = db.query(User).filter(
            (User.email == email) | (User.email_normalized == norm)).first()
        if exists:
            raise HTTPException(status_code=409, detail="Такой email уже зарегистрирован")

        plain = (body.password or "").strip() or _gen_password()
        emp = User(
            email=email,
            password_hash=hash_password(plain),
            role=EMPLOYEE_ROLE,
            account_id=body.account_ids[0],
            is_active=True,
            status="active",
            email_verified=True,
            email_verified_at=datetime.datetime.utcnow(),
            email_normalized=norm,
        )
        db.add(emp)
        db.flush()
        for acc in body.account_ids:
            db.execute(text(
                "INSERT INTO user_account_access (user_id, account_id, role,"
                " can_view, can_reply, can_manage_ads, can_view_reports, can_manage_team)"
                " VALUES (:u, :a, :r, TRUE, :rep, :ads, :rep2, FALSE)"
                " ON CONFLICT (user_id, account_id) DO NOTHING"),
                {"u": emp.id, "a": acc, "r": EMPLOYEE_ROLE,
                 "rep": bool(body.can_reply), "ads": bool(body.can_manage_ads),
                 "rep2": bool(body.can_view_reports)})
        db.commit()
        return {"status": "ok", "user_id": emp.id, "email": emp.email,
                "password": plain, "accounts": body.account_ids,
                "note": "Пароль показывается один раз — сохраните его"}
    finally:
        db.close()


@router.get("/list")
def list_team(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        allowed = _owned_accounts(db, user)
        if not allowed:
            return {"status": "ok", "employees": []}
        rows = db.execute(text(
            "SELECT u.id, u.email, u.is_active, a.account_id, a.can_reply,"
            " a.can_manage_ads, a.can_view_reports, a.created_at"
            " FROM user_account_access a JOIN users u ON u.id = a.user_id"
            " WHERE a.account_id = ANY(:accs) AND u.id <> :me"
            " ORDER BY u.id"), {"accs": allowed, "me": user.id}).fetchall()
        by_user = {}
        for r in rows:
            item = by_user.setdefault(r[0], {
                "user_id": r[0], "email": r[1], "is_active": bool(r[2]),
                "created_at": r[7].isoformat() if r[7] else None, "accounts": []})
            item["accounts"].append({
                "account_id": r[3], "can_reply": bool(r[4]),
                "can_manage_ads": bool(r[5]), "can_view_reports": bool(r[6])})
        return {"status": "ok", "employees": list(by_user.values())}
    finally:
        db.close()


class RevokeBody(BaseModel):
    user_id: int
    account_id: Optional[str] = None


@router.post("/revoke")
def revoke(body: RevokeBody, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        allowed = _owned_accounts(db, user)
        emp = db.query(User).filter(User.id == body.user_id).first()
        if not emp or emp.id == user.id:
            raise HTTPException(status_code=404, detail="Сотрудник не найден")
        links = [r[0] for r in db.execute(text(
            "SELECT account_id FROM user_account_access WHERE user_id = :u"),
            {"u": emp.id}).fetchall()]
        if not any(a in allowed for a in links):
            raise HTTPException(status_code=403, detail="Это не ваш сотрудник")

        targets = [body.account_id] if body.account_id else list(links)
        foreign = [a for a in targets if a not in allowed]
        if foreign:
            raise HTTPException(status_code=403, detail="Нет прав на аккаунты: "
                                + ", ".join(foreign))
        for acc in targets:
            db.execute(text(
                "DELETE FROM user_account_access"
                " WHERE user_id = :u AND account_id = :a"), {"u": emp.id, "a": acc})
        left = db.execute(text(
            "SELECT count(*) FROM user_account_access WHERE user_id = :u"),
            {"u": emp.id}).scalar()
        blocked = False
        if not left:
            # доступов не осталось — блокируем, но НЕ удаляем: история ответов
            # должна оставаться привязанной к реальному пользователю
            emp.is_active = False
            emp.status = "blocked"
            blocked = True
        db.commit()
        return {"status": "ok", "removed": targets,
                "accounts_left": int(left or 0), "blocked": blocked}
    finally:
        db.close()
