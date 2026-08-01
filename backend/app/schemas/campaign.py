from pydantic import BaseModel

class CampaignCreate(BaseModel):
    name: str


class CampaignResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True