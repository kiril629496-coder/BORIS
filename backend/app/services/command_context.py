# -*- coding: utf-8 -*-
"""Контекст текущей команды.

Зачем ContextVar, а не аргументы: gigachat_pool.chat_with_fallback зовёт
log_usage ПОЗИЦИОННО, протащить новые параметры через всех вызывающих нельзя.
Ставим контекст в начале запроса — usage, audit, логи и обработчик ошибок
получают command_id, владельца и аккаунт автоматически.
"""
import contextvars
import uuid
from app.services.exception_observability import observe_suppressed

_EMPTY = {}

current_command = contextvars.ContextVar("current_command", default=None)


def new_command_id() -> str:
    return str(uuid.uuid4())


def set_command(command_id=None, user_id=None, account_id=None,
                billing_owner_kind=None, billing_owner_id=None,
                request_id=None):
    """Ставит контекст. Возвращает token для reset()."""
    data = {
        "command_id": command_id,
        "user_id": user_id,
        "account_id": account_id,
        "billing_owner_kind": billing_owner_kind,
        "billing_owner_id": billing_owner_id,
        "request_id": request_id,
    }
    return current_command.set(data)


def get_command() -> dict:
    """Никогда не бросает: вне команды возвращает пустой словарь."""
    try:
        return current_command.get() or _EMPTY
    except Exception:
        return _EMPTY


def field(name):
    return get_command().get(name)


def reset(token):
    try:
        current_command.reset(token)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=51)
