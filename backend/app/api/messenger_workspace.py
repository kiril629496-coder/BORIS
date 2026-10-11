# -*- coding: utf-8 -*-
"""Legal-gated facade for BORIS Messenger shared business APIs.

The main BORIS dashboard keeps using canonical shared APIs. This facade reuses
those exact functions but requires current Messenger legal acceptance first,
so the Messenger workspace cannot be used through direct API calls before the
user accepts the current terms/privacy versions.
"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from app.api.legal import require_messenger_legal_user
from app.api import inbox_slots as inbox_api
from app.api import messenger as messenger_api
from app.api import crm as crm_api
from app.api import inbox_export as export_api
from app.db.session import SessionLocal
from app.services import messenger_voice as voice_service
from app.services import messenger_video as video_service

router = APIRouter(prefix="/api/messenger-workspace", tags=["messenger-workspace"])


@router.get("/voice/{asset_id}/stream")
def stream_voice(asset_id: UUID, request: Request):
    ticket = str(request.cookies.get(voice_service.VOICE_STREAM_COOKIE) or "")
    db = SessionLocal()
    try:
        return voice_service.playback_ticket_response(
            db,
            asset_id,
            ticket,
            request.headers.get("range"),
        )
    finally:
        db.close()


@router.get("/video/{asset_id}/stream")
def stream_video(asset_id: UUID, request: Request):
    ticket = str(request.cookies.get(video_service.VIDEO_STREAM_COOKIE) or "")
    db = SessionLocal()
    try:
        return video_service.playback_ticket_response(
            db, asset_id, ticket, request.headers.get("range"),
        )
    finally:
        db.close()


@router.get("/video/{asset_id}/thumbnail")
def thumbnail_video(asset_id: UUID, request: Request):
    ticket = str(request.cookies.get(video_service.VIDEO_STREAM_COOKIE) or "")
    db = SessionLocal()
    try:
        return video_service.thumbnail_ticket_response(db, asset_id, ticket)
    finally:
        db.close()


@router.get("/accounts")
def accounts(user=Depends(require_messenger_legal_user)):
    return inbox_api.inbox_accounts(user=user)


@router.get("/leads")
def leads(account_id: str, stage: str, q: str = "", user=Depends(require_messenger_legal_user)):
    return messenger_api.leads_by_stage_endpoint(account_id=account_id, stage=stage, q=q, user=user)


@router.get("/pending-drafts")
def pending_drafts(account_id: str, user=Depends(require_messenger_legal_user)):
    if not messenger_api._account_access_allowed(account_id, user, need_reply=False):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    return messenger_api.get_pending_drafts(account_id=account_id)


@router.post("/draft/approve")
def approve_draft(body: messenger_api.DraftActionRequest, user=Depends(require_messenger_legal_user)):
    return messenger_api.approve_draft(req=body, user=user)


@router.post("/draft/discard")
def discard_draft(body: messenger_api.DraftActionRequest, user=Depends(require_messenger_legal_user)):
    return messenger_api.discard_draft(req=body, user=user)


@router.get("/dialog")
def dialog(
    account_id: str,
    avito_chat_id: str,
    format: str = "json",
    date_from: str = "",
    date_to: str = "",
    user=Depends(require_messenger_legal_user),
):
    return export_api.export_dialog(
        account_id=account_id,
        avito_chat_id=avito_chat_id,
        format=format,
        date_from=date_from,
        date_to=date_to,
        user=user,
    )


@router.get("/tasks")
def tasks(account_id: str, chat_id: str = "", user=Depends(require_messenger_legal_user)):
    return crm_api.list_tasks(account_id=account_id, chat_id=chat_id, _cur=user)


@router.post("/tasks")
def create_task(body: crm_api.TaskIn, user=Depends(require_messenger_legal_user)):
    return crm_api.create_task(body=body, _cur=user)


@router.post("/tasks/done")
def done_task(body: crm_api.TaskAction, user=Depends(require_messenger_legal_user)):
    return crm_api.done_task(body=body, _cur=user)


@router.post("/tasks/update")
def update_task(body: crm_api.TaskAction, user=Depends(require_messenger_legal_user)):
    return crm_api.update_task(body=body, _cur=user)


@router.post("/tasks/delete")
def delete_task(body: crm_api.TaskAction, user=Depends(require_messenger_legal_user)):
    return crm_api.delete_task(body=body, _cur=user)


@router.post("/ownership/take")
def take(body: inbox_api.InboxTakeRequest, user=Depends(require_messenger_legal_user)):
    return inbox_api.inbox_take(req=body, user=user)


@router.post("/ownership/release")
def release(body: inbox_api.InboxTakeRequest, user=Depends(require_messenger_legal_user)):
    return inbox_api.inbox_release(req=body, user=user)


@router.post("/ownership/handoff")
def handoff(
    body: inbox_api.InboxTakeRequest,
    action: str = Query(..., pattern="^(human|mop)$"),
    user=Depends(require_messenger_legal_user),
):
    return inbox_api.inbox_mop_handoff(req=body, action=action, user=user)


@router.post("/send")
def send(
    body: inbox_api.InboxSendRequest,
    background: BackgroundTasks,
    user=Depends(require_messenger_legal_user),
):
    return inbox_api.inbox_send(req=body, background=background, user=user)
