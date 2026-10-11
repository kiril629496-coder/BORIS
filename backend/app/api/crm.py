# -*- coding: utf-8 -*-
"""Мини-CRM внутри Единого центра сообщений: напоминания и задачи по клиенту.
Первая версия персональная — без исполнителей и ролей (решение 04.08).

Логика вынесена в app/services/crm_service.py, чтобы ручки и Command Center
работали через ОДИН слой. Контракты ответов не изменились.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import crm_service as svc
from app.services.exception_observability import observe_suppressed

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
    """Аккаунт доступен текущему пользователю для чтения."""
    uid = _uid(user)
    if not svc.own(db, uid, account_id):
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    return uid


def _write(db, user, account_id):
    """CRM mutation requires reply permission; VIEWER remains strictly read-only."""
    uid = _own(db, user, account_id)
    if db.execute(text(
        "select 1 from accounts where account_id=:a and owner_user_id=:u "
        "union select 1 from user_account_access "
        "where user_id=:u and account_id=:a and can_reply=TRUE limit 1"
    ), {"a": account_id, "u": uid}).fetchone():
        return uid
    raise HTTPException(status_code=403, detail="Роль только для просмотра: изменение CRM недоступно")


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
        uid = _write(db, _cur, body.account_id)
        try:
            new_id = svc.create_task(db, uid, body.account_id, body.title,
                                     body.due_date, chat_id=body.chat_id,
                                     due_time=body.due_time, task_type=body.type,
                                     comment=body.comment)
        except ValueError as e:
            raise _bad(e)
        from app.api.internal_messenger import emit_messenger_event
        emit_messenger_event(db, body.account_id, "crm.task.changed", "crm_task", new_id, {
            "action": "created", "task_id": new_id, "chat_id": body.chat_id,
        })
        db.commit()
        try:
            from app.services import lead_notifications as notification_service
            notification_service.queue_event(
                body.account_id,
                f"crm-task-created:{body.account_id}:{new_id}",
                "crm_task",
                "CRM · Новая задача",
                body.title.strip()[:500],
                url=f"/messenger?account_id={body.account_id}&client_chat_id={body.chat_id}&task_id={new_id}",
                payload={
                    "target_user_ids": [uid] if uid else [],
                    "task_id": new_id,
                    "client_chat_id": body.chat_id,
                },
            )
        except Exception as _push_exc:
            # Push is secondary: task persistence/realtime must stay successful,
            # but the failure remains observable.
            observe_suppressed(__name__, _push_exc, line=119)
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
        _write(db, _cur, body.account_id)
        svc.done_task(db, body.account_id, body.task_id, body.comment)
        from app.api.internal_messenger import emit_messenger_event
        emit_messenger_event(db, body.account_id, "crm.task.changed", "crm_task", body.task_id, {
            "action": "done", "task_id": body.task_id,
        })
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/tasks/update")
def update_task(body: TaskAction, _cur=Depends(get_current_user)):
    """Перенос срока и правка — одной ручкой: меняем только присланные поля."""
    db = SessionLocal()
    try:
        _write(db, _cur, body.account_id)
        try:
            changed = svc.update_task(db, body.account_id, body.task_id,
                                      due_date=body.due_date, due_time=body.due_time,
                                      title=body.title, task_type=body.type,
                                      comment=body.comment)
        except ValueError as e:
            raise _bad(e)
        from app.api.internal_messenger import emit_messenger_event
        emit_messenger_event(db, body.account_id, "crm.task.changed", "crm_task", body.task_id, {
            "action": "updated", "task_id": body.task_id, "changed": changed,
        })
        db.commit()
        return {"status": "ok", "changed": changed}
    finally:
        db.close()


@router.post("/tasks/delete")
def delete_task(body: TaskAction, _cur=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _write(db, _cur, body.account_id)
        svc.delete_task(db, body.account_id, body.task_id)
        from app.api.internal_messenger import emit_messenger_event
        emit_messenger_event(db, body.account_id, "crm.task.changed", "crm_task", body.task_id, {
            "action": "deleted", "task_id": body.task_id,
        })
        db.commit()
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
