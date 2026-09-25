from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sqlalchemy import text

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.factory.client_launch_orchestrator import discover_contract
from app.services.onboarding_factory_sync import live_launch_contract
from app.services.platform_roles import is_private_platform_owner

router = APIRouter(
    prefix="/api/client-launch",
    tags=["Client Launch"],
)


class LaunchPlanRequest(BaseModel):
    account_id: str


def _require_account_access(current_user, account_id: str) -> None:
    """Reuse BORIS tenant truth; never expose another client's launch state."""
    if is_private_platform_owner(current_user):
        return

    aid = str(account_id or "").strip()
    uid = getattr(current_user, "id", None)
    user_account = str(
        getattr(current_user, "account_id", "")
        or ""
    ).strip()

    if not aid or uid is None:
        raise HTTPException(
            status_code=403,
            detail="Нет доступа к этому аккаунту",
        )

    db = SessionLocal()
    try:
        allowed = db.execute(text("""
            SELECT 1
            FROM accounts
            WHERE account_id=:a
              AND (
                    owner_user_id=:u
                    OR account_id=:user_account
              )

            UNION ALL

            SELECT 1
            FROM user_account_access
            WHERE user_id=:u
              AND account_id=:a
              AND can_view=TRUE

            LIMIT 1
        """), {
            "a": aid,
            "u": uid,
            "user_account": user_account,
        }).first()
    finally:
        db.close()

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Нет доступа к этому аккаунту",
        )


def _live_contract_or_404(account_id: str) -> dict:
    contract = live_launch_contract(account_id)

    if not contract.get("ok"):
        if contract.get("error") == "account_not_found":
            raise HTTPException(
                status_code=404,
                detail="account_not_found",
            )

        raise HTTPException(
            status_code=500,
            detail="launch_contract_unavailable",
        )

    return contract


@router.get("/contract")
def launch_contract(
    current_user=Depends(get_current_user),
):
    return {
        "ok": True,
        "module": "client-launch",
        "version": "stage-k",
        "write_gate": "required",
        "external_writes": False,
        "avito_publish": False,
        "oauth": False,
    }


@router.get("/sources")
def launch_sources(
    current_user=Depends(get_current_user),
):
    return {
        "ok": True,
        "sources": discover_contract(),
    }


@router.post("/plan")
def launch_plan(
    body: LaunchPlanRequest,
    current_user=Depends(get_current_user),
):
    if not body.account_id.strip():
        raise HTTPException(
            status_code=422,
            detail="account_id_required",
        )

    account_id = body.account_id.strip()
    _require_account_access(
        current_user,
        account_id,
    )

    return _live_contract_or_404(
        account_id
    )


@router.get("/summary/{account_id}")
def launch_summary(
    account_id: str,
    current_user=Depends(get_current_user),
):
    if not account_id.strip():
        raise HTTPException(
            status_code=422,
            detail="account_id_required",
        )

    account_id = account_id.strip()
    _require_account_access(
        current_user,
        account_id,
    )

    contract = _live_contract_or_404(
        account_id
    )

    return {
        "ok": contract["ok"],
        "account_id": contract["account_id"],
        "overall_state":
            contract["overall_state"],
        "owner_action_required":
            contract["owner_action_required"],
        "ready_count":
            contract["ready_count"],
        "blocked_count":
            contract["blocked_count"],
        "waiting_count":
            contract["waiting_count"],
        "gated_count":
            contract["gated_count"],
        "write_gate":
            contract["write_gate"],
        "truth":
            contract["truth"],
    }
