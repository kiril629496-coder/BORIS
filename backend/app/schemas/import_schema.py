from pydantic import BaseModel
from typing import List


class ImportItem(BaseModel):
    index: int
    value: str
    status: str


class ImportResponse(BaseModel):
    total: int
    results: List[ImportItem]