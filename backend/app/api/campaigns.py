from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.schemas.base import APIResponse
from app.schemas.campaign import CampaignResponse

from app.services.campaign_service import get_campaigns, create_campaign

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("", response_model=APIResponse[List[CampaignResponse]])
def list_campaigns(db: Session = Depends(get_db)):
    return APIResponse(data=get_campaigns(db))


@router.post("", response_model=APIResponse[CampaignResponse])
def add_campaign(name: str, db: Session = Depends(get_db)):
    return APIResponse(data=create_campaign(db, name))