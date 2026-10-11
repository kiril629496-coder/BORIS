"""Human-facing Avito preflight projection from the canonical delivery snapshot."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import text


def latest_wallet_snapshot(db, account_id: str) -> dict:
    """Last confirmed Avito wallet fact; never contacts Avito."""
    row = db.execute(text(
        "select key,value from storage where account_id=:a and key like 'daily_stats:%' "
        "order by key desc,id desc limit 1"
    ), {"a": str(account_id)}).fetchone()
    data = {}
    if row and row[1]:
        try:
            parsed = json.loads(row[1])
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}

    balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
    completeness = data.get("completeness") if isinstance(data.get("completeness"), dict) else {}
    collected = None
    raw_collected = data.get("collected_at")
    if raw_collected:
        try:
            collected = datetime.fromisoformat(str(raw_collected).replace("Z", "+00:00"))
            if collected.tzinfo is None:
                collected = collected.replace(tzinfo=timezone.utc)
            collected = collected.astimezone(timezone.utc)
        except (TypeError, ValueError):
            collected = None
    age_sec = max(0.0, (datetime.now(timezone.utc) - collected).total_seconds()) if collected else None

    def number(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    real = number(balance.get("real"))
    bonus = number(balance.get("bonus"))
    funding_row = db.execute(text(
        "select value from storage where account_id=:a and key='guardian_balance_funding_health' "
        "order by id desc limit 1"
    ), {"a": str(account_id)}).fetchone()
    funding = {}
    if funding_row and funding_row[0]:
        try:
            parsed = json.loads(funding_row[0])
            if isinstance(parsed, dict):
                funding = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            funding = {}

    pause_required = bool(funding.get("work_pause_required"))
    if real is None:
        state = "UNKNOWN"
    elif pause_required:
        state = "BLOCKED_LOW_BALANCE"
    elif real <= 0:
        state = "LOW_BALANCE"
    else:
        state = "BALANCE_KNOWN"
    return {
        "source": row[0] if row else None,
        "collected_at": collected.isoformat() if collected else None,
        "age_sec": round(age_sec, 3) if age_sec is not None else None,
        "fresh_24h": bool(age_sec is not None and age_sec <= 86400),
        "stats_date": data.get("stats_date"),
        "inventory_complete": bool(completeness.get("inventory_complete")),
        "complete": bool(completeness.get("complete")),
        "read_ok": real is not None,
        "real_rub": real,
        "bonus_rub": bonus,
        "funding_state": state,
        "guardian": {
            "present": bool(funding),
            "reason": funding.get("reason"),
            "checked_at": funding.get("checked_at"),
            "work_pause_required": pause_required,
            "owner_action_required": bool(funding.get("owner_action_required")),
            "resume_condition": funding.get("resume_condition"),
        },
    }


def _row(code, label, ok, message, *, blocking=True, status=None, details=None):
    return {
        "code": code, "label": label,
        "status": status or ("PASS" if ok else "BLOCKED"),
        "message": message, "blocking": bool(blocking),
        "details": details or {},
    }


def build_avito_preflight(delivery, *, marketing_blocked=False,
                          marketing_reason="", wallet=None, identity=None):
    d=dict(delivery or {}); wallet=dict(wallet or {}); identity=dict(identity or {})
    items=max(0,int(d.get("items") or 0)); approved=max(0,int(d.get("approved") or 0))
    validation=str(d.get("validation_state") or "NOT_CHECKED").upper()
    checks=[
        _row("marketing_actions","Действия BORIS",not marketing_blocked,
             marketing_reason if marketing_blocked else "Действия для аккаунта разрешены."),
        _row("account_scope","Аккаунт кампании",bool(d.get("account_bound")),
             "Аккаунт кампании подтверждён." if d.get("account_bound") else "В кампании найден другой аккаунт."),
        _row("publication_state","Повторная отправка",not bool(d.get("publication_locked")),
             "Кампания уже передана Avito." if d.get("publication_locked") else "Повторная отправка не заблокирована."),
        _row("approval","Согласование",items>0 and approved==items,f"Согласовано {approved} из {items}."),
        _row("required_data","Обязательные данные",bool(d.get("intake_ready")) and not d.get("projection_error"),
             ((d.get("projection_error") or {}).get("message") or "Обязательные данные заполнены.")),
        _row("semantic_media","Смысловая проверка фото",bool(d.get("semantic_ready")),
             "Фото соответствуют объявлениям." if d.get("semantic_ready") else f"Заблокировано {int(d.get('semantic_blocked') or 0)} объявлений."),
        _row("image_uniqueness","Уникальность изображений",bool(d.get("image_unique")),
             "Дубликаты изображений не найдены." if d.get("image_unique") else "Найдены одинаковые или слишком похожие изображения."),
        _row("content_uniqueness","Уникальность текста",bool(d.get("content_unique")),
             "Конфликтов текстов не найдено." if d.get("content_unique") else "Найдены слишком похожие тексты."),
        _row("xml_validation","Проверка Avito XML",validation=="READY",
             "Актуальный XML проверен." if validation=="READY" else "Нужна успешная актуальная проверка Avito.",
             status=("CHECKING" if validation in {"CHECKING","PROCESSING"} else None),
             details={"validation_state":validation,"last_validation":d.get("last_validation")}),
        _row("avito_connection","Подключение Avito",bool(d.get("avito_connected")),
             "Avito подключён." if d.get("avito_connected") else "Нужно подключить Avito."),
        _row("autoload_access","Автозагрузка Avito",bool(d.get("autoload_ready")),
             "Autoload доступен." if d.get("autoload_ready") else str(d.get("autoload_message") or "Autoload недоступен."),
             details={"http_status":d.get("autoload_http_status"),"feed_configured":d.get("feed_configured")}),
    ]
    duplicate_ids=list(identity.get("duplicate_feed_identities") or [])
    checks.append(_row("identity_reconcile","Идентификаторы объявлений",not duplicate_ids,
                       "Идентификаторы уникальны." if not duplicate_ids else "Найдены повторяющиеся feed identity; нужна exactly-once сверка.",
                       blocking=False,status=("WARN" if duplicate_ids else "PASS"),
                       details={"duplicate_feed_identities":duplicate_ids[:20],"existing_avito_items":int(identity.get("existing_avito_items") or 0)}))
    read_ok=bool(wallet.get("read_ok")); funding=str(wallet.get("funding_state") or "UNKNOWN")
    wallet_ok=read_ok and funding=="BALANCE_KNOWN"
    wallet_msg=(f"Последний подтверждённый баланс: {float(wallet.get('real_rub') or 0):.2f} ₽."
                if read_ok else "Подтверждённый баланс сейчас недоступен; BORIS не придумывает значение.")
    if funding=="BLOCKED_LOW_BALANCE": wallet_msg="Guardian сообщает о низком балансе Avito; потребуется пополнение."
    checks.append(_row("wallet","Баланс / аванс Avito",wallet_ok,wallet_msg,blocking=False,
                       status=("PASS" if wallet_ok else "WARN"),
                       details={"real_rub":wallet.get("real_rub"),"bonus_rub":wallet.get("bonus_rub"),
                                "fresh_24h":wallet.get("fresh_24h"),"funding_state":funding}))
    can_publish=bool(d.get("can_send")) and not marketing_blocked
    if d.get("publication_locked"): state,action="ALREADY_SUBMITTED","Ждать итог Avito"
    elif marketing_blocked: state,action="ACTIONS_DISABLED","Включить действия BORIS"
    elif d.get("projection_error") or not d.get("intake_ready"): state,action="NEEDS_DATA","Заполнить обязательные данные"
    elif not (items>0 and approved==items): state,action="NEEDS_APPROVAL","Согласовать объявления"
    elif not d.get("semantic_ready") or not d.get("image_unique") or not d.get("content_unique"): state,action="CONTENT_BLOCKED","Исправить объявления"
    elif validation!="READY": state,action="NEEDS_VALIDATION","Проверить фид Avito"
    elif not d.get("avito_connected") or not d.get("autoload_ready"): state,action="READY_AVITO_BLOCKED","Настроить доступ Avito"
    elif can_publish: state,action="PASS","Разместить в Avito"
    else: state,action="BLOCKED","Посмотреть блокеры"
    return {
        "status":"ok","preflight_contract":1,"campaign_id":d.get("campaign_id"),"account_id":d.get("account_id"),
        "state":state,"can_publish":can_publish,"primary_action":action,
        "blocked_reason":str(d.get("blocked_reason") or marketing_reason or ""),
        "checks":checks,"blockers":[x for x in checks if x["blocking"] and x["status"]!="PASS"],
        "summary":{"items":items,"approved":approved,"photos":int(d.get("photos") or 0),
                   "validation_state":validation,"avito_connected":bool(d.get("avito_connected")),
                   "autoload_ready":bool(d.get("autoload_ready")),"publication_locked":bool(d.get("publication_locked"))},
    }
