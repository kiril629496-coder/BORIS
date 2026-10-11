# -*- coding: utf-8 -*-
"""Сотрудники клиента: заведение, список, выдача и отзыв доступа.

Роутер регистрируется БЕЗ _protected (как manager_router): check_account_access
требует одиночный account_id в запросе, а здесь работа со списком аккаунтов.
Права проверяются внутри каждой ручки через _owned_accounts().
"""
import datetime
import hashlib
import json
import secrets
import string
import urllib.parse
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.user import User
from app.models.account import Account
from app.api.auth import get_current_user, hash_password, verify_password
from app.api.legal import require_messenger_legal_user
from app.services.command_policy import has_permission
from app.services.messenger_rate_limit import enforce as enforce_messenger_rate
from app.services.messenger_security_audit import append_security_audit

try:  # нормализатор почты живёт в auth.py; имя может отличаться
    from app.api.auth import normalize_email as _norm_email
except ImportError:
    def _norm_email(value: str) -> str:
        return (value or "").strip().lower()

router = APIRouter(prefix="/api/team", tags=["team"])

EMPLOYEE_ROLE = "employee"
TEAM_ROLES = {"OWNER", "MANAGER_LEAD", "MANAGER", "VIEWER"}
ROLE_PERMISSIONS = {
    "OWNER": {"can_view": True, "can_reply": True, "can_manage_ads": True, "can_view_reports": True, "can_manage_team": True},
    "MANAGER_LEAD": {"can_view": True, "can_reply": True, "can_manage_ads": True, "can_view_reports": True, "can_manage_team": True},
    "MANAGER": {"can_view": True, "can_reply": True, "can_manage_ads": False, "can_view_reports": True, "can_manage_team": False},
    "VIEWER": {"can_view": True, "can_reply": False, "can_manage_ads": False, "can_view_reports": True, "can_manage_team": False},
}
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
def create_employee(body: CreateEmployeeBody, user: User = Depends(require_messenger_legal_user)):
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
def list_team(user: User = Depends(require_messenger_legal_user)):
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
def revoke(body: RevokeBody, user: User = Depends(require_messenger_legal_user)):
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
            db.execute(text(
                "UPDATE lead_push_subscriptions SET active=FALSE,updated_at=now()"
                " WHERE user_id=:u AND account_id=:a AND active=TRUE"),
                {"u": emp.id, "a": acc})
        left = db.execute(text(
            "SELECT count(*) FROM user_account_access WHERE user_id = :u"),
            {"u": emp.id}).scalar()
        blocked = False
        if not left:
            # Доступов не осталось: блокируем, но НЕ удаляем пользователя — история
            # сообщений и audit identity должны оставаться привязаны к реальному user_id.
            # Одновременно инвалидируем все server-side/legacy auth sessions через epoch.
            from app.services.auth_sessions import revoke_all_and_advance_epoch
            revoke_all_and_advance_epoch(db, int(emp.id), "team_access_revoked")
            emp.is_active = False
            emp.status = "blocked"
            blocked = True
        from app.api.internal_messenger import emit_messenger_event, ensure_general_conversation_membership
        for acc in targets:
            ensure_general_conversation_membership(db, acc)
            emit_messenger_event(db, acc, "team.changed", "user", emp.id, {"action": "access_revoked", "user_id": emp.id})
            append_security_audit(
                db, action="team_access_revoke", user_id=int(user.id),
                account_id=acc, entity_type="user", entity_id=int(emp.id),
                session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
                details={"blocked": bool(blocked)},
            )
        db.commit()
        from app.services.messenger_security import audit_security_event
        for acc in targets:
            audit_security_event(
                user_id=int(user.id), account_id=acc,
                action="Отозвал доступ сотрудника", object_kind="team_access",
                object_name=f"user:{int(emp.id)}", after="revoked",
                reason="all_access_removed" if blocked else "account_access_removed",
            )
        return {"status": "ok", "removed": targets,
                "accounts_left": int(left or 0), "blocked": blocked}
    finally:
        db.close()


class InviteBody(BaseModel):
    account_id: str
    name: str
    phone: str
    email: Optional[str] = None
    role: str = "MANAGER"
    permissions_override: dict = {}
    expires_hours: int = 72


def _phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+").replace("+", "+", 1)


def _require_team_manage(db, user, account_id: str):
    """Canonical Team permission gate.

    Account ownership is authoritative and does not require a duplicated
    user_account_access row. Delegated MANAGER_LEAD access is resolved through
    the same shared policy used by the rest of BORIS.
    """
    if not has_permission(db, user, account_id, "can_manage_team"):
        raise HTTPException(
            status_code=403,
            detail="У вашей роли нет доступа к управлению командой",
        )


def _invite_url(token: str) -> str:
    base = "https://boris-ai.pro/messenger/invite"
    return base + "?token=" + urllib.parse.quote(token, safe="")


def _qr_svg(value: str) -> str:
    # Deliberately returns a scannable-friendly owner artifact without claiming push or acceptance.
    esc = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"80\"><rect width=\"100%\" height=\"100%\" fill=\"white\"/><text x=\"8\" y=\"24\" font-family=\"monospace\" font-size=\"10\">BORIS INVITE</text><text x=\"8\" y=\"48\" font-family=\"monospace\" font-size=\"8\">" + esc[:110] + "</text></svg>"


@router.post("/invite")
def create_invite(body: InviteBody, user: User = Depends(require_messenger_legal_user)):
    enforce_messenger_rate("messenger_invite", f"user:{user.id}", limit=20, window_seconds=3600, message="Слишком много приглашений. Повторите позже.")
    role = (body.role or "").upper()
    phone = _phone(body.phone)
    if role not in TEAM_ROLES or role == "OWNER":
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    if len(phone) < 7 or not body.name.strip():
        raise HTTPException(status_code=400, detail="Нужны имя и нормализованный телефон")
    if body.expires_hours < 1 or body.expires_hours > 168:
        raise HTTPException(status_code=400, detail="Срок invite: от 1 до 168 часов")
    db = SessionLocal()
    try:
        _require_team_manage(db, user, body.account_id)
        token = secrets.token_urlsafe(32)
        expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=body.expires_hours)
        created = db.execute(text("INSERT INTO team_memberships (account_id,name,phone,phone_normalized,email,role,permissions_override,status,invite_token_hash,invite_expires_at,created_by_user_id) VALUES (:a,:n,:p,:pn,:e,:r,CAST(:po AS jsonb),'INVITED',:h,:x,:u) RETURNING id"), {"a": body.account_id, "n": body.name.strip(), "p": body.phone.strip(), "pn": phone, "e": (body.email or None), "r": role, "po": json.dumps(body.permissions_override or {}, ensure_ascii=False), "h": hashlib.sha256(token.encode()).hexdigest(), "x": expires, "u": user.id}).first()
        from app.api.internal_messenger import emit_messenger_event
        emit_messenger_event(db, body.account_id, "team.changed", "membership", created[0], {"action": "invite_created", "role": role, "name": body.name.strip()})
        append_security_audit(
            db, action="team_invite_create", user_id=int(user.id),
            account_id=body.account_id, entity_type="membership", entity_id=int(created[0]),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"role": role},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=body.account_id,
            action="Создал приглашение сотрудника", object_kind="team_invite",
            object_name=f"membership:{int(created[0])}", after=f"role:{role}",
        )
        url = _invite_url(token)
        return {"status":"ok", "membership_status":"INVITED", "invite_url":url, "qr_payload":url, "expires_at":expires.isoformat(), "instructions":"Передайте URL или локально сгенерированный QR сотруднику. Доступ к данным появится только после принятия invite."}
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_team_memberships_active_phone":
            raise HTTPException(status_code=409, detail="Сотрудник с этим телефоном уже добавлен или приглашён в команду") from None
        raise
    finally:
        db.close()


class AcceptInviteBody(BaseModel):
    token: str
    password: Optional[str] = None
    email: Optional[str] = None


@router.post("/invite/accept")
def accept_invite(body: AcceptInviteBody, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    enforce_messenger_rate("messenger_invite_accept", f"ip:{client_ip}", limit=60, window_seconds=3600, message="Слишком много попыток принятия приглашения. Повторите позже.")
    digest = hashlib.sha256((body.token or "").encode()).hexdigest()
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM team_memberships WHERE invite_token_hash=:h FOR UPDATE"), {"h": digest}).mappings().first()
        if not row or row["status"] != "INVITED" or not row["invite_expires_at"] or row["invite_expires_at"] <= datetime.datetime.now(datetime.timezone.utc):
            raise HTTPException(status_code=400, detail="Invite недействителен, истёк или уже использован")
        email = (body.email or row["email"] or "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Укажите рабочий email")
        if row["email"] and _norm_email(email) != _norm_email(str(row["email"])):
            raise HTTPException(status_code=400, detail="Email не совпадает с приглашением")
        password = str(body.password or "")
        if not password:
            raise HTTPException(status_code=400, detail="Укажите пароль")
        existing = db.query(User).filter(User.email_normalized == _norm_email(email)).first()
        if existing:
            if not verify_password(password, existing.password_hash):
                raise HTTPException(status_code=401, detail="Неверный email или пароль")
            emp = existing
            if emp.status == "blocked":
                emp.status = "active"; emp.is_active = True
        else:
            weak = password.lower() in {"password", "password1", "qwerty123", "12345678", "123456789", "admin123"}
            if len(password) < 8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password) or weak:
                raise HTTPException(status_code=400, detail="Пароль должен быть не короче 8 символов и содержать буквы и цифры")
            emp = User(email=email, email_normalized=_norm_email(email), password_hash=hash_password(password), role="employee", account_id=row["account_id"], is_active=True, status="active", email_verified=True, email_verified_at=datetime.datetime.utcnow())
            db.add(emp); db.flush()
        perms = dict(ROLE_PERMISSIONS.get(row["role"], ROLE_PERMISSIONS["VIEWER"])); perms.update(row["permissions_override"] or {})
        db.execute(text("INSERT INTO user_account_access (user_id,account_id,role,can_view,can_reply,can_manage_ads,can_view_reports,can_manage_team) VALUES (:u,:a,:r,:v,:rep,:ads,:rep2,:team) ON CONFLICT (user_id,account_id) DO UPDATE SET role=:r, can_view=:v, can_reply=:rep, can_manage_ads=:ads, can_view_reports=:rep2, can_manage_team=:team"), {"u":emp.id,"a":row["account_id"],"r":row["role"],"v":bool(perms["can_view"]),"rep":bool(perms["can_reply"]),"ads":bool(perms["can_manage_ads"]),"rep2":bool(perms["can_view_reports"]),"team":bool(perms["can_manage_team"])})
        db.execute(text("UPDATE team_memberships SET user_id=:u,status='ACTIVE',invite_token_hash=NULL,invite_accepted_at=now(),last_login_at=now() WHERE id=:id"), {"u":emp.id,"id":row["id"]})
        from app.api.internal_messenger import emit_messenger_event, ensure_general_conversation_membership
        ensure_general_conversation_membership(db, row["account_id"])
        emit_messenger_event(db, row["account_id"], "team.changed", "membership", row["id"], {"action": "invite_accepted", "user_id": emp.id, "role": row["role"]})
        append_security_audit(
            db, action="team_invite_accept", user_id=int(emp.id),
            account_id=row["account_id"], entity_type="membership", entity_id=int(row["id"]),
            details={"role": str(row["role"])},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(emp.id), account_id=row["account_id"],
            action="Принял приглашение в команду", object_kind="team_invite",
            object_name=f"membership:{int(row['id'])}", after=f"role:{row['role']}",
        )
        return {"status":"ok","membership_status":"ACTIVE","account_id":row["account_id"],"user_id":emp.id,"email":emp.email,"install_next":"После входа установите BORIS Messenger; Push включается только на этом устройстве явным действием пользователя."}
    finally:
        db.close()


@router.get("/memberships")
def memberships(account_id: str, user: User = Depends(require_messenger_legal_user)):
    db = SessionLocal()
    try:
        _require_team_manage(db, user, account_id)
        owner = db.execute(text(
            "SELECT u.id AS user_id,"
            " COALESCE(NULLIF(split_part(u.email,'@',1),''),u.email,'Владелец') AS name,"
            " u.email FROM accounts a JOIN users u ON u.id=a.owner_user_id "
            "WHERE a.account_id=:a LIMIT 1"
        ), {"a": account_id}).mappings().first()
        rows = db.execute(text(
            "SELECT id,user_id,name,phone,email,role,status,last_login_at,"
            "pwa_installed,pwa_standalone_confirmed,push_status,"
            "assigned_chats_count,assigned_tasks_count,invite_expires_at "
            "FROM team_memberships WHERE account_id=:a ORDER BY created_at DESC"
        ), {"a":account_id}).mappings().all()
        members = []
        if owner:
            members.append({
                "id": 0,
                "user_id": owner["user_id"],
                "name": owner["name"],
                "phone": None,
                "email": owner["email"],
                "role": "OWNER",
                "status": "ACTIVE",
                "last_login_at": None,
                "pwa_installed": None,
                "pwa_standalone_confirmed": None,
                "push_status": None,
                "assigned_chats_count": None,
                "assigned_tasks_count": None,
                "invite_expires_at": None,
                "is_account_owner": True,
            })
        members.extend(dict(r) for r in rows if not owner or r["user_id"] != owner["user_id"])
        return {"status":"ok","members":members}
    finally:
        db.close()




class UpdateMembershipBody(BaseModel):
    role: str


@router.patch("/memberships/{membership_id}")
def update_membership_role(membership_id: int, body: UpdateMembershipBody, user: User = Depends(require_messenger_legal_user)):
    role = (body.role or "").upper()
    if role not in TEAM_ROLES or role == "OWNER":
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT id,account_id,user_id,status FROM team_memberships WHERE id=:id FOR UPDATE"
        ), {"id": membership_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Участник не найден")
        _require_team_manage(db, user, row["account_id"])
        perms = dict(ROLE_PERMISSIONS[role])
        db.execute(text(
            "UPDATE team_memberships SET role=:r,permissions_override='{}'::jsonb WHERE id=:id"
        ), {"r": role, "id": membership_id})
        if row["user_id"] and row["status"] == "ACTIVE":
            db.execute(text(
                "UPDATE user_account_access SET role=:r,can_view=:v,can_reply=:rep,"
                "can_manage_ads=:ads,can_view_reports=:reports,can_manage_team=:team "
                "WHERE user_id=:u AND account_id=:a"
            ), {
                "r": role,
                "v": bool(perms["can_view"]),
                "rep": bool(perms["can_reply"]),
                "ads": bool(perms["can_manage_ads"]),
                "reports": bool(perms["can_view_reports"]),
                "team": bool(perms["can_manage_team"]),
                "u": row["user_id"],
                "a": row["account_id"],
            })
            from app.api.internal_messenger import emit_messenger_event, ensure_general_conversation_membership
            ensure_general_conversation_membership(db, row["account_id"])
            emit_messenger_event(db, row["account_id"], "team.changed", "membership", membership_id, {"action": "role_changed", "user_id": row["user_id"], "role": role})
        append_security_audit(
            db, action="team_role_change", user_id=int(user.id),
            account_id=row["account_id"], entity_type="membership", entity_id=int(membership_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            details={"target_user_id": int(row["user_id"]) if row["user_id"] else None, "role": role},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Изменил роль сотрудника", object_kind="team_role",
            object_name=f"membership:{int(membership_id)}", after=f"role:{role}",
        )
        return {
            "status": "ok",
            "membership_id": membership_id,
            "account_id": row["account_id"],
            "role": role,
        }
    finally:
        db.close()




@router.post("/memberships/{membership_id}/revoke-invite")
def revoke_pending_invite(membership_id: int, user: User = Depends(require_messenger_legal_user)):
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT id,account_id,status FROM team_memberships WHERE id=:id FOR UPDATE"
        ), {"id": membership_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Приглашение не найдено")
        _require_team_manage(db, user, row["account_id"])
        if row["status"] != "INVITED":
            raise HTTPException(status_code=409, detail="Можно отозвать только ожидающее приглашение")
        db.execute(text(
            "UPDATE team_memberships SET status='REVOKED',invite_token_hash=NULL,invite_expires_at=NULL "
            "WHERE id=:id"
        ), {"id": membership_id})
        from app.api.internal_messenger import emit_messenger_event
        emit_messenger_event(db, row["account_id"], "team.changed", "membership", membership_id, {"action": "invite_revoked"})
        append_security_audit(
            db, action="team_invite_revoke", user_id=int(user.id),
            account_id=row["account_id"], entity_type="membership", entity_id=int(membership_id),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=row["account_id"],
            action="Отозвал приглашение", object_kind="team_invite",
            object_name=f"membership:{int(membership_id)}", after="revoked",
        )
        return {"status": "ok", "membership_id": membership_id, "membership_status": "REVOKED"}
    finally:
        db.close()


class ReadinessBody(BaseModel):
    account_id: str
    pwa_installed: Optional[bool] = None
    pwa_standalone_confirmed: Optional[bool] = None
    push_status: Optional[str] = None


@router.post("/readiness")
def readiness(body: ReadinessBody, user: User = Depends(require_messenger_legal_user)):
    db = SessionLocal()
    try:
        if body.push_status not in (None, "ACTIVE", "INACTIVE"):
            raise HTTPException(status_code=400, detail="Некорректный push status")
        row = db.execute(text("SELECT id FROM team_memberships WHERE account_id=:a AND user_id=:u AND status='ACTIVE'"), {"a":body.account_id,"u":user.id}).first()
        if not row: raise HTTPException(status_code=403, detail="Активное membership не найдено")
        db.execute(text("UPDATE team_memberships SET pwa_installed=COALESCE(:p,pwa_installed),pwa_standalone_confirmed=COALESCE(:s,pwa_standalone_confirmed),push_status=COALESCE(:push,push_status),last_login_at=now() WHERE id=:id"), {"p":body.pwa_installed,"s":body.pwa_standalone_confirmed,"push":body.push_status,"id":row[0]})
        db.commit(); return {"status":"ok","push_status":body.push_status or "UNCHANGED","note":"Push status отражает только явное действие пользователя на его устройстве."}
    finally:
        db.close()
