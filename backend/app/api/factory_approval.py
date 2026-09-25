"""FACTORY_N_V3 approval API.

Important:
approval endpoint is deliberately fail-closed until the real
BORIS authentication dependency is wired.

No identity is guessed.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.session import SessionLocal


FACTORY_N_V3 = "FACTORY_N_V3"

router = APIRouter(
    prefix="/api/factory/approvals",
    tags=["factory-approvals"],
)


@router.get("")
def list_pending():
    db = SessionLocal()

    try:
        rows = db.execute(text("""
            SELECT
                id,
                account_id,
                kind,
                status,
                payload,
                created_at,
                updated_at
            FROM factory_confirmations
            WHERE status='pending'
            ORDER BY created_at ASC, id ASC
        """)).mappings().all()

        return {
            "items": [dict(x) for x in rows],
            "count": len(rows),
        }

    finally:
        db.close()


@router.post("/{confirmation_id}/approve")
def approve(confirmation_id: int):
    raise HTTPException(
        status_code=401,
        detail="authentication_required",
    )
