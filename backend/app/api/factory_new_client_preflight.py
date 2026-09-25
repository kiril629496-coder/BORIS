from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import require_owner


router = APIRouter(
    prefix="/api/factory/new-client-launch",
    tags=["factory"],
)


class NewClientLaunchPreflightBody(BaseModel):
    owner_user_id: int = Field(..., gt=0)
    product: str = Field(..., min_length=1, max_length=64)
    account_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )


def _connect():
    return psycopg2.connect(
        os.environ["DATABASE_URL"]
    )


def evaluate_preflight(
    *,
    owner_user_id: int,
    product: str,
    account_id: str | None,
) -> dict[str, Any]:

    product = product.strip().lower()

    account_id = (
        account_id.strip()
        if account_id
        else None
    )

    con = _connect()

    try:
        with con.cursor() as cur:

            ###################################################################
            # OWNER
            ###################################################################

            cur.execute(
                """
                SELECT id
                FROM users
                WHERE id=%s
                """,
                (owner_user_id,),
            )

            owner_exists = (
                cur.fetchone() is not None
            )

            ###################################################################
            # LICENSE
            ###################################################################

            cur.execute(
                """
                SELECT
                    qty,
                    paid_until
                FROM ai_licenses
                WHERE user_id=%s
                  AND lower(product)=lower(%s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    owner_user_id,
                    product,
                ),
            )

            license_row = cur.fetchone()

            licensed_qty = 0
            paid_until = None

            if license_row:
                licensed_qty = int(
                    license_row[0] or 0
                )

                paid_until = license_row[1]

            now = datetime.now(
                timezone.utc
            )

            license_active = bool(
                license_row
                and licensed_qty > 0
                and (
                    paid_until is None
                    or paid_until >= now
                )
            )

            ###################################################################
            # SLOT CAPACITY
            ###################################################################

            cur.execute(
                """
                SELECT
                    slot_no,
                    account_id,
                    status
                FROM account_slots
                WHERE owner_user_id=%s
                  AND lower(product)=lower(%s)
                ORDER BY slot_no
                """,
                (
                    owner_user_id,
                    product,
                ),
            )

            slot_rows = cur.fetchall()

            #
            # Unique index blocks reuse of occupied slot_no even if account_id
            # is NULL, therefore next_free_slot_no must consider ALL rows.
            #
            occupied_numbers = {
                int(row[0])
                for row in slot_rows
                if row[0] is not None
            }

            #
            # Capacity counts only assignments to real accounts.
            #
            assigned_count = sum(
                1
                for row in slot_rows
                if row[1] is not None
            )

            free_slot_no = 1

            while free_slot_no in occupied_numbers:
                free_slot_no += 1

            free_capacity = max(
                0,
                licensed_qty - assigned_count,
            )

            ###################################################################
            # ACCOUNT COLLISION
            ###################################################################

            account_exists = False

            if account_id:

                cur.execute(
                    """
                    SELECT 1
                    FROM accounts
                    WHERE account_id=%s
                    LIMIT 1
                    """,
                    (account_id,),
                )

                account_exists = (
                    cur.fetchone() is not None
                )

            ###################################################################
            # BLOCKERS
            ###################################################################

            blockers: list[str] = []

            if not owner_exists:
                blockers.append(
                    "OWNER_NOT_FOUND"
                )

            if not license_row:
                blockers.append(
                    "LICENSE_NOT_FOUND"
                )

            elif not license_active:
                blockers.append(
                    "LICENSE_INACTIVE"
                )

            if (
                license_active
                and free_capacity <= 0
            ):
                blockers.append(
                    "NO_FREE_LICENSE_CAPACITY"
                )

            if account_exists:
                blockers.append(
                    "ACCOUNT_ID_ALREADY_EXISTS"
                )

            ready = (
                len(blockers) == 0
            )

            return {
                "ok": True,
                "ready": ready,

                "owner_user_id":
                    owner_user_id,

                "product":
                    product,

                "account_id":
                    account_id,

                "owner_exists":
                    owner_exists,

                "license": {
                    "exists":
                        license_row is not None,

                    "active":
                        license_active,

                    "qty":
                        licensed_qty,

                    "paid_until":
                        (
                            paid_until.isoformat()
                            if paid_until
                            else None
                        ),
                },

                "slots": {
                    "assigned":
                        assigned_count,

                    "free_capacity":
                        free_capacity,

                    "next_free_slot_no":
                        free_slot_no,

                    "occupied_slot_nos":
                        sorted(
                            occupied_numbers
                        ),
                },

                "account_exists":
                    account_exists,

                "blockers":
                    blockers,
            }

    finally:
        con.close()


@router.post("/preflight")
def new_client_launch_preflight(
    body: NewClientLaunchPreflightBody,
    _=Depends(require_owner),
) -> dict[str, Any]:

    return evaluate_preflight(
        owner_user_id=body.owner_user_id,
        product=body.product,
        account_id=body.account_id,
    )
