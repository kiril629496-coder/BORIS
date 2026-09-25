"""ZERO_TOUCH_LAUNCH_V1 — owner-facing launch health API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.platform_roles import is_platform_owner

# Reuse the already proven authentication adapter from Factory N.
from app.api.factory_confirmations import (
    current_user,
    _user_id,
)


router=APIRouter(
    prefix="/api/factory/launches",
    tags=["factory-launches"],
)


def _public_row(row):
    data=dict(row)

    return {
        "account_id":
            data["account_id"],

        "launch_status":
            data["launch_status"],

        "launch_ready":
            data["launch_ready"],

        "readiness_pct":
            data["readiness_pct"],

        "pending_decisions":
            data["pending_decisions"],

        "approved_decisions":
            data["approved_decisions"],

        "rejected_decisions":
            data["rejected_decisions"],

        "required_schedules":
            data["required_schedules"],

        "active_schedules":
            data["active_schedules"],

        "derived_values":
            data["derived_values"],

        "health":
            data["health"],

        "blockers":
            data["blockers"],

        "updated_at":
            data["updated_at"],

        "launched_at":
            data["launched_at"],
    }


@router.get("")
def list_launches(
    user=Depends(current_user),
):
    uid=_user_id(user)

    db=SessionLocal()

    try:
        rows=db.execute(text("""
            SELECT l.*
            FROM factory_client_launch l
            JOIN accounts a
              ON a.account_id=l.account_id
            WHERE a.owner_user_id=:uid
            ORDER BY
                CASE
                    WHEN l.launch_status='NEEDS_DECISION'
                    THEN 0

                    WHEN l.launch_ready
                    THEN 2

                    ELSE 1
                END,
                l.account_id
        """), {
            "uid":uid,
        }).mappings().all()

        summary={
            "total":len(rows),
            "launched":0,
            "needs_decision":0,
            "blocked":0,
            "applying":0,
        }

        for row in rows:
            status=row[
                "launch_status"
            ]

            if row[
                "launch_ready"
            ]:
                summary[
                    "launched"
                ]+=1

            elif status=="NEEDS_DECISION":
                summary[
                    "needs_decision"
                ]+=1

            elif status=="APPLYING":
                summary[
                    "applying"
                ]+=1

            else:
                summary[
                    "blocked"
                ]+=1

        return {
            "status":"ok",
            "summary":summary,
            "items":[
                _public_row(x)
                for x in rows
            ],
        }

    finally:
        db.close()


@router.get("/{account_id}")
def get_launch(
    account_id:str,
    user=Depends(current_user),
):
    uid=_user_id(user)

    db=SessionLocal()

    try:
        row=db.execute(text("""
            SELECT l.*
            FROM factory_client_launch l
            JOIN accounts a ON a.account_id=l.account_id
            WHERE l.account_id=:a
              AND (:platform_owner OR a.owner_user_id=:uid)
        """), {"a":account_id,"uid":uid,"platform_owner":is_platform_owner(user)}).mappings().first()

        if not row:
            # Missing launch state is a normal onboarding state, not an HTTP failure.
            # Keep authorization fail-closed: only expose the empty state when the
            # requested account belongs to this authenticated owner.
            account_exists=db.execute(text("""
                SELECT 1 FROM accounts
                WHERE account_id=:a AND (:platform_owner OR owner_user_id=:uid)
                LIMIT 1
            """), {"a":account_id,"uid":uid,"platform_owner":is_platform_owner(user)}).first()
            if not account_exists:
                raise HTTPException(status_code=404, detail="Account not found")
            return {"status":"ok","item":None,"events":[],"launch_exists":False}

        events=[
            dict(x)
            for x in db.execute(text("""
                SELECT
                    event_type,
                    before_status,
                    after_status,
                    created_at
                FROM factory_client_launch_events
                WHERE account_id=:a
                ORDER BY id DESC
                LIMIT 50
            """), {
                "a":account_id,
            }).mappings().all()
        ]

        return {
            "status":"ok",
            "item":
                _public_row(row),
            "events":
                events,
        }

    finally:
        db.close()
