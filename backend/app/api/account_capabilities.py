# -*- coding: utf-8 -*-

"""
BORIS universal account capabilities.

No client-specific branches.
Input: account_id
Output: available modules for that account.

READ ONLY.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.client_config.entitlements import has_entitlement
from app.services.exception_observability import observe_suppressed

router = APIRouter(
    prefix="/api/account-capabilities",
    tags=["account-capabilities"],
)


def _table_exists(db, table: str) -> bool:
    try:
        return bool(
            db.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema='public'
                      AND table_name=:t
                    LIMIT 1
                    """
                ),
                {"t": table},
            ).scalar()
        )
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=45)
        return False


def _count_by_account(db, table: str, account_id: str):
    if not _table_exists(db, table):
        return None

    try:
        cols = db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name=:t
                """
            ),
            {"t": table},
        ).scalars().all()

        if "account_id" not in cols:
            return None

        return int(
            db.execute(
                text(
                    f'''
                    SELECT count(*)
                    FROM "{table}"
                    WHERE account_id=:a
                    '''
                ),
                {"a": account_id},
            ).scalar()
            or 0
        )
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=86)
        return None


def _mop_mode(db, account_id: str):
    if not _table_exists(db, "mop_modes"):
        return None

    try:
        row = db.execute(
            text(
                """
                SELECT *
                FROM mop_modes
                WHERE account_id=:a
                LIMIT 1
                """
            ),
            {"a": account_id},
        ).mappings().first()

        return dict(row) if row else None
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=112)
        return None


@router.get("")
def get_account_capabilities(
    account_id: str,
    user=Depends(get_current_user),
):
    db = SessionLocal()

    try:
        mop = has_entitlement(
            db,
            account_id,
            "mop",
        )

        try:
            from app.api.messenger import get_manager_balance

            balance = get_manager_balance(account_id)
        except Exception:
            balance = {
                "active": bool(mop.get("allowed")),
                "purchased": 0,
                "used": 0,
                "left": 0,
            }

        crm_contacts = None
        crm_deals = None
        crm_tasks = None

        for t in ("crm_contacts", "contacts"):
            n = _count_by_account(db, t, account_id)
            if n is not None:
                crm_contacts = n
                break

        for t in ("crm_deals", "deals"):
            n = _count_by_account(db, t, account_id)
            if n is not None:
                crm_deals = n
                break

        for t in ("crm_tasks", "tasks"):
            n = _count_by_account(db, t, account_id)
            if n is not None:
                crm_tasks = n
                break

        entitled = bool(mop.get("allowed"))

        return {
            "status": "ok",
            "account_id": account_id,

            "crm": {
                "available": True,
                "contacts": crm_contacts,
                "deals": crm_deals,
                "tasks": crm_tasks,
            },

            "mop": {
                "entitled": entitled,
                "state": mop.get("state"),
                "reason": mop.get("reason"),
                "evidence": mop.get("evidence"),

                "balance": balance,
                "mode": _mop_mode(db, account_id),

                "drafts": entitled,
                "training": entitled,
                "auto_dialog": entitled,
                "schedule": entitled,
            },
        }

    finally:
        db.close()
