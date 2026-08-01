"""Шаблоны быстрых ответов «Единого окна сообщений».
Независимый сервис Inbox: хранение, поиск, категории, переменные, счётчик
использований. БЕЗ GPT — позже AI сможет рекомендовать шаблон поверх этой базы,
не меняя её структуру.
"""
import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/inbox", tags=["inbox-templates"])

CATEGORIES = ["Первое приветствие", "Стоимость", "Доставка", "Оплата", "Наличие",
              "Сроки", "Благодарность", "Завершение сделки", "Возражения", "Свои"]

VARIABLES = ["client_name", "item_title", "price", "account_name", "today"]


def _d(r):
    return dict(r._mapping)


def _render(db, tpl_text, account_id="", avito_chat_id=""):
    """Подстановка переменных. Неизвестные НЕ затираем — оставляем {{...}},
    чтобы оператор увидел и дописал руками (например цену, которой у нас нет)."""
    vals = {"client_name": "Покупатель",
            "today": (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).strftime("%d.%m.%Y")}
    if account_id:
        nm = db.execute(text("SELECT account_name FROM account_slots WHERE account_id=:a LIMIT 1"),
                        {"a": account_id}).scalar()
        if nm:
            vals["account_name"] = nm
    if account_id and avito_chat_id:
        it = db.execute(text("SELECT item_title FROM messenger_messages WHERE account_id=:a "
                             "AND avito_chat_id=:c AND item_title IS NOT NULL LIMIT 1"),
                        {"a": account_id, "c": avito_chat_id}).scalar()
        if it:
            vals["item_title"] = it
    out = tpl_text or ""
    for k, v in vals.items():
        out = out.replace("{{%s}}" % k, str(v))
    return out


class TplSave(BaseModel):
    id: int = 0
    name: str
    text: str
    category: str = "Свои"
    scope: str = "global"          # global | personal
    hotkey: str = ""
    sort_order: int = 100
    is_active: bool = True


class TplId(BaseModel):
    id: int


class TplPin(BaseModel):
    id: int
    pinned: bool = True


class TplUse(BaseModel):
    id: int
    account_id: str = ""
    avito_chat_id: str = ""


@router.get("/templates/meta")
def tpl_meta(user=Depends(get_current_user)):
    return {"status": "ok", "categories": CATEGORIES, "variables": VARIABLES}


@router.get("/templates")
def tpl_list(q: str = "", category: str = "", include_inactive: bool = False,
             user=Depends(get_current_user)):
    """Видимость: общие шаблоны организации + личные текущего сотрудника."""
    _q = (q or "").strip().lower()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT * FROM inbox_templates "
            " WHERE owner_user_id = :own "
            "   AND (scope = 'global' OR user_id = :uid) "
            " ORDER BY is_pinned DESC, sort_order ASC, use_count DESC, id ASC"),
            {"own": user.id, "uid": user.id}).all()
        items = [_d(r) for r in rows]
        if not include_inactive:
            items = [i for i in items if i.get("is_active")]
        if category:
            items = [i for i in items if (i.get("category") or "") == category]
        if _q:
            items = [i for i in items
                     if _q in str(i.get("name") or "").lower()
                     or _q in str(i.get("text") or "").lower()
                     or _q in str(i.get("category") or "").lower()]
        for i in items:
            for k in ("created_at", "updated_at", "last_used_at"):
                if i.get(k):
                    i[k] = i[k].isoformat()
        return {"status": "ok", "templates": items, "total": len(items)}
    finally:
        db.close()


@router.post("/templates/save")
def tpl_save(req: TplSave, user=Depends(get_current_user)):
    if not (req.name or "").strip() or not (req.text or "").strip():
        return {"status": "error", "message": "Название и текст обязательны"}
    scope = "personal" if req.scope == "personal" else "global"
    db = SessionLocal()
    try:
        if req.id:
            own = db.execute(text("SELECT owner_user_id FROM inbox_templates WHERE id=:i"),
                             {"i": req.id}).scalar()
            if own != user.id:
                return {"status": "error", "message": "Шаблон не найден"}
            db.execute(text(
                "UPDATE inbox_templates SET name=:n, text=:t, category=:c, scope=:s, "
                " user_id=:uid, hotkey=:h, sort_order=:so, is_active=:act, updated_at=now() "
                " WHERE id=:i"),
                {"n": req.name.strip(), "t": req.text, "c": req.category, "s": scope,
                 "uid": user.id if scope == "personal" else None, "h": req.hotkey or None,
                 "so": req.sort_order, "act": req.is_active, "i": req.id})
            db.commit()
            return {"status": "ok", "id": req.id}
        new_id = db.execute(text(
            "INSERT INTO inbox_templates (owner_user_id, user_id, scope, name, text, category, "
            " hotkey, sort_order, is_active, is_pinned, use_count, created_at, updated_at) "
            "VALUES (:own,:uid,:s,:n,:t,:c,:h,:so,:act,false,0,now(),now()) RETURNING id"),
            {"own": user.id, "uid": user.id if scope == "personal" else None, "s": scope,
             "n": req.name.strip(), "t": req.text, "c": req.category,
             "h": req.hotkey or None, "so": req.sort_order, "act": req.is_active}).scalar()
        db.commit()
        return {"status": "ok", "id": new_id}
    finally:
        db.close()


@router.post("/templates/delete")
def tpl_delete(req: TplId, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        r = db.execute(text("DELETE FROM inbox_templates WHERE id=:i AND owner_user_id=:own"),
                       {"i": req.id, "own": user.id})
        db.commit()
        return {"status": "ok" if r.rowcount else "error",
                "message": "" if r.rowcount else "Шаблон не найден"}
    finally:
        db.close()


@router.post("/templates/pin")
def tpl_pin(req: TplPin, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        r = db.execute(text("UPDATE inbox_templates SET is_pinned=:p, updated_at=now() "
                            " WHERE id=:i AND owner_user_id=:own"),
                       {"p": bool(req.pinned), "i": req.id, "own": user.id})
        db.commit()
        return {"status": "ok" if r.rowcount else "error"}
    finally:
        db.close()


@router.post("/templates/use")
def tpl_use(req: TplUse, user=Depends(get_current_user)):
    """Отдаёт готовый текст с подставленными переменными и считает использование."""
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM inbox_templates WHERE id=:i AND owner_user_id=:own"),
                         {"i": req.id, "own": user.id}).first()
        if not row:
            return {"status": "error", "message": "Шаблон не найден"}
        tpl = _d(row)
        rendered = _render(db, tpl.get("text"), req.account_id, req.avito_chat_id)
        db.execute(text("UPDATE inbox_templates SET use_count = coalesce(use_count,0)+1, "
                        " last_used_at = now() WHERE id=:i"), {"i": req.id})
        db.commit()
        return {"status": "ok", "text": rendered, "name": tpl.get("name")}
    finally:
        db.close()
