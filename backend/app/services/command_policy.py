# -*- coding: utf-8 -*-
"""Policy — серверная проверка прав, риска и лимитов.

Права определяет СЕРВЕР, не модель. Реестр — источник истины по
operation/risk/permission/units; в boris_commands они пишутся снимком.

Порядок обязателен: billing owner -> тарифный флаг -> доступ к аккаунту ->
остаток пула, и только потом AI-вызов.
"""
from sqlalchemy import text

from app.services import command_limits as L
from app.services import command_registry as R
from app.services.platform_roles import is_platform_owner

PERMISSION_COLUMNS = {
    "can_view": "can_view",
    "can_reply": "can_reply",
    "can_manage_ads": "can_manage_ads",
    "can_view_reports": "can_view_reports",
    "can_manage_team": "can_manage_team",
}

OWNER = "owner"


def _uid(user):
    if user is None:
        return None
    return getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)


def _sys_role(user):
    if user is None:
        return None
    return getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)


def account_visible(db, user, account_id):
    """Владение имеет приоритет, затем связь user_account_access.can_view."""
    uid = _uid(user)
    if uid is None or not account_id:
        return False
    if _sys_role(user) == OWNER:
        return True
    row = db.execute(text(
        "select 1 from accounts where account_id=:a and owner_user_id=:u"),
        {"a": account_id, "u": uid}).fetchone()
    if row:
        return True
    row = db.execute(text(
        "select 1 from user_account_access"
        " where user_id=:u and account_id=:a and can_view = TRUE limit 1"),
        {"a": account_id, "u": uid}).fetchone()
    return bool(row)


def is_owner_of(db, user, account_id):
    uid = _uid(user)
    if uid is None or not account_id:
        return False
    if _sys_role(user) == OWNER:
        return True
    row = db.execute(text(
        "select 1 from accounts where account_id=:a and owner_user_id=:u"),
        {"a": account_id, "u": uid}).fetchone()
    if row:
        return True
    row = db.execute(text(
        "select 1 from user_account_access"
        " where user_id=:u and account_id=:a and role='owner' limit 1"),
        {"a": account_id, "u": uid}).fetchone()
    return bool(row)


def has_permission(db, user, account_id, permission):
    """permission: None | 'platform_owner' | 'owner' | флаг из PERMISSION_COLUMNS.

    platform_owner — внутренняя экономика BORIS. Разрешается ТОЛЬКО системной
    ролью платформы: ни accounts.owner_user_id, ни user_account_access здесь
    не читаются. Иначе владелец клиентского аккаунта видел бы себестоимость
    провайдеров и маржу платформы.
    """
    if not permission:
        return True
    if permission == R.PLATFORM_OWNER:
        return is_platform_owner(user)
    if permission == OWNER:
        return is_owner_of(db, user, account_id)
    col = PERMISSION_COLUMNS.get(permission)
    if not col:
        return False
    uid = _uid(user)
    if _sys_role(user) == OWNER:
        return True
    row = db.execute(text(
        "select 1 from accounts where account_id=:a and owner_user_id=:u"),
        {"a": account_id, "u": uid}).fetchone()
    if row:
        return True
    row = db.execute(text(
        "select 1 from user_account_access"
        " where user_id=:u and account_id=:a and " + col + " = TRUE limit 1"),
        {"a": account_id, "u": uid}).fetchone()
    return bool(row)


def precheck(db, user, account_id, source="text", min_units=1):
    """Возвращает (owner, error_code). owner = (kind, id) либо None."""
    if not account_id:
        return None, "forbidden_account"

    owner = L.resolve_billing_owner(db, account_id)
    if not owner:
        return None, "no_billing_owner"

    kind, oid = owner

    if not L.has_feature(db, kind, oid, R.FEATURE_BASE):
        return owner, "feature_disabled"

    if source == "voice" and not L.has_feature(db, kind, oid, R.FEATURE_VOICE):
        return owner, "voice_disabled"

    if not account_visible(db, user, account_id):
        return owner, "forbidden_account"

    st = L.pool_state(db, kind, oid)
    if st["expired"]:
        return owner, "pool_expired"
    if st["left"] < min_units:
        return owner, "pool_exhausted"

    return owner, None


def authorize_operation(db, user, account_id, operation_id):
    """Возвращает (snapshot, error_code). snapshot только из реестра."""
    op = R.get(operation_id)
    if not op:
        return R.snapshot(operation_id), "unknown_intent"

    snap = R.snapshot(operation_id)

    owner = L.resolve_billing_owner(db, account_id)
    if not owner:
        return snap, "no_billing_owner"
    kind, oid = owner

    if not L.has_feature(db, kind, oid, op.feature):
        return snap, "feature_disabled"

    if not has_permission(db, user, account_id, op.permission):
        return snap, "forbidden_operation"

    if op.units and not L.has_units(db, kind, oid, op.units):
        return snap, "pool_exhausted"

    return snap, None


def needs_confirmation(operation_id):
    return R.needs_confirmation(operation_id)
