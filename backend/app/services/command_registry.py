# -*- coding: utf-8 -*-
"""Operation Registry — единственный источник истины по операциям BORIS.

Golden Rule: любая новая функция BORIS обязана зарегистрировать здесь
Operation, Permission, Risk, Units, Feature, Executor. После регистрации
она автоматически видна Command Center, Telegram, REST и Scheduler.

В boris_commands operation/risk/permission/units пишутся СНИМКОМ на момент
выполнения — база хранит историю, истина живёт здесь.

Этап 1: executor у всех прикладных операций = None (заглушка возвращает
not_implemented). Боевые интенты подключаются этапом 2.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

FEATURE_BASE = "command_center_enabled"
FEATURE_VOICE = "command_center_voice_enabled"
FEATURE_TTS = "command_center_tts_enabled"
FEATURE_PDF = "command_center_pdf_enabled"
FEATURE_MOBILE = "command_center_mobile_enabled"

ALL_FEATURES = (FEATURE_BASE, FEATURE_VOICE, FEATURE_TTS,
                FEATURE_PDF, FEATURE_MOBILE)

# Право на ВНУТРЕННЮЮ экономику BORIS: себестоимость провайдеров, маржа,
# закупочные цены, unit economics. Разрешается только системной ролью
# платформы. Владение клиентским Avito-аккаунтом его НЕ даёт.
PLATFORM_OWNER = "platform_owner"


@dataclass(frozen=True)
class Operation:
    id: str
    risk: str
    permission: Optional[str]
    units: int
    feature: str
    executor: Optional[str] = None
    intents: Tuple[str, ...] = field(default_factory=tuple)
    title: str = ""


def _op(*a, **kw) -> Operation:
    return Operation(*a, **kw)


REGISTRY: Dict[str, Operation] = {op.id: op for op in (
    _op("command.parse", "green", None, 0, FEATURE_BASE,
        title="Разбор команды"),
    _op("chat.smalltalk", "green", "can_view", 0, FEATURE_BASE,
        intents=("chit_chat",), title="Приветствие или общий вопрос"),

    _op("ui.navigate", "green", "can_view", 0, FEATURE_BASE,
        intents=("navigation",), title="Переход в раздел"),
    _op("crm.leads_search", "green", "can_view", 1, FEATURE_BASE,
        intents=("client_search",), title="Поиск клиента"),
    _op("analytics.daily", "green", "can_view", 1, FEATURE_BASE,
        intents=("analytics_query",), title="Аналитика за день"),
    _op("analytics.week", "green", "can_view", 1, FEATURE_BASE,
        intents=("analytics_query",), title="Аналитика за неделю"),
    _op("analytics.accounts", "green", "can_view", 3, FEATURE_BASE,
        intents=("analytics_query",), title="Сводка по аккаунтам"),
    _op("analytics.manager", "green", "can_view_reports", 2, FEATURE_BASE,
        intents=("analytics_query",), title="Работа менеджеров"),
    _op("analytics.revenue", "green", PLATFORM_OWNER, 2, FEATURE_BASE,
        intents=("analytics_query",), title="Деньги и себестоимость"),

    _op("crm.lead_note_add", "yellow", "can_reply", 1, FEATURE_BASE,
        intents=("crm_add_note",), title="Заметка по клиенту"),
    _op("crm.task_create", "yellow", "can_reply", 1, FEATURE_BASE,
        intents=("task_create", "reminder_create"), title="Задача или напоминание"),
    _op("crm.lead_update", "yellow", "can_reply", 1, FEATURE_BASE,
        intents=("crm_create_or_update",), title="Карточка клиента"),
    _op("knowledge.fact_add", "yellow", "owner", 1, FEATURE_BASE,
        intents=("knowledge_add",), title="Факт в базу знаний"),
)}


def get(operation_id: str) -> Optional[Operation]:
    return REGISTRY.get(operation_id)


def for_intent(intent: str):
    return [o for o in REGISTRY.values() if intent in o.intents]


_ANALYTICS_BY_SCOPE = {
    # revenue/money сняты 11.08: вопрос клиента «сколько денег» не должен
    # вести на operational finance платформы (analytics.revenue отдаёт наш
    # расход на модели). Вернуть, когда появится клиентская экономика.
    "manager": "analytics.manager",
    "managers": "analytics.manager",
    "accounts": "analytics.accounts",
    "all_accounts": "analytics.accounts",
}

_ANALYTICS_BY_PERIOD = {
    "today": "analytics.daily",
    "day": "analytics.daily",
    "yesterday": "analytics.daily",
    "week": "analytics.week",
    "7d": "analytics.week",
}

_SIMPLE = {
    "navigation": "ui.navigate",
    "chit_chat": "chat.smalltalk",
    "client_search": "crm.leads_search",
    "crm_add_note": "crm.lead_note_add",
    "task_create": "crm.task_create",
    "reminder_create": "crm.task_create",
    "crm_create_or_update": "crm.lead_update",
    "knowledge_add": "knowledge.fact_add",
}


def resolve_operation(intent, entities=None) -> Optional[str]:
    """intent + entities -> id операции. Без AI, без обращений к БД."""
    if not intent:
        return None
    ent = entities or {}
    if intent == "analytics_query":
        scope = str(ent.get("scope") or "").lower()
        if scope in _ANALYTICS_BY_SCOPE:
            return _ANALYTICS_BY_SCOPE[scope]
        period = str(ent.get("period") or "").lower()
        if period in _ANALYTICS_BY_PERIOD:
            return _ANALYTICS_BY_PERIOD[period]
        return "analytics.daily"
    return _SIMPLE.get(intent)


def snapshot(operation_id: str) -> dict:
    """Поля, которые пишутся в boris_commands как снимок реестра."""
    o = REGISTRY.get(operation_id)
    if not o:
        return {"operation": operation_id, "risk_level": None,
                "permission": None, "units": 0}
    return {"operation": o.id, "risk_level": o.risk,
            "permission": o.permission, "units": o.units}


def needs_confirmation(operation_id: str) -> bool:
    o = REGISTRY.get(operation_id)
    return bool(o and o.risk in ("yellow", "red"))
