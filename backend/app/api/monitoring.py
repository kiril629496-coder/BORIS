# -*- coding: utf-8 -*-
"""
API экрана «Поиск клиентов» (Рост -> Поиск клиентов).

Тонкая обвязка: весь SQL живёт в app/monitoring/store.py и покрыт тестами.
Здесь только маршруты, доступ и приведение типов.

ДОСТУП: весь роутер — только владелец. Мониторинг ищет клиентов для БОРИСа,
это не клиентские данные. Правило проекта: любой эндпоинт с общей статистикой
закрывается require_owner.

Подключение в main.py — две строки рядом с остальными роутерами:
    from app.api.monitoring import router as monitoring_router
    app.include_router(monitoring_router)
"""
from __future__ import annotations

from typing import Any
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import require_owner
from app.monitoring import budget, store
from app.api.monitoring_outcomes import router as monitoring_outcomes_router

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])
router.include_router(monitoring_outcomes_router)


def _conn():
    return store.connect()


@router.get("/overview")
def overview(days: int = Query(7, ge=1, le=90), _=Depends(require_owner)) -> dict[str, Any]:
    """Сводка для шапки экрана: находки по категориям, лиды, последний прогон."""
    con = _conn()
    try:
        with con, con.cursor() as cur:
            return store.dashboard_stats(cur, days=days)
    finally:
        con.close()


@router.get("/sales-autopilot")
def sales_autopilot(_=Depends(require_owner)) -> dict[str, Any]:
    """Live owner-facing state of the canonical Telegram sales autopilot."""
    con = _conn()
    try:
        with con, con.cursor() as cur:
            react_max_age=int(os.getenv('TG_CONTACT_REACT_MAX_AGE_DAYS','365') or 365)
            cur.execute(f"""
              SELECT
                count(*) FILTER (WHERE (contacted_at AT TIME ZONE 'Europe/Moscow')::date = (now() AT TIME ZONE 'Europe/Moscow')::date AND outbound_transport='telegram_personal') AS sent_today,
                count(*) FILTER (WHERE (followup_sent_at AT TIME ZONE 'Europe/Moscow')::date = (now() AT TIME ZONE 'Europe/Moscow')::date) AS followups_today,
                count(*) FILTER (WHERE status='qualified') AS qualified_total,
                count(*) FILTER (WHERE status IN ('replied','qualified')) AS replied_total,
                count(*) FILTER (WHERE status='new' AND monitor_intent IS DISTINCT FROM 'existing_contact_reactivation'
                                 AND boris_fit>=75 AND coalesce(source_posted_at,discovered_at)>=now()-interval '72 hours') AS fresh_external_waiting,
                count(*) FILTER (WHERE status='new' AND monitor_intent='existing_contact_reactivation' AND boris_fit>=75
                                 AND approval_required=FALSE AND (contact_username IS NOT NULL OR contact_peer_id IS NOT NULL)
                                 AND coalesce(source_posted_at,discovered_at)>=now()-make_interval(days => {react_max_age})) AS reactivation_waiting,
                count(*) FILTER (WHERE status='skipped' AND approval_note LIKE 'reactivation_%filter_v%') AS safety_filtered_total
              FROM boris_sales_hot_leads
            """)
            row=cur.fetchone() or (0,0,0,0,0,0,0)
            cur.execute("SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=TRUE")
            active_sources=int((cur.fetchone() or [0])[0] or 0)
            cur.execute("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_scan_cooldown_until'")
            cooldown_raw=(cur.fetchone() or [None])[0]
            cooldown_until=None
            scan_state='active'
            if cooldown_raw:
                try:
                    from datetime import datetime, timezone
                    cooldown_dt=datetime.fromisoformat(str(cooldown_raw).replace('Z','+00:00'))
                    if cooldown_dt.tzinfo is None:
                        cooldown_dt=cooldown_dt.replace(tzinfo=timezone.utc)
                    if cooldown_dt>datetime.now(timezone.utc):
                        cooldown_until=cooldown_dt.isoformat()
                        scan_state='cooldown'
                except Exception:
                    cooldown_until=None
            cur.execute("""
              SELECT max(coalesce(contacted_at,followup_sent_at,updated_at))
              FROM boris_sales_hot_leads
              WHERE outbound_transport='telegram_personal' OR followup_sent_at IS NOT NULL
            """)
            last_activity=(cur.fetchone() or [None])[0]
            return {
              'enabled': True,
              'mode': 'autopilot',
              'sent_today': int(row[0] or 0),
              'followups_today': int(row[1] or 0),
              'qualified_total': int(row[2] or 0),
              'replied_total': int(row[3] or 0),
              'fresh_external_waiting': int(row[4] or 0),
              'reactivation_waiting': int(row[5] or 0),
              'safety_filtered_total': int(row[6] or 0),
              'active_sources': active_sources,
              'scan_state': scan_state,
              'scan_cooldown_until': cooldown_until,
              'daily_cap': int(os.getenv('HOT_LEAD_AUTO_DAILY_CAP','10') or 10),
              'min_fit': int(os.getenv('HOT_LEAD_AUTO_MIN_FIT','75') or 75),
              'first_contact_interval_min': int(os.getenv('HOT_LEAD_AUTO_MIN_INTERVAL_MINUTES','10') or 10),
              'send_window': f"{os.getenv('HOT_LEAD_AUTO_SEND_START_HOUR','10')}:00–{os.getenv('HOT_LEAD_AUTO_SEND_END_HOUR','19')}:00 МСК",
              'last_activity_at': last_activity.isoformat() if last_activity else None,
            }
    finally:
        con.close()


@router.get("/findings")
def findings(
    status: str | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    source_id: str | None = None,
    q: str | None = None,
    hide_repeats: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _=Depends(require_owner),
) -> dict[str, Any]:
    con = _conn()
    try:
        with con, con.cursor() as cur:
            return store.list_findings(
                cur, status=status, min_score=min_score, source_id=source_id,
                query=q, hide_repeats=hide_repeats, limit=limit, offset=offset)
    finally:
        con.close()


@router.get("/findings/{message_id}")
def finding(message_id: str, _=Depends(require_owner)) -> dict[str, Any]:
    con = _conn()
    try:
        with con, con.cursor() as cur:
            item = store.finding_detail(cur, message_id)
    finally:
        con.close()
    if not item:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    return item


@router.post("/findings/{message_id}/status")
def change_status(message_id: str, status: str = Query(...),
                  _=Depends(require_owner)) -> dict[str, Any]:
    allowed = {"new", "seen", "rejected", "in_work", "closed", "lead"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Статус вне списка: {sorted(allowed)}")
    con = _conn()
    try:
        with con, con.cursor() as cur:
            store.set_status(cur, message_id, status)
    finally:
        con.close()
    return {"ok": True, "status": status}


@router.post("/findings/{message_id}/reply")
def reply(message_id: str, _=Depends(require_owner)) -> dict[str, Any]:
    """
    Готовит черновик ответа. НИКОМУ ничего не отправляет — текст возвращается
    на экран, писать человеку менеджер идёт сам.
    """
    con = _conn()
    try:
        with con, con.cursor() as cur:
            text = store.prepare_reply(cur, message_id)
    finally:
        con.close()
    if not text:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    return {"reply": text, "sent": False}


@router.post("/findings/{message_id}/lead")
def make_lead(message_id: str, manager_email: str | None = None,
              _=Depends(require_owner)) -> dict[str, Any]:
    """Повторный вызов лид не дублирует — возвращает существующий."""
    con = _conn()
    try:
        with con, con.cursor() as cur:
            lead_id = store.create_lead(cur, message_id, manager_email)
    finally:
        con.close()
    if not lead_id:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    return {"lead_id": lead_id}


@router.get("/leads")
def leads(status: str | None = None, limit: int = Query(50, ge=1, le=200),
          _=Depends(require_owner)) -> dict[str, Any]:
    con = _conn()
    try:
        with con, con.cursor() as cur:
            return {"items": store.list_leads(cur, status=status, limit=limit)}
    finally:
        con.close()


@router.get("/sources")
def sources(_=Depends(require_owner)) -> dict[str, Any]:
    con = _conn()
    try:
        with con, con.cursor() as cur:
            return {"items": store.load_sources(cur)}
    finally:
        con.close()


@router.get("/budget")
def budget_status(_=Depends(require_owner)) -> dict[str, Any]:
    """Бюджет, потрачено, остаток, прогноз до конца месяца, средняя цена анализа."""
    con = _conn()
    try:
        with con, con.cursor() as cur:
            return budget.forecast(cur)
    finally:
        con.close()


@router.post("/scan")
def request_scan(_=Depends(require_owner)) -> dict[str, Any]:
    """
    Ставит заявку на внеочередной прогон. Сам сбор делает monitor_runner
    по расписанию — веб-процесс в Telegram не ходит.
    """
    con = _conn()
    try:
        with con, con.cursor() as cur:
            store.request_scan(cur)
    finally:
        con.close()
    return {"ok": True, "queued": True}
