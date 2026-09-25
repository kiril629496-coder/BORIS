# -*- coding: utf-8 -*-
"""Мини-CRM внутри Единого центра сообщений: напоминания и задачи по клиенту.
Первая версия персональная — без исполнителей и ролей (решение 04.08).

Логика вынесена в app/services/crm_service.py, чтобы ручки и Command Center
работали через ОДИН слой. Контракты ответов не изменились.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.session import SessionLocal
from app.services import crm_service as svc

try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/crm", tags=["crm"])

TASK_TYPES = svc.TASK_TYPES

_ERR = {"no_title": "Не указан заголовок задачи", "bad_date": "Неверная дата"}


def _uid(user):
    return getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)


def _own(db, user, account_id):
    """Аккаунт принадлежит текущему пользователю — иначе чужие задачи не отдаём."""
    uid = _uid(user)
    if not svc.own(db, uid, account_id):
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    return uid


def _bad(e):
    return HTTPException(status_code=422, detail=_ERR.get(str(e), "Неверные данные"))


@router.get("/tasks")
def list_tasks(account_id: str, chat_id: str = "", _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _own(db, _cur, account_id)
        res = svc.list_tasks(db, account_id, chat_id)
        return {"status": "ok", "tasks": res["tasks"], "counts": res["counts"]}
    finally:
        db.close()


@router.get("/badges")
def badges(account_id: str, _cur=Depends(get_current_user)):
    """Счётчики по всем диалогам сразу — для меток в списке."""
    db = SessionLocal()
    try:
        _own(db, _cur, account_id)
        return {"status": "ok", "badges": svc.badges(db, account_id)}
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
        try:
            new_id = svc.create_task(db, uid, body.account_id, body.title,
                                     body.due_date, chat_id=body.chat_id,
                                     due_time=body.due_time, task_type=body.type,
                                     comment=body.comment)
        except ValueError as e:
            raise _bad(e)
        return {"status": "ok", "id": new_id}
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
        svc.done_task(db, body.account_id, body.task_id, body.comment)
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/tasks/update")
def update_task(body: TaskAction, _cur=Depends(get_current_user)):
    """Перенос срока и правка — одной ручкой: меняем только присланные поля."""
    db = SessionLocal()
    try:
        _own(db, _cur, body.account_id)
        try:
            changed = svc.update_task(db, body.account_id, body.task_id,
                                      due_date=body.due_date, due_time=body.due_time,
                                      title=body.title, task_type=body.type,
                                      comment=body.comment)
        except ValueError as e:
            raise _bad(e)
        return {"status": "ok", "changed": changed}
    finally:
        db.close()


@router.post("/tasks/delete")
def delete_task(body: TaskAction, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _own(db, _cur, body.account_id)
        svc.delete_task(db, body.account_id, body.task_id)
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/today")
def today_tasks(_cur=Depends(get_current_user)):
    """Сводка для колокольчика по всем аккаунтам пользователя."""
    db = SessionLocal()
    try:
        res = svc.today_tasks(db, _uid(_cur))
        res["status"] = "ok"
        return res
    finally:
        db.close()
