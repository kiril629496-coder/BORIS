from fastapi import (
    APIRouter,
    Depends,
)

from app.api.auth import (
    get_current_user,
)

from app.db.session import (
    SessionLocal,
)

from app.factory.client_completion import (
    evaluate,
    prepare_client,
)


router = APIRouter(
    prefix="/api/factory/client-completion",
    tags=["factory"],
)


def _db():

    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.get("/{account_id}")
def status(
    account_id: str,
    db=Depends(_db),
    user=Depends(get_current_user),
):

    return evaluate(
        db,
        account_id,
        ensure_campaign=False,
    )


@router.post("/{account_id}/prepare")
def prepare(
    account_id: str,
    db=Depends(_db),
    user=Depends(get_current_user),
):

    return prepare_client(
        db,
        account_id,
    )
