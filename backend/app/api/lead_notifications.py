# -*- coding: utf-8 -*-
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.auth import get_current_user
from app.services import lead_notifications as svc

router = APIRouter(prefix="/api/lead-notifications", tags=["lead-notifications"])
public_router = APIRouter(prefix="/api/public/lead-notifications", tags=["lead-notifications-public"])

def _uid(user):
    return int(getattr(user, "id", 0) or 0)

def _assert_access(account_id: str, user) -> None:
    if not account_id:
        raise HTTPException(400, "account_id required")
    if str(getattr(user, "role", "") or "") == "owner":
        return
    uid=_uid(user)
    db=SessionLocal()
    try:
        if str(getattr(user,"account_id","") or "") == account_id:
            return
        if db.execute(text("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u"),{"a":account_id,"u":uid}).first():
            return
        if db.execute(text("SELECT 1 FROM user_account_access WHERE account_id=:a AND user_id=:u AND can_view=true"),{"a":account_id,"u":uid}).first():
            return
    finally: db.close()
    raise HTTPException(403, "ACCOUNT_ACCESS_DENIED")

@router.get("/settings")
def settings(account_id: str, user=Depends(get_current_user)):
    _assert_access(account_id,user)
    return {"status":"ok","settings":svc.get_settings(account_id)}

@router.post("/settings")
def save_settings(body: dict=Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); _assert_access(account_id,user)
    return {"status":"ok","settings":svc.save_settings(account_id,body)}

@router.get("/vapid-public-key")
def vapid_public_key(account_id: str, user=Depends(get_current_user)):
    _assert_access(account_id,user)
    key=svc.public_key()
    if not key: raise HTTPException(503,"WEB_PUSH_NOT_CONFIGURED")
    return {"status":"ok","public_key":key}

@router.post("/subscribe")
def subscribe(request: Request, body: dict=Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); _assert_access(account_id,user)
    result=svc.register_subscription(account_id,_uid(user),body.get("subscription") or {},request.headers.get("user-agent",""))
    if result.get("status")!="ok": raise HTTPException(400,"INVALID_PUSH_SUBSCRIPTION")
    return result

@router.post("/unsubscribe")
def unsubscribe(body: dict=Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); _assert_access(account_id,user)
    return svc.unsubscribe(account_id,str(body.get("endpoint") or ""))

@router.get("/max/status")
def max_status(account_id: str, live: bool=True, user=Depends(get_current_user)):
    _assert_access(account_id,user)
    return svc.max_status(account_id,live=bool(live))

@router.post("/max/connect")
def connect_max(body: dict=Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); _assert_access(account_id,user)
    if str(body.get("max_bot_token") or "").strip():
        svc.save_settings(account_id,{**svc.get_settings(account_id),"max_enabled":True,"max_bot_token":str(body.get("max_bot_token") or "").strip()})
    result=svc.connect_max_webhook(account_id)
    if result.get("status") in {"token_required","invalid_token","max_error","max_unavailable"}:
        raise HTTPException(400,result)
    return result

@router.post("/max/test")
def max_test(body: dict=Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); _assert_access(account_id,user)
    return svc.test_max_delivery(account_id)

@public_router.post("/max/webhook/{account_id}")
def max_webhook(account_id: str, request: Request, body: dict=Body(...)):
    result=svc.handle_max_webhook(account_id,request.headers.get("x-max-bot-api-secret", ""),body or {})
    if result.get("status")=="unauthorized": raise HTTPException(401,"INVALID_MAX_WEBHOOK_SECRET")
    if result.get("status")=="not_configured": raise HTTPException(404,"MAX_WEBHOOK_NOT_CONFIGURED")
    return {"ok":True,"status":result.get("status")}

@router.post("/test")
def test_notification(body: dict=Body(...), user=Depends(get_current_user)):
    account_id=str(body.get("account_id") or "").strip(); _assert_access(account_id,user)
    return svc.queue_event(
        account_id,
        "manual-test:%s:%s"%(_uid(user),int(__import__("time").time())),
        "test","🔔 Тест BORIS","Уведомления о лидах подключены. Это тестовая доставка.",
        severity="critical",url="/messages?account_id="+account_id,payload={"test":True},
    )

@router.get("/outbox")
def outbox(account_id: str, limit: int=50, user=Depends(get_current_user)):
    _assert_access(account_id,user); svc.ensure_schema(); db=SessionLocal()
    try:
        rows=db.execute(text("""
          SELECT id,event_type,severity,title,status,attempts,last_error,created_at,sent_at
          FROM lead_notification_outbox WHERE account_id=:a ORDER BY id DESC LIMIT :l
        """),{"a":account_id,"l":max(1,min(limit,100))}).mappings().all()
        return {"status":"ok","items":[dict(x) for x in rows]}
    finally: db.close()
