"""
BORIS CRM — Stage E.2 Connection Control API.

No external CRM writes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.auth import get_current_user

from .service import user_id

from .connection_control import (
    ALLOWED_PROVIDERS,
    all_connection_status,
    authorization_url,
    consume_oauth_state,
    exchange_code,
    provider_identity,
    remote_pipelines,
    save_connection,
)


router = APIRouter(
    prefix="/api/crm/connection-control",
    tags=["CRM Connection Control"],
)


def uid(current_user):
    try:
        return user_id(current_user)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="CRM user context unavailable",
        ) from exc


class OAuthStartRequest(BaseModel):
    provider: str


class CallbackResult(BaseModel):
    ok: bool
    provider: str
    status: str


@router.get("/status")
def connection_control_status(
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    return {
        "ok": True,
        "owner_user_id": owner,
        "providers": all_connection_status(
            owner
        ),
        "external_writes": False,
    }


@router.post("/oauth/start")
def oauth_start(
    req: OAuthStartRequest,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    provider = req.provider.lower().strip()

    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail="unsupported provider",
        )

    try:

        url = authorization_url(
            owner,
            provider,
        )

    except RuntimeError as exc:

        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    return {
        "ok": True,
        "provider": provider,
        "authorization_url": url,
        "external_writes": False,
    }


@router.get(
    "/oauth/{provider}/callback"
)
def oauth_callback(
    provider: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    provider = provider.lower().strip()

    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail="unsupported provider",
        )

    if error:
        raise HTTPException(
            status_code=400,
            detail=f"oauth_error:{error}",
        )

    if not code:
        raise HTTPException(
            status_code=400,
            detail="oauth_code_missing",
        )

    if not state:
        raise HTTPException(
            status_code=400,
            detail="oauth_state_missing",
        )

    if not consume_oauth_state(
        owner,
        provider,
        state,
    ):
        raise HTTPException(
            status_code=400,
            detail="oauth_state_invalid_or_expired",
        )

    try:

        token_data = exchange_code(
            provider,
            code,
        )

        identity = provider_identity(
            provider,
            token_data,
        )

        save_connection(
            owner,
            provider,
            token_data,
            identity,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail="provider_oauth_exchange_failed",
        ) from exc

    return {
        "ok": True,
        "provider": provider,
        "status": "active",
        "external_writes": False,
    }


@router.get(
    "/{provider}/pipelines"
)
def provider_pipelines(
    provider: str,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    provider = provider.lower().strip()

    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail="unsupported provider",
        )

    try:

        data = remote_pipelines(
            owner,
            provider,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail="provider_read_failed",
        ) from exc

    return {
        "ok": True,
        "provider": provider,
        "data": data,
        "external_writes": False,
    }


@router.post("/disconnect/{provider}")
def disconnect(
    provider: str,
    current_user=Depends(get_current_user),
):
    #
    # This is a BORIS-side disconnect.
    # It does not revoke the external application.
    #
    owner = uid(current_user)

    provider = provider.lower().strip()

    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail="unsupported provider",
        )

    from sqlalchemy import text
    from .db import SessionLocal

    db = SessionLocal()

    try:

        db.execute(
            text(
                """
                UPDATE crm_external_connections
                SET status = 'revoked',
                    updated_at = NOW()
                WHERE owner_user_id = :owner
                  AND provider = :provider
                """
            ),
            {
                "owner": owner,
                "provider": provider,
            },
        )

        db.commit()

    finally:
        db.close()

    return {
        "ok": True,
        "provider": provider,
        "status": "revoked",
        "external_writes": False,
    }
