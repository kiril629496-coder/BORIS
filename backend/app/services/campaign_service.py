from sqlalchemy.orm import Session
from app.repositories import campaign_repository as repo


def get_campaigns(db: Session):
    return repo.get_all(db)


def create_campaign(db: Session, name: str):
    return repo.create(db, name)