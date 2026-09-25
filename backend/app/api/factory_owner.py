"""
FACTORY_N12_OWNER_API_V1

Decision-only owner API.

This module:
- reads pending confirmations;
- approves through the existing approval engine;
- rejects pending confirmations.

It does NOT:
- call H;
- call K;
- call subprocess;
- write factory_schedule;
- publish anything;
- spend money;
- send messages.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.auth import require_owner
from app.factory.approval import approve_one


MARKER = "FACTORY_N12_OWNER_API_V1"

router = APIRouter(
    prefix="/api/factory/owner",
    tags=["factory-owner"],
)




def _serialize(row):
    result = dict(row)

    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat()

    return result


@router.get("/pending")
def pending(user=Depends(require_owner)):
    db = SessionLocal()

    try:
        rows = db.execute(text("""
            SELECT
                id,
                account_id,
                field,
                operation,
                fingerprint,
                status,
                payload,
                created_at,
                updated_at,
                decided_at,
                decided_by
            FROM factory_confirmations
            WHERE status='pending'
            ORDER BY created_at ASC, id ASC
        """)).mappings().all()

        return {
            "marker": MARKER,
            "count": len(rows),
            "items": [_serialize(row) for row in rows],
        }

    finally:
        db.close()


@router.get("/{confirmation_id}")
def get_confirmation(confirmation_id: int, user=Depends(require_owner)):
    db = SessionLocal()

    try:
        row = db.execute(text("""
            SELECT
                id,
                account_id,
                field,
                operation,
                fingerprint,
                status,
                payload,
                created_at,
                updated_at,
                decided_at,
                decided_by
            FROM factory_confirmations
            WHERE id=:id
        """), {
            "id": confirmation_id,
        }).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="decision_not_found",
            )

        return {
            "marker": MARKER,
            "item": _serialize(row),
        }

    finally:
        db.close()


@router.post("/{confirmation_id}/approve")
def approve(
    confirmation_id: int,
    user=Depends(require_owner),
):
    db = SessionLocal()

    try:
        row = db.execute(text("""
            SELECT
                id,
                account_id,
                field,
                operation,
                fingerprint,
                status
            FROM factory_confirmations
            WHERE id=:id
        """), {
            "id": confirmation_id,
        }).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="decision_not_found",
            )

        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"decision_not_pending:{row['status']}",
            )

        owner_id = getattr(user, "id", None)

        if owner_id is None:
            raise HTTPException(
                status_code=500,
                detail="authenticated_owner_id_missing",
            )

        result = approve_one(
            db,
            confirmation_id,
            int(owner_id),
        )

        db.commit()

        return {
            "marker": MARKER,
            "result": "approved",
            "confirmation_id": result.confirmation_id,
            "account_id": result.account_id,
            "field": result.field,
            "operation": result.operation,
            "status": result.status,
            "execution": "NOT_STARTED_BY_API",
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@router.post("/{confirmation_id}/reject")
def reject(
    confirmation_id: int,
    user=Depends(require_owner),
):
    db = SessionLocal()

    try:
        row = db.execute(text("""
            UPDATE factory_confirmations
            SET
                status='rejected',
                updated_at=:now,
                decided_at=:now,
                decided_by=:actor
            WHERE id=:id
              AND status='pending'
            RETURNING
                id,
                account_id,
                field,
                operation,
                status
        """), {
            "id": confirmation_id,
            "now": datetime.now(timezone.utc),
            "actor": str(
                getattr(user, "id", None)
            ),
        }).mappings().first()

        if row is None:
            exists = db.execute(text("""
                SELECT status
                FROM factory_confirmations
                WHERE id=:id
            """), {
                "id": confirmation_id,
            }).scalar()

            if exists is None:
                raise HTTPException(
                    status_code=404,
                    detail="decision_not_found",
                )

            raise HTTPException(
                status_code=409,
                detail=f"decision_not_pending:{exists}",
            )

        db.commit()

        return {
            "marker": MARKER,
            "result": "rejected",
            "confirmation_id": int(row["id"]),
            "account_id": str(row["account_id"]),
            "field": str(row["field"]),
            "operation": str(row["operation"]),
            "status": str(row["status"]),
            "execution": "NOT_STARTED",
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
