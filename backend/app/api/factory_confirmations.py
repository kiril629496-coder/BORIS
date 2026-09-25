"""FACTORY_N_V1 — owner-facing Factory confirmation API.

Endpoints:
GET  /api/factory/confirmations
POST /api/factory/confirmations/{id}/approve
POST /api/factory/confirmations/{id}/reject
POST /api/factory/confirmations/bulk

Security:
- authenticated user required
- user can operate only accounts owned by them
- stale/non-pending cannot be decided
- approval itself writes confirmation decision only
- optional immediate Factory cycle invokes existing H→K path
"""

import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal
from app.factory import confirmations as C


router = APIRouter(
    prefix="/api/factory/confirmations",
    tags=["factory-confirmations"],
)


# ---------------------------------------------------------------------------
# Auth adapter
# ---------------------------------------------------------------------------

def _auth_dependency():
    """Resolve an existing BORIS auth dependency without creating a second auth."""

    candidates = [
        ("app.auth", "get_current_user"),
        ("app.auth", "require_user"),
        ("app.api.auth", "get_current_user"),
        ("app.dependencies", "get_current_user"),
        ("app.api.dependencies", "get_current_user"),
    ]

    import importlib

    for module_name, attr in candidates:
        try:
            mod = importlib.import_module(module_name)
            fn = getattr(mod, attr, None)

            if callable(fn):
                return fn
        except Exception:
            continue

    raise RuntimeError(
        "FACTORY_N: existing auth dependency was not found"
    )


_EXISTING_AUTH = _auth_dependency()


def current_user(user=Depends(_EXISTING_AUTH)):
    return user


def _user_id(user):
    if isinstance(user, dict):
        for key in ("id", "user_id"):
            if user.get(key) is not None:
                return int(user[key])

    for key in ("id", "user_id"):
        value = getattr(user, key, None)

        if value is not None:
            return int(value)

    raise HTTPException(
        status_code=401,
        detail="Unable to resolve authenticated user id",
    )


def _assert_owner(db, user_id: int, confirmation_id: int):
    row = db.execute(text("""
        SELECT
            c.id,
            c.account_id,
            c.field,
            c.operation,
            c.status,
            c.payload,
            a.owner_user_id
        FROM factory_confirmations c
        JOIN accounts a
          ON a.account_id=c.account_id
        WHERE c.id=:id
    """), {
        "id": confirmation_id,
    }).mappings().first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Confirmation not found",
        )

    if row["owner_user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="Confirmation belongs to another owner",
        )

    return row


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BulkDecision(BaseModel):
    ids: list[int]
    decision: Literal["approved", "rejected"]
    # FACTORY_N8_SINGLE_EXECUTION_PATH_V1
    # Kept for backward request compatibility only.
    # API NEVER executes approved Factory actions.
    # K is the sole execution owner.


class SingleDecision(BaseModel):
    # FACTORY_N8_SINGLE_EXECUTION_PATH_V1
    # Kept for backward request compatibility only.
    # API NEVER executes approved Factory actions.
    # K is the sole execution owner.
    pass  # BORIS_EMPTY_CLASS_SYNTAX_FIX_V1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_factory_cycle():
    """Run the already-proven H→K pipeline synchronously.

    This is deliberate:
    approval is committed first, then H regenerates E/J/F and K consumes only
    current approved fingerprints.
    """

    import subprocess

    runtime_backend = os.environ.get("BORIS_RUNTIME_BACKEND_ROOT", "/root/BORIS/backend")
    proc = subprocess.run(
        [
            "/root/BORIS/backend/venv/bin/python3",
            os.path.join(runtime_backend, "factory_h_runner.py"),
        ],
        cwd="/root/BORIS/backend",
        text=True,
        capture_output=True,
        timeout=180,
    )

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(
            proc.stdout.splitlines()[-30:]
        ),
        "stderr_tail": "\n".join(
            proc.stderr.splitlines()[-30:]
        ),
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@router.get("")
def list_confirmations(user=Depends(current_user)):
    uid = _user_id(user)

    db = SessionLocal()

    try:
        rows = db.execute(text("""
            SELECT
                c.id,
                c.account_id,
                c.field,
                c.operation,
                c.status,
                c.payload,
                c.created_at,
                c.decided_at,
                c.decided_by,
                f.readiness_pct,
                f.launch_status
            FROM factory_confirmations c
            JOIN accounts a
              ON a.account_id=c.account_id
            LEFT JOIN factory_launch_state f
              ON f.account_id=c.account_id
            WHERE a.owner_user_id=:uid
              AND c.status IN (
                  'pending',
                  'approved',
                  'rejected',
                  'stale'
              )
            ORDER BY
                CASE c.status
                    WHEN 'pending' THEN 0
                    ELSE 1
                END,
                c.account_id,
                c.id
        """), {
            "uid": uid,
        }).mappings().all()

        pending = sum(
            1
            for x in rows
            if x["status"] == "pending"
        )

        return {
            "status": "ok",
            "pending": pending,
            "items": [
                dict(x)
                for x in rows
            ],
        }

    finally:
        db.close()


def _decide_one(
    confirmation_id: int,
    decision: str,
    user,
):
    uid = _user_id(user)

    db = SessionLocal()

    try:
        _assert_owner(
            db,
            uid,
            confirmation_id,
        )

        result = C.decide(
            db,
            confirmation_id,
            decision,
            "owner:%s" % uid,
        )

        db.commit()

    except HTTPException:
        db.rollback()
        raise

    except ValueError as e:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=str(e),
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    cycle = None


    return {
        "status": "ok",
        "decision": result,
        "factory": cycle,
    }


@router.post("/{confirmation_id}/approve")
def approve(
    confirmation_id: int,
    body: SingleDecision,
    user=Depends(current_user),
):
    return _decide_one(
        confirmation_id,
        "approved",
        user,
    )


@router.post("/{confirmation_id}/reject")
def reject(
    confirmation_id: int,
    body: SingleDecision,
    user=Depends(current_user),
):
    return _decide_one(
        confirmation_id,
        "rejected",
        user,
    )


@router.post("/bulk")
def bulk_decide(
    body: BulkDecision,
    user=Depends(current_user),
):
    uid = _user_id(user)

    ids = list(dict.fromkeys(body.ids))

    if not ids:
        raise HTTPException(
            status_code=422,
            detail="No confirmation ids supplied",
        )

    if len(ids) > 100:
        raise HTTPException(
            status_code=422,
            detail="Maximum 100 confirmations per bulk request",
        )

    db = SessionLocal()

    results = []

    try:
        # Fail the whole batch if even one row is foreign/missing/non-pending.
        rows=[]

        for cid in ids:
            rows.append(
                _assert_owner(
                    db,
                    uid,
                    cid,
                )
            )

        not_pending=[
            x["id"]
            for x in rows
            if x["status"] != "pending"
        ]

        if not_pending:
            raise HTTPException(
                status_code=409,
                detail={
                    "message":
                        "Some confirmations are not pending",
                    "ids":
                        not_pending,
                },
            )

        for cid in ids:
            results.append(
                C.decide(
                    db,
                    cid,
                    body.decision,
                    "owner:%s" % uid,
                )
            )

        db.commit()

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    cycle = None


    return {
        "status": "ok",
        "count": len(results),
        "decision": body.decision,
        "items": results,
        "factory": cycle,
    }
