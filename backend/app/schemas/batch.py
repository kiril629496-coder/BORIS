from pydantic import BaseModel, Field


class BatchRequest(BaseModel):
    product: str = Field(min_length=1)
    price: int = Field(ge=0)
    city: str = Field(min_length=1)
    count: int = Field(ge=1)


class BatchItem(BaseModel):
    title: str
    description: str
    price: int
    city: str


class BatchResponse(BaseModel):
    success: bool
    total: int
    items: list[BatchItem]
