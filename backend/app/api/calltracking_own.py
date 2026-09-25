from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response

from app.api.auth import get_current_user_or_internal
from app.services.calltracking_own import (
    account_status,
    attach_call,
    guardian,
    pool_add_number,
    pool_list,
    pool_upsert,
    script_source,
    session_allocate,
    session_heartbeat,
    session_release,
    site_list,
    site_upsert,
    static_binding_list,
    static_binding_upsert,
)

router = APIRouter(prefix="/api/calltracking/own", tags=["calltracking-own"])
public_router = APIRouter(prefix="/api/calltracking/v1", tags=["calltracking-public"])


def _actor_user_id(current_user):
    if current_user is None:
        return None
    return getattr(current_user, "id", None) or (current_user.get("id") if isinstance(current_user, dict) else None)


def _actor_role(current_user):
    if current_user is None:
        return None
    return getattr(current_user, "role", None) or (current_user.get("role") if isinstance(current_user, dict) else None)


def _assert_account_access(account_id: str, current_user, owner_only: bool = False) -> None:
    if current_user is None:
        return
    uid = _actor_user_id(current_user)
    if uid is None:
        raise HTTPException(403, "Нет доступа к аккаунту")
    from app.db.session import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        owner = db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a"), {"a": account_id}).scalar()
        if owner is None:
            raise HTTPException(404, "Аккаунт не найден")
        if int(owner) == int(uid):
            return
        if owner_only:
            raise HTTPException(403, "Настройка коллтрекинга доступна только владельцу аккаунта")
        allowed = db.execute(text("""
        SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE
        """), {"u": int(uid), "a": account_id}).first()
        if not allowed:
            raise HTTPException(403, "Нет доступа к аккаунту")
    finally:
        db.close()


def _public_origin(request: Request) -> str:
    # Dynamic calltracking is intentionally cross-origin. Attribution is accepted
    # only when the browser supplies an Origin/Referer matching the site's allowlist.
    origin = str(request.headers.get("origin") or "").strip()
    if origin:
        return origin
    referer = str(request.headers.get("referer") or "").strip()
    if referer:
        return referer
    return ""


@router.get("/status")
def status(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account_access(account_id, current_user)
    return account_status(account_id)


@router.get("/sites")
def sites(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account_access(account_id, current_user)
    return {"status": "ok", "items": site_list(account_id)}


@router.post("/sites")
def save_site(payload: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account_access(account_id, current_user, owner_only=True)
    result = site_upsert(account_id, payload)
    if result.get("status") != "ok":
        raise HTTPException(400, result)
    return result


@router.get("/static-bindings")
def bindings(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account_access(account_id, current_user)
    return {"status": "ok", "items": static_binding_list(account_id)}


@router.post("/static-bindings")
def save_binding(payload: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account_access(account_id, current_user, owner_only=True)
    result = static_binding_upsert(account_id, payload)
    if result.get("status") != "ok":
        raise HTTPException(400, result)
    return result


@router.get("/pools")
def pools(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_account_access(account_id, current_user)
    return {"status": "ok", "items": pool_list(account_id)}


@router.post("/pools")
def save_pool(payload: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account_access(account_id, current_user, owner_only=True)
    result = pool_upsert(account_id, payload)
    if result.get("status") != "ok":
        raise HTTPException(400, result)
    return result


@router.post("/pools/{pool_id}/numbers/{did_id}")
def add_pool_number(pool_id: int, did_id: int, account_id: str,
                    current_user=Depends(get_current_user_or_internal)):
    _assert_account_access(account_id, current_user, owner_only=True)
    result = pool_add_number(account_id, pool_id, did_id)
    if result.get("status") != "ok":
        raise HTTPException(400, result)
    return result


@router.post("/calls/{call_id}/attribute")
def attribute_call(call_id: str, payload: dict = Body(...),
                   current_user=Depends(get_current_user_or_internal)):
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(422, "account_id обязателен")
    _assert_account_access(account_id, current_user)
    return attach_call(account_id, call_id)


@router.post("/guardian")
def run_guardian(payload: dict = Body(default={}), current_user=Depends(get_current_user_or_internal)):
    # This endpoint is owner/internal only; the runtime worker will normally call it automatically.
    if current_user is not None and _actor_role(current_user) != "owner":
        raise HTTPException(403, "Только владелец")
    return guardian(limit=int(payload.get("limit") or 1000))


@public_router.get("/script.js")
def script(site_key: str):
    js = script_source(site_key)
    return Response(
        content=js,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        },
    )


@public_router.post("/session")
def create_public_session(request: Request, site_key: str, payload: dict = Body(...)):
    origin = _public_origin(request)
    if not origin:
        raise HTTPException(403, "Origin обязателен")
    result = session_allocate(site_key, payload, origin)
    if result.get("status") in {"site_not_found_or_origin_denied"}:
        raise HTTPException(403, "Сайт не разрешён")
    if result.get("status") not in {"ok"}:
        # Pool exhaustion is operational state, not an internal exception.
        code = 503 if result.get("status") in {"pool_not_configured", "pool_not_found", "pool_exhausted_no_fallback"} else 400
        raise HTTPException(code, result)
    return result


@public_router.post("/heartbeat")
def public_heartbeat(request: Request, site_key: str, payload: dict = Body(...)):
    origin = _public_origin(request)
    if not origin:
        raise HTTPException(403, "Origin обязателен")
    session_id = str(payload.get("session_id") or "").strip()
    visitor_id = str(payload.get("visitor_id") or "").strip()
    if not session_id or not visitor_id:
        raise HTTPException(422, "session_id и visitor_id обязательны")
    result = session_heartbeat(site_key, session_id, visitor_id, origin)
    if result.get("status") == "site_not_found_or_origin_denied":
        raise HTTPException(403, "Сайт не разрешён")
    if result.get("status") != "ok":
        raise HTTPException(404, result)
    return result


@public_router.post("/release")
async def public_release(request: Request, site_key: str):
    origin = _public_origin(request)
    if not origin:
        raise HTTPException(403, "Origin обязателен")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(422, "Некорректное тело")
    session_id = str((payload or {}).get("session_id") or "").strip()
    visitor_id = str((payload or {}).get("visitor_id") or "").strip()
    if not session_id or not visitor_id:
        raise HTTPException(422, "session_id и visitor_id обязательны")
    result = session_release(site_key, session_id, visitor_id, origin)
    if result.get("status") == "site_not_found_or_origin_denied":
        raise HTTPException(403, "Сайт не разрешён")
    if result.get("status") != "ok":
        raise HTTPException(404, result)
    return result
