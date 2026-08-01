from pydantic import BaseModel


class ImportTaskResponse(BaseModel):
    id: int
    status: str