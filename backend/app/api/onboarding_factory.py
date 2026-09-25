"""Zero-touch Factory endpoints for the existing onboarding."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.services import onboarding_factory_sync as sync


router = APIRouter(
    prefix="/api/onboarding",
    tags=["onboarding-factory"],
)


@router.get("/factory-readiness")
def factory_readiness(
    current_user=Depends(get_current_user),
):
    try:
        return sync.readiness(current_user)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "factory_readiness_failed",
                "message": str(e)[:500],
            },
        )


@router.post("/factory-sync")
def factory_sync(
    full: bool = False,
    current_user=Depends(get_current_user),
):
    try:
        if full:
            return sync.full_sync(current_user)

        return sync.bridge_sync(current_user)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "factory_sync_failed",
                "message": str(e)[:1000],
            },
        )
