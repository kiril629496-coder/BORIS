# -*- coding: utf-8 -*-
"""Единственное определение ПЛАТФОРМЕННОГО владельца BORIS.

Семантик владельца в системе две, и путать их дорого:

  * platform owner — владелец платформы BORIS. Видит внутреннюю экономику:
    себестоимость провайдеров, маржу, закупочные цены, unit economics.
  * owner аккаунта — клиент, которому принадлежит Avito-аккаунт. Управляет
    СВОИМ бизнесом и своими данными, но внутренней экономики BORIS не видит.

Здесь описан ТОЛЬКО первый. Ни accounts.owner_user_id, ни
user_account_access тут не читаются и читаться не должны: именно на этом
смешении в Command Center клиент получал доступ к себестоимости AI.

Модуль лежит в service layer намеренно — чтобы им могли пользоваться и
слой ручек (app/api/auth.require_owner), и сервисы (command_policy),
без зависимости сервисов от API.
"""

PLATFORM_OWNER_ROLE = "owner"
PLATFORM_OWNER_ID = 2
PLATFORM_OWNER_EMAIL = "ostapenko-kirill-86@yandex.ru"


def role_of(user):
    """Системная роль пользователя. Понимает и ORM-объект, и словарь."""
    if user is None:
        return None
    r = getattr(user, "role", None)
    if r is None and isinstance(user, dict):
        r = user.get("role")
    return r


def is_platform_owner(user) -> bool:
    """True только для владельца ПЛАТФОРМЫ.

    Владение клиентским Avito-аккаунтом этого права не даёт никогда.
    """
    return role_of(user) == PLATFORM_OWNER_ROLE


def is_private_platform_owner(user) -> bool:
    """Strict identity for BORIS-wide private/internal owner surfaces.

    `role=owner` is intentionally reusable by QA fixtures, so role alone must
    never authorize cross-client reports, provider costs, or platform economics.
    """
    try:
        uid = int(getattr(user, "id", -1) if not isinstance(user, dict) else user.get("id", -1))
    except Exception:
        uid = -1
    email = str((getattr(user, "email", "") if not isinstance(user, dict) else user.get("email", "")) or "").strip().lower()
    return uid == PLATFORM_OWNER_ID and email == PLATFORM_OWNER_EMAIL
