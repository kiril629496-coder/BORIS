"""
BORIS CRM — Sync Core API.

Stage E.1:
    - diagnostics
    - connection state
    - outbox queue
    - sync stats

NO EXTERNAL CRM WRITE.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user

from .service import user_id
from .sync_core import (
    ALLOWED_PROVIDERS,
    enqueue,
    install_sync_core,
    connection_status,
    stats,
)


router = APIRouter(
    prefix="/api/crm/sync-core",
    tags=["CRM Sync Core"],
)


def uid(current_user):
    try:
        return user_id(current_user)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="CRM user context unavailable",
        ) from exc


class QueueRequest(BaseModel):
    provider: str
    operation: str
    entity_type: str
    boris_entity_id: Optional[int] = None
    external_entity_id: Optional[str] = None
    payload: dict
    origin: str = "boris"
    idempotency_key: Optional[str] = None


@router.get("/health")
def sync_core_health(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    return {
        "ok": True,
        "module": "crm_sync_core",
        "version": "e1",
        "owner_user_id": owner,
        "providers": sorted(ALLOWED_PROVIDERS),
        "external_writes": False,
    }


@router.get("/connections")
def sync_core_connections(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    return {
        provider: connection_status(owner, provider)
        for provider in sorted(ALLOWED_PROVIDERS)
    }


@router.get("/stats")
def sync_core_stats(current_user=Depends(get_current_user)):
    owner = uid(current_user)

    return {
        "ok": True,
        "owner_user_id": owner,
        **stats(owner),
        "external_writes": False,
    }


@router.post("/queue")
def sync_core_queue(
    req: QueueRequest,
    current_user=Depends(get_current_user),
):
    owner = uid(current_user)

    try:
        result = enqueue(
            owner_user_id=owner,
            provider=req.provider,
            operation=req.operation,
            entity_type=req.entity_type,
            boris_entity_id=req.boris_entity_id,
            external_entity_id=req.external_entity_id,
            payload=req.payload,
            origin=req.origin,
            idempotency_key=req.idempotency_key,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "ok": True,
        **result,
        "external_write": False,
    }
