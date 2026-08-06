# -*- coding: utf-8 -*-
"""Мини-CRM внутри Единого центра сообщений: напоминания и задачи по клиенту.
Первая версия персональная — без исполнителей и ролей (решение 04.08)."""
import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from app.db.session import SessionLocal
try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/crm", tags=["crm"])

TASK_TYPES = ("позвонить", "написать", "отправить фото", "отправить КП",
              "запросить оплату", "уточнить доставку", "другое")


def _own(db, user, account_id):
    """Аккаунт принадлежит текущему пользователю — иначе чужие задачи не отдаём."""
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    row = db.execute(text("select 1 from accounts where account_id=:a and owner_user_id=:u"),
                     {"a": account_id, "u": uid}).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    return uid


def _shape(r, today):
    """Статус для показа считаем от даты, а не храним — иначе пришлось бы пересчитывать каждую ночь."""
    status = r.status
    if status != "done":
        if r.due_date < today:
            status = "overdue"
        elif r.due_date == today:
            status = "today"
        else:
            status = "planned"
    return {"id": r.id, "chat_id": r.avito_chat_id,
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "due_time": r.due_time or "", "type": r.task_type, "title": r.title,
            "comment": r.comment or "", "status": status,
            "done_at": r.done_at.isoformat() if r.done_at else None,
            "done_comment": r.done_comment or ""}


@router.get("/tasks")
def list_tasks(account_id: str, chat_id: str = "", _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _own(db, _cur, account_id)
        today = datetime.date.today()
        q = "select * from crm_tasks where account_id=:a"
        p = {"a": account_id}
        if chat_id:
            q += " and avito_chat_id=:c"
            p["c"] = chat_id
        q += " order by (status='done'), due_date, due_time nulls last, id"
        rows = db.execute(text(q), p).fetchall()
        items = [_shape(r, today) for r in rows]
        return {"status": "ok", "tasks": items,
                "counts": {k: sum(1 for i in items if i["status"] == k)
                           for k in ("today", "overdue", "planned", "done")}}
    finally:
        db.close()


@router.get("/badges")
def badges(account_id: str, _cur=Depends(get_current_user)):
    """Счётчики по всем диалогам сразу — для меток в списке, без запроса на каждый диалог."""
    db = SessionLocal()
    try:
        _own(db, _cur, account_id)
        today = datetime.date.today()
        rows = db.execute(text(
            "select avito_chat_id, count(*) filter (where due_date < :t) as overdue, "
            "count(*) filter (where due_date = :t) as today, count(*) as total "
            "from crm_tasks where account_id=:a and status<>'done' and avito_chat_id is not null "
            "group by avito_chat_id"), {"a": account_id, "t": today}).fetchall()
        return {"status": "ok", "badges": {r[0]: {"overdue": r[1], "today": r[2], "total": r[3]} for r in rows}}
    finally:
        db.close()


class TaskIn(BaseModel):
    account_id: str
    chat_id: str = ""
    due_date: str
    due_time: str = ""
    type: str = "позвонить"
    title: str
    comment: str = ""


@router.post("/tasks")
def create_task(body: TaskIn, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        uid = _own(db, _cur, body.account_id)
        if not (body.title or "").strip():
            raise HTTPException(status_code=422, detail="Не указан заголовок задачи")
        try:
            d = datetime.date.fromisoformat(body.due_date[:10])
        except Exception:
            raise HTTPException(status_code=422, detail="Неверная дата")
        t = (body.type or "").strip() or "другое"
        if t not in TASK_TYPES:
            t = "другое"
        r = db.execute(text(
            "insert into crm_tasks (account_id, avito_chat_id, due_date, due_time, task_type, title, comment, created_by) "
            "values (:a,:c,:d,:tm,:tp,:ti,:cm,:u) returning id"),
            {"a": body.account_id, "c": body.chat_id or None, "d": d, "tm": (body.due_time or "")[:5],
             "tp": t, "ti": body.title.strip()[:300], "cm": (body.comment or "").strip(), "u": str(uid)}).fetchone()
        db.commit()
        return {"status": "ok", "id": r[0]}
    finally:
        db.close()


class TaskAction(BaseModel):
    account_id: str
    task_id: int
    comment: str = ""
    due_date: str = ""
    due_time: str = ""
    title: str = ""
    type: str = ""


@router.post("/tasks/done")
def done_task(body: TaskAction, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _own(db, _cur, body.account_id)
        db.execute(text("update crm_tasks set status='done', done_at=now(), done_comment=:c, updated_at=now() "
                        "where id=:i and account_id=:a"),
                   {"c": (body.comment or "").strip(), "i": body.task_id, "a": body.account_id})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/tasks/update")
def update_task(body: TaskAction, _cur=Depends(get_current_user)):
    """Перенос срока и правка — одной ручкой: меняем только присланные поля."""
    db = SessionLocal()
    try:
        _own(db, _cur, body.account_id)
        sets, p = [], {"i": body.task_id, "a": body.account_id}
        if body.due_date:
            try:
                p["d"] = datetime.date.fromisoformat(body.due_date[:10])
            except Exception:
                raise HTTPException(status_code=422, detail="Неверная дата")
            sets.append("due_date=:d")
        if body.due_time:
            sets.append("due_time=:tm"); p["tm"] = body.due_time[:5]
        if body.title:
            sets.append("title=:ti"); p["ti"] = body.title.strip()[:300]
        if body.type and body.type in TASK_TYPES:
            sets.append("task_type=:tp"); p["tp"] = body.type
        if body.comment:
            sets.append("comment=:cm"); p["cm"] = body.comment.strip()
        if not sets:
            return {"status": "ok", "changed": 0}
        sets.append("status='planned'")
        sets.append("updated_at=now()")
        db.execute(text("update crm_tasks set " + ", ".join(sets) + " where id=:i and account_id=:a"), p)
        db.commit()
        return {"status": "ok", "changed": len(sets)}
    finally:
        db.close()


@router.post("/tasks/delete")
def delete_task(body: TaskAction, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _own(db, _cur, body.account_id)
        db.execute(text("delete from crm_tasks where id=:i and account_id=:a"),
                   {"i": body.task_id, "a": body.account_id})
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/today")
def today_tasks(_cur=Depends(get_current_user)):
    """Сводка для колокольчика: задачи на сегодня и просроченные по ВСЕМ
    аккаунтам пользователя. Отдельная ручка, потому что /tasks и /badges
    работают в рамках одного аккаунта, а шапка кабинета — общая."""
    uid = getattr(_cur, "id", None) or (_cur.get("id") if isinstance(_cur, dict) else None)
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            select t.id, t.account_id, t.avito_chat_id, t.due_date, t.due_time,
                   t.task_type, t.title, a.name
              from crm_tasks t
              join accounts a on a.account_id = t.account_id
             where a.owner_user_id = :u
               and t.status = 'planned'
               and t.due_date <= CURRENT_DATE
             order by t.due_date asc, coalesce(t.due_time, '99:99') asc
             limit 50
        """), {"u": uid}).fetchall()

        from datetime import date as _d
        today = _d.today()
        items, n_today, n_late = [], 0, 0
        for r in rows:
            late = r.due_date < today
            if late:
                n_late += 1
            else:
                n_today += 1
            items.append({
                "id": r.id, "account_id": r.account_id, "avito_chat_id": r.avito_chat_id,
                "дата": r.due_date.isoformat(), "время": r.due_time or "",
                "тип": r.task_type, "заголовок": r.title,
                "аккаунт": r.name or r.account_id,
                "просрочено": late,
            })
        return {"status": "ok", "всего": len(items),
                "сегодня": n_today, "просрочено": n_late, "задачи": items}
    finally:
        db.close()
