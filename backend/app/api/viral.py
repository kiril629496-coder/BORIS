from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.user import User
from app.models.account import Account
from app.api.auth import get_current_user
from app.api.inbox_slots import _connected_account_ids
from app.api.inbox_daily import account_stats
from sqlalchemy import text as sql_text

router = APIRouter(prefix="/api/viral", tags=["viral-growth"])

_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: Dict[str, list[float]] = {}
_MARKET_JOB_LOCK = threading.Lock()
_MARKET_JOBS_RUNNING: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _rate_limit(request: Request, scope: str, default_limit: int) -> None:
    try:
        limit = int(os.getenv(f"BORIS_VIRAL_{scope.upper()}_PER_HOUR", str(default_limit)))
    except Exception:
        limit = default_limit
    host = (request.client.host if request.client else "unknown")[:120]
    key = f"{scope}:{host}"
    cutoff = time.time() - 3600
    with _RATE_LOCK:
        bucket = [t for t in _RATE_BUCKETS.get(key, []) if t >= cutoff]
        if len(bucket) >= max(1, limit):
            raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте немного позже.")
        bucket.append(time.time())
        _RATE_BUCKETS[key] = bucket


def _persistent_rate_limit(identity: str, scope: str, default_limit: int) -> None:
    identity = str(identity or "").strip()
    if not identity:
        raise HTTPException(status_code=400, detail="Не определён аккаунт")
    try:
        limit = int(os.getenv(f"BORIS_VIRAL_{scope.upper()}_ACCOUNT_PER_HOUR", str(default_limit)))
    except Exception:
        limit = default_limit
    from app.services import verification as _vf
    db = SessionLocal()
    try:
        kind = ("viral_" + scope)[:80]
        rate_key = ("viral:" + identity)[:240]
        used = _vf.count_events(db, kind, rate_key=rate_key, minutes=60)
        if used >= max(1, limit):
            raise HTTPException(status_code=429, detail="Лимит операций для аккаунта исчерпан. Попробуйте позже.")
        _vf.log_event(db, kind, rate_key)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
    finally:
        db.close()


class CalculatorInput(BaseModel):
    leads_per_month: int = Field(ge=1, le=1_000_000)
    average_check_rub: float = Field(gt=0, le=1_000_000_000)
    conversion_percent: float = Field(ge=0, le=100)
    managers_count: int = Field(default=1, ge=1, le=100_000)
    avg_response_minutes: float = Field(default=15, ge=0, le=100_000)
    unanswered_leads: int = Field(default=0, ge=0, le=1_000_000)
    followup_percent: Optional[float] = Field(default=None, ge=0, le=100)
    kpi_completion_percent: Optional[float] = Field(default=None, ge=0, le=300)
    reactivation_percent: Optional[float] = Field(default=None, ge=0, le=100)
    ad_contact_rate_percent: Optional[float] = Field(default=None, ge=0, le=100)
    niche: Optional[str] = Field(default=None, max_length=120)
    region: Optional[str] = Field(default=None, max_length=120)




class ConnectedAuditInput(BaseModel):
    account_id: str = Field(min_length=1, max_length=160)
    average_check_rub: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    sales_conversion_percent: Optional[float] = Field(default=None, ge=0, le=100)
    period_days: int = Field(default=30, ge=1, le=90)

class MarketAuditInput(BaseModel):
    query: str = Field(min_length=2, max_length=180)
    region: str = Field(min_length=2, max_length=120)
    source_url: Optional[str] = Field(default=None, max_length=1000)


class SourceHintInput(BaseModel):
    source_url: str = Field(min_length=8, max_length=1000)


class AuditQuestionInput(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class ShareInput(BaseModel):
    audit_id: str = Field(min_length=8, max_length=80)


class RevokeInput(BaseModel):
    revoke_secret: str = Field(min_length=24, max_length=200)


class EventInput(BaseModel):
    event: str = Field(min_length=2, max_length=80)
    session_id: Optional[str] = Field(default=None, max_length=120)
    referral_code: Optional[str] = Field(default=None, max_length=120)
    source: Optional[str] = Field(default=None, max_length=120)
    utm_source: Optional[str] = Field(default=None, max_length=180)
    utm_medium: Optional[str] = Field(default=None, max_length=180)
    utm_campaign: Optional[str] = Field(default=None, max_length=180)
    utm_content: Optional[str] = Field(default=None, max_length=180)
    utm_term: Optional[str] = Field(default=None, max_length=180)
    landing_path: Optional[str] = Field(default=None, max_length=240)
    first_seen_at: Optional[str] = Field(default=None, max_length=80)
    qa: bool = False


class GatewayStateInput(BaseModel):
    stage: str = Field(min_length=2, max_length=80)
    contact_name: Optional[str] = Field(default=None, max_length=120)
    niche: Optional[str] = Field(default=None, max_length=120)
    region: Optional[str] = Field(default=None, max_length=120)
    marketing_opt_in: Optional[bool] = None
    session_id: Optional[str] = Field(default=None, max_length=120)
    utm_source: Optional[str] = Field(default=None, max_length=180)
    utm_medium: Optional[str] = Field(default=None, max_length=180)
    utm_campaign: Optional[str] = Field(default=None, max_length=180)
    utm_content: Optional[str] = Field(default=None, max_length=180)
    utm_term: Optional[str] = Field(default=None, max_length=180)
    landing_path: Optional[str] = Field(default=None, max_length=240)
    first_seen_at: Optional[str] = Field(default=None, max_length=80)
    qa: Optional[bool] = None
    referral_code: Optional[str] = Field(default=None, max_length=120)


class GatewayCompleteInput(BaseModel):
    audit_id: str = Field(min_length=8, max_length=80)


class SalesContactInput(BaseModel):
    whatsapp_url: Optional[str] = Field(default="", max_length=500)
    max_url: Optional[str] = Field(default="", max_length=500)
    phone: Optional[str] = Field(default="", max_length=80)
    message_template: Optional[str] = Field(default="", max_length=1000)
    contact_name: Optional[str] = Field(default="Кирилл О.", max_length=120)
    contact_title: Optional[str] = Field(default="Разработчик BORIS", max_length=180)
    trial_enabled: bool = True
    trial_days: int = Field(default=4, ge=1, le=30)
    trial_title: Optional[str] = Field(default="", max_length=240)
    trial_description: Optional[str] = Field(default="", max_length=800)


class EmailSettingsInput(BaseModel):
    marketing_opt_in: bool



def _crm_quality_snapshot(db, account_id: str, period_days: int) -> dict[str, Any]:
    try:
        row = db.execute(sql_text("""
            SELECT
              count(*)::int AS deals,
              count(*) FILTER (
                WHERE lower(coalesce(status,'')) IN ('won','success','closed_won','successful')
              )::int AS won,
              count(*) FILTER (
                WHERE lower(coalesce(status,'')) IN ('lost','closed_lost','failed','failure')
              )::int AS lost,
              count(*) FILTER (WHERE coalesce(amount_kopeks,0) > 0)::int AS amount_filled,
              coalesce(sum(amount_kopeks) FILTER (
                WHERE lower(coalesce(status,'')) IN ('won','success','closed_won','successful')
              ),0)::bigint AS won_amount_kopeks,
              max(updated_at) AS last_updated_at
            FROM boris_crm_deals
            WHERE avito_account_id=:a
              AND created_at >= now() - make_interval(days => :days)
        """), {"a": account_id, "days": int(period_days)}).mappings().first() or {}
    except Exception:
        return {
            "status": "unavailable",
            "quality": "low",
            "deals": 0, "won": 0, "lost": 0,
            "avg_check_rub": None,
            "deal_to_won_percent": None,
            "won_revenue_rub": None,
            "money_ready": False,
            "message": "CRM временно недоступна для денежного анализа",
        }

    deals = int(row.get("deals") or 0)
    won = int(row.get("won") or 0)
    lost = int(row.get("lost") or 0)
    amount_filled = int(row.get("amount_filled") or 0)
    revenue = float(row.get("won_amount_kopeks") or 0) / 100.0
    avg_check = revenue / won if won > 0 and revenue > 0 else None
    conversion = won / deals * 100.0 if deals > 0 and won > 0 else None
    money_ready = bool(deals >= 5 and won > 0 and avg_check and conversion)
    if money_ready and amount_filled >= won:
        quality = "high"
    elif deals > 0:
        quality = "medium" if won > 0 else "low"
    else:
        quality = "low"
    if money_ready:
        message = "CRM достаточно заполнена для денежного анализа"
    elif deals > 0 and won == 0:
        message = "CRM содержит сделки, но нет подтверждённых won-сделок для среднего чека и конверсии"
    elif deals > 0:
        message = "CRM пока недостаточно заполнена для надёжного денежного анализа"
    else:
        message = "В CRM нет сделок за выбранный период"

    return {
        "status": "ok",
        "quality": quality,
        "deals": deals,
        "won": won,
        "lost": lost,
        "amount_filled": amount_filled,
        "won_revenue_rub": round(revenue, 2),
        "avg_check_rub": round(avg_check, 2) if avg_check else None,
        "deal_to_won_percent": round(conversion, 2) if conversion is not None else None,
        "last_updated_at": row.get("last_updated_at").isoformat() if row.get("last_updated_at") else None,
        "money_ready": money_ready,
        "message": message,
    }


def _response_score(minutes: float) -> int:
    if minutes <= 5: return 20
    if minutes <= 15: return 17
    if minutes <= 30: return 13
    if minutes <= 60: return 8
    if minutes <= 120: return 4
    return 1


def _unanswered_score(leads: int, unanswered: int) -> int:
    share = unanswered / max(leads, 1)
    if share <= .01: return 20
    if share <= .03: return 17
    if share <= .05: return 14
    if share <= .10: return 9
    if share <= .20: return 5
    return 1


def _conversion_score(conv: float) -> int:
    if conv >= 20: return 15
    if conv >= 12: return 13
    if conv >= 8: return 11
    if conv >= 5: return 8
    if conv >= 2: return 5
    return 2


def _pct_score(value: Optional[float], maximum: int, neutral: int) -> int:
    if value is None:
        return neutral
    return int(round(_clamp(value, 0, 100) / 100 * maximum))


def _score(payload: CalculatorInput) -> Dict[str, Any]:
    response = _response_score(payload.avg_response_minutes)
    unanswered = _unanswered_score(payload.leads_per_month, min(payload.unanswered_leads, payload.leads_per_month))
    conversion = _conversion_score(payload.conversion_percent)
    handling = int(round((response / 20 * 7) + (unanswered / 20 * 8)))
    kpi = _pct_score(payload.kpi_completion_percent, 10, 6)
    followup_raw = payload.followup_percent
    if followup_raw is None and payload.reactivation_percent is not None:
        followup_raw = payload.reactivation_percent
    followup = _pct_score(followup_raw, 10, 5)
    ads = _pct_score(payload.ad_contact_rate_percent, 10, 7)
    total = int(_clamp(response + unanswered + conversion + handling + kpi + followup + ads, 0, 100))
    known_optional = sum(v is not None for v in [
        payload.followup_percent, payload.kpi_completion_percent,
        payload.reactivation_percent, payload.ad_contact_rate_percent
    ])
    confidence = "medium" if known_optional >= 2 else "low"
    return {
        "total": total,
        "label": "Сильный" if total >= 80 else "Хороший" if total >= 65 else "Есть потенциал" if total >= 45 else "Нужны изменения",
        "mode": "manual",
        "mode_label": "Предварительная оценка",
        "confidence": confidence,
        "breakdown": {
            "response_speed": {"score": response, "max": 20, "label": "Скорость ответа"},
            "unanswered": {"score": unanswered, "max": 20, "label": "Клиенты без ответа"},
            "conversion": {"score": conversion, "max": 15, "label": "Конверсия"},
            "lead_handling": {"score": handling, "max": 15, "label": "Обработка лидов"},
            "kpi": {"score": kpi, "max": 10, "label": "Выполнение KPI"},
            "followup": {"score": followup, "max": 10, "label": "Follow-up и реактивация"},
            "ads": {"score": ads, "max": 10, "label": "Рекламная эффективность"},
        },
    }


def _loss(payload: CalculatorInput) -> Dict[str, Any]:
    leads = payload.leads_per_month
    unanswered = min(payload.unanswered_leads, leads)
    conv = _clamp(payload.conversion_percent / 100.0, 0, 1)
    check = payload.average_check_rub

    unanswered_loss = unanswered * conv * check

    if payload.avg_response_minutes <= 5:
        delay_factor = 0.0
    elif payload.avg_response_minutes <= 15:
        delay_factor = 0.03
    elif payload.avg_response_minutes <= 30:
        delay_factor = 0.07
    elif payload.avg_response_minutes <= 60:
        delay_factor = 0.12
    elif payload.avg_response_minutes <= 120:
        delay_factor = 0.18
    else:
        delay_factor = 0.25

    answered = max(0, leads - unanswered)
    slow_response_loss = answered * conv * check * delay_factor

    reactivation_rate = _clamp((payload.reactivation_percent if payload.reactivation_percent is not None else 10) / 100.0, 0, 1)
    recoverable_leads = int(round(unanswered + answered * (0.03 + 0.07 * reactivation_rate)))
    base = unanswered_loss + slow_response_loss
    low = int(round(base * 0.70))
    high = int(round(base * 1.30))
    return {
        "confirmed": {
            "leads_per_month": leads,
            "unanswered_leads": unanswered,
            "avg_response_minutes": round(payload.avg_response_minutes, 1),
        },
        "estimated": {
            "recoverable_leads": max(0, recoverable_leads),
            "unanswered_loss_rub": int(round(unanswered_loss)),
            "slow_response_loss_rub": int(round(slow_response_loss)),
            "lost_revenue_low": max(0, low),
            "lost_revenue_high": max(0, high),
        },
        "confidence": "estimate",
        "assumptions": {
            "conversion_percent": payload.conversion_percent,
            "average_check_rub": round(payload.average_check_rub, 2),
            "response_delay_factor": delay_factor,
        },
        "disclaimer": "Это расчётный диапазон, а не подтверждённая потеря выручки.",
    }


def _reasons(payload: CalculatorInput, score: Dict[str, Any], loss: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    if payload.unanswered_leads:
        rows.append({"kind": "unanswered", "value": min(payload.unanswered_leads, payload.leads_per_month), "text": "клиентов могут оставаться без ответа"})
    if payload.avg_response_minutes > 15:
        rows.append({"kind": "slow_response", "value": round(payload.avg_response_minutes, 1), "text": "минут — среднее время ответа"})
    rows.append({"kind": "reactivation", "value": loss["estimated"]["recoverable_leads"], "text": "клиентов потенциально можно вернуть в работу"})
    return rows[:3]


def _storage_put(account_id: str, key: str, value: Dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if row:
            row.value = raw
        else:
            db.add(Storage(account_id=account_id, key=key, value=raw))
        db.commit()
    finally:
        db.close()


def _storage_get(account_id: str, key: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).order_by(desc(Storage.id)).first()
        if not row or not row.value:
            return None
        return json.loads(row.value)
    except Exception:
        return None
    finally:
        db.close()


def _safe_company(account_id: str) -> dict[str, Any]:
    """Return only public company identity; never account/email/internal ids."""
    db = SessionLocal()
    try:
        row = db.query(Account).filter(Account.account_id == str(account_id or "")).first()
        name = str(getattr(row, "name", "") or "").strip()
        return {"name": name or "Ваш бизнес"}
    except Exception:
        return {"name": "Ваш бизнес"}
    finally:
        db.close()


def _public_market_payload(market_result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Explicit public allowlist for market enrichment."""
    if not isinstance(market_result, dict):
        return None
    demand = market_result.get("demand") or {}
    competitors = market_result.get("competitors") or {}
    recommended = market_result.get("recommended_ads") or {}
    positioning = market_result.get("positioning") or {}
    top_requests = []
    for row in list(demand.get("top_requests") or [])[:12]:
        if not isinstance(row, dict):
            continue
        phrase = " ".join(str(row.get("phrase") or "").split()).strip()[:120]
        if not phrase:
            continue
        count = row.get("count")
        try:
            count = int(count) if count is not None else None
        except (TypeError, ValueError):
            count = None
        top_requests.append({"phrase": phrase, "count": count})
    ideas = [
        " ".join(str(x or "").split()).strip()[:240]
        for x in list(positioning.get("ideas_to_test") or [])[:4]
        if str(x or "").strip()
    ]
    return {
        "market_score": {
            "total": int(((market_result.get("market_score") or {}).get("total") or 0)),
            "label": str(((market_result.get("market_score") or {}).get("label") or ""))[:160],
            "confidence": str(((market_result.get("market_score") or {}).get("confidence") or "low"))[:32],
        },
        "demand": {
            "level": str(demand.get("level") or "")[:120],
            "total_count": int(demand.get("total_count") or 0) if demand.get("total_count") is not None else None,
            "region_scope": str(demand.get("region_scope") or "")[:160],
            "top_requests": top_requests,
        },
        "competitors": {
            "direct_sample": competitors.get("direct_sample"),
            "estimated_competitors": competitors.get("estimated_competitors"),
            "competing_offers_sample": competitors.get("competing_offers_sample"),
            "relevant_ads_sample": competitors.get("relevant_ads_sample"),
            "count_basis": str(competitors.get("count_basis") or "")[:80],
        },
        "recommended_ads": {
            "low": int(recommended.get("low") or 0),
            "high": int(recommended.get("high") or 0),
            "grounded": bool(recommended.get("grounded")),
            "competition_grounded": bool(recommended.get("competition_grounded")),
        },
        "summary": str(market_result.get("summary") or "")[:1200],
        "positioning": {"ideas_to_test": ideas},
        "result_status": str(market_result.get("status") or "ok")[:40],
    }


def _public_loss_points(audit_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    labels = {
        "unanswered": "Есть обращения без ответа",
        "slow_response": "Клиенты могут слишком долго ждать",
        "slow_outliers": "Есть отдельные долгие ответы",
        "reactivation": "Старые обращения можно вернуть",
        "conversion": "Стоит проверить переход к следующему шагу",
        "response": "Скорость ответа можно улучшить",
    }
    out: list[Dict[str, Any]] = []
    for row in list(audit_data.get("reasons") or [])[:4]:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        existing_title = " ".join(str(row.get("title") or "").split()).strip()[:180]
        text = " ".join(str(row.get("text") or row.get("description") or "").split()).strip()[:320]
        title = labels.get(kind) or existing_title or text or "Точка роста"
        item: Dict[str, Any] = {"title": title}
        if text and text != title:
            item["description"] = text
        value = row.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            item["value"] = value
        out.append(item)
    return out


def _client_opportunities(audit_data: Dict[str, Any], market: Optional[Dict[str, Any]]) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    rec = ((market or {}).get("recommended_ads") or {})
    if rec.get("grounded") and int(rec.get("high") or 0) > 0:
        rows.append({
            "code": "coverage",
            "title": "Можно охватить больше спроса",
            "description": (
                f"Для старта BORIS рекомендует поддерживать {int(rec.get('low') or 0)}–"
                f"{int(rec.get('high') or 0)} разных рекламных предложений и масштабировать лучшие."
            ),
        })
    reasons = list(audit_data.get("reasons") or [])
    kinds = {str(x.get("kind") or "") for x in reasons if isinstance(x, dict)}
    if {"slow_response", "slow_outliers", "unanswered", "response"} & kinds:
        rows.append({
            "code": "response",
            "title": "Клиенты не должны ждать ответа",
            "description": "BORIS может быстрее замечать новые обращения, отвечать и контролировать следующий шаг.",
        })
    if "reactivation" in kinds:
        rows.append({
            "code": "reactivation",
            "title": "Старые обращения можно вернуть в работу",
            "description": "Повторные касания и напоминания помогают не терять уже полученный спрос.",
        })
    defaults = [
        {
            "code": "measurement",
            "title": "Измерять путь клиента целиком",
            "description": "BORIS собирает обращения, следующие шаги и результат в одной системе.",
        },
        {
            "code": "hypotheses",
            "title": "Тестировать разные предложения",
            "description": "Вместо одной подачи BORIS сравнивает гипотезы и помогает масштабировать сильные.",
        },
        {
            "code": "control",
            "title": "Снять ручной контроль с владельца",
            "description": "Напоминания, задачи и контроль обработки работают автоматически.",
        },
    ]
    seen = {str(x.get("code") or "") for x in rows}
    for item in defaults:
        if item["code"] not in seen:
            rows.append(item)
            seen.add(item["code"])
        if len(rows) >= 3:
            break
    return rows[:3]


def _client_journey(audit_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    confirmed = ((audit_data.get("loss") or {}).get("confirmed") or {})
    total = confirmed.get("dialogs_period")
    answered = confirmed.get("answered_dialogs")
    unresolved = confirmed.get("currently_or_never_unanswered")
    return [
        {"code": "seen", "title": "Клиент увидел предложение", "value": None, "state": "measured_later"},
        {
            "code": "contacted", "title": "Написал или позвонил",
            "value": int(total) if total is not None else None,
            "state": "measured" if total is not None else "measured_later",
        },
        {
            "code": "answered", "title": "Получил ответ",
            "value": int(answered) if answered is not None else None,
            "state": "measured" if answered is not None else "measured_later",
        },
        {
            "code": "next_step", "title": "Перешёл к расчёту / записи / замеру",
            "value": None, "state": "measured_later",
        },
        {"code": "won", "title": "Купил", "value": None, "state": "measured_later"},
        {
            "code": "attention", "title": "Требуют внимания",
            "value": int(unresolved) if unresolved is not None else None,
            "state": "measured" if unresolved is not None else "measured_later",
        },
    ]


def _client_data_status(account_id: str, market_audit_id: str, market_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if market_result:
        state = "READY"
        market_state = "ready"
        progress = 100
    elif market_audit_id:
        job = _storage_get(account_id, f"market_job:{market_audit_id}") or {}
        if str(job.get("status") or "").upper() == "ERROR":
            state = "FAILED_PARTIAL"
            market_state = "unavailable"
            progress = 80
        else:
            state = "PARTIAL_READY"
            market_state = "loading"
            progress = 80
    else:
        state = "READY"
        market_state = "not_started"
        progress = 100
    last_updated_at = (
        str((market_result or {}).get("created_at") or (market_result or {}).get("updated_at") or "").strip()
        or _now().isoformat()
    )
    return {
        "state": state,
        "progress_percent": progress,
        "last_updated_at": last_updated_at,
        "sections": [
            {"code": "company", "label": "Компания", "state": "ready"},
            {"code": "sales", "label": "Продажи", "state": "ready"},
            {"code": "demand", "label": "Спрос", "state": market_state},
            {"code": "advertising", "label": "Реклама", "state": market_state},
            {"code": "recommendations", "label": "Рекомендации", "state": "ready"},
        ],
        "updated_at": _now().isoformat(),
    }


def _client_payload(
    audit_data: Dict[str, Any],
    *,
    company: Dict[str, Any],
    market: Optional[Dict[str, Any]],
    data_status: Dict[str, Any],
    referral_code: str = "",
) -> Dict[str, Any]:
    score = audit_data.get("score") or {}
    loss = audit_data.get("loss") or {}
    estimated = loss.get("estimated") or {}
    opportunities = _client_opportunities(audit_data, market)
    demand = (market or {}).get("demand") or {}
    recommended = (market or {}).get("recommended_ads") or {}
    company_name = str(company.get("name") or "Ваш бизнес")
    market_summary = str((market or {}).get("summary") or "").strip()
    summary = market_summary or (
        "BORIS уже собрал базовый результат. Дополнительные данные рынка будут добавляться по мере готовности."
        if data_status.get("state") == "PARTIAL_READY"
        else "BORIS нашёл точки, где можно усилить рекламу и обработку обращений."
    )
    return {
        "company": {"name": company_name},
        "summary": {"headline": f"Мы нашли точки роста для «{company_name}»", "text": summary[:1200]},
        "growth_score": {
            "total": int(score.get("total") or 0),
            "label": str(score.get("label") or ""),
        },
        "top_opportunities": opportunities,
        "demand": {
            "total_count": demand.get("total_count"),
            "level": demand.get("level") or "",
            "region_scope": demand.get("region_scope") or "",
            "top_requests": list(demand.get("top_requests") or [])[:12],
        },
        "advertising": {
            "recommended_low": int(recommended.get("low") or 0),
            "recommended_high": int(recommended.get("high") or 0),
            "grounded": bool(recommended.get("grounded")),
            "competition_grounded": bool(recommended.get("competition_grounded")),
        },
        "customer_journey": _client_journey(audit_data),
        "loss_points": _public_loss_points(audit_data),
        "recommendations": opportunities,
        "boris_actions": [
            {"title": "Приведёт больше клиентов", "items": ["объявления", "изображения и баннеры", "рекламные гипотезы", "анализ эффективности"]},
            {"title": "Не даст потерять обращение", "items": ["ответ 24/7", "CRM и следующий шаг", "напоминания", "контроль обработки"]},
            {"title": "Вернёт старых клиентов", "items": ["повторные касания", "реактивация", "персональные предложения"]},
            {"title": "Покажет владельцу результат", "items": ["обращения", "продажи", "эффективность рекламы", "точки роста"]},
        ],
        "first_24h_plan": [
            "Изучить компанию",
            "Изучить рекламу и обращения",
            "Подготовить улучшенные предложения",
            "Подключить обработку новых клиентов",
            "Настроить контроль следующих шагов",
            "Начать показывать владельцу результаты",
        ],
        "money": {
            "available": bool(estimated.get("money_available", estimated.get("lost_revenue_low") is not None)),
            "lost_revenue_low": estimated.get("lost_revenue_low"),
            "lost_revenue_high": estimated.get("lost_revenue_high"),
            "disclaimer": loss.get("disclaimer") or "Это расчётный диапазон, а не подтверждённая потеря выручки.",
        },
        "referral": {
            "code": referral_code,
            "path": f"/r/{referral_code}" if referral_code else "",
            "reward_text": "+7 дней BORIS",
            "reward_active": True,
        },
        "data_status": data_status,
    }


def _parse_datetime_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extend_referral_base_access(account_id: str, owner_user_id: int, target: datetime, reward_id: str) -> None:
    """Set the canonical access clocks to an exact target without creating payment history."""
    db = SessionLocal()
    try:
        db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"viral_reward_access:{account_id}"})
        row = db.query(Storage).filter(
            Storage.account_id == str(account_id),
            Storage.key == "billing",
        ).order_by(desc(Storage.id)).first()
        billing: Dict[str, Any] = {}
        if row and row.value:
            try:
                parsed = json.loads(row.value)
                billing = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                billing = {}
        current_until = _parse_datetime_utc(billing.get("paid_until"))
        effective = max([x for x in (current_until, target) if x is not None])
        billing["paid_until"] = effective.isoformat()
        billing["access_extension"] = {
            "source": "viral_referral_reward",
            "reward_id": reward_id,
            "extended_at": _now().isoformat(),
        }
        raw = json.dumps(billing, ensure_ascii=False, separators=(",", ":"))
        if row:
            row.value = raw
        else:
            db.add(Storage(account_id=str(account_id), key="billing", value=raw))
        db.execute(sql_text(
            "UPDATE account_slots SET paid_until=:t,updated_at=now(),"
            " status=CASE WHEN status='readonly' THEN 'connected' ELSE status END "
            "WHERE account_id=:a AND product='inbox'"
        ), {"a": str(account_id), "t": effective.replace(tzinfo=None)})
        db.execute(sql_text(
            "UPDATE users SET subscription_expires_at=:t WHERE id=:u "
            "AND (subscription_expires_at IS NULL OR subscription_expires_at<:t)"
        ), {"u": int(owner_user_id), "t": effective.replace(tzinfo=None)})
        db.execute(sql_text(
            "UPDATE product_limits SET paid_until=:d,updated_at=now() "
            "WHERE billing_owner_kind='user' AND billing_owner_id=:u "
            "AND resource='commands' AND (paid_until IS NULL OR paid_until<:d)"
        ), {"u": str(owner_user_id), "d": effective.date()})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _maybe_grant_referral_reward(referred_user_id: int, qualification: str) -> Dict[str, Any]:
    """Crash-safe, idempotent +7 day referral reward keyed by referred user."""
    if qualification != "account_connected":
        return {"status": "not_qualified"}
    db = SessionLocal()
    ledger_account = "viral_referral_reward"
    ledger_key = f"referred:{int(referred_user_id)}"
    try:
        db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": ledger_key})
        referred = db.query(User).filter(User.id == int(referred_user_id)).first()
        if not referred or not str(getattr(referred, "referred_by", "") or "").strip():
            db.rollback()
            return {"status": "no_referrer"}
        referrer = db.query(User).filter(User.email == str(referred.referred_by)).first()
        if not referrer or int(referrer.id) == int(referred.id):
            db.rollback()
            return {"status": "invalid_referrer"}
        if not bool(getattr(referred, "email_verified", False)) or str(getattr(referred, "status", "")) != "active":
            db.rollback()
            return {"status": "not_verified"}
        referrer_account_id = str(getattr(referrer, "account_id", "") or "").strip()
        if not referrer_account_id:
            db.rollback()
            return {"status": "referrer_has_no_account"}

        row = db.query(Storage).filter(
            Storage.account_id == ledger_account,
            Storage.key == ledger_key,
        ).order_by(desc(Storage.id)).first()
        ledger = {}
        if row and row.value:
            try:
                ledger = json.loads(row.value)
            except (TypeError, ValueError, json.JSONDecodeError):
                ledger = {}
        if str(ledger.get("status") or "") == "GRANTED":
            db.rollback()
            return {"status": "already_granted", "reward": ledger}

        target = _parse_datetime_utc(ledger.get("target_paid_until"))
        if target is None:
            from app.services.subscription_gate import base_subscription_access
            current_access = base_subscription_access(db, referrer_account_id)
            candidates = [_now()]
            paid_until = _parse_datetime_utc(current_access.get("paid_until"))
            if paid_until:
                candidates.append(paid_until)
            user_until = _parse_datetime_utc(getattr(referrer, "subscription_expires_at", None))
            if user_until:
                candidates.append(user_until)
            target = max(candidates) + timedelta(days=7)
            ledger = {
                "id": "vrr_" + uuid.uuid4().hex,
                "referrer_user_id": int(referrer.id),
                "referred_user_id": int(referred.id),
                "referral_code": str(getattr(referrer, "ref_code", "") or ""),
                "event_type": "QUALIFIED",
                "reward_type": "BORIS_ACCESS_DAYS",
                "reward_value": 7,
                "qualification": qualification,
                "status": "GRANTING",
                "created_at": _now().isoformat(),
                "activated_at": None,
                "target_paid_until": target.isoformat(),
                "metadata": {"referred_account_id": str(getattr(referred, "account_id", "") or "")},
            }
            raw = json.dumps(ledger, ensure_ascii=False, separators=(",", ":"))
            if row:
                row.value = raw
            else:
                row = Storage(account_id=ledger_account, key=ledger_key, value=raw)
                db.add(row)
            db.commit()

        from app.services.subscription_gate import base_subscription_access
        current_access = base_subscription_access(db, referrer_account_id)
        current_until = _parse_datetime_utc(current_access.get("paid_until"))
        user_until = _parse_datetime_utc(getattr(referrer, "subscription_expires_at", None))
        effective_until = max([x for x in (current_until, user_until) if x is not None], default=None)
        if effective_until is None or effective_until < target:
            _extend_referral_base_access(
                referrer_account_id,
                int(referrer.id),
                target,
                str(ledger.get("id") or ledger_key),
            )

        db.expire_all()
        row = db.query(Storage).filter(
            Storage.account_id == ledger_account,
            Storage.key == ledger_key,
        ).order_by(desc(Storage.id)).first()
        latest = dict(ledger)
        latest["status"] = "GRANTED"
        latest["activated_at"] = _now().isoformat()
        raw = json.dumps(latest, ensure_ascii=False, separators=(",", ":"))
        if row:
            row.value = raw
        else:
            db.add(Storage(account_id=ledger_account, key=ledger_key, value=raw))
        db.commit()
        return {"status": "granted", "reward": latest}
    except Exception as exc:
        db.rollback()
        return {"status": "error", "error": type(exc).__name__}
    finally:
        db.close()



def _audit_run_start(account_id: str, kind: str, **meta) -> dict[str, Any]:
    run_id = "ar_" + uuid.uuid4().hex
    now = _now()
    run = {
        "run_id": run_id,
        "account_id": str(account_id or ""),
        "kind": str(kind or ""),
        "status": "running",
        "started_at": now.isoformat(),
        "finished_at": None,
        "duration_ms": None,
        "cache_hit": False,
        "ai_cost_rub": 0.0,
        "proxy_cost_rub": 0.0,
        "wordstat_cost_rub": 0.0,
        "email_cost_rub": 0.0,
        "browser_traffic_mb": 0.0,
        "total_variable_cost_rub": 0.0,
        "cost_complete": True,
        **meta,
    }
    _storage_put("viral_audit_run", f"run:{run_id}", run)
    return run


def _audit_run_finish(run: dict[str, Any], *, status: str = "ok", **updates) -> dict[str, Any]:
    now = _now()
    started = now
    try:
        started = datetime.fromisoformat(str(run.get("started_at") or ""))
    except (TypeError, ValueError):
        started = now
    finished = dict(run)
    finished.update(updates)
    finished["status"] = status
    finished["finished_at"] = now.isoformat()
    finished["duration_ms"] = max(0, int(round((now - started).total_seconds() * 1000)))
    ai = float(finished.get("ai_cost_rub") or 0)
    proxy = float(finished.get("proxy_cost_rub") or 0)
    wordstat = float(finished.get("wordstat_cost_rub") or 0)
    email = float(finished.get("email_cost_rub") or 0)
    finished["total_variable_cost_rub"] = round(ai + proxy + wordstat + email, 4)
    finished["cost_complete"] = bool(finished.get("cost_complete", True))
    _storage_put("viral_audit_run", f"run:{finished['run_id']}", finished)
    return finished


def _audit_run_rows(limit: int = 500) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Storage)
            .filter(Storage.account_id == "viral_audit_run", Storage.key.like("run:%"))
            .order_by(Storage.id.desc())
            .limit(max(1, min(int(limit or 500), 5000)))
            .all()
        )
        out = []
        for row in rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                continue
            if isinstance(value, dict):
                out.append(value)
        return out
    finally:
        db.close()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    rows = sorted(float(x) for x in values)
    if len(rows) == 1:
        return rows[0]
    rank = (len(rows) - 1) * _clamp(p, 0.0, 1.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return rows[lo]
    weight = rank - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def _market_cache_ttl_hours() -> int:
    try:
        return max(1, min(int(os.getenv("BORIS_MARKET_CACHE_TTL_HOURS", "48")), 168))
    except Exception:
        return 48


def _market_cache_get(query: str, region: str) -> Optional[Dict[str, Any]]:
    from app.services.audit_market import cache_key
    key = "market:" + cache_key(query, region)
    data = _storage_get("viral_market_cache", key)
    if not data:
        return None
    try:
        created = datetime.fromisoformat(str(data.get("created_at") or ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (_now() - created) > timedelta(hours=_market_cache_ttl_hours()):
            return None
    except (TypeError, ValueError):
        return None
    return data


def _market_cache_refresh_hours() -> int:
    try:
        return max(1, min(int(os.getenv("BORIS_MARKET_CACHE_REFRESH_HOURS", "6")), _market_cache_ttl_hours()))
    except (TypeError, ValueError):
        return min(6, _market_cache_ttl_hours())


def _market_cache_age_hours(data: Dict[str, Any]) -> Optional[float]:
    try:
        created = datetime.fromisoformat(str(data.get("created_at") or ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (_now() - created).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return None


def _run_market_cache_refresh(cache_id: str, query: str, region: str, job_key: str) -> None:
    try:
        from app.services.audit_market import run_market_audit
        result = run_market_audit(query, region, progress=None)
        _storage_put("viral_market_cache", cache_id, result)
        _storage_put("viral_market_cache_meta", f"refresh:{cache_id}", {
            "status": "ok",
            "refreshed_at": _now().isoformat(),
            "result_status": str(result.get("status") or ""),
        })
    except Exception as exc:
        _storage_put("viral_market_cache_meta", f"refresh:{cache_id}", {
            "status": "error",
            "error_type": type(exc).__name__,
            "failed_at": _now().isoformat(),
        })
    finally:
        with _MARKET_JOB_LOCK:
            _MARKET_JOBS_RUNNING.discard(job_key)


def _start_market_cache_refresh(query: str, region: str, cached: Dict[str, Any]) -> bool:
    from app.services.audit_market import cache_key
    age = _market_cache_age_hours(cached)
    if age is None or age < float(_market_cache_refresh_hours()):
        return False
    cache_id = "market:" + cache_key(query, region)
    job_key = "cache-refresh:" + cache_id
    with _MARKET_JOB_LOCK:
        if job_key in _MARKET_JOBS_RUNNING:
            return False
        _MARKET_JOBS_RUNNING.add(job_key)
    thread = threading.Thread(
        target=_run_market_cache_refresh,
        args=(cache_id, query, region, job_key),
        daemon=True,
        name=f"viral-market-cache-{cache_id[-8:]}",
    )
    thread.start()
    return True


def _market_job_state(account_id: str, audit_id: str, **updates) -> Dict[str, Any]:
    key = f"market_job:{audit_id}"
    current = _storage_get(account_id, key) or {
        "audit_id": audit_id,
        "status": "QUERY_READY",
        "created_at": _now().isoformat(),
    }
    current.update(updates)
    current["updated_at"] = _now().isoformat()
    _storage_put(account_id, key, current)
    return current


def _send_full_audit_email(account_id: str, market_audit_id: str, market_result: Dict[str, Any]) -> bool:
    state = _storage_get(account_id, "viral_gateway_state") or {}
    if not bool(state.get("audit_completed")) or not bool(state.get("marketing_opt_in")):
        return False
    core_audit_id = str(state.get("latest_audit_id") or "").strip()
    if not core_audit_id:
        return False
    idem_key = f"audit_email:{market_audit_id}:full"
    if _storage_get(account_id, idem_key):
        return True

    db = SessionLocal()
    try:
        client_user = db.query(User).filter(User.account_id == account_id).first()
        email = str(getattr(client_user, "email", "") or "").strip()
    finally:
        db.close()
    if not email:
        return False

    core = _storage_get("viral_audit", f"audit:{core_audit_id}") or {}
    sales_score = int(((core.get("score") or {}).get("total") or 0))
    market_score = int(((market_result.get("market_score") or {}).get("total") or 0))
    demand = str(((market_result.get("demand") or {}).get("level") or "Недостаточно данных"))
    ads = market_result.get("recommended_ads") or {}
    ads_text = (
        f"{int(ads.get('low') or 0)}–{int(ads.get('high') or 0)} объявлений"
        if bool(ads.get("grounded")) else "пока недостаточно данных для точного диапазона"
    )
    result_url = (
        "https://boris-ai.pro/audit/result?audit="
        + core_audit_id
        + "&market="
        + market_audit_id
    )
    contact_cfg = _storage_get("viral_global", "sales_contact") or {}
    trial_days = max(1, min(int(contact_cfg.get("trial_days") or 4), 30))
    trial_enabled = bool(contact_cfg.get("trial_enabled", True))

    subject = "Ваш полный аудит BORIS готов"
    body = (
        f"Как сейчас работают продажи: {sales_score}/100.\n"
        f"Потенциал роста на рынке: {market_score}/100.\n"
        f"Спрос: {demand}.\n"
        f"Рекомендованный старт рекламы: {ads_text}.\n\n"
        f"Открыть полный аудит: {result_url}\n"
        + (f"\nПосле результата можно запросить бесплатный тест BORIS на {trial_days} дня." if trial_enabled else "")
        + "\n\nНастройки писем: https://boris-ai.pro/audit/email-settings"
    )
    trial_html = (
        f'<div style="margin-top:20px;padding:18px;background:#edf4ff;border-radius:14px">'
        f'<div style="font-size:18px;font-weight:800">Запустить бесплатный тест BORIS · {trial_days} дня</div>'
        f'<div style="margin-top:6px;font-size:13px;color:#53617a">Поможем подключить BORIS и начнём с найденных в аудите точек.</div>'
        f'</div>'
        if trial_enabled else ""
    )
    html = f"""<!doctype html><html><body style="margin:0;background:#f6f8fc;font-family:Arial,Helvetica,sans-serif;color:#15213b">
    <div style="max-width:640px;margin:0 auto;padding:28px 16px">
      <div style="font-size:20px;font-weight:800;margin-bottom:18px">BORIS <span style="color:#2878ff">AUDIT</span></div>
      <div style="background:#fff;border:1px solid #dfe6f2;border-radius:18px;padding:24px">
        <div style="font-size:12px;font-weight:800;color:#8490a6;text-transform:uppercase;letter-spacing:.08em">Полный аудит готов</div>
        <div style="display:flex;gap:12px;margin-top:16px">
          <div style="flex:1;padding:16px;background:#f8faff;border-radius:14px"><div style="font-size:12px;color:#8490a6">Как сейчас работают продажи</div><div style="font-size:30px;font-weight:800">{sales_score}/100</div></div>
          <div style="flex:1;padding:16px;background:#f8faff;border-radius:14px"><div style="font-size:12px;color:#8490a6">Потенциал роста</div><div style="font-size:30px;font-weight:800">{market_score}/100</div></div>
        </div>
        <div style="margin-top:18px;font-size:14px;color:#53617a">Спрос: <b style="color:#15213b">{demand}</b></div>
        <div style="margin-top:6px;font-size:14px;color:#53617a">Реклама: <b style="color:#15213b">{ads_text}</b></div>
        {trial_html}
        <a href="{result_url}" style="display:inline-block;margin-top:22px;background:#2878ff;color:#fff;text-decoration:none;font-weight:800;padding:13px 20px;border-radius:10px">Открыть полный аудит</a>
      </div>
    </div></body></html>"""
    try:
        from app.services import email_service as _email_service
        ok, _reason, _message_id = _email_service.send_email(
            email, subject, body, html=html, tenant_scope="audit:full_result"
        )
        if ok:
            _storage_put(account_id, idem_key, {
                "audit_id": core_audit_id,
                "market_audit_id": market_audit_id,
                "email_type": "full_audit_result",
                "sent_at": _now().isoformat(),
            })
        return bool(ok)
    except Exception:
        return False


def _run_market_job(account_id: str, audit_id: str, query: str, region: str, run: dict[str, Any]) -> None:
    from app.services.audit_market import run_market_audit, cache_key
    job_key = account_id + ":" + audit_id
    try:
        def progress(stage: str) -> None:
            _market_job_state(account_id, audit_id, status=stage)

        result = run_market_audit(query, region, account_id=account_id, progress=progress)
        cache_id = "market:" + cache_key(query, region)
        _storage_put("viral_market_cache", cache_id, result)
        _storage_put(account_id, f"market_result:{audit_id}", result)

        state = _storage_get(account_id, "viral_gateway_state") or {}
        next_stage = "FULL_AUDIT_COMPLETED" if bool(state.get("audit_completed")) else "MARKET_AUDIT_COMPLETED"
        state.update({
            "stage": next_stage,
            "market_audit_completed": True,
            "latest_market_audit_id": audit_id,
            "latest_market_score": int(((result.get("market_score") or {}).get("total") or 0)),
            "market_demand_level": str(((result.get("demand") or {}).get("level") or "")),
            "market_competitors": ((result.get("competitors") or {}).get("direct_sample")),
            "market_estimated_competitors": ((result.get("competitors") or {}).get("estimated_competitors")),
            "market_competing_offers": int(((result.get("competitors") or {}).get("competing_offers_sample") or 0)),
            "market_category_slug": result.get("category_slug"),
            "market_recommended_ads": result.get("recommended_ads") or {},
            "updated_at": _now().isoformat(),
        })
        _storage_put(account_id, "viral_gateway_state", state)
        context = _storage_get(account_id, "viral_gateway_context") or {}
        context.update({
            "source": "audit_gateway",
            "market_audit_completed": True,
            "market_audit_id": audit_id,
            "market_score": int(((result.get("market_score") or {}).get("total") or 0)),
            "market_score_confidence": str(((result.get("market_score") or {}).get("confidence") or "low")),
            "market_demand_level": str(((result.get("demand") or {}).get("level") or "")),
            "market_competitors": ((result.get("competitors") or {}).get("direct_sample")),
            "market_competing_offers": int(((result.get("competitors") or {}).get("competing_offers_sample") or 0)),
            "market_recommended_ads": result.get("recommended_ads") or {},
            "updated_at": _now().isoformat(),
        })
        _storage_put(account_id, "viral_gateway_context", context)
        if next_stage == "FULL_AUDIT_COMPLETED":
            _send_full_audit_email(account_id, audit_id, result)
        event_id = "ve_" + uuid.uuid4().hex
        _storage_put("viral_event", f"event:{event_id}", {
            "event_id": event_id,
            "event": "gateway_stage",
            "stage": next_stage,
            "account_id": account_id,
            "created_at": _now().isoformat(),
        })
        _market_job_state(
            account_id, audit_id,
            status="ANALYSIS_READY",
            result_status=result.get("status"),
            market_snapshot_id=result.get("market_snapshot_id"),
        )
        transport = result.get("transport") or {}
        _audit_run_finish(
            run,
            status="ok",
            cache_hit=False,
            result_status=result.get("status"),
            browser_success=bool(transport.get("browser_success")),
            wordstat_success=bool(transport.get("wordstat_success")),
            browser_traffic_mb=float(transport.get("browser_traffic_mb") or 0),
            proxy_cost_rub=float(transport.get("proxy_cost_rub") or 0),
            wordstat_cost_rub=float(transport.get("wordstat_cost_rub") or 0),
            cost_complete=bool(transport.get("proxy_cost_known", True)),
            partial=bool(result.get("partial")),
            market_score=int(((result.get("market_score") or {}).get("total") or 0)),
        )
    except Exception as exc:
        _market_job_state(
            account_id, audit_id,
            status="ERROR",
            message="Часть рыночных данных временно недоступна",
            error_type=type(exc).__name__,
        )
        _audit_run_finish(run, status="error", cache_hit=False, error_type=type(exc).__name__)
    finally:
        with _MARKET_JOB_LOCK:
            _MARKET_JOBS_RUNNING.discard(job_key)


def _start_market_job(account_id: str, audit_id: str, query: str, region: str, run: dict[str, Any]) -> None:
    job_key = account_id + ":" + audit_id
    with _MARKET_JOB_LOCK:
        if job_key in _MARKET_JOBS_RUNNING:
            return
        _MARKET_JOBS_RUNNING.add(job_key)
    thread = threading.Thread(
        target=_run_market_job,
        args=(account_id, audit_id, query, region, run),
        daemon=True,
        name=f"viral-market-{audit_id[-8:]}",
    )
    thread.start()


def _cancel_pending_audit_nurture(account_id: str, reason: str) -> int:
    db = SessionLocal()
    try:
        result = db.execute(sql_text("""
            UPDATE email_queue
               SET status='cancelled',
                   last_error=:reason,
                   next_attempt_at=NULL,
                   updated_at=NOW()
             WHERE source='audit_nurture'
               AND ref_type='audit_gateway'
               AND ref_id=:account_id
               AND status IN ('queued','retrying')
        """), {"reason": ("AUDIT_NURTURE_CANCELLED:" + str(reason or ""))[:240],
                 "account_id": account_id})
        db.commit()
        return int(result.rowcount or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def _queue_nurture_email(
    *,
    account_id: str,
    to_email: str,
    audit_id: str,
    email_type: str,
    subject: str,
    body: str,
    delay_hours: int,
) -> Dict[str, Any]:
    from app.services import email_queue as _eq
    idem = f"audit_nurture:{account_id}:{audit_id}:{email_type}"
    queued = _eq.enqueue_email(
        to_email,
        subject,
        body,
        source="audit_nurture",
        idempotency_key=idem,
        send_now=False,
        ref_type="audit_gateway",
        ref_id=account_id,
    )
    queue_id = queued.get("id")
    if queue_id:
        db = SessionLocal()
        try:
            scheduled = _now() + timedelta(hours=max(1, int(delay_hours)))
            db.execute(sql_text("""
                UPDATE email_queue
                   SET next_attempt_at=:scheduled,
                       updated_at=NOW()
                 WHERE id=:queue_id
                   AND status IN ('queued','retrying')
            """), {"scheduled": scheduled, "queue_id": int(queue_id)})
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return queued


def _schedule_manual_audit_nurture(user, account_id: str, audit: Dict[str, Any]) -> list[Dict[str, Any]]:
    state = _storage_get(account_id, "viral_gateway_state") or {}
    if not bool(state.get("marketing_opt_in")):
        return []
    email = str(getattr(user, "email", "") or "").strip()
    if not email:
        return []
    audit_id = str(audit.get("audit_id") or "").strip()
    score = int(((audit.get("score") or {}).get("total") or 0))
    result_url = f"https://boris-ai.pro/audit/result?audit={audit_id}"
    scheduled = []
    scheduled.append(_queue_nurture_email(
        account_id=account_id,
        to_email=email,
        audit_id=audit_id,
        email_type="connect_avito",
        subject="Предварительный результат можно проверить на реальных данных",
        body=(
            f"Предварительная оценка работы с клиентами: {score}/100.\n\n"
            "Подключите Avito, и BORIS пересчитает результат по реальным обращениям, "
            "просмотрам, контактам и скорости первого ответа.\n\n"
            "Получить точный аудит: https://boris-ai.pro/audit/connect\n\n"
            "Настройки писем: https://boris-ai.pro/audit/email-settings"
        ),
        delay_hours=24,
    ))
    scheduled.append(_queue_nurture_email(
        account_id=account_id,
        to_email=email,
        audit_id=audit_id,
        email_type="recommendations",
        subject="3 точки, которые стоит проверить после аудита BORIS",
        body=(
            f"Оценка работы с клиентами: {score}/100.\n\n"
            "1. Проверьте обращения без ответа.\n"
            "2. Сравните фактическую скорость первого ответа с целевой.\n"
            "3. Подключите Avito, чтобы проверить конверсию просмотр → контакт.\n\n"
            f"Открыть результат: {result_url}\n\n"
            "Настройки писем: https://boris-ai.pro/audit/email-settings"
        ),
        delay_hours=72,
    ))
    return scheduled


@router.get("/health")
def viral_health():
    return {"status": "ok", "service": "viral-growth", "version": 1, "llm_required": False}



@router.post("/market/source-hint")
def market_source_hint(payload: SourceHintInput, request: Request, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    _rate_limit(request, "market_source_hint", 30)
    _persistent_rate_limit(account_id, "market_source_hint", 10)
    try:
        from app.services.audit_market import infer_source_hint
        result = infer_source_hint(payload.source_url)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="Не удалось прочитать публичную страницу")


@router.post("/market/start")
def market_start(payload: MarketAuditInput, request: Request, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    _rate_limit(request, "market_audit", 20)
    _persistent_rate_limit(account_id, "market_audit", 3)

    query = " ".join(str(payload.query or "").split()).strip()
    region = " ".join(str(payload.region or "").split()).strip()
    if len(query) < 2 or len(region) < 2:
        raise HTTPException(status_code=400, detail="Укажите направление и регион")

    audit_id = "vm_" + uuid.uuid4().hex
    run = _audit_run_start(account_id, "market", audit_id=audit_id, query=query, region=region)
    cached = _market_cache_get(query, region)
    if cached:
        _storage_put(account_id, f"market_result:{audit_id}", cached)
        state = _storage_get(account_id, "viral_gateway_state") or {}
        next_stage = "FULL_AUDIT_COMPLETED" if bool(state.get("audit_completed")) else "MARKET_AUDIT_COMPLETED"
        state.update({
            "stage": next_stage,
            "market_audit_completed": True,
            "latest_market_audit_id": audit_id,
            "latest_market_score": int(((cached.get("market_score") or {}).get("total") or 0)),
            "market_demand_level": str(((cached.get("demand") or {}).get("level") or "")),
            "market_competitors": ((cached.get("competitors") or {}).get("direct_sample")),
            "market_estimated_competitors": ((cached.get("competitors") or {}).get("estimated_competitors")),
            "market_competing_offers": int(((cached.get("competitors") or {}).get("competing_offers_sample") or 0)),
            "market_category_slug": cached.get("category_slug"),
            "market_recommended_ads": cached.get("recommended_ads") or {},
            "market_query": query,
            "market_region": region,
            "updated_at": _now().isoformat(),
        })
        _storage_put(account_id, "viral_gateway_state", state)
        context = _storage_get(account_id, "viral_gateway_context") or {}
        context.update({
            "source": "audit_gateway",
            "market_audit_completed": True,
            "market_audit_id": audit_id,
            "market_score": int(((cached.get("market_score") or {}).get("total") or 0)),
            "market_score_confidence": str(((cached.get("market_score") or {}).get("confidence") or "low")),
            "market_demand_level": str(((cached.get("demand") or {}).get("level") or "")),
            "market_competitors": ((cached.get("competitors") or {}).get("direct_sample")),
            "market_competing_offers": int(((cached.get("competitors") or {}).get("competing_offers_sample") or 0)),
            "market_recommended_ads": cached.get("recommended_ads") or {},
            "updated_at": _now().isoformat(),
        })
        _storage_put(account_id, "viral_gateway_context", context)
        if next_stage == "FULL_AUDIT_COMPLETED":
            _send_full_audit_email(account_id, audit_id, cached)
        event_id = "ve_" + uuid.uuid4().hex
        _storage_put("viral_event", f"event:{event_id}", {
            "event_id": event_id,
            "event": "gateway_stage",
            "stage": next_stage,
            "account_id": account_id,
            "created_at": _now().isoformat(),
        })
        _market_job_state(
            account_id, audit_id,
            status="ANALYSIS_READY",
            cached=True,
            result_status=cached.get("status"),
            market_snapshot_id=cached.get("market_snapshot_id"),
        )
        transport = cached.get("transport") or {}
        run_done = _audit_run_finish(
            run,
            status="ok",
            cache_hit=True,
            result_status=cached.get("status"),
            browser_success=bool(transport.get("browser_success")),
            wordstat_success=bool(transport.get("wordstat_success")),
            browser_traffic_mb=float(transport.get("browser_traffic_mb") or 0),
            proxy_cost_rub=float(transport.get("proxy_cost_rub") or 0),
            cost_complete=bool(transport.get("proxy_cost_known", True)),
        )
        background_refresh_started = _start_market_cache_refresh(query, region, cached)
        return {
            "status": "ok",
            "audit_id": audit_id,
            "stage": "ANALYSIS_READY",
            "cached": True,
            "background_refresh_started": bool(background_refresh_started),
            "run": run_done,
        }

    _market_job_state(
        account_id,
        audit_id,
        status="QUERY_READY",
        query=query,
        region=region,
        source_url=str(payload.source_url or "").strip()[:1000],
        cached=False,
    )
    state = _storage_get(account_id, "viral_gateway_state") or {}
    state.update({
        "market_query": query,
        "market_region": region,
        "market_source_url": str(payload.source_url or "").strip()[:1000],
        "updated_at": _now().isoformat(),
    })
    _storage_put(account_id, "viral_gateway_state", state)
    _start_market_job(account_id, audit_id, query, region, run)
    return {"status": "ok", "audit_id": audit_id, "stage": "QUERY_READY", "cached": False, "run_id": run["run_id"]}


@router.get("/market/{audit_id}/status")
def market_status(audit_id: str, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    job = _storage_get(account_id, f"market_job:{audit_id}")
    if not job:
        raise HTTPException(status_code=404, detail="Рыночный аудит не найден")
    return {"status": "ok", "job": job}


@router.get("/market/{audit_id}")
def market_result(audit_id: str, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    job = _storage_get(account_id, f"market_job:{audit_id}")
    if not job:
        raise HTTPException(status_code=404, detail="Рыночный аудит не найден")
    result = _storage_get(account_id, f"market_result:{audit_id}")
    if not result:
        return {
            "status": "running" if job.get("status") != "ERROR" else "error",
            "stage": job.get("status"),
            "message": job.get("message"),
        }
    return {"status": "ok", "audit_id": audit_id, "result": result}


@router.post("/calculator")
def calculator(payload: CalculatorInput, request: Request, user=Depends(get_current_user)):
    _rate_limit(request, "calculator", 20)
    owner_account_id = _gateway_account_id(user)
    _persistent_rate_limit(owner_account_id, "calculator", 5)
    audit_id = "va_" + uuid.uuid4().hex
    run = _audit_run_start(owner_account_id, "manual", audit_id=audit_id)
    score = _score(payload)
    loss = _loss(payload)
    result = {
        "status": "ok",
        "audit_id": audit_id,
        "created_at": _now().isoformat(),
        "owner_account_id": owner_account_id,
        "score": {**score, "score_version": "manual_v1"},
        "loss": {**loss, "loss_formula_version": "manual_v1"},
        "sources": [{"source": "user_declared", "freshness": "declared_now", "collected_at": _now().isoformat()}],
        "reasons": _reasons(payload, score, loss),
        "cta": {"label": "Исправить с BORIS", "href": "/#register"},
        "share_available": True,
    }
    # Save only derived, client-safe data. Raw form values such as niche/region are not exposed publicly.
    _storage_put("viral_audit", f"audit:{audit_id}", result)
    result["run"] = _audit_run_finish(run, status="ok", result_status="ok")
    return result




@router.get("/connected-readiness/{account_id}")
def connected_readiness(account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        allowed = {str(aid): str(name) for aid, name in _connected_account_ids(db, user)}
        if account_id not in allowed:
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        account = db.query(Account).filter(Account.account_id == account_id).first()
        connected = bool(account and account.avito_client_id and account.avito_client_secret)
        latest_stats = (
            db.query(Storage)
            .filter(Storage.account_id == account_id, Storage.key.like("daily_stats:%"))
            .order_by(Storage.key.desc())
            .first()
        )
        try:
            messages = int(db.execute(sql_text(
                "SELECT count(*) FROM messenger_messages WHERE account_id=:a"
            ), {"a": account_id}).scalar() or 0)
        except Exception:
            messages = 0
        gateway = _storage_get(_gateway_account_id(user), "viral_gateway_state") or {}
        assumptions_ready = bool(
            float(gateway.get("declared_average_check_rub") or 0) > 0
            and gateway.get("declared_sales_conversion_percent") is not None
        )
        stats_ready = latest_stats is not None
        messages_ready = messages > 0
        return {
            "status": "ok",
            "account_id": account_id,
            "account_name": allowed[account_id],
            "connection_ready": connected,
            "assumptions_ready": assumptions_ready,
            "money_ready": assumptions_ready,
            "stats_ready": stats_ready,
            "messages_ready": messages_ready,
            "can_run": bool(connected and (stats_ready or messages_ready)),
            "message_count": messages,
            "latest_stats_key": str(latest_stats.key or "") if latest_stats else None,
            "money_message": (
                "Денежный диапазон будет рассчитан"
                if assumptions_ready
                else "Операционный аудит доступен; денежный диапазон появится после среднего чека и конверсии"
            ),
        }
    finally:
        db.close()


@router.post("/connected-audit")
def connected_audit(payload: ConnectedAuditInput, request: Request, user=Depends(get_current_user)):
    _rate_limit(request, "deep_audit", 3)
    db = SessionLocal()
    run = None
    try:
        allowed = {str(aid): str(name) for aid, name in _connected_account_ids(db, user)}
        if payload.account_id not in allowed:
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        _persistent_rate_limit(payload.account_id, "connected_audit", 3)
        run = _audit_run_start(
            payload.account_id,
            "connected",
            period_days=payload.period_days,
            audit_id=None,
        )

        stats = account_stats(db, payload.account_id, getattr(user, "id", None), period_days=payload.period_days)
        since = int((_now() - timedelta(days=payload.period_days)).timestamp())
        account_owner_id = db.execute(sql_text(
            "SELECT avito_user_id FROM account_slots "
            "WHERE account_id=:a AND avito_user_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ), {"a": payload.account_id}).scalar()
        row = db.execute(sql_text("""
            WITH first_in AS (
              SELECT avito_chat_id, min(avito_created_at) AS first_in
              FROM messenger_messages
              WHERE account_id=:a
                AND avito_created_at >= :since
                AND lower(coalesce(direction,'')) LIKE 'in%'
                AND coalesce(msg_type,'') <> 'system'
                AND (:owner_id IS NULL OR item_owner_id IS NULL OR item_owner_id = :owner_id)
              GROUP BY avito_chat_id
            ),
            answered AS (
              SELECT f.avito_chat_id, f.first_in,
                     min(m.avito_created_at) FILTER (
                       WHERE lower(coalesce(m.direction,'')) LIKE 'out%'
                         AND m.avito_created_at >= f.first_in
                         AND coalesce(m.msg_type,'') <> 'system'
                     ) AS first_out
              FROM first_in f
              LEFT JOIN messenger_messages m
                ON m.account_id=:a AND m.avito_chat_id=f.avito_chat_id
              GROUP BY f.avito_chat_id, f.first_in
            )
            SELECT count(*) AS dialogs,
                   count(*) FILTER (WHERE first_out IS NOT NULL) AS answered_dialogs,
                   avg((first_out-first_in)/60.0) FILTER (WHERE first_out IS NOT NULL) AS avg_response_minutes,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY (first_out-first_in)/60.0)
                     FILTER (WHERE first_out IS NOT NULL) AS median_response_minutes,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY (first_out-first_in)/60.0)
                     FILTER (WHERE first_out IS NOT NULL) AS p90_response_minutes,
                   max((first_out-first_in)/60.0) FILTER (WHERE first_out IS NOT NULL) AS max_response_minutes,
                   count(*) FILTER (WHERE first_out IS NULL) AS never_answered
            FROM answered
        """), {"a": payload.account_id, "since": since, "owner_id": account_owner_id}).mappings().first() or {}

        avg_response = float(row.get("avg_response_minutes") or 0.0)
        median_response = float(row.get("median_response_minutes") or 0.0)
        p90_response = float(row.get("p90_response_minutes") or 0.0)
        max_response = float(row.get("max_response_minutes") or 0.0)
        observed_dialogs = int(row.get("dialogs") or stats.get("new_dialogs") or 0)
        answered_dialogs = int(row.get("answered_dialogs") or 0)
        never_answered = int(row.get("never_answered") or 0)
        current_unanswered = int(stats.get("unanswered") or 0)
        unanswered_for_risk = max(never_answered, current_unanswered)


        sla_rows = db.execute(sql_text("""
            WITH first_in AS (
              SELECT avito_chat_id, min(avito_created_at) AS first_in
              FROM messenger_messages
              WHERE account_id=:a
                AND avito_created_at >= :since
                AND lower(coalesce(direction,'')) LIKE 'in%'
                AND coalesce(msg_type,'') <> 'system'
                AND (:owner_id IS NULL OR item_owner_id IS NULL OR item_owner_id = :owner_id)
              GROUP BY avito_chat_id
            ),
            answered AS (
              SELECT f.avito_chat_id, f.first_in,
                     min(m.avito_created_at) FILTER (
                       WHERE lower(coalesce(m.direction,'')) LIKE 'out%'
                         AND m.avito_created_at >= f.first_in
                         AND coalesce(m.msg_type,'') <> 'system'
                     ) AS first_out
              FROM first_in f
              LEFT JOIN messenger_messages m
                ON m.account_id=:a AND m.avito_chat_id=f.avito_chat_id
              GROUP BY f.avito_chat_id, f.first_in
            )
            SELECT
              count(*) FILTER (WHERE first_out IS NOT NULL AND (first_out-first_in)/60.0 <= 5) AS le_5,
              count(*) FILTER (WHERE first_out IS NOT NULL AND (first_out-first_in)/60.0 > 5 AND (first_out-first_in)/60.0 <= 15) AS m5_15,
              count(*) FILTER (WHERE first_out IS NOT NULL AND (first_out-first_in)/60.0 > 15 AND (first_out-first_in)/60.0 <= 30) AS m15_30,
              count(*) FILTER (WHERE first_out IS NOT NULL AND (first_out-first_in)/60.0 > 30 AND (first_out-first_in)/60.0 <= 60) AS m30_60,
              count(*) FILTER (WHERE first_out IS NOT NULL AND (first_out-first_in)/60.0 > 60 AND (first_out-first_in)/60.0 <= 120) AS h1_2,
              count(*) FILTER (WHERE first_out IS NOT NULL AND (first_out-first_in)/60.0 > 120) AS gt_2h
            FROM answered
        """), {"a": payload.account_id, "since": since, "owner_id": account_owner_id}).mappings().first() or {}
        sla_distribution = {
            "до 5 минут": int(sla_rows.get("le_5") or 0),
            "5–15 минут": int(sla_rows.get("m5_15") or 0),
            "15–30 минут": int(sla_rows.get("m15_30") or 0),
            "30–60 минут": int(sla_rows.get("m30_60") or 0),
            "1–2 часа": int(sla_rows.get("h1_2") or 0),
            "2+ часа": int(sla_rows.get("gt_2h") or 0),
        }

        latest_stats = (
            db.query(Storage)
            .filter(Storage.account_id == payload.account_id, Storage.key.like("daily_stats:%"))
            .order_by(Storage.key.desc())
            .first()
        )
        avito_views = avito_contacts = 0
        if latest_stats and latest_stats.value:
            try:
                parsed = json.loads(latest_stats.value)
                items = parsed.get("items") or []
                if items:
                    avito_views = sum(int(x.get("views") or x.get("uniqViews") or 0) for x in items)
                    avito_contacts = sum(int(x.get("contacts") or x.get("uniqContacts") or 0) for x in items)
                else:
                    avito_views = int(parsed.get("views") or parsed.get("uniqViews") or 0)
                    avito_contacts = int(parsed.get("contacts") or parsed.get("uniqContacts") or 0)
            except Exception:
                avito_views = avito_contacts = 0
        avito_conversion = round(avito_contacts / avito_views * 100, 2) if avito_views > 0 else None
        if observed_dialogs <= 0 and avito_views <= 0:
            raise HTTPException(status_code=409, detail="Недостаточно реальных данных для точного аудита")
        avito_stats_collected_at = None
        avito_stats_dt = None
        if latest_stats:
            try:
                _date = str(latest_stats.key or "").split("daily_stats:", 1)[1]
                avito_stats_dt = datetime.fromisoformat(_date + "T23:59:59+00:00")
                avito_stats_collected_at = avito_stats_dt.isoformat()
            except Exception:
                avito_stats_dt = None
                avito_stats_collected_at = None
        max_stats_age_days = max(1, int(os.getenv("BORIS_VIRAL_AVITO_STATS_MAX_AGE_DAYS", "2")))
        avito_stats_fresh = bool(
            avito_stats_dt is not None
            and (_now() - avito_stats_dt) <= timedelta(days=max_stats_age_days)
        )

        gateway_before = _storage_get(_gateway_account_id(user), "viral_gateway_state") or {}
        previous_score = gateway_before.get("latest_score")
        crm = _crm_quality_snapshot(db, payload.account_id, payload.period_days)
        if bool(crm.get("money_ready")):
            average_check_rub = float(crm.get("avg_check_rub") or 0)
            sales_conversion_percent = float(crm.get("deal_to_won_percent") or 0)
            monetary_source = "crm_actual"
        else:
            average_check_rub = float(payload.average_check_rub or gateway_before.get("declared_average_check_rub") or 0)
            sales_conversion_percent = float(payload.sales_conversion_percent if payload.sales_conversion_percent is not None else gateway_before.get("declared_sales_conversion_percent") or 0)
            monetary_source = "user_declared" if average_check_rub > 0 and sales_conversion_percent > 0 else "none"
        if sales_conversion_percent < 0 or sales_conversion_percent > 100:
            raise HTTPException(status_code=400, detail="Некорректная конверсия в продажу")
        monetary_inputs_ready = average_check_rub > 0 and sales_conversion_percent > 0

        kpi_row = db.query(Storage).filter(
            Storage.account_id == payload.account_id, Storage.key == "kpi_settings"
        ).first()
        target_daily = None
        if kpi_row and kpi_row.value:
            try:
                target_daily = float((json.loads(kpi_row.value) or {}).get("target_leads_per_day") or 0) or None
            except Exception:
                target_daily = None

        min_dialogs = max(1, int(os.getenv("BORIS_VIRAL_MIN_DIALOGS", "10")))
        min_avito_views = max(1, int(os.getenv("BORIS_VIRAL_MIN_AVITO_VIEWS", "100")))
        response_sample_ok = observed_dialogs >= min_dialogs
        conversion_sample_ok = avito_views >= min_avito_views and avito_conversion is not None and avito_stats_fresh
        response_metric_minutes = p90_response if p90_response > 0 else avg_response
        response_points = _response_score(response_metric_minutes) if response_sample_ok else 10
        unanswered_points = (
            _unanswered_score(max(observed_dialogs, 1), min(unanswered_for_risk, max(observed_dialogs, 1)))
            if response_sample_ok else 10
        )
        conversion_points = _conversion_score(avito_conversion) if conversion_sample_ok else 8
        handling_points = int(round((response_points / 20 * 7) + (unanswered_points / 20 * 8)))
        if target_daily:
            period_target = target_daily * payload.period_days
            kpi_ratio = min(100.0, observed_dialogs / max(period_target, 1) * 100)
            kpi_points = _pct_score(kpi_ratio, 10, 0)
        else:
            kpi_points = 6
        followup_points = 5
        ads_points = (
            _pct_score(min((avito_conversion or 0) * 5, 100), 10, 7)
            if conversion_sample_ok else 7
        )
        total_score = int(_clamp(
            response_points + unanswered_points + conversion_points + handling_points +
            kpi_points + followup_points + ads_points, 0, 100
        ))

        confirmed_at_risk = min(unanswered_for_risk, observed_dialogs)
        recoverable = confirmed_at_risk
        if monetary_inputs_ready:
            close_conv = sales_conversion_percent / 100.0
            base_loss = confirmed_at_risk * close_conv * average_check_rub
            low = int(round(base_loss * .70))
            high = int(round(base_loss * 1.30))
        else:
            low = None
            high = None

        audit_id = "vc_" + uuid.uuid4().hex
        result = {
            "status": "ok",
            "audit_id": audit_id,
            "created_at": _now().isoformat(),
            "account_id": payload.account_id,
            "owner_account_id": _gateway_account_id(user),
            "account_name": allowed[payload.account_id],
            "period_days": payload.period_days,
            "score": {
                "total": total_score,
                "label": "Сильный" if total_score >= 80 else "Хороший" if total_score >= 65 else "Есть потенциал" if total_score >= 45 else "Нужны изменения",
                "mode": "connected",
                "mode_label": "На основе данных BORIS",
                "score_version": "connected_v1",
                "previous_score": int(previous_score) if previous_score is not None else None,
                "comparison": {
                    "declared_avg_response_minutes": gateway_before.get("declared_avg_response_minutes"),
                    "measured_avg_response_minutes": round(avg_response, 1),
                    "declared_unanswered_leads": gateway_before.get("declared_unanswered_leads"),
                    "measured_unanswered_leads": confirmed_at_risk,
                },
                "confidence": "high" if observed_dialogs >= 20 and avito_views >= 100 else "medium",
                "breakdown": {
                    "response_speed": {"score": response_points, "max": 20, "label": "Скорость ответа"},
                    "unanswered": {"score": unanswered_points, "max": 20, "label": "Клиенты без ответа"},
                    "conversion": {"score": conversion_points, "max": 15, "label": "Из просмотров в обращения"},
                    "lead_handling": {"score": handling_points, "max": 15, "label": "Обработка лидов"},
                    "kpi": {"score": kpi_points, "max": 10, "label": "Выполнение плана"},
                    "followup": {"score": followup_points, "max": 10, "label": "Повторная работа с клиентами"},
                    "ads": {"score": ads_points, "max": 10, "label": "Рекламная эффективность"},
                },
                "data_gaps": [
                    x for x, missing in [
                        ("Недостаточно диалогов для уверенной оценки скорости ответа", not response_sample_ok),
                        ("Недостаточно просмотров Avito для уверенной оценки конверсии", not conversion_sample_ok),
                        ("Статистика Avito устарела", latest_stats is not None and not avito_stats_fresh),
                        ("Нет свежей Avito-конверсии", avito_conversion is None),
                        ("Не задан план по обращениям", target_daily is None),
                        (str(crm.get("message") or "CRM пока недостаточно заполнена для денежного анализа"), not bool(crm.get("money_ready"))),
                        ("Повторная работа с клиентами пока оценена предварительно", True),
                    ] if missing
                ],
            },
            "loss": {
                "confirmed": {
                    "dialogs_period": observed_dialogs,
                    "answered_dialogs": answered_dialogs,
                    "never_answered_in_period": never_answered,
                    "currently_unanswered": current_unanswered,
                    "currently_or_never_unanswered": confirmed_at_risk,
                    "avg_response_minutes": round(avg_response, 1),
                    "median_response_minutes": round(median_response, 1),
                    "p90_response_minutes": round(p90_response, 1),
                    "max_response_minutes": round(max_response, 1),
                    "sla_distribution": sla_distribution,
                    "avito_views_latest": avito_views,
                    "avito_contacts_latest": avito_contacts,
                    "view_to_contact_conversion_pct": avito_conversion,
                },
                "estimated": {
                    "recoverable_leads": recoverable,
                    "lost_revenue_low": low,
                    "lost_revenue_high": high,
                    "money_available": monetary_inputs_ready,
                },
                "confidence": "estimate" if monetary_inputs_ready else "operational_only",
                "loss_formula_version": "connected_v1",
                "assumptions": {
                    "sales_conversion_percent": sales_conversion_percent if sales_conversion_percent > 0 else None,
                    "average_check_rub": average_check_rub if average_check_rub > 0 else None,
                    "monetary_source": monetary_source,
                },
                "disclaimer": (
                    "Денежный диапазон рассчитан по фактическим лидам BORIS и подтверждённым данным CRM."
                    if monetary_inputs_ready and monetary_source == "crm_actual"
                    else "Денежный диапазон рассчитан по фактическим лидам BORIS и указанным вами среднему чеку/конверсии в продажу."
                    if monetary_inputs_ready
                    else "Операционные показатели рассчитаны по реальным данным. Денежный диапазон не рассчитан: нет подтверждённого среднего чека и конверсии в продажу."
                ),
            },
            "crm": crm,
            "sources": [
                {"source": "BORIS messages", "freshness": "live", "collected_at": _now().isoformat()},
                {"source": "Avito", "freshness": ("fresh" if avito_stats_fresh else ("stale" if latest_stats else "missing")), "collected_at": avito_stats_collected_at},
            ] + ([
                {"source": "user_declared", "freshness": "declared_now", "collected_at": _now().isoformat()}
            ] if monetary_inputs_ready else []),
            "reasons": [
                {"kind": "unanswered", "value": confirmed_at_risk, "text": "диалогов сейчас или за период остались без ответа"},
                {"kind": "response", "value": round(response_metric_minutes, 1), "text": "минут — 90% клиентов получают ответ быстрее"},
                {"kind": "slow_outliers", "value": int(sla_distribution.get("2+ часа") or 0), "text": "ответов заняли больше 2 часов"},
                {"kind": "reactivation", "value": recoverable, "text": "лидов требуют проверки на возврат в работу"},
            ],
            "cta": {"label": "Исправить с BORIS", "href": "/messages"},
            "share_available": True,
        }
        # Store only derived snapshot; public serializer later strips account identity and monetary assumptions.
        _storage_put("viral_audit", f"audit:{audit_id}", result)
        if run is not None:
            result["run"] = _audit_run_finish(
                run,
                status="ok",
                audit_id=audit_id,
                result_status="ok",
                score=total_score,
                money_available=monetary_inputs_ready,
                browser_success=True,
                wordstat_success=False,
            )
        return result
    except Exception as exc:
        if run is not None:
            _audit_run_finish(run, status="error", error_type=type(exc).__name__)
        raise
    finally:
        db.close()



@router.get("/recommendations/{audit_id}")
def audit_recommendations(audit_id: str, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    audit = _storage_get("viral_audit", f"audit:{audit_id}")
    if not audit or str(audit.get("owner_account_id") or "") != account_id:
        raise HTTPException(status_code=404, detail="Аудит не найден")

    reasons = list(audit.get("reasons") or [])
    kinds = [str(x.get("kind") or "") for x in reasons]
    score = audit.get("score") or {}
    breakdown = score.get("breakdown") or {}

    recommendations = []
    if "unanswered" in kinds or int(((breakdown.get("unanswered") or {}).get("score") or 20)) < 14:
        recommendations.append({
            "code": "unanswered",
            "title": "Разобрать клиентов без ответа",
            "description": "Начните с обращений, которые уже требуют внимания.",
            "href": "/messages",
            "priority": 1,
        })
    if "response" in kinds or int(((breakdown.get("response_speed") or {}).get("score") or 20)) < 14:
        recommendations.append({
            "code": "slow_response",
            "title": "Ускорить первый ответ",
            "description": "AI-менеджер и сценарии сообщений помогут сократить время реакции.",
            "href": "/messages",
            "priority": 2,
        })
    if int(((breakdown.get("conversion") or {}).get("score") or 15)) < 11:
        recommendations.append({
            "code": "weak_ads",
            "title": "Проверить объявления",
            "description": "Посмотрите слабые объявления и гипотезы по конверсии.",
            "href": "/dashboard/campaign",
            "priority": 3,
        })
    if int(((breakdown.get("followup") or {}).get("score") or 10)) < 7:
        recommendations.append({
            "code": "followup",
            "title": "Проверить повторную работу с клиентами",
            "description": "Посмотрите сделки и задачи, которые требуют продолжения.",
            "href": "/dashboard/crm",
            "priority": 4,
        })
    if "reactivation" in kinds:
        recommendations.append({
            "code": "reactivation",
            "title": "Проверить клиентов для возврата",
            "description": "Сначала посмотрите найденных клиентов; массовые сообщения не запускаются автоматически.",
            "href": "/reactivation",
            "priority": 5,
        })
    if int(((breakdown.get("kpi") or {}).get("score") or 10)) < 7:
        recommendations.append({
            "code": "kpi_gap",
            "title": "Проверить выполнение плана",
            "description": "Сопоставьте цель и фактический поток обращений.",
            "href": "/dashboard/analytics",
            "priority": 6,
        })

    seen = set()
    clean = []
    for item in sorted(recommendations, key=lambda x: int(x.get("priority") or 99)):
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        clean.append(item)
    return {"status": "ok", "audit_id": audit_id, "recommendations": clean[:4]}


@router.post("/audit/{audit_id}/ask")
def audit_ask(audit_id: str, payload: AuditQuestionInput, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    _persistent_rate_limit(account_id, "audit_ask", 60)
    audit_data = _storage_get("viral_audit", f"audit:{audit_id}")
    if not audit_data or str(audit_data.get("owner_account_id") or "") != account_id:
        raise HTTPException(status_code=404, detail="Аудит не найден")
    recommendations = audit_recommendations(audit_id, user).get("recommendations") or []
    from app.services.audit_explainer import build_answer
    result = build_answer(audit_data, payload.question, recommendations)
    return {"status": "ok", **result}


@router.get("/audit/{audit_id}")
def audit(audit_id: str, request: Request, user=Depends(get_current_user)):
    _rate_limit(request, "public_report", 120)
    data = _storage_get("viral_audit", f"audit:{audit_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Аудит не найден")
    if str(data.get("owner_account_id") or "") != _gateway_account_id(user):
        raise HTTPException(status_code=404, detail="Аудит не найден")
    # Authenticated audit retrieval is still client-safe. Full source inputs,
    # account identity and monetary assumptions are never returned by this route.
    raw_loss = data.get("loss") or {}
    raw_estimated = raw_loss.get("estimated") or {}
    raw_assumptions = raw_loss.get("assumptions") or {}
    confirmed = raw_loss.get("confirmed") or {}
    average_check = raw_assumptions.get("average_check_rub")
    if average_check is None:
        average_check = raw_assumptions.get("average_check_rub_declared")
    sales_conversion = raw_assumptions.get("conversion_percent")
    if sales_conversion is None:
        sales_conversion = raw_assumptions.get("sales_conversion_percent")
    if sales_conversion is None:
        sales_conversion = raw_assumptions.get("sales_conversion_percent_declared")
    problem_leads = (
        confirmed.get("currently_or_never_unanswered")
        or confirmed.get("unanswered_leads")
        or raw_estimated.get("recoverable_leads")
        or 0
    )
    account_id = str(data.get("owner_account_id") or "")
    gateway = _storage_get(account_id, "viral_gateway_state") or {}
    market_id = str(gateway.get("latest_market_audit_id") or "")
    market_result = _storage_get(account_id, f"market_result:{market_id}") if market_id else None
    public_market = _public_market_payload(market_result)
    data_status = _client_data_status(account_id, market_id, market_result)
    client_view = _client_payload(
        data,
        company=_safe_company(account_id),
        market=public_market,
        data_status=data_status,
    )
    return {
        "status": "ok",
        **client_view,
        "audit_id": audit_id,
        "created_at": data.get("created_at"),
        "account_id": data.get("account_id") or data.get("owner_account_id"),
        "score": data.get("score"),
        "loss": {
            "confirmed": raw_loss.get("confirmed") or {},
            "estimated": {
                "recoverable_leads": int(raw_estimated.get("recoverable_leads") or 0),
                "lost_revenue_low": int(raw_estimated.get("lost_revenue_low") or 0) if raw_estimated.get("lost_revenue_low") is not None else None,
                "lost_revenue_high": int(raw_estimated.get("lost_revenue_high") or 0) if raw_estimated.get("lost_revenue_high") is not None else None,
                "money_available": bool(raw_estimated.get("money_available", raw_estimated.get("lost_revenue_low") is not None)),
            },
            "loss_formula_version": raw_loss.get("loss_formula_version"),
            "calculation_basis": {
                "problem_leads": int(problem_leads or 0),
                "sales_conversion_percent": float(sales_conversion or 0),
                "average_check_rub": float(average_check or 0),
                "range_low_multiplier": 0.70,
                "range_high_multiplier": 1.30,
            },
            "disclaimer": raw_loss.get("disclaimer") or "Это расчётный диапазон, а не подтверждённая потеря выручки.",
        },
        "crm": data.get("crm") or None,
        "sources": data.get("sources") or [],
        "reasons": data.get("reasons", []),
        "cta": data.get("cta"),
        "share_available": bool(data.get("share_available")),
    }


@router.get("/score/{audit_id}")
def score(audit_id: str, request: Request, user=Depends(get_current_user)):
    _rate_limit(request, "public_report", 120)
    data = _storage_get("viral_audit", f"audit:{audit_id}")
    if not data or str(data.get("owner_account_id") or "") != _gateway_account_id(user):
        raise HTTPException(status_code=404, detail="Аудит не найден")
    return {"status": "ok", "audit_id": audit_id, "score": data.get("score")}


@router.post("/share")
def create_share(payload: ShareInput, request: Request, user=Depends(get_current_user)):
    _rate_limit(request, "share", 10)
    account_id = _gateway_account_id(user)
    _persistent_rate_limit(account_id, "share", 10)
    audit = _storage_get("viral_audit", f"audit:{payload.audit_id}")
    if not audit or str(audit.get("owner_account_id") or "") != account_id:
        raise HTTPException(status_code=404, detail="Аудит не найден")

    token = secrets.token_urlsafe(24)
    revoke_secret = secrets.token_urlsafe(32)
    ttl_days = max(1, min(int(os.getenv("BORIS_VIRAL_SHARE_TTL_DAYS", "30")), 365))
    now = _now()
    gateway = _storage_get(account_id, "viral_gateway_state") or {}
    market_id = str(gateway.get("latest_market_audit_id") or "")
    market_result = _storage_get(account_id, f"market_result:{market_id}") if market_id else None
    safe_market = _public_market_payload(market_result)
    company = _safe_company(account_id)
    data_status = _client_data_status(account_id, market_id, market_result)
    referral_code = ""
    try:
        referral_code = str((referral_info(user) or {}).get("referral_code") or "")
    except Exception:
        referral_code = ""
    client = _client_payload(
        audit,
        company=company,
        market=safe_market,
        data_status=data_status,
        referral_code=referral_code,
    )
    public = {
        "status": "ok",
        "version": 2,
        "token": token,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
        "revoked_at": None,
        "scope": "client_safe_audit_v2",
        "score": audit.get("score"),
        "market": safe_market,
        "loss": audit.get("loss"),
        "reasons": _public_loss_points(audit),
        "company": company,
        "client": client,
        "referral_code": referral_code,
        # Internal hydration references are persisted but never returned publicly.
        "_owner_account_id": account_id,
        "_market_audit_id": market_id,
        "cta": {"label": "Проверить свой бизнес", "href": f"/r/{referral_code}" if referral_code else "/audit"},
        "robots": "noindex,nofollow",
        "revoke_secret_hash": hashlib.sha256(revoke_secret.encode("utf-8")).hexdigest(),
    }
    _storage_put("viral_public", f"report:{token}", public)
    event_id = "ve_" + uuid.uuid4().hex
    _storage_put("viral_event", f"event:{event_id}", {
        "event_id": event_id,
        "event": "viral_report_shared",
        "account_id": account_id,
        "referral_code": referral_code or None,
        "source": "audit_result",
        "created_at": now.isoformat(),
    })
    return {
        "status": "ok",
        "token": token,
        "public_url": f"/v/{token}",
        "expires_at": public["expires_at"],
        "revoke_secret": revoke_secret,
        "referral_path": f"/r/{referral_code}" if referral_code else "",
    }


@router.get("/public/{token}")
def public_report(token: str, request: Request, response: Response):
    _rate_limit(request, "public_report", 120)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    data = _storage_get("viral_public", f"report:{token}")
    if not data:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    if data.get("revoked_at"):
        raise HTTPException(status_code=404, detail="Отчёт недоступен")
    try:
        if datetime.fromisoformat(data["expires_at"]) < _now():
            raise HTTPException(status_code=404, detail="Срок действия отчёта истёк")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Отчёт недоступен")

    # A shared audit becomes readable as soon as the base audit is ready. Market
    # enrichment is hydrated independently and never blocks the public page.
    account_id = str(data.get("_owner_account_id") or "")
    market_id = str(data.get("_market_audit_id") or "")
    market_result = _storage_get(account_id, f"market_result:{market_id}") if account_id and market_id else None
    safe_market = _public_market_payload(market_result) if market_result else data.get("market")
    data_status = (
        _client_data_status(account_id, market_id, market_result)
        if account_id else ((data.get("client") or {}).get("data_status") or {
            "state": "READY", "progress_percent": 100, "sections": [], "updated_at": _now().isoformat(),
        })
    )
    client = _client_payload(
        data,
        company=data.get("company") or {"name": "Ваш бизнес"},
        market=safe_market if isinstance(safe_market, dict) else None,
        data_status=data_status,
        referral_code=str(data.get("referral_code") or ""),
    )
    if account_id and market_result and data.get("market") != safe_market:
        data["market"] = safe_market
        data["client"] = client
        _storage_put("viral_public", f"report:{token}", data)

    raw_loss = data.get("loss") or {}
    raw_estimated = raw_loss.get("estimated") or {}
    safe_loss = {
        "estimated": {
            "recoverable_leads": int(raw_estimated.get("recoverable_leads") or 0),
            "lost_revenue_low": int(raw_estimated.get("lost_revenue_low") or 0) if raw_estimated.get("lost_revenue_low") is not None else None,
            "lost_revenue_high": int(raw_estimated.get("lost_revenue_high") or 0) if raw_estimated.get("lost_revenue_high") is not None else None,
            "money_available": bool(raw_estimated.get("money_available", raw_estimated.get("lost_revenue_low") is not None)),
        },
        "disclaimer": raw_loss.get("disclaimer") or "Это расчётный диапазон, а не подтверждённая потеря выручки.",
    }
    contact = sales_contact()
    contacts = {
        "contact_name": contact.get("contact_name") or "Кирилл О.",
        "contact_title": contact.get("contact_title") or "Разработчик BORIS",
        "whatsapp_url": contact.get("whatsapp_url") or "",
        "max_url": contact.get("max_url") or "",
        "trial_enabled": bool(contact.get("trial_enabled", True)),
        "trial_days": int(contact.get("trial_days") or 4),
        "trial_title": contact.get("trial_title") or "Попробовать BORIS 4 дня бесплатно",
        "boris_url": "https://boris-ai.pro/go/boris",
    }
    return {
        "status": "ok",
        **client,
        "cta": data.get("cta") or {"label": "Проверить свой бизнес", "href": "/audit"},
        "contacts": contacts,
        "share": {
            "token": token,
            "path": f"/v/{token}",
            "referral_path": f"/r/{str(data.get('referral_code') or '')}" if data.get("referral_code") else "/audit",
        },
        "created_at": data.get("created_at"),
        "expires_at": data.get("expires_at"),
        "robots": "noindex,nofollow",
    }


@router.post("/share/{token}/revoke")
def revoke_share(token: str, payload: RevokeInput, request: Request):
    _rate_limit(request, "share", 10)
    data = _storage_get("viral_public", f"report:{token}")
    if not data:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    actual = hashlib.sha256(payload.revoke_secret.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(actual, str(data.get("revoke_secret_hash") or "")):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    data["revoked_at"] = _now().isoformat()
    _storage_put("viral_public", f"report:{token}", data)
    return {"status": "ok", "revoked": True}




def _gateway_account_id(user) -> str:
    account_id = str(getattr(user, "account_id", "") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Для этого пользователя нет клиентского аккаунта")
    return account_id


@router.get("/gateway/state")
def gateway_state(user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    state = _storage_get(account_id, "viral_gateway_state") or {
        "stage": "REGISTERED",
        "audit_completed": False,
        "latest_audit_id": None,
        "marketing_opt_in": False,
        "updated_at": _now().isoformat(),
    }
    return {"status": "ok", "state": state}


@router.post("/gateway/state")
def gateway_state_update(payload: GatewayStateInput, user=Depends(get_current_user)):
    allowed_stages = {
        "REGISTERED", "AUDIT_STARTED", "MANUAL_AUDIT_COMPLETED",
        "SOURCE_CONNECTION_STARTED", "AVITO_CONNECTED",
        "CONNECTED_AUDIT_COMPLETED", "MARKET_AUDIT_COMPLETED",
        "FULL_AUDIT_COMPLETED", "TRIAL_INTEREST", "TRIAL_STARTED",
        "MAIN_BORIS_ENTERED", "SPECIALIST_CONTACTED", "PAID",
    }
    if payload.stage not in allowed_stages:
        raise HTTPException(status_code=400, detail="Некорректный этап Audit Gateway")
    account_id = _gateway_account_id(user)
    current = _storage_get(account_id, "viral_gateway_state") or {}
    current.update({
        "stage": payload.stage,
        "updated_at": _now().isoformat(),
    })
    if payload.contact_name is not None:
        current["contact_name"] = payload.contact_name.strip()
    if payload.niche is not None:
        current["niche"] = payload.niche.strip()
    if payload.region is not None:
        current["region"] = payload.region.strip()
    if payload.marketing_opt_in is not None:
        current["marketing_opt_in"] = bool(payload.marketing_opt_in)
    attribution = dict(current.get("first_touch") or {})
    for key, value in {
        "session_id": payload.session_id,
        "utm_source": payload.utm_source,
        "utm_medium": payload.utm_medium,
        "utm_campaign": payload.utm_campaign,
        "utm_content": payload.utm_content,
        "utm_term": payload.utm_term,
        "landing_path": payload.landing_path,
        "first_seen_at": payload.first_seen_at,
        "referral_code": payload.referral_code,
    }.items():
        clean = str(value or "").strip()
        if clean and key not in attribution:
            attribution[key] = clean
    if payload.qa is not None and "qa" not in attribution:
        attribution["qa"] = bool(payload.qa)
    if attribution:
        current["first_touch"] = attribution
    _storage_put(account_id, "viral_gateway_state", current)
    if payload.stage in {"TRIAL_STARTED", "SPECIALIST_CONTACTED", "PAID"}:
        _cancel_pending_audit_nurture(account_id, payload.stage)
    referral_reward = None
    if payload.stage in {"AVITO_CONNECTED", "CONNECTED_AUDIT_COMPLETED", "FULL_AUDIT_COMPLETED"}:
        referral_reward = _maybe_grant_referral_reward(int(getattr(user, "id", 0) or 0), "account_connected")
    event_base = {
        "stage": payload.stage,
        "user_id": int(getattr(user, "id", 0) or 0) or None,
        "account_id": account_id,
        "session_id": attribution.get("session_id"),
        "referral_code": attribution.get("referral_code"),
        "utm_source": attribution.get("utm_source"),
        "utm_medium": attribution.get("utm_medium"),
        "utm_campaign": attribution.get("utm_campaign"),
        "utm_content": attribution.get("utm_content"),
        "utm_term": attribution.get("utm_term"),
        "landing_path": attribution.get("landing_path"),
        "first_seen_at": attribution.get("first_seen_at") or attribution.get("captured_at"),
        "qa": bool(attribution.get("qa")),
        "created_at": _now().isoformat(),
    }
    event_id = "ve_" + uuid.uuid4().hex
    _storage_put("viral_event", f"event:{event_id}", {
        "event_id": event_id,
        "event": "gateway_stage",
        **event_base,
    })
    if payload.stage in {"CONNECTED_AUDIT_COMPLETED", "FULL_AUDIT_COMPLETED"}:
        connected_event_id = "ve_" + uuid.uuid4().hex
        _storage_put("viral_event", f"event:{connected_event_id}", {
            "event_id": connected_event_id,
            "event": "connected_audit",
            **event_base,
        })
    return {"status": "ok", "state": current, "referral_reward": referral_reward}


@router.get("/gateway/email-settings")
def gateway_email_settings(user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    state = _storage_get(account_id, "viral_gateway_state") or {}
    return {"status": "ok", "marketing_opt_in": bool(state.get("marketing_opt_in"))}


@router.post("/gateway/email-settings")
def gateway_email_settings_update(payload: EmailSettingsInput, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    state = _storage_get(account_id, "viral_gateway_state") or {}
    state["marketing_opt_in"] = bool(payload.marketing_opt_in)
    state["updated_at"] = _now().isoformat()
    _storage_put(account_id, "viral_gateway_state", state)
    if not payload.marketing_opt_in:
        _cancel_pending_audit_nurture(account_id, "marketing_opt_out")
    return {"status": "ok", "marketing_opt_in": bool(payload.marketing_opt_in)}


@router.get("/gateway/context")
def gateway_context(user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    context = _storage_get(account_id, "viral_gateway_context") or {}
    return {"status": "ok", "context": context}


@router.post("/gateway/complete")
def gateway_complete(payload: GatewayCompleteInput, user=Depends(get_current_user)):
    account_id = _gateway_account_id(user)
    audit = _storage_get("viral_audit", f"audit:{payload.audit_id}")
    if not audit or str(audit.get("owner_account_id") or "") != account_id:
        raise HTTPException(status_code=404, detail="Аудит не найден")

    current = _storage_get(account_id, "viral_gateway_state") or {}
    audit_mode = str(((audit.get("score") or {}).get("mode") or "manual"))
    market_already_completed = bool(current.get("market_audit_completed"))
    completed_stage = (
        "FULL_AUDIT_COMPLETED"
        if market_already_completed
        else ("CONNECTED_AUDIT_COMPLETED" if audit_mode == "connected" else "MANUAL_AUDIT_COMPLETED")
    )
    current.update({
        "stage": completed_stage,
        "audit_completed": True,
        "latest_audit_id": payload.audit_id,
        "latest_score": int(((audit.get("score") or {}).get("total") or 0)),
        "updated_at": _now().isoformat(),
    })
    _assumptions = ((audit.get("loss") or {}).get("assumptions") or {})
    if _assumptions.get("average_check_rub") is not None and _assumptions.get("monetary_source") != "crm_actual":
        current["declared_average_check_rub"] = float(_assumptions.get("average_check_rub") or 0)
    if _assumptions.get("conversion_percent") is not None:
        current["declared_sales_conversion_percent"] = float(_assumptions.get("conversion_percent") or 0)
    if _assumptions.get("average_check_rub_declared") is not None:
        current["declared_average_check_rub"] = float(_assumptions.get("average_check_rub_declared") or 0)
    if _assumptions.get("sales_conversion_percent") is not None and _assumptions.get("monetary_source") != "crm_actual":
        current["declared_sales_conversion_percent"] = float(_assumptions.get("sales_conversion_percent") or 0)
    if _assumptions.get("sales_conversion_percent_declared") is not None:
        current["declared_sales_conversion_percent"] = float(_assumptions.get("sales_conversion_percent_declared") or 0)
    _confirmed = ((audit.get("loss") or {}).get("confirmed") or {})
    if audit_mode == "manual":
        if _confirmed.get("avg_response_minutes") is not None:
            current["declared_avg_response_minutes"] = float(_confirmed.get("avg_response_minutes") or 0)
        if _confirmed.get("unanswered_leads") is not None:
            current["declared_unanswered_leads"] = int(_confirmed.get("unanswered_leads") or 0)
    history = list(current.get("history") or [])
    history.append({
        "audit_id": payload.audit_id,
        "score": int(((audit.get("score") or {}).get("total") or 0)),
        "mode": audit_mode,
        "score_version": str(((audit.get("score") or {}).get("score_version") or ("connected_v1" if audit_mode == "connected" else "manual_v1"))),
        "created_at": audit.get("created_at") or _now().isoformat(),
    })
    current["history"] = history[-24:]
    _storage_put(account_id, "viral_gateway_state", current)
    if market_already_completed:
        market_audit_id = str(current.get("latest_market_audit_id") or "").strip()
        market_result = _storage_get(account_id, f"market_result:{market_audit_id}") if market_audit_id else None
        if market_audit_id and market_result:
            _send_full_audit_email(account_id, market_audit_id, market_result)

    _context = {
        "source": "audit_gateway",
        "audit_id": payload.audit_id,
        "score": int(((audit.get("score") or {}).get("total") or 0)),
        "score_version": str(((audit.get("score") or {}).get("score_version") or ("connected_v1" if audit_mode == "connected" else "manual_v1"))),
        "main_problems": [str(x.get("kind") or "") for x in (audit.get("reasons") or []) if x.get("kind")][:3],
        "completed_at": audit.get("created_at") or _now().isoformat(),
        "connected": audit_mode == "connected",
        "market_audit_completed": market_already_completed,
        "market_score": current.get("latest_market_score"),
        "market_demand_level": current.get("market_demand_level"),
    }
    _storage_put(account_id, "viral_gateway_context", _context)

    email_sent = False
    email_key = f"audit_email:{payload.audit_id}:result"
    already_sent = _storage_get(account_id, email_key)
    if not already_sent:
        try:
            email = str(getattr(user, "email", "") or "").strip()
            if email:
                score = int(((audit.get("score") or {}).get("total") or 0))
                estimated = ((audit.get("loss") or {}).get("estimated") or {})
                low = int(estimated.get("lost_revenue_low") or 0)
                high = int(estimated.get("lost_revenue_high") or 0)
                url = f"https://boris-ai.pro/audit/result?audit={payload.audit_id}"
                subject = "BORIS закончил анализ реальных обращений" if audit_mode == "connected" else "Ваш разбор бизнеса BORIS готов"
                previous = ((audit.get("score") or {}).get("previous_score"))
                if audit_mode == "connected":
                    comparison = (
                        f"Предварительная оценка: {int(previous)}/100.\n"
                        f"Оценка по реальным данным: {score}/100.\n\n"
                        if previous is not None else
                        f"Оценка по реальным данным: {score}/100.\n\n"
                    )
                    tail = (
                        "Результат пересчитан по фактическим обращениям BORIS и доступной статистике Avito. "
                        "AI эти цифры не меняет."
                    )
                else:
                    comparison = f"Как сейчас работают продажи: {score}/100.\n\n"
                    tail = "Следующий шаг — подключить реальные данные Avito, чтобы BORIS пересчитал результат по обращениям и рекламе."
                body = (
                    comparison
                    + f"Расчётный диапазон потенциальных потерь: {low:,}–{high:,} ₽ в месяц.\n"
                    + "Это оценка, а не подтверждённая потеря выручки.\n\n"
                    + f"Открыть аудит: {url}\n\n"
                    + tail
                ).replace(",", " ")
                html = f"""<!doctype html><html><body style="margin:0;background:#f6f8fc;font-family:Arial,Helvetica,sans-serif;color:#15213b">
                <div style="max-width:640px;margin:0 auto;padding:28px 16px">
                  <div style="font-size:20px;font-weight:800;margin-bottom:18px">BORIS <span style="color:#2878ff">AUDIT</span></div>
                  <div style="background:#fff;border:1px solid #dfe6f2;border-radius:18px;padding:24px">
                    <div style="font-size:12px;font-weight:800;color:#8490a6;text-transform:uppercase;letter-spacing:.08em">Как сейчас работают продажи</div>
                    <div style="font-size:44px;font-weight:800;line-height:1.1;margin-top:8px">{score}<span style="font-size:18px;color:#8490a6">/100</span></div>
                    {"<div style='margin-top:8px;font-size:14px;color:#53617a'>Предварительно: "+str(int(previous))+"/100 → по реальным данным: "+str(score)+"/100</div>" if audit_mode == "connected" and previous is not None else ""}
                    <div style="margin-top:22px;padding:18px;background:#f8faff;border-radius:14px">
                      <div style="font-size:13px;color:#53617a">Расчётный диапазон потенциальных потерь</div>
                      <div style="font-size:28px;font-weight:800;margin-top:6px">{low:,}–{high:,} ₽</div>
                      <div style="font-size:12px;color:#8490a6;margin-top:6px">Это оценка, а не подтверждённая потеря выручки.</div>
                    </div>
                    <a href="{url}" style="display:inline-block;margin-top:22px;background:#2878ff;color:#fff;text-decoration:none;font-weight:800;padding:13px 20px;border-radius:10px">Открыть аудит</a>
                  </div>
                  <div style="font-size:12px;color:#8490a6;margin-top:14px">BORIS показывает факты отдельно от расчётных оценок.</div>
                </div></body></html>""".replace(",", " ")
                from app.services import email_service as _email_service
                sent_ok, sent_reason, _message_id = _email_service.send_email(
                    email, subject, body, html=html, tenant_scope="audit:result"
                )
                email_sent = bool(sent_ok)
                if email_sent:
                    _storage_put(account_id, email_key, {
                        "audit_id": payload.audit_id,
                        "email_type": "audit_result",
                        "campaign_type": "AUDIT_TRANSACTIONAL",
                        "sent_at": _now().isoformat(),
                    })
        except Exception:
            email_sent = False
    else:
        email_sent = True

    nurture = []
    if audit_mode == "manual":
        try:
            nurture = _schedule_manual_audit_nurture(user, account_id, audit)
        except Exception:
            nurture = []
    else:
        _cancel_pending_audit_nurture(account_id, "connected_audit_completed")

    return {"status": "ok", "state": current, "email_sent": email_sent, "nurture_scheduled": len(nurture)}



def _require_owner(user) -> None:
    if str(getattr(user, "role", "") or "").lower() != "owner":
        raise HTTPException(status_code=403, detail="Только владелец BORIS")


def _attribution_source_label(first_touch: dict[str, Any] | None) -> str:
    touch = first_touch or {}
    if str(touch.get("referral_code") or "").strip():
        return "Referral"
    source = str(touch.get("utm_source") or "").strip().lower()
    campaign = str(touch.get("utm_campaign") or "").strip().lower()
    if campaign == "ocean":
        if source in {"tg", "telegram"}:
            return "Ocean TG"
        if source in {"vk", "vkontakte", "вк"}:
            return "Ocean VK"
        return "Ocean"
    if source == "email":
        return "Email"
    if source in {"", "direct", "(direct)", "none"}:
        return "Direct"
    return "Other"


def _safe_rate(numerator: int, denominator: int, min_denominator: int = 30) -> Optional[float]:
    if denominator < min_denominator:
        return None
    return round((float(numerator) / float(denominator)) * 100.0, 1)


@router.get("/owner/ocean-funnel")
def owner_ocean_funnel(user=Depends(get_current_user)):
    """OWNER-only Ocean acquisition funnel, deduped by audit session/account.

    QA, localhost and obvious bot traffic are excluded from commercial metrics.
    Conversion percentages stay hidden until the denominator reaches 30.
    """
    _require_owner(user)
    db = SessionLocal()
    try:
        state_rows = db.query(Storage).filter(Storage.key == "viral_gateway_state").all()
        states: dict[str, dict[str, Any]] = {}
        first_touches: dict[str, dict[str, Any]] = {}
        session_to_account: dict[str, str] = {}
        qa_accounts: set[str] = set()
        qa_sessions: set[str] = set()

        def qa_session(session_id: str) -> bool:
            return session_id.strip().lower().startswith("qa_")

        def touch_time(touch: dict[str, Any], fallback: Any = None) -> Optional[datetime]:
            for value in (touch.get("first_seen_at"), touch.get("captured_at"), touch.get("created_at"), fallback):
                parsed = _parse_datetime_utc(value)
                if parsed is not None:
                    return parsed
            return None

        for row in state_rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                value = {}
            if not isinstance(value, dict):
                continue
            account_id = str(row.account_id or "")
            states[account_id] = value
            first_touch = value.get("first_touch") if isinstance(value.get("first_touch"), dict) else {}
            if first_touch:
                first_touches[account_id] = first_touch
            session_id = str(first_touch.get("session_id") or "").strip()
            if session_id:
                session_to_account[session_id] = account_id
            if bool(first_touch.get("qa")) or qa_session(session_id):
                # Only the account's own durable QA acquisition excludes the
                # account. A separate QA event/session must not taint it.
                qa_accounts.add(account_id)
                if session_id:
                    qa_sessions.add(session_id)

        def excluded_path(session_id: str, account_id: str) -> bool:
            if qa_session(session_id) or session_id in qa_sessions or account_id in qa_accounts:
                return True
            durable = first_touches.get(account_id)
            # A later Ocean visit does not change an existing acquisition from
            # another campaign (including a recorded Direct first-touch).
            return durable is not None and str(durable.get("utm_campaign") or "").strip().lower() != "ocean"

        event_rows = db.query(Storage).filter(Storage.account_id == "viral_event").all()
        raw_events: list[dict[str, Any]] = []
        for row in event_rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                continue
            if not isinstance(value, dict):
                continue
            # Explicit QA marks this session even when this particular event
            # omitted campaign. It must not quarantine the event's account.
            if bool(value.get("qa")):
                marked_session = str(value.get("session_id") or "").strip()
                if marked_session:
                    qa_sessions.add(marked_session)
            if str(value.get("utm_campaign") or "").strip().lower() != "ocean":
                continue
            raw_events.append(value)

        # Resolve the same session's authenticated account before processing
        # its anonymous events. The durable state remains the primary link.
        for event in raw_events:
            session_id = str(event.get("session_id") or "").strip()
            account_id = str(event.get("account_id") or "").strip()
            if session_id and account_id:
                session_to_account.setdefault(session_id, account_id)

        events: list[dict[str, Any]] = []
        for value in raw_events:
            if bool(value.get("qa")) or bool(value.get("is_local")) or bool(value.get("is_bot")):
                continue
            session_id = str(value.get("session_id") or "").strip()
            account_id = str(value.get("account_id") or session_to_account.get(session_id) or "").strip()
            if excluded_path(session_id, account_id):
                continue
            events.append(value)

        account_ids = {
            str(account_id)
            for account_id, state in states.items()
            if account_id in first_touches and not excluded_path(
                str(first_touches[account_id].get("session_id") or "").strip(), account_id)
        }
        account_ids.update(
            str(e.get("account_id") or session_to_account.get(str(e.get("session_id") or ""), "") or "")
            for e in events
        )
        account_ids.discard("")

        users_by_account: dict[str, Any] = {}
        accounts_by_id: dict[str, Any] = {}
        if account_ids:
            for item in db.query(User).filter(User.account_id.in_(account_ids)).all():
                users_by_account.setdefault(str(getattr(item, "account_id", "") or ""), item)
            for item in db.query(Account).filter(Account.account_id.in_(account_ids)).all():
                accounts_by_id[str(getattr(item, "account_id", "") or "")] = item

        sessions: dict[str, dict[str, Any]] = {}
        attribution_rank: dict[str, tuple] = {}

        def ensure_session(
            session_id: str,
            account_id: str = "",
            touch: Optional[dict[str, Any]] = None,
            created_at: Optional[str] = None,
        ) -> dict[str, Any]:
            key = session_id or ("account:" + account_id if account_id else "unknown")
            item = sessions.setdefault(key, {
                "session_id": session_id or None,
                "account_id": account_id or None,
                "source": _attribution_source_label(touch),
                "campaign": "ocean",
                "post": str((touch or {}).get("utm_content") or ""),
                "date": created_at,
                "visit": False,
                "audit": False,
                "registration": False,
                "result": False,
                "trial": False,
                "connected": False,
            })
            if account_id and not item.get("account_id"):
                item["account_id"] = account_id
            durable = first_touches.get(account_id)
            chosen = durable if durable is not None else (touch or {})
            stamp = touch_time(chosen, (states.get(account_id) or {}).get("registered_at") if durable is not None else created_at)
            if durable is not None:
                # Milestones may arrive with a new campaign/post or no UTM.
                # Their touch cannot replace the durable source/post pair.
                item.update(source=_attribution_source_label(chosen),
                            post=str(chosen.get("utm_content") or ""),
                            date=stamp.isoformat() if stamp is not None else None)
            elif stamp is not None:
                order = (stamp, _parse_datetime_utc(chosen.get("created_at")) or stamp,
                         str(chosen.get("event_id") or ""),
                         json.dumps({"source": _attribution_source_label(chosen),
                                     "post": str(chosen.get("utm_content") or ""),
                                     "referral": str(chosen.get("referral_code") or "").strip()},
                                    ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                if key not in attribution_rank or order < attribution_rank[key]:
                    attribution_rank[key] = order
                    item.update(source=_attribution_source_label(chosen),
                                post=str(chosen.get("utm_content") or ""), date=stamp.isoformat())
            return item

        # Registered states provide durable account/session linkage even when an
        # older browser event did not carry Authorization.
        for account_id, state in states.items():
            first_touch = first_touches.get(account_id)
            if first_touch is None:
                continue
            session_id = str(first_touch.get("session_id") or "").strip()
            if excluded_path(session_id, account_id):
                continue
            item = ensure_session(
                session_id,
                account_id,
                first_touch,
                str(first_touch.get("first_seen_at") or first_touch.get("captured_at") or state.get("registered_at") or ""),
            )
            item["registration"] = True
            item["audit"] = bool(state.get("audit_completed")) or str(state.get("stage") or "") not in {"", "REGISTERED"}
            item["result"] = bool(state.get("audit_completed"))
            stage = str(state.get("stage") or "")
            item["trial"] = bool(state.get("trial_interest")) or stage in {"TRIAL_INTEREST", "TRIAL_STARTED", "PAID"}
            item["connected"] = (
                stage in {"AVITO_CONNECTED", "CONNECTED_AUDIT_COMPLETED", "FULL_AUDIT_COMPLETED"}
                or any(str(h.get("mode") or "").lower() == "connected" for h in (state.get("history") or []) if isinstance(h, dict))
            )

        for event in events:
            session_id = str(event.get("session_id") or "").strip()
            account_id = str(event.get("account_id") or session_to_account.get(session_id) or "").strip()
            touch = {
                "utm_source": event.get("utm_source"),
                "utm_campaign": event.get("utm_campaign"),
                "utm_content": event.get("utm_content"),
                "referral_code": event.get("referral_code"),
                "first_seen_at": event.get("first_seen_at"),
                "created_at": event.get("created_at"),
                "event_id": event.get("event_id"),
            }
            # An unlinked, untimed event cannot establish a first acquisition.
            # A durable mapped first-touch still permits its later milestones.
            if account_id not in first_touches and touch_time(touch) is None:
                continue
            item = ensure_session(session_id, account_id, touch, str(event.get("created_at") or ""))
            name = str(event.get("event") or "")
            stage = str(event.get("stage") or "")
            if name in {"audit_view", "viral_page_view"}:
                item["visit"] = True
            if name in {"audit_start", "viral_calculator_started", "market_audit_started"}:
                item["audit"] = True
            if name in {"registration_completed", "viral_registration"}:
                item["registration"] = True
            if name in {"market_audit_completed", "audit_result_view", "audit_completed_view"}:
                item["result"] = True
            if name in {"trial_cta_click", "trial_cta_clicked", "trial_interest"}:
                item["trial"] = True
            if name == "connected_audit" or (name == "gateway_stage" and stage in {"CONNECTED_AUDIT_COMPLETED", "FULL_AUDIT_COMPLETED"}):
                item["connected"] = True

        rows: list[dict[str, Any]] = []
        for item in sessions.values():
            account_id = str(item.get("account_id") or "")
            account = accounts_by_id.get(account_id)
            client_user = users_by_account.get(account_id)
            rows.append({
                **item,
                "user": str(getattr(client_user, "email", "") or ""),
                "company": str(getattr(account, "name", "") or account_id),
            })
        rows.sort(key=lambda x: str(x.get("date") or ""), reverse=True)

        def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
            visits = sum(1 for x in items if x.get("visit"))
            audits = sum(1 for x in items if x.get("audit"))
            registrations = sum(1 for x in items if x.get("registration"))
            results = sum(1 for x in items if x.get("result"))
            trials = sum(1 for x in items if x.get("trial"))
            connected = sum(1 for x in items if x.get("connected"))
            return {
                "visits": visits,
                "audits": audits,
                "registrations": registrations,
                "results": results,
                "trial_cta": trials,
                "connected": connected,
                "rates": {
                    "visit_to_audit": _safe_rate(audits, visits),
                    "audit_to_registration": _safe_rate(registrations, audits),
                    "registration_to_result": _safe_rate(results, registrations),
                    "result_to_trial": _safe_rate(trials, results),
                    "trial_to_connected": _safe_rate(connected, trials),
                },
            }

        by_source = {
            "tg": summarize([x for x in rows if x.get("source") == "Ocean TG"]),
            "vk": summarize([x for x in rows if x.get("source") == "Ocean VK"]),
        }

        post_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in rows:
            key = (str(item.get("source") or "Ocean"), str(item.get("post") or ""))
            post_groups.setdefault(key, []).append(item)
        posts = []
        for (source, post), items in post_groups.items():
            posts.append({"source": source, "post": post, **summarize(items)})
        posts.sort(key=lambda x: (int(x.get("visits") or 0), int(x.get("registrations") or 0)), reverse=True)

        # Operational Ocean delivery alerts are kept next to the funnel so the
        # OWNER sees acquisition and the delivery state in one place. Old
        # historical posts created before the Audit CTA rollout are ignored.
        alerts: list[dict[str, Any]] = []
        ocean_projects: dict[tuple[str, str], dict[str, Any]] = {}
        for project_row in db.query(Storage).filter(Storage.key == "posting_projects").all():
            try:
                project_items = json.loads(project_row.value or "[]")
            except Exception:
                project_items = []
            for project in project_items if isinstance(project_items, list) else []:
                if not isinstance(project, dict) or "океан клиент" not in str(project.get("name") or "").lower():
                    continue
                ocean_projects[(str(project_row.account_id or ""), str(project.get("id") or ""))] = project

        offer_text = "Получите бесплатно аудит за пару минут + тест BORIS для повышения эффективности рекламы"
        rollout_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
        for post_row in db.query(Storage).filter(Storage.key == "posting_posts").all():
            account_id = str(post_row.account_id or "")
            try:
                post_items = json.loads(post_row.value or "[]")
            except Exception:
                post_items = []
            for post in post_items if isinstance(post_items, list) else []:
                if not isinstance(post, dict):
                    continue
                project = ocean_projects.get((account_id, str(post.get("project_id") or "")))
                if not project:
                    continue
                created_at = _parse_datetime_utc(post.get("created_at") or post.get("published_at"))
                if created_at and created_at < rollout_at:
                    continue
                post_id = str(post.get("id") or "")
                delivery_texts = post.get("delivery_texts") if isinstance(post.get("delivery_texts"), dict) else {}
                for platform in ("tg", "vk"):
                    text = str(delivery_texts.get(platform) or "")
                    if not text:
                        continue
                    if offer_text not in text or "https://boris-ai.pro/audit?" not in text:
                        alerts.append({
                            "severity": "critical",
                            "code": "OCEAN_CTA_MISSING",
                            "post_id": post_id,
                            "platform": platform,
                            "message": "В Ocean-публикации отсутствует канонический CTA бесплатного аудита.",
                        })
                    if text.count("https://") != 1:
                        alerts.append({
                            "severity": "critical",
                            "code": "OCEAN_URL_COUNT_INVALID",
                            "post_id": post_id,
                            "platform": platform,
                            "url_count": text.count("https://"),
                            "message": "В Ocean-публикации должна быть ровно одна коммерческая ссылка.",
                        })

                ids = post.get("ids") if isinstance(post.get("ids"), dict) else {}
                vk_failure = ((post.get("delivery_failures") or {}).get("vk") or {}) if isinstance(post.get("delivery_failures"), dict) else {}
                if ids.get("tg") and not ids.get("vk") and str(post.get("status") or "") == "partial":
                    age_hours = None
                    if created_at:
                        age_hours = round(max(0.0, (_now() - created_at).total_seconds() / 3600.0), 1)
                    if age_hours is not None and age_hours >= 6:
                        alerts.append({
                            "severity": "warning",
                            "code": "VK_PENDING_OVER_6H",
                            "post_id": post_id,
                            "age_hours": age_hours,
                            "dependency_state": str(vk_failure.get("dependency_state") or "queued"),
                            "message": "VK не завершил доставку Ocean-поста. Telegram опубликован; повтор VK выполняется автоматически.",
                        })

        return {
            "status": "ok",
            "minimum_rate_denominator": 30,
            "summary": summarize(rows),
            "by_source": by_source,
            "posts": posts[:200],
            "rows": rows[:1000],
            "alerts": alerts[:200],
        }
    finally:
        db.close()


@router.get("/owner/funnel")
def owner_funnel(user=Depends(get_current_user)):
    _require_owner(user)
    db = SessionLocal()
    try:
        states = db.query(Storage).filter(Storage.key == "viral_gateway_state").all()
        parsed_states = []
        for row in states:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                value = {}
            if isinstance(value, dict):
                parsed_states.append(value)

        def _event_count(name: str) -> int:
            needle = '"event":"' + name + '"'
            return int(db.query(Storage).filter(
                Storage.account_id == "viral_event",
                Storage.value.contains(needle),
            ).count())

        def _stage_accounts(stage: str) -> set[str]:
            rows = db.query(Storage).filter(
                Storage.account_id == "viral_event",
                Storage.value.contains('"event":"gateway_stage"'),
                Storage.value.contains('"stage":"' + stage + '"'),
            ).all()
            accounts: set[str] = set()
            for row in rows:
                try:
                    value = json.loads(row.value or "{}")
                except Exception:
                    continue
                account_id = str(value.get("account_id") or "")
                if account_id:
                    accounts.add(account_id)
            return accounts

        def _unique_stage_count(stage: str) -> int:
            return len(_stage_accounts(stage))

        audited = sum(1 for s in parsed_states if bool(s.get("audit_completed")))
        connected = sum(
            1 for s in parsed_states
            if any(str(h.get("mode") or "") == "connected" for h in (s.get("history") or []))
        )
        stages = [str(s.get("stage") or "") for s in parsed_states]
        reward_rows = db.query(Storage).filter(Storage.account_id == "viral_referral_reward").all()
        referral_rewards_granted = 0
        referral_reward_days = 0
        referral_qualified = 0
        qualified_accounts: set[str] = set()
        for reward_row in reward_rows:
            try:
                reward = json.loads(reward_row.value or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            reward_status = str(reward.get("status") or "")
            if reward_status in {"GRANTING", "GRANTED"}:
                referral_qualified += 1
                referred_account_id = str(((reward.get("metadata") or {}).get("referred_account_id") or "")).strip()
                if referred_account_id:
                    qualified_accounts.add(referred_account_id)
            if reward_status == "GRANTED":
                referral_rewards_granted += 1
                referral_reward_days += int(reward.get("reward_value") or 0)
        referral_trial_started = len(qualified_accounts & _stage_accounts("TRIAL_STARTED"))
        return {
            "status": "ok",
            "funnel": {
                "audit_page_views": _event_count("viral_page_view"),
                "registrations": _event_count("viral_registration"),
                "share_opens": _event_count("viral_share_opened"),
                "share_cta_clicks": _event_count("viral_cta_clicked"),
                "whatsapp_clicks": _event_count("whatsapp_clicked"),
                "max_clicks": _event_count("max_clicked"),
                "verified_or_entered_gateway": len(parsed_states),
                "audit_completed": audited,
                "connected_audit_completed": connected,
                "market_audit_completed": sum(1 for s in parsed_states if bool(s.get("market_audit_completed"))),
                "full_audit_completed": _unique_stage_count("FULL_AUDIT_COMPLETED"),
                "trial_cta_clicked": _event_count("trial_cta_clicked"),
                "trial_started": _unique_stage_count("TRIAL_STARTED"),
                "main_boris_entered": _unique_stage_count("MAIN_BORIS_ENTERED"),
                "specialist_contacted": _unique_stage_count("SPECIALIST_CONTACTED"),
                "paid_stage": _unique_stage_count("PAID"),
                "audit_opened": _event_count("audit_opened"),
                "audit_25_viewed": _event_count("audit_25_viewed"),
                "audit_50_viewed": _event_count("audit_50_viewed"),
                "audit_75_viewed": _event_count("audit_75_viewed"),
                "audit_completed_view": _event_count("audit_completed_view"),
                "cta_test_clicked": _event_count("cta_test_clicked"),
                "share_clicked": _event_count("share_clicked"),
                "share_completed": _event_count("share_completed"),
                "check_my_business_clicked": _event_count("check_my_business_clicked"),
                "referral_visit": _event_count("referral_visit") + _event_count("viral_referral_visit"),
                "registration_started": _event_count("registration_started"),
                "registration_completed": _event_count("registration_completed") + _event_count("viral_registration"),
                "referral_qualified": referral_qualified,
                "referral_trial_started": referral_trial_started,
                "referral_rewards_granted": referral_rewards_granted,
                "referral_reward_days": referral_reward_days,
            },
        }
    finally:
        db.close()



@router.get("/owner/audits")
def owner_audits(
    period: str = "30d",
    niche: Optional[str] = None,
    city: Optional[str] = None,
    referral: Optional[str] = None,
    source: Optional[str] = None,
    mode: Optional[str] = None,
    readiness: Optional[str] = None,
    user=Depends(get_current_user),
):
    """OWNER-only audit register with practical production filters."""
    _require_owner(user)
    period_key = str(period or "30d").strip().lower()
    now = _now()
    cutoff = None
    if period_key == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period_key in {"7d", "7", "week"}:
        cutoff = now - timedelta(days=7)
    elif period_key in {"30d", "30", "month"}:
        cutoff = now - timedelta(days=30)
    elif period_key in {"90d", "90"}:
        cutoff = now - timedelta(days=90)
    elif period_key not in {"all", "*"}:
        raise HTTPException(status_code=400, detail="Некорректный период")

    runs = _audit_run_rows(5000)
    db = SessionLocal()
    try:
        state_rows = db.query(Storage).filter(Storage.key == "viral_gateway_state").all()
        states: dict[str, dict[str, Any]] = {}
        for row in state_rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                value = {}
            if isinstance(value, dict):
                states[str(row.account_id or "")] = value

        account_ids = {str(r.get("account_id") or "") for r in runs if str(r.get("account_id") or "")}
        users_by_account: dict[str, Any] = {}
        accounts_by_id: dict[str, Any] = {}
        if account_ids:
            for item in db.query(User).filter(User.account_id.in_(account_ids)).all():
                users_by_account.setdefault(str(getattr(item, "account_id", "") or ""), item)
            for item in db.query(Account).filter(Account.account_id.in_(account_ids)).all():
                accounts_by_id[str(getattr(item, "account_id", "") or "")] = item

        niche_q = str(niche or "").strip().lower()
        city_q = str(city or "").strip().lower()
        referral_q = str(referral or "").strip().lower()
        source_q = str(source or "").strip().lower().replace("_", " ")
        mode_q = str(mode or "").strip().lower()
        readiness_q = str(readiness or "").strip().upper()

        rows = []
        for run in runs:
            started = _parse_datetime_utc(run.get("started_at"))
            if cutoff is not None and started is not None and started < cutoff:
                continue
            account_id = str(run.get("account_id") or "")
            state = states.get(account_id) or {}
            first_touch = state.get("first_touch") or {}
            history = list(state.get("history") or [])
            kind = str(run.get("kind") or "manual").lower()
            niche_value = str(
                run.get("query")
                or state.get("niche")
                or state.get("market_query")
                or ""
            ).strip()
            city_value = str(
                run.get("region")
                or state.get("region")
                or state.get("market_region")
                or ""
            ).strip()
            referral_code = str(first_touch.get("referral_code") or "").strip()
            source_label = _attribution_source_label(first_touch)
            campaign_value = str(first_touch.get("utm_campaign") or "").strip()
            post_value = str(first_touch.get("utm_content") or "").strip()
            run_status = str(run.get("status") or "").lower()
            result_status = str(run.get("result_status") or "").lower()
            if run_status == "error":
                readiness_value = "FAILED"
            elif run_status == "running":
                readiness_value = "COLLECTING"
            elif "partial" in result_status or "degraded" in result_status:
                readiness_value = "PARTIAL_READY"
            else:
                readiness_value = "READY"
            connected_flag = bool(
                kind == "connected"
                or any(str(item.get("mode") or "").lower() == "connected" for item in history if isinstance(item, dict))
            )
            stage = str(state.get("stage") or "REGISTERED")
            trial_interest = stage in {"TRIAL_INTEREST", "TRIAL_STARTED", "PAID"}
            is_referral = bool(referral_code)

            if niche_q and niche_q not in niche_value.lower():
                continue
            if city_q and city_q not in city_value.lower():
                continue
            if referral_q in {"referral", "yes", "true", "1"} and not is_referral:
                continue
            if referral_q in {"non_referral", "direct", "no", "false", "0"} and is_referral:
                continue
            if source_q and source_q != "all" and source_label.lower() != source_q:
                continue
            if mode_q and mode_q != "all" and kind != mode_q:
                continue
            if readiness_q and readiness_q != "ALL" and readiness_value != readiness_q:
                continue

            client_user = users_by_account.get(account_id)
            account = accounts_by_id.get(account_id)
            rows.append({
                "run_id": str(run.get("run_id") or ""),
                "audit_id": str(run.get("audit_id") or ""),
                "date": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "account_id": account_id,
                "company": str(getattr(account, "name", "") or account_id),
                "user": str(getattr(client_user, "email", "") or ""),
                "niche": niche_value,
                "city": city_value,
                "type": kind,
                "source": source_label,
                "campaign": campaign_value,
                "post": post_value,
                "status": str(run.get("status") or ""),
                "readiness": readiness_value,
                "duration_ms": run.get("duration_ms"),
                "referral": is_referral,
                "referral_code": referral_code,
                "trial_interest": trial_interest,
                "connected": connected_flag,
                "error": str(run.get("error") or run.get("error_type") or ""),
                "variable_cost_rub": float(run.get("total_variable_cost_rub") or 0),
                "updated_at": state.get("updated_at") or run.get("finished_at") or run.get("started_at"),
            })

        rows.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
        return {
            "status": "ok",
            "period": period_key,
            "total": len(rows),
            "audits": rows[:1000],
            "facets": {
                "niches": sorted({str(x.get("niche") or "") for x in rows if str(x.get("niche") or "")})[:200],
                "cities": sorted({str(x.get("city") or "") for x in rows if str(x.get("city") or "")})[:200],
                "types": sorted({str(x.get("type") or "") for x in rows if str(x.get("type") or "")}),
                "sources": sorted({str(x.get("source") or "") for x in rows if str(x.get("source") or "")}),
                "campaigns": sorted({str(x.get("campaign") or "") for x in rows if str(x.get("campaign") or "")}),
                "readiness": sorted({str(x.get("readiness") or "") for x in rows if str(x.get("readiness") or "")}),
            },
        }
    finally:
        db.close()


def _audit_market_browser_status() -> dict[str, Any]:
    account_id = str(os.getenv("BORIS_AUDIT_MARKET_BROWSER_ACCOUNT_ID", "") or "").strip()
    if not account_id:
        return {
            "configured": False,
            "status": "UNCONFIGURED",
            "online": False,
            "requires_login": False,
            "identity_verified": None,
            "age_seconds": None,
        }
    try:
        from app.api.browser_gateway import _account_gateway_state
        state = _account_gateway_state(account_id) or {}
    except Exception:
        return {
            "configured": True,
            "status": "ERROR",
            "online": False,
            "requires_login": False,
            "identity_verified": None,
            "age_seconds": None,
        }

    if state.get("identity_mismatch"):
        status = "IDENTITY_MISMATCH"
    elif state.get("requires_login"):
        status = "RELOGIN"
    elif state.get("online"):
        status = "ONLINE"
    else:
        status = "OFFLINE"

    gateway = state.get("gateway") or {}
    return {
        "configured": True,
        "status": status,
        "online": bool(state.get("online")),
        "requires_login": bool(state.get("requires_login")),
        "identity_verified": state.get("identity_verified"),
        "age_seconds": gateway.get("age_seconds"),
        "device_name": gateway.get("device_name"),
        "last_seen_at": gateway.get("last_seen_at"),
    }


@router.get("/owner/costs")
def owner_costs(user=Depends(get_current_user)):
    _require_owner(user)
    runs = _audit_run_rows(1200)
    completed = [r for r in runs if r.get("status") in {"ok", "error"} and r.get("duration_ms") is not None]
    ok_runs = [r for r in completed if r.get("status") == "ok"]

    def summarize(kind: str) -> dict[str, Any]:
        rows = [r for r in ok_runs if str(r.get("kind") or "") == kind]
        durations = [float(r.get("duration_ms") or 0) for r in rows]
        costs = [float(r.get("total_variable_cost_rub") or 0) for r in rows]
        return {
            "runs": len(rows),
            "p50_ms": round(_percentile(durations, 0.50), 1) if durations else 0.0,
            "p95_ms": round(_percentile(durations, 0.95), 1) if durations else 0.0,
            "avg_cost_rub": round(sum(costs) / len(costs), 4) if costs else 0.0,
            "max_cost_rub": round(max(costs), 4) if costs else 0.0,
        }

    market_all = [r for r in completed if str(r.get("kind") or "") == "market"]
    market_ok = [r for r in market_all if r.get("status") == "ok"]
    market_cold = [r for r in market_ok if not bool(r.get("cache_hit"))]
    cache_hits = sum(1 for r in market_ok if bool(r.get("cache_hit")))
    browser_rows = [r for r in market_cold if "browser_success" in r]
    wordstat_rows = [r for r in market_cold if "wordstat_success" in r]

    def rate(true_count: int, total: int) -> float:
        return round(true_count / total * 100.0, 1) if total else 0.0

    browser_success = rate(sum(1 for r in browser_rows if bool(r.get("browser_success"))), len(browser_rows))
    wordstat_success = rate(sum(1 for r in wordstat_rows if bool(r.get("wordstat_success"))), len(wordstat_rows))
    cache_hit_rate = rate(cache_hits, len(market_ok))
    cold_durations = [float(r.get("duration_ms") or 0) for r in market_cold]
    all_costs = [float(r.get("total_variable_cost_rub") or 0) for r in ok_runs]
    avg_cost = round(sum(all_costs) / len(all_costs), 4) if all_costs else 0.0
    cold_p95 = round(_percentile(cold_durations, 0.95), 1) if cold_durations else 0.0

    alerts = []
    if browser_rows and browser_success < 80.0:
        alerts.append({"code": "browser_success_low", "severity": "warning", "text": f"Browser success {browser_success}% — ниже цели 80%"})
    if all_costs and avg_cost > 3.0:
        alerts.append({"code": "audit_cost_high", "severity": "warning", "text": f"Средняя переменная стоимость {avg_cost} ₽ — выше порога 3 ₽"})
    if cold_durations and cold_p95 > 30000:
        alerts.append({"code": "market_p95_slow", "severity": "warning", "text": f"p95 холодного Market Audit {round(cold_p95/1000,1)} с — выше 30 с"})

    try:
        from proxy_pool import get_avito_proxy_health
        avito_route = get_avito_proxy_health() or {}
    except Exception:
        avito_route = {"state": "unknown", "checked_at": None, "detail": "health_unavailable"}

    wordstat_ready = bool(
        (os.getenv("YANDEX_CLOUD_API_KEY") or os.getenv("YANDEX_AI_API_KEY") or os.getenv("YANDEX_API_KEY"))
        and (os.getenv("YANDEX_CLOUD_FOLDER_ID") or os.getenv("YANDEX_GPT_FOLDER_ID"))
    )

    return {
        "status": "ok",
        "summary": {
            "total_runs": len(completed),
            "successful_runs": len(ok_runs),
            "error_runs": len(completed) - len(ok_runs),
            "avg_variable_cost_rub": avg_cost,
            "market_cache_hit_rate": cache_hit_rate,
            "browser_success_rate": browser_success,
            "wordstat_success_rate": wordstat_success,
            "market_cold_p95_ms": cold_p95,
        },
        "by_kind": {
            "manual": summarize("manual"),
            "connected": summarize("connected"),
            "market": summarize("market"),
        },
        "alerts": alerts,
        "market_browser": _audit_market_browser_status(),
        "market_route": {
            "state": str(avito_route.get("state") or "unknown").upper(),
            "checked_at": avito_route.get("checked_at"),
            "detail": avito_route.get("detail"),
            "open_ports": int(avito_route.get("open_ports") or 0),
            "tested_ports": int(avito_route.get("tested_ports") or 0),
        },
        "wordstat": {
            "status": "READY" if wordstat_ready else "UNCONFIGURED",
            "cost_rub": float(os.getenv("BORIS_WORDSTAT_COST_RUB", "0.02") or 0.02),
        },
        "recent": runs[:30],
    }


@router.get("/owner/leads")
def owner_leads(user=Depends(get_current_user)):
    _require_owner(user)
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "viral_gateway_state").all()
        leads = []
        for row in rows:
            account_id = str(row.account_id or "")
            try:
                state = json.loads(row.value or "{}")
            except Exception:
                state = {}
            client_user = db.query(User).filter(User.account_id == account_id).first()
            account = db.query(Account).filter(Account.account_id == account_id).first()
            history = list(state.get("history") or [])
            latest = history[-1] if history else {}
            leads.append({
                "account_id": account_id,
                "company": str(getattr(account, "name", "") or account_id),
                "email": str(getattr(client_user, "email", "") or ""),
                "score": state.get("latest_score"),
                "market_score": state.get("latest_market_score"),
                "demand_level": state.get("market_demand_level"),
                "competitors": state.get("market_competitors"),
                "estimated_competitors": state.get("market_estimated_competitors"),
                "competing_offers": state.get("market_competing_offers"),
                "market_category_slug": state.get("market_category_slug"),
                "recommended_ads": state.get("market_recommended_ads") or {},
                "trial_interest": str(state.get("stage") or "") in {"TRIAL_INTEREST","TRIAL_STARTED","PAID"},
                "mode": latest.get("mode") or "manual",
                "stage": state.get("stage") or "REGISTERED",
                "first_touch": state.get("first_touch") or {},
                "updated_at": state.get("updated_at"),
            })
        leads.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"status": "ok", "leads": leads, "total": len(leads)}
    finally:
        db.close()


@router.post("/owner/sales-contact")
def owner_sales_contact_update(payload: SalesContactInput, user=Depends(get_current_user)):
    _require_owner(user)
    data = {
        "whatsapp_url": str(payload.whatsapp_url or "").strip(),
        "max_url": str(payload.max_url or "").strip(),
        "phone": str(payload.phone or "").strip(),
        "message_template": str(payload.message_template or "").strip(),
        "contact_name": str(payload.contact_name or "Кирилл").strip() or "Кирилл",
        "contact_title": str(payload.contact_title or "Помогу запустить BORIS для вашего бизнеса").strip() or "Помогу запустить BORIS для вашего бизнеса",
        "trial_enabled": bool(payload.trial_enabled),
        "trial_days": int(payload.trial_days or 4),
        "trial_title": str(payload.trial_title or "").strip(),
        "trial_description": str(payload.trial_description or "").strip(),
        "updated_at": _now().isoformat(),
    }
    _storage_put("viral_global", "sales_contact", data)
    return {"status": "ok", "contact": data}


@router.get("/sales-contact")
def sales_contact():
    saved = _storage_get("viral_global", "sales_contact") or {}
    whatsapp = str(saved.get("whatsapp_url") or os.getenv("BORIS_SALES_WHATSAPP_URL", "") or "").strip()
    max_url = str(saved.get("max_url") or os.getenv("BORIS_SALES_MAX_URL", "") or "").strip()
    phone = str(saved.get("phone") or os.getenv("BORIS_SALES_PHONE", "") or "").strip()
    message_template = str(saved.get("message_template") or "").strip()
    contact_name = str(saved.get("contact_name") or "Кирилл О.").strip() or "Кирилл О."
    contact_title = str(saved.get("contact_title") or "Разработчик BORIS").strip() or "Разработчик BORIS"
    trial_enabled = bool(saved.get("trial_enabled", True))
    trial_days = max(1, min(int(saved.get("trial_days") or 4), 30))
    trial_title = str(saved.get("trial_title") or f"Попробовать BORIS {trial_days} дня бесплатно").strip()
    trial_description = str(saved.get("trial_description") or "Поможем подключить BORIS к вашему бизнесу и начнём с найденных в аудите точек.").strip()
    return {
        "status": "ok",
        "whatsapp_url": whatsapp,
        "max_url": max_url,
        "phone": phone,
        "message_template": message_template,
        "contact_name": contact_name,
        "contact_title": contact_title,
        "trial_enabled": trial_enabled,
        "trial_days": trial_days,
        "trial_title": trial_title,
        "trial_description": trial_description,
        "configured": bool(whatsapp or max_url),
    }


@router.get("/referral")
def referral_info(user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == getattr(user, "id", None)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        code = str(getattr(row, "ref_code", "") or "").strip().lower()
        if not code:
            # Existing BORIS registration already accepts User.ref_code. Reuse it
            # instead of creating a second referral identity system.
            for _ in range(8):
                candidate = "b" + secrets.token_urlsafe(7).replace("-", "").replace("_", "").lower()[:9]
                if not db.query(User).filter(User.ref_code == candidate).first():
                    code = candidate
                    row.ref_code = code
                    db.commit()
                    break
        if not code:
            raise HTTPException(status_code=503, detail="Не удалось создать реферальный код")
        referred = db.query(User).filter(User.referred_by == row.email).count()
        visit_pattern = '%"event":"viral_referral_visit"%'
        code_pattern = f'%"referral_code":"{code}"%'
        visits = db.query(Storage).filter(
            Storage.account_id == "viral_event",
            Storage.value.like(visit_pattern),
            Storage.value.like(code_pattern),
        ).count()
        rewards = []
        for reward_row in db.query(Storage).filter(Storage.account_id == "viral_referral_reward").all():
            try:
                reward = json.loads(reward_row.value or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if int(reward.get("referrer_user_id") or 0) == int(row.id):
                rewards.append(reward)
        qualified = sum(1 for x in rewards if str(x.get("status") or "") in {"GRANTING", "GRANTED"})
        granted = [x for x in rewards if str(x.get("status") or "") == "GRANTED"]
        reward_days = sum(int(x.get("reward_value") or 0) for x in granted)
        return {
            "status": "ok",
            "referral_code": code,
            "link": f"/r/{code}",
            "visits": int(visits),
            "registrations": int(referred),
            "qualified": int(qualified),
            "activated": int(len(granted)),
            "reward_days": int(reward_days),
            "rewards": [
                {
                    "id": str(x.get("id") or ""),
                    "status": str(x.get("status") or ""),
                    "reward_value": int(x.get("reward_value") or 0),
                    "activated_at": x.get("activated_at"),
                }
                for x in granted[-20:]
            ],
            "reward_rule": {
                "qualification": "account_connected",
                "reward": "+7 дней BORIS",
                "state": "active",
            },
        }
    finally:
        db.close()


@router.get("/referral/stats")
def referral_stats(user=Depends(get_current_user)):
    return referral_info(user)


@router.post("/events")
def record_event(payload: EventInput, request: Request):
    _rate_limit(request, "events", 120)
    allowed = {
        "viral_page_view", "viral_calculator_started", "viral_calculator_completed",
        "viral_score_created", "viral_report_shared", "viral_share_opened",
        "viral_cta_clicked", "viral_registration", "viral_account_connected",
        "viral_trial_started", "viral_paid", "viral_referral_visit",
        "trial_offer_viewed", "trial_cta_clicked", "market_audit_completed",
        "specialist_contact_clicked", "whatsapp_clicked", "max_clicked",
        "audit_opened", "audit_25_viewed", "audit_50_viewed", "audit_75_viewed",
        "audit_completed_view", "cta_test_clicked", "share_clicked", "share_completed",
        "check_my_business_clicked", "referral_visit", "registration_started",
        "registration_completed", "trial_started",
        "audit_view", "audit_start", "market_audit_started",
        "audit_result_view", "trial_cta_click", "trial_interest", "connected_audit",
    }
    if payload.event not in allowed:
        raise HTTPException(status_code=400, detail="Неизвестное событие")
    user_id = None
    account_id = None
    auth_header = str(request.headers.get("authorization") or "")
    if auth_header.lower().startswith("bearer "):
        try:
            event_user = get_current_user(auth_header.split(" ", 1)[1].strip())
            user_id = int(getattr(event_user, "id", 0) or 0) or None
            account_id = str(getattr(event_user, "account_id", "") or "").strip() or None
        except Exception:
            user_id = None
            account_id = None

    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    real_ip = forwarded or str(request.headers.get("x-real-ip") or "").strip() or str(request.client.host if request.client else "")
    request_host = str(request.headers.get("host") or "").split(":", 1)[0].strip().lower()
    user_agent = str(request.headers.get("user-agent") or "").lower()
    private_ip = (
        real_ip in {"127.0.0.1", "::1", "localhost"}
        or real_ip.startswith("10.")
        or real_ip.startswith("192.168.")
    )
    public_host = request_host not in {"", "127.0.0.1", "localhost", "::1"}
    is_local = bool(private_ip and not public_host)
    is_bot = any(token in user_agent for token in ("bot", "crawler", "spider", "headless", "healthcheck", "uptime"))

    event_id = "ve_" + uuid.uuid4().hex
    item = {
        "event_id": event_id,
        "event": payload.event,
        "user_id": user_id,
        "account_id": account_id,
        "session_id": payload.session_id,
        "referral_code": payload.referral_code,
        "source": payload.source,
        "utm_source": payload.utm_source,
        "utm_medium": payload.utm_medium,
        "utm_campaign": payload.utm_campaign,
        "utm_content": payload.utm_content,
        "utm_term": payload.utm_term,
        "landing_path": payload.landing_path,
        "first_seen_at": payload.first_seen_at,
        "qa": bool(payload.qa),
        "is_local": is_local,
        "is_bot": is_bot,
        "created_at": _now().isoformat(),
    }
    _storage_put("viral_event", f"event:{event_id}", item)
    return {"status": "ok", "event_id": event_id}
