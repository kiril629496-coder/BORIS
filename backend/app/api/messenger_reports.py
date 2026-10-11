# -*- coding: utf-8 -*-
"""Read-only BORIS Messenger business report facade.

This module deliberately reuses persisted canonical BORIS evidence. It does not
refresh providers, mutate campaigns, start reactivation, or call external AI.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.legal import require_messenger_legal_user
from app.db.session import SessionLocal
from app.services.platform_roles import is_private_platform_owner
from app.services.messenger_rate_limit import enforce as enforce_messenger_rate

router = APIRouter(prefix="/api/messenger-reports", tags=["messenger-reports"])

_ACTIVE_REACTIVATION = {"candidate", "needs_review", "approved", "scheduled", "cooldown"}
_ELIGIBLE_REACTIVATION = {"candidate", "approved", "scheduled"}
_REASON_LABELS = {
    "no_reply": "Не ответили после диалога",
    "price_requested": "Запрашивали цену и пропали",
    "thinking": "Обещали подумать",
    "old_warm": "Старый тёплый лид",
}
_STATUS_LABELS = {
    "candidate": "Можно писать",
    "approved": "Можно писать",
    "scheduled": "Запланировано",
    "needs_review": "Требуется проверка",
    "cooldown": "Ждём",
    "manager_taken_over": "Требуется человек",
    "replied": "Ответил",
    "converted": "Конвертирован",
    "blocked": "Нельзя реактивировать",
}


def _rate_report(user, *, share: bool = False) -> None:
    enforce_messenger_rate(
        "messenger_report_share" if share else "messenger_report_read",
        f"user:{int(user.id)}",
        limit=30 if share else 120,
        window_seconds=60,
        message="Слишком много запросов отчётов. Подождите немного.",
    )


class ShareReportBody(BaseModel):
    account_id: str
    conversation_id: int
    report_type: str
    period_days: int = 7
    date_from: Optional[str] = None
    date_to: Optional[str] = None


def _json(value: Any, fallback: Any = None) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return {} if fallback is None else fallback


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _freshness(timestamp: Any) -> dict[str, Any]:
    dt = _parse_dt(timestamp)
    if not dt:
        return {"state": "unknown", "updated_at": None, "age_seconds": None}
    age = max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
    state = "fresh" if age <= 6 * 3600 else ("stale" if age <= 24 * 3600 else "old")
    return {"state": state, "updated_at": dt.isoformat(), "age_seconds": age}


def _account_permissions(db, user, account_id: str) -> dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="Аккаунт не выбран")
    account = db.execute(text(
        "SELECT account_id,owner_user_id,COALESCE(name,account_id) AS name "
        "FROM accounts WHERE account_id=:a LIMIT 1"
    ), {"a": account_id}).mappings().first()
    if not account:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    if is_private_platform_owner(user) or int(account["owner_user_id"] or 0) == int(user.id):
        return {
            "account_id": account_id,
            "account_name": account["name"],
            "role": "OWNER",
            "can_view_reports": True,
            "can_view_finance": True,
            "can_view_marketing_reports": True,
        }
    access = db.execute(text(
        "SELECT role,can_view,can_view_reports FROM user_account_access "
        "WHERE account_id=:a AND user_id=:u LIMIT 1"
    ), {"a": account_id, "u": int(user.id)}).mappings().first()
    if not access or not bool(access["can_view"]):
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    if not bool(access["can_view_reports"]):
        raise HTTPException(status_code=403, detail="У вашей роли нет доступа к отчётам")
    membership = db.execute(text(
        "SELECT permissions_override FROM team_memberships "
        "WHERE account_id=:a AND user_id=:u AND status='ACTIVE' LIMIT 1"
    ), {"a": account_id, "u": int(user.id)}).mappings().first()
    overrides = dict((membership or {}).get("permissions_override") or {})
    return {
        "account_id": account_id,
        "account_name": account["name"],
        "role": str(access["role"] or "MEMBER"),
        "can_view_reports": True,
        # Finance is intentionally fail-closed for employees until explicitly
        # granted in team permissions_override.
        "can_view_finance": bool(overrides.get("can_view_finance", False)),
        "can_view_marketing_reports": bool(overrides.get("can_view_marketing_reports", True)),
    }


def _require_reports(db, user, account_id: str, *, finance: bool = False, marketing: bool = False) -> dict[str, Any]:
    perms = _account_permissions(db, user, account_id)
    if finance and not perms["can_view_finance"]:
        raise HTTPException(status_code=403, detail="Финансовые данные доступны только владельцу или роли с отдельным разрешением")
    if marketing and not perms["can_view_marketing_reports"]:
        raise HTTPException(status_code=403, detail="Маркетинговые отчёты недоступны вашей роли")
    return perms


def _storage_rows(db, account_id: str, prefix: str) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(
        "SELECT key,value FROM storage WHERE account_id=:a AND key LIKE :p ORDER BY key"
    ), {"a": account_id, "p": prefix + "%"}).mappings().all()]


def _storage_json(db, account_id: str, key: str) -> dict[str, Any]:
    raw = db.execute(text(
        "SELECT value FROM storage WHERE account_id=:a AND key=:k LIMIT 1"
    ), {"a": account_id, "k": key}).scalar()
    out = _json(raw, {})
    return out if isinstance(out, dict) else {}


def _daily_snapshots(db, account_id: str) -> list[dict[str, Any]]:
    rows = _storage_rows(db, account_id, "daily_stats:")
    out = []
    for row in rows:
        data = _json(row["value"], {})
        if not isinstance(data, dict):
            continue
        day = str(data.get("stats_date") or data.get("date") or row["key"].split(":", 1)[-1])[:10]
        try:
            parsed = date.fromisoformat(day)
        except Exception:
            continue
        data["_day"] = parsed
        out.append(data)
    return sorted(out, key=lambda x: x["_day"])


def _resolve_period(days: int, date_from: Optional[str] = None, date_to: Optional[str] = None) -> tuple[date, date, int]:
    if date_from or date_to:
        if not date_from or not date_to:
            raise HTTPException(status_code=400, detail="Для своего периода укажите обе даты")
        try:
            start = date.fromisoformat(str(date_from)[:10])
            end = date.fromisoformat(str(date_to)[:10])
        except Exception:
            raise HTTPException(status_code=400, detail="Некорректный формат периода")
        if end < start:
            raise HTTPException(status_code=400, detail="Дата окончания раньше даты начала")
        span = (end - start).days + 1
        if span > 60:
            raise HTTPException(status_code=400, detail="Период отчёта не может превышать 60 дней")
        return start, end, span
    span = max(1, min(int(days or 7), 60))
    end = date.today()
    return end - timedelta(days=span - 1), end, span


def _period_snapshots(
    db,
    account_id: str,
    days: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    start, end, span = _resolve_period(days, date_from, date_to)
    all_rows = _daily_snapshots(db, account_id)
    prev_start = start - timedelta(days=span)
    current = [x for x in all_rows if start <= x["_day"] <= end]
    previous = [x for x in all_rows if prev_start <= x["_day"] < start]
    return current, previous, {"days": span, "date_from": start.isoformat(), "date_to": end.isoformat()}


def _aggregate_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    views = contacts = 0
    ad_spend = all_spend = 0.0
    for snap in rows:
        for item in snap.get("items") or []:
            views += int(item.get("views") or 0)
            contacts += int(item.get("contacts") or 0)
        spending = snap.get("spending") or {}
        if str(spending.get("status") or "") == "ok":
            ad_spend += float(spending.get("ad_spend_rub") or 0)
            all_spend += float(spending.get("all_spend_rub") or 0)
    return {
        "views": views,
        "contacts": contacts,
        "ad_spend_rub": round(ad_spend, 2),
        "all_spend_rub": round(all_spend, 2),
        "cpl_rub": round(ad_spend / contacts, 2) if contacts else None,
        "conversion_pct": round(contacts * 100.0 / views, 2) if views else None,
    }


def _pct_delta(now: Optional[float], before: Optional[float]) -> Optional[float]:
    if now is None or before is None or abs(float(before)) < 1e-9:
        return None
    return round((float(now) - float(before)) * 100.0 / abs(float(before)), 1)


def _finance_data(db, account_id: str) -> dict[str, Any]:
    snaps = _daily_snapshots(db, account_id)
    latest = snaps[-1] if snaps else {}
    balance = latest.get("balance") or {}
    spending = latest.get("spending") or {}
    wallet = balance.get("real")
    bonus = balance.get("bonus")
    generated = latest.get("collected_at") or spending.get("confirmed_at")
    guard = _storage_json(db, account_id, "guardian_balance_funding_health")
    today = date.today()
    def spend_for(days: int) -> float:
        start = today - timedelta(days=days - 1)
        total = 0.0
        for snap in snaps:
            if start <= snap["_day"] <= today:
                sp = snap.get("spending") or {}
                if str(sp.get("status") or "") == "ok":
                    total += float(sp.get("ad_spend_rub") or 0)
        return round(total, 2)
    return {
        "wallet_rub": round(float(wallet), 2) if wallet is not None else None,
        "bonus_rub": round(float(bonus), 2) if bonus is not None else None,
        # Avito does not expose a distinct numeric advance amount in the current
        # provider contract. Never invent one from wallet/bonus.
        "advance_rub": None,
        "advance_status": "provider_not_exposed",
        "advance_label": "Отдельная сумма аванса не предоставляется Avito API",
        "funding_state": guard.get("state") or guard.get("status"),
        "funding_checked_at": guard.get("checked_at"),
        "spend_today_rub": spend_for(1),
        "spend_7d_rub": spend_for(7),
        "spend_30d_rub": spend_for(30),
        "latest_stats_date": latest.get("stats_date") or latest.get("date"),
        "source": "daily_stats + guardian_balance_funding_health",
        "freshness": _freshness(generated or guard.get("checked_at")),
    }


def _advertising_data(
    db,
    account_id: str,
    days: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict[str, Any]:
    current, previous, period = _period_snapshots(db, account_id, days, date_from, date_to)
    cur = _aggregate_stats(current)
    prev = _aggregate_stats(previous)
    latest = current[-1] if current else {}
    active = [x for x in (latest.get("items") or []) if str(x.get("status") or "").lower() == "active"]
    breakdown = (latest.get("spending") or {}).get("breakdown") or {}
    promotion_spend_today = float(breakdown.get("promotion") or 0) + float(breakdown.get("presence") or 0)
    cur["active_ads"] = len(active)
    cur["promotion_spend_today_rub"] = round(promotion_spend_today, 2)
    cur["period_days"] = int(period["days"])
    cur["period"] = period
    cur["comparison"] = {
        "views_pct": _pct_delta(cur["views"], prev["views"]),
        "contacts_pct": _pct_delta(cur["contacts"], prev["contacts"]),
        "ad_spend_pct": _pct_delta(cur["ad_spend_rub"], prev["ad_spend_rub"]),
        "cpl_pct": _pct_delta(cur["cpl_rub"], prev["cpl_rub"]),
    }
    cur["top_ads"] = sorted([
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or "Объявление"),
            "views": int(item.get("views") or 0),
            "contacts": int(item.get("contacts") or 0),
            "conversion_pct": float(item.get("conversion") or 0),
            "status": item.get("status"),
        }
        for item in active
    ], key=lambda x: (x["contacts"], x["views"]), reverse=True)[:8]
    generated = latest.get("collected_at") or (latest.get("spending") or {}).get("confirmed_at")
    cur["source"] = "daily_stats"
    cur["freshness"] = _freshness(generated)
    return cur


def _reactivation_data(db, account_id: str) -> dict[str, Any]:
    rows = db.execute(text(
        "SELECT c.id,c.avito_chat_id,c.primary_reason,c.status,c.summary,c.score,c.confidence,"
        "c.watch_since,c.recommended_contact_at,c.updated_at,"
        "(SELECT COALESCE(rm.final_text,rm.edited_text,rm.generated_text) FROM reactivation_messages rm "
        " WHERE rm.candidate_id=c.id AND rm.status<>'cancelled' ORDER BY rm.id DESC LIMIT 1) AS proposed_text "
        "FROM reactivation_candidates c WHERE c.account_id=:a "
        "ORDER BY c.updated_at DESC NULLS LAST,c.id DESC"
    ), {"a": account_id}).mappings().all()
    counts: dict[str, int] = defaultdict(int)
    reasons: dict[str, int] = defaultdict(int)
    eligible = review = cooldown = human = 0
    items = []
    latest_at = None
    for r in rows:
        status = str(r["status"] or "")
        reason = str(r["primary_reason"] or "other")
        counts[status] += 1
        if status in _ACTIVE_REACTIVATION:
            reasons[reason] += 1
        if status in _ELIGIBLE_REACTIVATION:
            eligible += 1
        elif status == "needs_review":
            review += 1
        elif status == "cooldown":
            cooldown += 1
        elif status == "manager_taken_over":
            human += 1
        updated = r["updated_at"]
        if updated and (latest_at is None or updated > latest_at):
            latest_at = updated
        if status in _ACTIVE_REACTIVATION or status == "manager_taken_over":
            items.append({
                "candidate_id": int(r["id"]),
                "chat_ref": str(r["avito_chat_id"] or "")[-6:],
                "reason": reason,
                "reason_label": _REASON_LABELS.get(reason, reason.replace("_", " ").capitalize()),
                "status": status,
                "status_label": _STATUS_LABELS.get(status, status),
                "summary": r["summary"],
                "score": int(r["score"] or 0),
                "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
                "watch_since": r["watch_since"].isoformat() if r["watch_since"] else None,
                "recommended_contact_at": r["recommended_contact_at"].isoformat() if r["recommended_contact_at"] else None,
                "recommendation": (
                    "Подготовить мягкое возвращение с уточнением актуальности"
                    if reason == "no_reply" else
                    "Вернуться к запросу цены и уточнить, актуальна ли покупка"
                    if reason == "price_requested" else
                    "Проверить контекст и подготовить персональное возвращение"
                ),
                "proposed_text": r["proposed_text"],
            })
    return {
        "eligible_now": eligible,
        "needs_review": review,
        "cooldown": cooldown,
        "human_required": human,
        "status_counts": dict(counts),
        "reason_groups": [
            {"reason": key, "label": _REASON_LABELS.get(key, key.replace("_", " ").capitalize()), "count": value}
            for key, value in sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "clients": items[:60],
        "source": "reactivation_candidates + reactivation_messages",
        "freshness": _freshness(latest_at.isoformat() if latest_at else None),
    }


def _latest_titles(db, account_id: str) -> dict[str, str]:
    snaps = _daily_snapshots(db, account_id)
    latest = snaps[-1] if snaps else {}
    return {str(x.get("id")): str(x.get("title") or "Объявление") for x in (latest.get("items") or [])}


def _prepared_title_hypotheses(db, account_id: str, start: date, end: date) -> list[dict[str, Any]]:
    """Return title proposals as hypotheses, never as measured A/B results."""
    rows = _storage_rows(db, account_id, "kpi_prepare:")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        data = _json(row["value"], {})
        if not isinstance(data, dict) or str(data.get("action") or "") != "prepare_weak_titles":
            continue
        dt = _parse_dt(data.get("ai_completed_at") or data.get("ai_started_at"))
        if dt and not (start <= dt.date() <= end):
            continue
        proposals = data.get("proposal") or []
        if not isinstance(proposals, list):
            continue
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            item_id = str(proposal.get("item_id") or "").strip()
            old_title = str(proposal.get("old_title") or "").strip()
            new_title = str(proposal.get("new_title") or "").strip()
            if not item_id or not new_title:
                continue
            key = (item_id, old_title, new_title)
            if key in seen:
                continue
            seen.add(key)
            apply_status = str(proposal.get("apply_status") or "prepared")
            out.append({
                "item_id": item_id,
                "kind": "Заголовок",
                "changed": ["Заголовок"],
                "hypothesis": str(proposal.get("reason") or "Новый заголовок повысит релевантность объявления"),
                "variant_a": {"label": "A", "title": old_title or None},
                "variant_b": {"label": "B", "title": new_title},
                "classification": "prepared",
                "confidence": "not_measured",
                "prepared_at": dt.isoformat() if dt else None,
                "apply_status": apply_status,
                "next_action": (
                    "Дождаться измеримых результатов после публикации"
                    if apply_status in {"submitted", "applied", "published"}
                    else "Сначала применить вариант и только потом измерять результат"
                ),
                "source": "kpi_prepare:prepare_weak_titles",
            })
    return sorted(out, key=lambda x: str(x.get("prepared_at") or ""), reverse=True)


def _ab_data(
    db,
    account_id: str,
    days: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict[str, Any]:
    start, end, days = _resolve_period(days, date_from, date_to)
    journals = []
    for row in _storage_rows(db, account_id, "marketing_experiment_journal:"):
        d = _json(row["value"], {})
        try:
            day = date.fromisoformat(str(d.get("date") or row["key"].split(":",1)[-1])[:10])
        except Exception:
            continue
        if start <= day <= end:
            journals.append(d)
    titles = _latest_titles(db, account_id)
    seen = set()
    tests = []
    pending = []
    latest_generated = None
    for journal in journals:
        generated = journal.get("generated_at")
        gdt = _parse_dt(generated)
        if gdt and (latest_generated is None or gdt > latest_generated):
            latest_generated = gdt
        for item in ((journal.get("portfolio") or {}).get("bid") or []):
            key = (
                str(item.get("item_id") or ""),
                str(item.get("finished_at") or ""),
                str(item.get("action") or ""),
                str(item.get("old_bid_rub") or ""),
                str(item.get("new_bid_rub") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            item_id = str(item.get("item_id") or "")
            before = item.get("before") or {}
            after = item.get("after") or {}
            effect = str(item.get("effect") or "")
            classification = str(item.get("classification") or "")
            measured = bool(after.get("days")) and effect not in {"", "inconclusive_no_signal"}
            action = str(item.get("action") or "")
            old_bid = item.get("old_bid_rub")
            new_bid = item.get("new_bid_rub")
            hypothesis = (
                f"Повышение ставки с {old_bid:g} ₽ до {new_bid:g} ₽ увеличит охват и контакты"
                if action == "raise" and isinstance(old_bid,(int,float)) and isinstance(new_bid,(int,float))
                else f"Снижение ставки с {old_bid:g} ₽ до {new_bid:g} ₽ сохранит результат при меньшей цене продвижения"
                if action == "lower" and isinstance(old_bid,(int,float)) and isinstance(new_bid,(int,float))
                else "Изменение ставки продвижения улучшит результат объявления"
            )
            tests.append({
                "item_id": item_id,
                "title": titles.get(item_id, f"Объявление {item_id[-6:]}" if item_id else "Объявление"),
                "kind": "Продвижение",
                "changed": ["Продвижение"],
                "hypothesis": hypothesis,
                "variant_a": {"label": "A", "bid_rub": old_bid, "views": before.get("views"), "contacts": before.get("contacts"), "conversion": before.get("conversion")},
                "variant_b": {"label": "B", "bid_rub": new_bid, "views": after.get("views"), "contacts": after.get("contacts"), "conversion": after.get("conversion")},
                "effect": effect or None,
                "raw_classification": classification or None,
                "classification": (classification or "measured") if measured else "insufficient_data",
                "confidence": "measured" if measured else "insufficient_data",
                "decision": item.get("decision"),
                "next_action": item.get("next_action") or item.get("decision") or "Продолжить наблюдение",
                "finished_at": item.get("finished_at"),
                "delta": item.get("delta") or {},
            })
        for item in journal.get("pending_measurements") or []:
            key = (str(item.get("item_id") or ""), str(item.get("started_at") or ""), str(item.get("action") or ""))
            if key in seen:
                continue
            seen.add(key)
            item_id = str(item.get("item_id") or "")
            progress = item.get("progress") or {}
            pending.append({
                "item_id": item_id,
                "title": titles.get(item_id, f"Объявление {item_id[-6:]}" if item_id else "Объявление"),
                "kind": "Продвижение",
                "hypothesis": "Изменение ставки продвижения даст измеримый эффект",
                "classification": "pending",
                "confidence": "insufficient_data",
                "started_at": item.get("started_at"),
                "progress": progress,
                "next_action": "Продолжить тест до накопления достаточных данных",
            })
    prepared = _prepared_title_hypotheses(db, account_id, start, end)
    for item in prepared:
        item_id = str(item.get("item_id") or "")
        item["title"] = titles.get(item_id, f"Объявление {item_id[-6:]}" if item_id else "Объявление")
    winners = sum(1 for x in tests if x["confidence"] == "measured" and (x["classification"] in {"winner", "scale"} or str(x.get("effect") or "").startswith("improved")))
    losers = sum(1 for x in tests if x["confidence"] == "measured" and (x["classification"] == "loser" or x.get("effect") == "worsened"))
    neutral = max(0, len(tests) - winners - losers)
    return {
        "period_days": days,
        "period": {"days": days, "date_from": start.isoformat(), "date_to": end.isoformat()},
        "completed": len(tests),
        "winners": winners,
        "losers": losers,
        "neutral_or_observe": neutral,
        "pending": len(pending),
        "prepared": len(prepared),
        "tests": sorted(tests, key=lambda x: str(x.get("finished_at") or ""), reverse=True)[:80],
        "pending_tests": pending[:40],
        "prepared_hypotheses": prepared[:80],
        "source": "marketing_experiment_journal + kpi_prepare:prepare_weak_titles",
        "freshness": _freshness(latest_generated.isoformat() if latest_generated else None),
        "scope_note": "Показаны только фактически зафиксированные измеряемые эксперименты; неподтверждённые варианты не объявляются победителями.",
    }


def _response(perms: dict[str, Any], report_type: str, data: dict[str, Any], period: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "account_id": perms["account_id"],
        "account_name": perms["account_name"],
        "report_type": report_type,
        "period": period or {},
        "generated_at": _iso_now(),
        "permissions": {
            "can_view_finance": bool(perms["can_view_finance"]),
            "can_view_marketing_reports": bool(perms["can_view_marketing_reports"]),
        },
        "freshness": data.get("freshness") or {"state": "unknown"},
        "data": data,
    }


@router.get("/finance")
def finance_report(account_id: str, user=Depends(require_messenger_legal_user)):
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id, finance=True)
        data = _finance_data(db, account_id)
        return _response(perms, "finance", data)
    finally:
        db.close()


@router.get("/advertising")
def advertising_report(
    account_id: str,
    period_days: int = Query(default=7, ge=1, le=60),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    user=Depends(require_messenger_legal_user),
):
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id, marketing=True)
        data = _advertising_data(db, account_id, period_days, date_from, date_to)
        return _response(perms, "advertising", data, data.get("period") or {"days": period_days})
    finally:
        db.close()


@router.get("/reactivation")
def reactivation_report(account_id: str, user=Depends(require_messenger_legal_user)):
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id)
        return _response(perms, "reactivation", _reactivation_data(db, account_id))
    finally:
        db.close()


@router.get("/ab-tests")
def ab_tests_report(
    account_id: str,
    period_days: int = Query(default=30, ge=1, le=60),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    user=Depends(require_messenger_legal_user),
):
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id, marketing=True)
        data = _ab_data(db, account_id, period_days, date_from, date_to)
        return _response(perms, "ab_tests", data, data.get("period") or {"days": period_days})
    finally:
        db.close()


@router.get("/ads/{item_id}/experiments")
def ad_experiments(
    item_id: str,
    account_id: str,
    period_days: int = Query(default=60, ge=1, le=60),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    user=Depends(require_messenger_legal_user),
):
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id, marketing=True)
        data = _ab_data(db, account_id, period_days, date_from, date_to)
        filtered = [x for x in data["tests"] if str(x.get("item_id")) == str(item_id)]
        pending = [x for x in data["pending_tests"] if str(x.get("item_id")) == str(item_id)]
        prepared = [x for x in data.get("prepared_hypotheses", []) if str(x.get("item_id")) == str(item_id)]
        result = {
            "item_id": str(item_id),
            "title": (filtered or pending or prepared or [{"title": f"Объявление {str(item_id)[-6:]}"}])[0]["title"],
            "tests": filtered,
            "pending_tests": pending,
            "prepared_hypotheses": prepared,
            "source": data["source"],
            "freshness": data["freshness"],
        }
        return _response(perms, "ad_experiments", result, {"days": period_days})
    finally:
        db.close()


@router.get("/summary")
def summary_report(account_id: str, period_days: int = Query(default=7, ge=1, le=60), user=Depends(require_messenger_legal_user)):
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id)
        advertising = _advertising_data(db, account_id, period_days) if perms["can_view_marketing_reports"] else None
        reactivation = _reactivation_data(db, account_id)
        ab = _ab_data(db, account_id, min(max(period_days, 7), 60)) if perms["can_view_marketing_reports"] else None
        finance = _finance_data(db, account_id) if perms["can_view_finance"] else None
        recommendations = []
        if advertising and advertising.get("cpl_rub") is not None:
            kpi = _storage_json(db, account_id, "kpi_settings")
            max_cpl = float(kpi.get("max_cost_per_lead_rub") or 0)
            if max_cpl > 0 and float(advertising["cpl_rub"]) > max_cpl:
                recommendations.append(f"CPL {advertising['cpl_rub']:.0f} ₽ выше целевого {max_cpl:.0f} ₽ — проверить проигрывающие объявления и продвижение.")
        reactivation_freshness = str((reactivation.get("freshness") or {}).get("state") or "unknown")
        if reactivation_freshness in {"old", "unknown"}:
            recommendations.append("Данные реактивации требуют обновления — не запускать возврат клиентов по устаревшему снимку.")
        elif reactivation["eligible_now"]:
            recommendations.append(f"Есть {reactivation['eligible_now']} клиентов, которых можно вернуть в работу.")
        if ab and ab["pending"]:
            recommendations.append(f"{ab['pending']} A/B-тестов ещё набирают данные — не объявлять победителей раньше времени.")
        if ab and ab.get("prepared"):
            recommendations.append(
                f"Подготовлено {ab['prepared']} гипотез по заголовкам без измеримого результата — "
                "их нельзя считать победителями до накопления статистики."
            )
        if not recommendations:
            recommendations.append("Критических действий по доступным данным сейчас не требуется.")
        data = {
            "finance": finance,
            "advertising": advertising,
            "reactivation": reactivation,
            "ab_tests": ab,
            "recommendations": recommendations,
            "source": "messenger_reports_facade",
            "freshness": advertising.get("freshness") if advertising else reactivation.get("freshness"),
        }
        return _response(perms, "summary", data, {"days": period_days})
    finally:
        db.close()


@router.get("/ask")
def ask_report(account_id: str, q: str = Query(min_length=2, max_length=240), period_days: int = Query(default=7, ge=1, le=60), user=Depends(require_messenger_legal_user)):
    """Deterministic fact-first report assistant. No external AI call."""
    _rate_report(user)
    db = SessionLocal()
    try:
        perms = _require_reports(db, user, account_id)
        query = q.strip().lower()
        if any(word in query for word in ("баланс", "кошел", "аванс", "деньг")):
            if not perms["can_view_finance"]:
                raise HTTPException(status_code=403, detail="Финансовые данные недоступны вашей роли")
            data = _finance_data(db, account_id)
            answer = f"Кошелёк Avito: {data['wallet_rub'] if data['wallet_rub'] is not None else 'нет подтверждённых данных'} ₽. Аванс: отдельная сумма API не предоставляется."
            kind = "finance"
        elif any(word in query for word in ("реактив", "вернуть", "разбуд")):
            data = _reactivation_data(db, account_id)
            answer = f"Сейчас можно реактивировать: {data['eligible_now']}. Требуют проверки: {data['needs_review']}. На паузе: {data['cooldown']}."
            kind = "reactivation"
        elif any(word in query for word in ("a/b", "аб", "гипотез", "тест")):
            if not perms["can_view_marketing_reports"]:
                raise HTTPException(status_code=403, detail="Маркетинговые отчёты недоступны вашей роли")
            data = _ab_data(db, account_id, period_days)
            answer = (
                f"За период завершено измеряемых тестов: {data['completed']}. "
                f"Победителей: {data['winners']}. Проигравших: {data['losers']}. "
                f"Набирают данные: {data['pending']}. "
                f"Подготовлено гипотез без измерения: {data.get('prepared', 0)}."
            )
            kind = "ab_tests"
        else:
            if not perms["can_view_marketing_reports"]:
                raise HTTPException(status_code=403, detail="Маркетинговые отчёты недоступны вашей роли")
            data = _advertising_data(db, account_id, period_days)
            answer = f"За {period_days} дн.: просмотры {data['views']}, контакты {data['contacts']}, расход {data['ad_spend_rub']} ₽, CPL {data['cpl_rub'] if data['cpl_rub'] is not None else 'нет данных'} ₽."
            kind = "advertising"
        return _response(perms, "ask", {"answer": answer, "kind": kind, "facts": data, "freshness": data.get("freshness")}, {"days": period_days})
    finally:
        db.close()


def _share_text(report_type: str, account_name: str, data: dict[str, Any], period_days: int, date_from: Optional[str] = None, date_to: Optional[str] = None) -> str:
    range_query = f"&date_from={date_from}&date_to={date_to}" if date_from and date_to else ""
    if report_type == "summary":
        advertising = data.get("advertising") or {}
        reactivation = data.get("reactivation") or {}
        ab = data.get("ab_tests") or {}
        finance = data.get("finance") or {}
        wallet_line = (
            f"Кошелёк Avito: {finance.get('wallet_rub')} ₽\n"
            if finance and finance.get("wallet_rub") is not None else ""
        )
        recs = data.get("recommendations") or []
        return (
            f"☀️ Утренний отчёт · {account_name}\n"
            + wallet_line
            + f"Контакты за {period_days} дн.: {advertising.get('contacts',0)} · "
              f"Расход: {advertising.get('ad_spend_rub',0)} ₽ · "
              f"CPL: {advertising.get('cpl_rub') if advertising.get('cpl_rub') is not None else 'нет данных'} ₽\n"
            + f"Можно реактивировать: {reactivation.get('eligible_now',0)} · "
              f"A/B набирают данные: {ab.get('pending',0)}\n"
            + (("Рекомендация: " + str(recs[0]) + "\n") if recs else "")
            + f"Подробнее: https://boris-ai.pro/messenger?section=reports&report=summary&period_days={period_days}"
        )
    if report_type == "finance":
        return (
            f"📊 Финансы · {account_name}\n"
            f"Кошелёк Avito: {data.get('wallet_rub') if data.get('wallet_rub') is not None else 'нет подтверждённых данных'} ₽\n"
            f"Аванс: отдельная сумма не предоставляется API\n"
            f"Расход сегодня: {data.get('spend_today_rub',0)} ₽\n"
            f"Подробнее: https://boris-ai.pro/messenger?section=reports&report=finance"
        )
    if report_type == "reactivation":
        return (
            f"🔁 Реактивация · {account_name}\n"
            f"Можно вернуть в работу: {data.get('eligible_now',0)}\n"
            f"Требуют проверки: {data.get('needs_review',0)}\n"
            f"На паузе: {data.get('cooldown',0)}\n"
            f"Подробнее: https://boris-ai.pro/messenger?section=reports&report=reactivation"
        )
    if report_type == "ab_tests":
        return (
            f"🧪 A/B-тесты · {period_days} дн.\n"
            f"Завершено: {data.get('completed',0)} · Победителей: {data.get('winners',0)} · "
            f"Проигравших: {data.get('losers',0)} · Набирают данные: {data.get('pending',0)} · "
            f"Подготовлено без измерения: {data.get('prepared',0)}\n"
            f"Подробнее: https://boris-ai.pro/messenger?section=reports&report=ab_tests&period_days={period_days}{range_query}"
        )
    return (
        f"📈 Реклама · {period_days} дн. · {account_name}\n"
        f"Просмотры: {data.get('views',0)} · Контакты: {data.get('contacts',0)}\n"
        f"Расход: {data.get('ad_spend_rub',0)} ₽ · CPL: {data.get('cpl_rub') if data.get('cpl_rub') is not None else 'нет данных'} ₽\n"
        f"Подробнее: https://boris-ai.pro/messenger?section=reports&report=advertising&period_days={period_days}{range_query}"
    )


@router.post("/share")
def share_report(body: ShareReportBody, user=Depends(require_messenger_legal_user)):
    _rate_report(user, share=True)
    report_type = str(body.report_type or "").strip()
    if report_type not in {"summary", "finance", "advertising", "reactivation", "ab_tests"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип отчёта")
    db = SessionLocal()
    try:
        perms = _require_reports(
            db, user, body.account_id,
            finance=report_type == "finance",
            marketing=report_type in {"advertising", "ab_tests"},
        )
        member = db.execute(text(
            "SELECT 1 FROM internal_conversations c JOIN internal_conversation_members m ON m.conversation_id=c.id "
            "WHERE c.id=:c AND c.account_id=:a AND m.user_id=:u LIMIT 1"
        ), {"c": int(body.conversation_id), "a": body.account_id, "u": int(user.id)}).first()
        if not member:
            raise HTTPException(status_code=404, detail="Чат не найден")
        if report_type == "summary":
            advertising = _advertising_data(db, body.account_id, body.period_days) if perms["can_view_marketing_reports"] else None
            reactivation = _reactivation_data(db, body.account_id)
            ab = _ab_data(db, body.account_id, min(max(body.period_days, 7), 60)) if perms["can_view_marketing_reports"] else None
            finance = _finance_data(db, body.account_id) if perms["can_view_finance"] else None
            data = {
                "finance": finance, "advertising": advertising, "reactivation": reactivation, "ab_tests": ab,
                "recommendations": summary_report(body.account_id, body.period_days, user)["data"]["recommendations"],
            }
        elif report_type == "finance":
            data = _finance_data(db, body.account_id)
        elif report_type == "reactivation":
            data = _reactivation_data(db, body.account_id)
        elif report_type == "ab_tests":
            data = _ab_data(db, body.account_id, body.period_days, body.date_from, body.date_to)
        else:
            data = _advertising_data(db, body.account_id, body.period_days, body.date_from, body.date_to)
        actual_days = int((data.get("period") or {}).get("days") or body.period_days)
        message = _share_text(report_type, perms["account_name"], data, actual_days, body.date_from, body.date_to)
    finally:
        db.close()
    # Reuse the canonical Messenger send path for membership/RBAC, event and push.
    from app.api.internal_messenger import MessageCreate, send_message
    result = send_message(int(body.conversation_id), MessageCreate(body=message), user=user)
    return {"status": "ok", "report_type": report_type, "message": result.get("message")}
