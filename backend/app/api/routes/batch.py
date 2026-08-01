from fastapi import APIRouter

from app.schemas.batch import BatchItem, BatchRequest, BatchResponse
from services.batch.service import BatchService

router = APIRouter(prefix="/batch", tags=["batch"])
batch_service = BatchService()


@router.post("/create", response_model=BatchResponse)
async def create_batch(request: BatchRequest) -> BatchResponse:
    items = batch_service.create_batch(
        product=request.product,
        price=request.price,
        city=request.city,
        count=request.count,
    )

    return BatchResponse(
        success=True,
        total=request.count,
        items=[
            BatchItem(
                title=item.title,
                description=item.description,
                price=item.price,
                city=item.city,
            )
            for item in items
        ],
    )
