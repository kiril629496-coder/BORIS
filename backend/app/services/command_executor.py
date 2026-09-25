# -*- coding: utf-8 -*-
"""Исполнитель операций Command Center.

Этап 1: реестр исполнителей ПУСТ — любая операция возвращает
not_implemented. Так шина проверяется отдельно от бизнес-логики.

Этап 2 подключает боевые операции вызовом register(): исполнитель — это
обычная функция сервисного слоя модуля, а НЕ HTTP-запрос в собственный
endpoint. Endpoint и Command Center обязаны сходиться в один service layer.
"""
from app.services import command_registry as R

EXECUTORS = {}


def register(operation_id, fn):
    """Подключение исполнителя. Операция должна быть в реестре."""
    if not R.get(operation_id):
        raise ValueError("операция %s не зарегистрирована" % operation_id)
    EXECUTORS[operation_id] = fn
    return fn


PREVIEWERS = {}


def register_preview(operation_id, fn):
    """Предпросмотр yellow/red операции: что именно изменится."""
    PREVIEWERS[operation_id] = fn
    return fn


def preview(db, user, operation_id, entities=None, account_id=None,
            command_id=None, entity_type=None, entity_id=None):
    """Возвращает {summary, changes}. Ничего не меняет и не бросает."""
    fn = PREVIEWERS.get(operation_id)
    if fn is None:
        return {}
    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id,
           "entity_type": entity_type, "entity_id": entity_id}
    try:
        return fn(db, user, ctx) or {}
    except Exception as e:
        return {"error": str(e)[:200]}


def is_implemented(operation_id):
    return operation_id in EXECUTORS


def execute(db, user, operation_id, entities=None, account_id=None,
            command_id=None, entity_type=None, entity_id=None):
    """Возвращает словарь результата. Наружу не бросает."""
    op = R.get(operation_id)
    if not op:
        return {"status": "failed", "error_code": "unknown_intent",
                "answer": None, "changes": [], "actions": []}

    fn = EXECUTORS.get(operation_id)
    if fn is None:
        return {"status": "not_implemented", "error_code": "not_implemented",
                "answer": None, "changes": [], "actions": [],
                "detail": "операция %s пока не подключена" % operation_id}

    ctx = {"entities": entities or {}, "account_id": account_id,
           "command_id": command_id, "operation": operation_id,
           "entity_type": entity_type, "entity_id": entity_id}
    try:
        res = fn(db, user, ctx) or {}
    except Exception as e:
        return {"status": "failed", "error_code": "executor_failed",
                "answer": None, "changes": [], "actions": [],
                "detail": str(e)[:300]}

    res.setdefault("status", "ok")
    res.setdefault("answer", None)
    res.setdefault("changes", [])
    res.setdefault("actions", [])
    return res
