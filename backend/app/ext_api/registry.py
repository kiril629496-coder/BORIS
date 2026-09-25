# -*- coding: utf-8 -*-
"""Реестр операций внешнего шлюза.

Классы операций (пункт 9 ТЗ):
    READ_ONLY        — ничего не меняет
    SAFE_MUTATION    — меняет только внутренние данные BORIS (кампании, черновики)
    PAID_ACTION      — тратит деньги (генерация медиа, ставки)
    EXTERNAL_PUBLISH — уходит наружу (Avito, Telegram, CRM)

Правило: available=true только если для операции резолвится штатный
исполнитель ИЛИ операция реализована шлюзом поверх уже существующих таблиц
BORIS без дублирования бизнес-логики (native=True).
"""

READ_ONLY = "READ_ONLY"
SAFE_MUTATION = "SAFE_MUTATION"
PAID_ACTION = "PAID_ACTION"
EXTERNAL_PUBLISH = "EXTERNAL_PUBLISH"

# В первой production-версии шлюза платное и внешнее выключено жёстко (п. 18).
PHASE1_BLOCKED_CLASSES = {PAID_ACTION, EXTERNAL_PUBLISH}

OPERATIONS = {
    # ---------------------------------------------------------- read-only
    "health_check": dict(
        cls=READ_ONLY, scope="read", confirm=False, mode="sync", native=True,
        summary="Состояние production-зависимостей", params={}),
    "capabilities": dict(
        cls=READ_ONLY, scope="read", confirm=False, mode="sync", native=True,
        summary="Какие операции реально доступны", params={}),
    "client_status": dict(
        cls=READ_ONLY, scope="read", confirm=False, mode="sync", native=True,
        summary="Сводка по аккаунту: кампании, элементы, задачи",
        params={"account_id": {"required": True, "type": str}}),
    "kpi_check": dict(
        cls=READ_ONLY, scope="read", confirm=False, mode="sync", native=True,
        executors=["app.services.kpi:check_account", "app.kpi_autopilot_runner:check_account"],
        summary="Срез KPI по аккаунту из существующих данных",
        params={"account_id": {"required": True, "type": str}}),
    "verify": dict(
        cls=READ_ONLY, scope="read", confirm=False, mode="sync", native=True,
        summary="Повторный postflight ранее выполненной задачи",
        params={"job_id": {"required": True, "type": int}}),

    # ------------------------------------------------------ safe mutation
    "campaign_create": dict(
        cls=SAFE_MUTATION, scope="write", confirm=False, mode="sync", native=True,
        executors=["app.services.campaign_service:new_campaign"],
        summary="Создать кампанию (идемпотентно по имени внутри аккаунта)",
        params={"account_id": {"required": True, "type": str},
                "name": {"required": True, "type": str}}),
    "ads_create": dict(
        cls=SAFE_MUTATION, scope="write", confirm=False, mode="async", native=True,
        executors=["app.services.campaign_service:add_item"],
        summary="Залить объявления в кампанию как черновики",
        params={"account_id": {"required": True, "type": str},
                "items": {"required": True, "type": list}}),
    "launch_client": dict(
        cls=SAFE_MUTATION, scope="write", confirm=True, mode="async", native=True,
        executors=["app.api.plan_items:execute_plan_item"],
        summary="Подготовительный запуск клиента: кампания + объявления-черновики. "
                "Публикация, медиа и баннеры в этой версии не выполняются.",
        params={"account_id": {"required": True, "type": str},
                "items": {"required": False, "type": list},
                "ads": {"required": False, "type": int},
                "publish": {"required": False, "type": bool}}),

    # ------------------------------------------------------- paid actions
    "media_generate": dict(
        cls=PAID_ACTION, scope="tasks", confirm=True, mode="async", native=False,
        executors=["app.services.media:generate_images",
                   "app.services.images:generate_for_item",
                   "app.tasks:generate_images"],
        # найдено 19.08 грепом по боевой сборке: отдельного генератора изображений
        # (не баннеров) в app/ нет. Пока NOT_IMPLEMENTED — это честный статус.
        summary="Генерация изображений под объявления",
        params={"account_id": {"required": True, "type": str}}),
    "banner_generate": dict(
        cls=PAID_ACTION, scope="tasks", confirm=True, mode="async", native=False,
        # ⬇ подтверждено грепом 19.08: app/api/avito.py:2959
        # Single production banner executor. Avito advertising styles are routed
        # inside generate_banner to the safe visual-only + local-text compositor.
        executors=["app.api.avito:generate_banner"],
        summary="Генерация баннеров",
        params={"account_id": {"required": True, "type": str}}),
    "optimize": dict(
        cls=PAID_ACTION, scope="tasks", confirm=True, mode="async", native=False,
        # ⬇ штатный оркестратор BORIS, подтверждён грепом: app/api/plan_items.py:463
        executors=["app.api.plan_items:execute_plan_item",
                   "app.services.cpx_executor:apply", "app.services.cpx:apply_bids"],
        summary="Изменение ставок через штатный CPX executor",
        params={"account_id": {"required": True, "type": str}}),

    # --------------------------------------------------- external publish
    "feed_build": dict(
        cls=EXTERNAL_PUBLISH, scope="publish", confirm=True, mode="async", native=False,
        # ⬇ подтверждено грепом 19.08: app/api/avito.py:2244
        executors=["app.api.avito:generate_feed", "app.services.feed:generate_feed"],
        summary="Сборка фида для Avito",
        params={"account_id": {"required": True, "type": str}}),
    "publish": dict(
        cls=EXTERNAL_PUBLISH, scope="publish", confirm=True, mode="async", native=False,
        executors=["app.api.avito:publish_listings", "app.services.publish:publish_listings"],
        summary="Публикация объявлений в Avito",
        params={"account_id": {"required": True, "type": str}}),
    "telegram_report": dict(
        cls=EXTERNAL_PUBLISH, scope="publish", confirm=True, mode="async", native=False,
        executors=["app.services.telegram:send_report", "app.notifications:send_telegram"],
        summary="Отправка отчёта в Telegram",
        params={"account_id": {"required": True, "type": str}}),
}


def get(name):
    return OPERATIONS.get(name)


def names():
    return sorted(OPERATIONS)
