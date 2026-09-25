# -*- coding: utf-8 -*-
"""Owner-facing audit trail for AI financial/safety guards.

This is observability only: it never authorizes a provider call and never
replaces api_usage/ai_budget. Guard telemetry must also never break a business
flow, so writes are best-effort and contain no prompts, API keys or payloads.
"""
from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.exception_observability import observe_suppressed

_TABLE_SQL = 'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'
_INDEX_SQL = 'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'

_MESSAGES = {
    "cache_hit": "Использован уже готовый результат — новый платный вызов не выполнялся",
    "PAID_IMAGE_DAILY_CAP": "Создание изображений остановлено: достигнут дневной защитный лимит",
    "PAID_IMAGE_BURST_CAP": "Создание изображений временно остановлено: слишком много платных вызовов за короткое время",
    "PAID_IMAGE_OUTCOME_UNCERTAIN": "Результат платного запроса неоднозначен — повторная покупка автоматически запрещена",
    "OPENAI_TEXT_DAILY_CAP": "Генерация текста остановлена: достигнут дневной защитный лимит",
    "OPENAI_TEXT_BURST_CAP": "Генерация текста временно остановлена: слишком много платных вызовов за короткое время",
    "OPENAI_TEXT_PROVIDER_FAILED": "Платный текстовый вызов завершился ошибкой; скрытый повтор провайдера не выполнялся",
    "CAMPAIGN_TEXT_BUDGET_NOT_ALLOWED": "Повтор этого платного текстового запроса запрещён защитой бюджета",
    "CAMPAIGN_TEXT_BUDGET_RESERVE_FAILED": "Платный текстовый запрос не запущен: бюджет не удалось безопасно зарезервировать",
    "AI_BUDGET_RESERVE_FAILED": "Платный AI-запрос не запущен: бюджет не удалось безопасно зарезервировать",
    "AI_BUDGET_NOT_ALLOWED": "Платный AI-запрос остановлен защитой бюджета до обращения к провайдеру",
    "VISION_DAILY_CAP": "Визуальная проверка остановлена: достигнут дневной защитный лимит",
    "VISION_BURST_CAP": "Визуальная проверка временно остановлена: слишком много проверок за короткое время",
    "VISION_OUTCOME_UNCERTAIN": "Результат платной визуальной проверки неоднозначен — повтор автоматически запрещён",
    "TTS_OUTCOME_UNCERTAIN": "Результат платной озвучки неоднозначен — повторная покупка автоматически запрещена",
    "AUDIO_OUTCOME_UNCERTAIN": "Результат платной аудио-операции неоднозначен — автоматический повтор запрещён",
    "CATEGORY_OUTCOME_UNCERTAIN": "Результат платного определения категории неоднозначен — повторная покупка автоматически запрещена",
    "CATEGORY_DAILY_CAP": "Определение категории остановлено: достигнут дневной защитный лимит",
    "CATEGORY_BURST_CAP": "Определение категории временно остановлено: слишком много платных вызовов за короткое время",
    "CAMPAIGN_AI_OUTCOME_UNCERTAIN": "Результат платного AI-запроса кампании неоднозначен — повторная покупка автоматически запрещена",
    "CAMPAIGN_AI_DAILY_CAP": "AI кампании остановлен: достигнут дневной защитный лимит",
    "CAMPAIGN_AI_BURST_CAP": "AI кампании временно остановлен: слишком много платных вызовов за короткое время",
    "MASS_AI_DAILY_CAP": "Массовый AI-запуск остановлен аварийным дневным лимитом",
    "provider_success": "Платный вызов выполнен и учтён",
}

_SAFE_DETAIL_KEYS = {
    "used", "cap", "window_min", "spent_rub", "daily_used", "daily_cap",
    "daily_spent_rub", "override", "request_id_exact", "cache_hit",
    "reason", "provider_status", "module", "calls_planned", "status",
}


def human_message(event_code: str, fallback: str | None = None) -> str:
    return _MESSAGES.get(str(event_code or ""), (fallback or "AI-защита изменила выполнение операции"))[:320]


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _SAFE_DETAIL_KEYS:
        if key not in details:
            continue
        value = details.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    return out


def _ensure(db) -> None:
    db.execute(text(_TABLE_SQL))
    db.execute(text(_INDEX_SQL))


def log_guard_event(
    *,
    account_id: str | None,
    source: str,
    operation: str | None,
    event_code: str,
    status: str = "blocked",
    user_message: str | None = None,
    intent_key: str | None = None,
    details: dict[str, Any] | None = None,
) -> bool:
    """Persist one owner-visible guard event. Never raises to the caller."""
    # Stdlib unittest/CI can exercise guard branches against the production-shaped
    # DB connection. Test telemetry must never pollute the owner's real audit log.
    # Production services do not set BORIS_TEST_MODE.
    if os.getenv("BORIS_TEST_MODE") == "1":
        return True
    # Synthetic QA accounts are never real client accounts. Some safety tests
    # intentionally hit production-shaped DB paths; dropping these events here
    # prevents owner-facing guard timelines from being polluted even if a test
    # forgets to set BORIS_TEST_MODE.
    if str(account_id or "").startswith("__qa_"):
        return True
    db = SessionLocal()
    try:
        _ensure(db)
        db.execute(text("""
            insert into ai_guard_event_log(
                account_id, source, operation, event_code, status,
                user_message, intent_key, details_json
            ) values (
                :account_id, :source, :operation, :event_code, :status,
                :user_message, :intent_key, cast(:details as jsonb)
            )
        """), {
            "account_id": str(account_id) if account_id else None,
            "source": str(source or "unknown")[:64],
            "operation": str(operation or "")[:160] or None,
            "event_code": str(event_code or "UNKNOWN_GUARD")[:96],
            "status": str(status or "blocked")[:32],
            "user_message": human_message(str(event_code or ""), user_message),
            "intent_key": str(intent_key or "")[:160] or None,
            "details": json.dumps(_safe_details(details), ensure_ascii=False),
        })
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=142)
        return False
    finally:
        db.close()


def recent_events(*, days: int = 7, account_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Owner/private view; no prompt/provider payloads are stored or returned."""
    db = SessionLocal()
    try:
        _ensure(db)
        params = {"days": max(1, min(int(days), 90)), "limit": max(1, min(int(limit), 500))}
        account_sql = ""
        if account_id:
            account_sql = " and account_id=:account_id"
            params["account_id"] = str(account_id)
        rows = db.execute(text("""
            select id, created_at, account_id, source, operation, event_code,
                   status, user_message, intent_key, details_json
            from ai_guard_event_log
            where created_at >= now() - make_interval(days => :days)
        """ + account_sql + " order by created_at desc, id desc limit :limit"), params).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.commit()
        db.close()


def event_summary(*, days: int = 7, account_id: str | None = None) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        _ensure(db)
        params = {"days": max(1, min(int(days), 90))}
        account_sql = ""
        if account_id:
            account_sql = " and account_id=:account_id"
            params["account_id"] = str(account_id)
        rows = db.execute(text("""
            select event_code, status, min(user_message) user_message, count(*) events,
                   max(created_at) last_at
            from ai_guard_event_log
            where created_at >= now() - make_interval(days => :days)
        """ + account_sql + " group by event_code,status order by events desc,last_at desc"), params).mappings().all()
        return [dict(x) for x in rows]
    finally:
        db.commit()
        db.close()
