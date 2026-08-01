"""Универсальная очередь заявок на подключение категорий.

Хранение — в Storage (key="category_requests"), без отдельной таблицы:
миграция боевой БД тут не нужна, объёмы копеечные, механизм уже
используется под tree_cache, card_answers и seller_defaults.
Когда очередь перерастёт Storage — выносится в таблицу без спешки,
формат записи под это уже заложен.

К ADVIZ не привязано: это очередь на подключение ЛЮБОЙ категории,
источник структуры может быть любым.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.models.storage import Storage

router = APIRouter(prefix="/api/category-requests", tags=["category-requests"])

KEY = "category_requests"
STATUSES = ("new", "processing", "completed", "rejected")
MAX_KEEP = 500


class CategoryRequestIn(BaseModel):
    account_id: str
    scenario_id: Optional[str] = None
    category_query: Optional[str] = ""
    category_state: Optional[str] = ""
    category_reason: Optional[str] = ""
    path: Optional[str] = ""
    question: Optional[str] = ""
    options: Optional[List[str]] = None
    context: Optional[Dict[str, Any]] = None


class StatusIn(BaseModel):
    account_id: str
    status: str
    note: Optional[str] = ""


def _resolver_version() -> str:
    try:
        from app.services.tree_resolver import RESOLVER_VERSION
        return str(RESOLVER_VERSION)
    except Exception:
        return "unknown"


def _load(db, account_id: str) -> List[Dict[str, Any]]:
    row = db.query(Storage).filter(Storage.account_id == account_id,
                                   Storage.key == KEY).first()
    if not row or not row.value:
        return []
    try:
        data = json.loads(row.value)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _save(db, account_id: str, items: List[Dict[str, Any]]) -> None:
    if len(items) > MAX_KEEP:
        items = items[-MAX_KEEP:]
    blob = json.dumps(items, ensure_ascii=False)
    row = db.query(Storage).filter(Storage.account_id == account_id,
                                   Storage.key == KEY).first()
    if row:
        row.value = blob
    else:
        db.add(Storage(account_id=account_id, key=KEY, value=blob))
    db.commit()


@router.post("")
def create_request(req: CategoryRequestIn):
    """Принимает заявку от клиента. Ничего не решает — только ставит в очередь."""
    acc = (req.account_id or "").strip()
    if not acc:
        raise HTTPException(400, "account_id обязателен")
    rec = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "new",
        "account_id": acc,
        "scenario_id": req.scenario_id or "",
        "category_query": (req.category_query or "").strip()[:300],
        "category_state": (req.category_state or "").strip()[:60],
        "category_reason": (req.category_reason or "").strip()[:60],
        "path": (req.path or "").strip()[:500],
        "question": (req.question or "").strip()[:300],
        "options": list(req.options or [])[:20],
        "resolver_version": _resolver_version(),
        "context": req.context or {},
    }
    db = SessionLocal()
    try:
        items = _load(db, acc)
        items.append(rec)
        _save(db, acc, items)
    finally:
        db.close()
    return {"ok": True, "id": rec["id"], "status": rec["status"],
            "created_at": rec["created_at"]}


@router.get("")
def list_requests(account_id: str, status: Optional[str] = None,
                  limit: int = 100):
    """Список заявок аккаунта. Для будущей админки."""
    acc = (account_id or "").strip()
    if not acc:
        raise HTTPException(400, "account_id обязателен")
    if status and status not in STATUSES:
        raise HTTPException(400, "неизвестный статус: %s" % status)
    db = SessionLocal()
    try:
        items = _load(db, acc)
    finally:
        db.close()
    if status:
        items = [x for x in items if x.get("status") == status]
    items = list(reversed(items))[:max(1, min(int(limit or 100), 500))]
    return {"ok": True, "total": len(items), "statuses": list(STATUSES),
            "items": items}


@router.post("/{request_id}/status")
def set_status(request_id: str, req: StatusIn):
    """Смена статуса. Пока не используется — заложено под админку."""
    if req.status not in STATUSES:
        raise HTTPException(400, "статус должен быть одним из %s"
                            % (STATUSES,))
    acc = (req.account_id or "").strip()
    if not acc:
        raise HTTPException(400, "account_id обязателен")
    db = SessionLocal()
    try:
        items = _load(db, acc)
        found = None
        for it in items:
            if it.get("id") == request_id:
                it["status"] = req.status
                it["updated_at"] = datetime.now(timezone.utc).isoformat()
                if req.note:
                    ctx = it.get("context")
                    if not isinstance(ctx, dict):
                        ctx = {}
                    ctx["note"] = str(req.note)[:500]
                    it["context"] = ctx
                found = it
                break
        if not found:
            raise HTTPException(404, "заявка не найдена")
        _save(db, acc, items)
    finally:
        db.close()
    return {"ok": True, "id": request_id, "status": req.status}
