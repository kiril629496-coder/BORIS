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

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import require_owner
from app.monitoring import budget, store

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


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
